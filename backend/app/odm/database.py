"""The ``db`` object: MongoDB connection per app, one session per app context, schema (collections and indexes)."""
import os
import threading

from flask import current_app, g
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import CollectionInvalid

from . import columns as types
from .model import CheckConstraint, Index, Model, Relationship, UniqueConstraint, registry
from .query import Query
from .session import IntegrityError, Session

# Case-insensitive comparisons, sorting and unique keys (e-mail addresses, codes, names).
COLLATION = {"locale": "en", "strength": 2}
COUNTERS = "_counters"


class _Connection:
    def __init__(self, uri, name):
        self.uri, self.name = uri, name
        self._client = self._pid = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None or self._pid != os.getpid():    # never share a client across a fork
            with self._lock:
                if self._client is None or self._pid != os.getpid():
                    self._client = MongoClient(self.uri, appname="plms", serverSelectionTimeoutMS=10000)
                    self._pid = os.getpid()
        return self._client

    @property
    def mongo(self):
        return self.client[self.name]

    def next_ids(self, model, count):
        """Reserve `count` consecutive ids and return the first. Done outside any transaction so concurrent requests
        never conflict on the counter."""
        doc = self.mongo[COUNTERS].find_one_and_update(
            {"_id": model.__tablename__}, {"$inc": {"seq": count}}, upsert=True, return_document=ReturnDocument.AFTER)
        return doc["seq"] - count + 1

    def reserve_id(self, model, value):
        self.mongo[COUNTERS].update_one({"_id": model.__tablename__}, {"$max": {"seq": value}}, upsert=True)


class _SessionProxy:
    """``db.session`` - the session of the current app context."""

    def __getattr__(self, name):
        return getattr(Database.current_session(), name)

    def remove(self):
        Database.close_session()


class Database:
    Model = Model
    Column = types.Column
    ForeignKey = types.ForeignKey
    Integer = types.Integer
    BigInteger = types.BigInteger
    Boolean = types.Boolean
    String = types.String
    Text = types.Text
    Date = types.Date
    DateTime = types.DateTime
    Numeric = types.Numeric
    JSON = types.JSON
    relationship = Relationship
    UniqueConstraint = UniqueConstraint
    Index = Index
    CheckConstraint = CheckConstraint
    or_ = staticmethod(types.or_)
    and_ = staticmethod(types.and_)

    def __init__(self):
        self.session = _SessionProxy()

    @staticmethod
    def false():
        return False

    def init_app(self, app):
        app.extensions["mongo"] = _Connection(app.config["MONGODB_URI"], app.config["MONGODB_DB"])
        app.teardown_appcontext(lambda exc: self.close_session())

    @staticmethod
    def connection():
        return current_app.extensions["mongo"]

    @staticmethod
    def current_session():
        session = g.get("_mongo_session")
        if session is None:
            session = g._mongo_session = Session(Database.connection())
        return session

    @staticmethod
    def close_session():
        session = g.pop("_mongo_session", None)
        if session is not None:
            session.close()

    def select(self, *entities):
        return Query(self.current_session, entities)

    def paginate(self, query, page=1, per_page=20, error_out=False):
        return query.paginate(page=page, per_page=per_page)

    def ping(self):
        self.connection().client.admin.command("ping")

    # ---------------------------------------------------------------- schema
    def create_all(self):
        """Create the collections and indexes. Idempotent."""
        mongo = self.connection().mongo
        existing = set(mongo.list_collection_names())
        for table, model in registry.by_table.items():
            if table not in existing:
                try:
                    mongo.create_collection(table, collation=COLLATION)
                except CollectionInvalid:
                    pass
            collection = mongo[table]
            for name, column in model._columns.items():
                if column.primary_key:
                    continue
                if column.unique:
                    # NULLs do not collide, as in SQL
                    partial = ({"partialFilterExpression": {name: {"$gt": _lowest(column)}}}
                               if column.nullable else {})
                    collection.create_index(name, unique=True, name=f"uq_{name}", **partial)
                elif column.index or column.foreign_key:
                    collection.create_index(name, name=f"ix_{name}")
            for rule in getattr(model, "__table_args__", ()):
                if isinstance(rule, UniqueConstraint):
                    collection.create_index([(f, 1) for f in rule.fields], unique=True, name=rule.name)
                elif isinstance(rule, Index):
                    collection.create_index([(f, 1) for f in rule.fields], name=rule.name)
        if COUNTERS not in existing:
            try:
                mongo.create_collection(COUNTERS)
            except CollectionInvalid:
                pass

    def clear_all(self):
        """Delete every document and restart the ids (tests)."""
        self.close_session()
        mongo = self.connection().mongo
        for name in mongo.list_collection_names():
            if mongo[name].find_one({}, {"_id": 1}) is not None:
                mongo[name].delete_many({})

    def drop_all(self):
        self.close_session()
        connection = self.connection()
        connection.client.drop_database(connection.name)


def _lowest(column):
    """A value below every real value of the column's type, so the partial index skips NULLs."""
    return "" if isinstance(column.type, types.String) else float("-inf")


__all__ = ["Database", "IntegrityError"]
