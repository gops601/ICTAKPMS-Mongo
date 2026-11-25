"""Unit of work: identity map, change tracking, referential integrity and MongoDB transactions.

* Reads before the first write run outside a transaction; the first write (or row lock) starts one, and ``commit()``
  ends it. Anything not committed when the app context ends is rolled back.
* ``with_for_update()`` / ``refresh(obj, with_for_update=True)`` write-lock documents: a competing transaction fails
  with a transient error and the request is retried (see ``app.odm.retry``).
* A flush sends one command per collection, not one per object: MongoDB aborts a transaction after 60 seconds, and
  every round trip to a hosted cluster counts against that.
* Foreign keys are enforced here on delete (CASCADE / SET NULL / RESTRICT), unique keys by MongoDB indexes.
"""
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError, PyMongoError

from .model import registry
from .query import NoResultFound, Query


MAX_BATCH = 500


class IntegrityError(Exception):
    """A unique, required-field, range or reference rule was violated."""


def _write_error(exc):
    """A duplicate key is a broken rule; anything else (a write conflict, say) is passed on unchanged."""
    errors = exc.details.get("writeErrors", []) if isinstance(exc, BulkWriteError) else []
    if isinstance(exc, DuplicateKeyError) or any(e.get("code") == 11000 for e in errors):
        return IntegrityError(str(exc))
    return exc


class Session:
    def __init__(self, database):
        self.database = database                 # callable-free handle: .mongo (pymongo Database) and .client
        self.client_session = None
        self.identity = {}
        self.new = []
        self.dirty = {}
        self.pending_delete = []
        self.commits = 0
        self._flushing = False
        self._reads = {}                         # identical reads between two writes are answered once

    # -------------------------------------------------------------- basics
    def collection(self, model):
        return self.database.mongo[model.__tablename__]

    def query(self, *entities):
        return Query(lambda: self, entities)

    def cached_read(self, model, key, load):
        """Repeating a read with no write in between returns the first answer (like a repeatable-read snapshot), which
        saves a round trip each time. A write empties the cache of its collection; locks, commits and rollbacks empty
        all of it."""
        cache = self._reads.setdefault(model.__tablename__, {})
        key = repr(key)
        if key not in cache:
            cache[key] = load()
        return cache[key]

    def _written(self, model):
        self._reads.pop(model.__tablename__, None)

    def _begin(self):
        if self.client_session is None:
            self.client_session = self.database.client.start_session()
        if not self.client_session.in_transaction:
            self.client_session.start_transaction()

    def materialise(self, model, doc):
        """The one object of this session for a loaded document."""
        key = (model, doc["_id"])
        obj = self.identity.get(key)
        full = set(doc) >= {c.db_name for c in model._columns.values()}
        if obj is None:
            if not full:                         # a projected (sorted) document: read the whole one
                doc = self.collection(model).find_one({"_id": doc["_id"]}, session=self.client_session)
            obj = self.identity[key] = model._from_document(doc, self)
        elif obj._expired:
            obj._apply_document(doc)
        return obj

    def reload(self, obj):
        """Read an expired object again, together with the other expired objects of its model (one round trip)."""
        model = type(obj)
        peers = {o._key: o for (m, _), o in list(self.identity.items()) if m is model and o._expired}
        peers[obj._key] = obj
        found = set()
        for doc in self.collection(model).find({"_id": {"$in": list(peers)}}, session=self.client_session):
            peers[doc["_id"]]._apply_document(doc)
            found.add(doc["_id"])
        if obj._key not in found:
            raise NoResultFound(f"{model.__name__} {obj._key} no longer exists")

    def get(self, model, ident):
        if ident is None:
            return None
        self.autoflush()
        obj = self.identity.get((model, ident))
        if obj is not None and not obj._expired:
            return obj
        doc = self.cached_read(model, ("get", ident), lambda: self.collection(model).find_one(
            {"_id": ident}, session=self.client_session))
        if doc is None:
            self.identity.pop((model, ident), None)
            return None
        return self.materialise(model, doc)

    def scalars(self, query):
        return query.scalars()

    def scalar(self, query):
        return query.scalar()

    # ------------------------------------------------------- relationships
    def load_relationship(self, obj, rel):
        """Lazy loading in batches: the first access loads the relationship for every loaded object of the model, so a
        loop over many objects costs one query instead of one each."""
        target, key, many_to_one = rel.resolve()
        if obj._key is None:                     # not saved yet
            if many_to_one:
                return self.get(target, obj._data.get(key.name))
            return [] if rel.is_list else None
        self.autoflush()
        obj._load_if_expired()
        model = type(obj)
        peers = [o for (m, _), o in list(self.identity.items())
                 if m is model and o is not obj and not o._expired and rel.name not in o._rel]
        self._prefetch_one([obj, *peers[:MAX_BATCH]], rel.name)
        return obj._rel.get(rel.name)

    def prefetch(self, objects, *paths):
        """Load relationships for many objects with one query per level, e.g. ``prefetch(projects, "batch.ou")``."""
        self.autoflush()
        for path in paths:
            level = list(objects)
            for name in path.split("."):
                if not level:
                    break
                level = self._prefetch_one(level, name)
        return objects

    def _prefetch_one(self, objects, name):
        for o in objects:
            o._load_if_expired()
        rel = type(objects[0])._relationships[name]
        todo = [o for o in objects if name not in o._rel]
        target, key, many_to_one = rel.resolve()
        if todo and many_to_one:
            ids = {o._data.get(key.name) for o in todo} - {None}
            missing = [i for i in ids if (target, i) not in self.identity or self.identity[(target, i)]._expired]
            if missing:
                self.query(target).where(target._pk.in_(missing)).all()
            for o in todo:
                ident = o._data.get(key.name)
                o._rel[name] = self.identity.get((target, ident)) if ident is not None else None
        elif todo:
            q = self.query(target).where(key.in_([o._key for o in todo]))
            if rel.order_field:
                q = q.order_by(target._columns[rel.order_field])
            grouped = {}
            for child in q.all():
                grouped.setdefault(child._data.get(key.name), []).append(child)
            for o in todo:
                children = grouped.get(o._key, [])
                o._rel[name] = children if rel.is_list else (children[0] if children else None)
        found = {}
        for o in objects:
            value = o._rel.get(name)
            for item in (value if isinstance(value, list) else [value]):
                if item is not None:
                    found[id(item)] = item
        return list(found.values())

    def _forget_collections(self, model, values):
        """A child was written: parents holding a cached collection of that model must read it again."""
        for column in model._columns.values():
            if not column.foreign_key or values.get(column.name) is None:
                continue
            parent_model = registry.by_table[column.foreign_key.table]
            parent = self.identity.get((parent_model, values[column.name]))
            if parent is None:
                continue
            for name, rel in parent_model._relationships.items():
                target, key, many_to_one = rel.resolve()
                if not many_to_one and target is model and key is column:
                    parent._rel.pop(name, None)

    # --------------------------------------------------------- state changes
    def add(self, obj):
        if obj._persistent:
            return
        if not any(o is obj for o in self.new):
            obj._session = self
            self.new.append(obj)

    def add_all(self, objects):
        for obj in objects:
            self.add(obj)

    def mark_dirty(self, obj):
        self.dirty[id(obj)] = obj

    def delete(self, obj):
        if not obj._persistent:
            self.new = [o for o in self.new if o is not obj]
        elif not obj._deleted:
            self.pending_delete.append(obj)

    def autoflush(self):
        if not self._flushing and (self.new or self.dirty or self.pending_delete):
            self.flush()

    def flush(self):
        if self._flushing:
            return
        self._flushing = True
        try:
            if self.dirty:
                self._update(list(self.dirty.values()))
                self.dirty = {}
            while self.pending_delete:
                obj = self.pending_delete.pop(0)
                if not obj._deleted:
                    self._delete_documents(type(obj), [obj._key])
            while self.new:
                # pending objects cannot refer to each other (they have no id yet), so each model is inserted at once
                model = type(self.new[0])
                group = [o for o in self.new if type(o) is model]
                self._insert(model, group)
                self.new = [o for o in self.new if type(o) is not model]
        finally:
            self._flushing = False

    def _validate(self, model, values, names):
        for name in names:
            column = model._columns[name]
            if not column.nullable and values.get(name) is None:
                raise IntegrityError(f"{model.__name__}.{name} is required")
        for rule in getattr(model, "__table_args__", ()):
            field = getattr(rule, "field", None)
            if field in names and values.get(field) is not None and not rule.low <= values[field] <= rule.high:
                raise IntegrityError(f"{model.__name__}.{field} must be between {rule.low} and {rule.high}")

    def _insert(self, model, objects):
        pk = model._pk
        for obj in objects:
            for name, column in model._columns.items():
                if obj._data.get(name) is None and column.default is not None:
                    obj._data[name] = column.default_value(column.default)
        unnumbered = [o for o in objects if o._data.get(pk.name) is None]
        if unnumbered:
            first = self.database.next_ids(model, len(unnumbered))
            for offset, obj in enumerate(unnumbered):
                obj._data[pk.name] = first + offset
        for obj in objects:
            if obj not in unnumbered and isinstance(obj._data[pk.name], int):
                self.database.reserve_id(model, obj._data[pk.name])
            self._validate(model, obj._data, model._columns)
        docs = [{c.db_name: c.type.to_db(o._data.get(n)) for n, c in model._columns.items()} for o in objects]
        self._begin()
        self._written(model)
        try:
            self.collection(model).insert_many(docs, session=self.client_session)
        except (DuplicateKeyError, BulkWriteError) as exc:
            for obj in unnumbered:
                obj._data[pk.name] = None
            raise _write_error(exc)
        for obj, doc in zip(objects, docs):
            obj._persistent, obj._key, obj._dirty = True, doc["_id"], set()
            self.identity[(model, obj._key)] = obj
            self._forget_collections(model, obj._data)

    def _update(self, objects):
        operations = {}
        for obj in objects:
            model = type(obj)
            if not obj._dirty or obj._deleted:
                continue
            for name, column in model._columns.items():
                if column.onupdate is not None and name not in obj._dirty:
                    obj._data[name] = column.default_value(column.onupdate)
                    obj._dirty.add(name)
            self._validate(model, obj._data, obj._dirty)
            changes = {model._columns[n].db_name: model._columns[n].type.to_db(obj._data.get(n)) for n in obj._dirty}
            operations.setdefault(model, []).append((obj, UpdateOne({"_id": obj._key}, {"$set": changes})))
        for model, group in operations.items():
            self._begin()
            self._written(model)
            try:
                self.collection(model).bulk_write([op for _, op in group], session=self.client_session)
            except (DuplicateKeyError, BulkWriteError) as exc:
                raise _write_error(exc)
            for obj, _ in group:
                obj._dirty = set()
                self._forget_collections(model, obj._data)

    def bulk_update(self, model, flt, values):
        columns = {(k if isinstance(k, str) else k.name): v for k, v in values.items()}
        for name, column in model._columns.items():
            if column.onupdate is not None and name not in columns:
                columns[name] = column.default_value(column.onupdate)
        self._validate(model, columns, columns)
        ids = [d["_id"] for d in self.collection(model).find(flt, {"_id": 1}, session=self.client_session)]
        if not ids:
            return 0
        self._begin()
        self._written(model)
        changes = {model._columns[n].db_name: model._columns[n].type.to_db(v) for n, v in columns.items()}
        try:
            self.collection(model).update_many({"_id": {"$in": ids}}, {"$set": changes}, session=self.client_session)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc
        for ident in ids:
            obj = self.identity.get((model, ident))
            if obj is not None:
                obj._expire()
        return len(ids)

    def bulk_delete(self, model, flt):
        ids = [d["_id"] for d in self.collection(model).find(flt, {"_id": 1}, session=self.client_session)]
        self._delete_documents(model, ids)
        return len(ids)

    def _delete_documents(self, model, ids):
        """Delete with the foreign-key rules. Every RESTRICT is checked before anything is removed."""
        if not ids:
            return
        doomed = {model: set(ids)}
        queue = [(model, list(ids))]
        set_null, restrict = [], []
        while queue:
            parent, parent_ids = queue.pop()
            for child, column in registry.references_to(parent.__tablename__):
                flt = {column.db_name: {"$in": parent_ids}}
                found = [d["_id"] for d in self.collection(child).find(flt, {"_id": 1}, session=self.client_session)]
                if not found:
                    continue
                rule = column.foreign_key.ondelete
                if rule == "CASCADE":
                    fresh = [i for i in found if i not in doomed.setdefault(child, set())]
                    if fresh:
                        doomed[child].update(fresh)
                        queue.append((child, fresh))
                elif rule == "SET NULL":
                    set_null.append((child, column, found))
                else:
                    restrict.append((parent, child, column, found))
        for parent, child, column, found in restrict:
            if set(found) - doomed.get(child, set()):
                raise IntegrityError(f"{parent.__name__} is still referenced by {child.__name__}.{column.name}")
        self._begin()
        for child, column, found in set_null:
            keep = [i for i in found if i not in doomed.get(child, set())]
            if not keep:
                continue
            self._written(child)
            self.collection(child).update_many({"_id": {"$in": keep}}, {"$set": {column.db_name: None}},
                                               session=self.client_session)
            for ident in keep:
                obj = self.identity.get((child, ident))
                if obj is not None and not obj._expired:
                    obj._data[column.name] = None
                    obj._rel = {}
        for klass, gone in doomed.items():
            gone = list(gone)
            self._written(klass)
            removed = []
            for ident in gone:
                obj = self.identity.pop((klass, ident), None)
                if obj is not None:
                    obj._deleted = True
                    obj._expired = False
                    self.dirty.pop(id(obj), None)
                    removed.append(obj._data)
            keys = [c for c in klass._columns.values() if c.foreign_key]
            if keys:
                projection = {c.db_name: 1 for c in keys}
                for doc in self.collection(klass).find({"_id": {"$in": gone}}, projection, session=self.client_session):
                    removed.append({c.name: doc.get(c.db_name) for c in keys})
            self.collection(klass).delete_many({"_id": {"$in": gone}}, session=self.client_session)
            for values in removed:
                self._forget_collections(klass, values)

    # ------------------------------------------------------------- locking
    def lock_matching(self, model, flt):
        self._begin()
        self._reads.clear()
        self.collection(model).update_many(flt, {"$inc": {"_lock": 1}}, session=self.client_session)
        # what was read before the lock may be stale
        for doc in self.collection(model).find(flt, session=self.client_session):
            obj = self.identity.get((model, doc["_id"]))
            if obj is not None and not obj._dirty:
                obj._apply_document(doc)

    def refresh(self, obj, with_for_update=False):
        self.autoflush()
        self._reads.clear()
        if with_for_update:
            self._begin()
            doc = self.collection(type(obj)).find_one_and_update(
                {"_id": obj._key}, {"$inc": {"_lock": 1}}, session=self.client_session,
                return_document=ReturnDocument.AFTER)
            if doc is None:
                raise NoResultFound(f"{type(obj).__name__} {obj._key} no longer exists")
            obj._apply_document(doc)
        else:
            self.reload(obj)

    def expire(self, obj, attributes=None):
        self.autoflush()
        self._reads.clear()
        if attributes is None:
            obj._expire()
        else:
            for name in attributes:
                obj._rel.pop(name, None)

    def expire_all(self):
        self._reads.clear()
        for obj in self.identity.values():
            obj._expire()

    # -------------------------------------------------------- transactions
    def commit(self):
        self.flush()
        cs = self.client_session
        if cs is not None and cs.in_transaction:
            for attempt in range(5):
                try:
                    cs.commit_transaction()
                    break
                except PyMongoError as exc:
                    if attempt == 4 or not exc.has_error_label("UnknownTransactionCommitResult"):
                        raise
            self.commits += 1
        self.expire_all()

    def rollback(self):
        cs = self.client_session
        if cs is not None and cs.in_transaction:
            try:
                cs.abort_transaction()
            except PyMongoError:
                pass
        for obj in self.new:
            obj._session = None
        self.new, self.dirty, self.pending_delete = [], {}, []
        self.expire_all()

    def close(self):
        self.rollback()
        if self.client_session is not None:
            self.client_session.end_session()
            self.client_session = None
        self.identity = {}
