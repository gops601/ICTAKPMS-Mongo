"""Model base class: one collection per model, integer ids, references by id and lazy relationships."""
from .columns import Column


class UniqueConstraint:
    def __init__(self, *fields, name=None):
        self.fields, self.name = fields, name


class Index:
    def __init__(self, name, *fields):
        self.name, self.fields = name, fields


class CheckConstraint:
    """A field range enforced when a document is written: ``CheckConstraint("week_no BETWEEN 1 AND 4")``."""

    def __init__(self, text, name=None):
        field, _, rest = text.partition(" BETWEEN ")
        low, _, high = rest.partition(" AND ")
        self.field, self.low, self.high, self.name = field.strip(), int(low), int(high), name


class Relationship:
    """Reference to related documents, resolved from the foreign keys of the two models.

    Many-to-one when this model holds the key, one-to-many (or one-to-one with ``uselist=False``) otherwise.
    Deleting is governed by each key's ``ondelete``; ``back_populates`` / ``cascade`` are accepted for readability.
    """

    def __init__(self, target, order_by=None, uselist=None, back_populates=None, cascade=None, lazy=None):
        self.target_name, self.order_by, self.uselist = target, order_by, uselist
        self.name = self.model = None
        self._resolved = None

    def __set_name__(self, owner, name):
        self.model, self.name = owner, name

    def resolve(self):
        """-> (target model, key column, many_to_one)"""
        if self._resolved is None:
            target = registry.by_name[self.target_name]
            own = [c for c in self.model._columns.values()
                   if c.foreign_key and c.foreign_key.table == target.__tablename__]
            if own:
                key = next((c for c in own if c.name == f"{self.name}_id"), own[0])
                self._resolved = (target, key, True)
            else:
                key = next(c for c in target._columns.values()
                           if c.foreign_key and c.foreign_key.table == self.model.__tablename__)
                self._resolved = (target, key, False)
        return self._resolved

    @property
    def is_list(self):
        return not self.resolve()[2] and self.uselist is not False

    @property
    def order_field(self):
        return self.order_by.split(".")[-1] if self.order_by else None

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        obj._load_if_expired()
        if self.name not in obj._rel:
            if obj._session is None:
                return [] if self.is_list else None
            obj._rel[self.name] = obj._session.load_relationship(obj, self)
        return obj._rel[self.name]


class Registry:
    def __init__(self):
        self.by_name = {}
        self.by_table = {}

    def references_to(self, table):
        """Every (model, column) whose foreign key points at `table`."""
        return [(m, c) for m in self.by_name.values() for c in m._columns.values()
                if c.foreign_key and c.foreign_key.table == table]


registry = Registry()


class ModelMeta(type):
    def __init__(cls, name, bases, namespace):
        super().__init__(name, bases, namespace)
        if "__tablename__" not in namespace:
            return
        columns = {}
        for klass in reversed(cls.__mro__):
            for attr, value in vars(klass).items():
                if isinstance(value, Column):
                    columns[attr] = value
        cls._columns = {}
        for attr, column in columns.items():
            bound = column.bind(cls, attr)
            setattr(cls, attr, bound)
            cls._columns[attr] = bound
        cls._pk = next(c for c in cls._columns.values() if c.primary_key)
        cls._relationships = {a: v for a, v in namespace.items() if isinstance(v, Relationship)}
        registry.by_name[name] = cls
        registry.by_table[cls.__tablename__] = cls


class Model(metaclass=ModelMeta):
    def __init__(self, **kwargs):
        self._init_state()
        unknown = set(kwargs) - set(self._columns)
        if unknown:
            raise TypeError(f"{type(self).__name__} has no column(s): {', '.join(sorted(unknown))}")
        for name, value in kwargs.items():
            setattr(self, name, value)

    def _init_state(self):
        self._data, self._rel = {}, {}
        self._session = None
        self._persistent = self._expired = self._deleted = False
        self._dirty = set()
        self._key = None

    @classmethod
    def _from_document(cls, doc, session):
        obj = cls.__new__(cls)
        obj._init_state()
        obj._session, obj._persistent = session, True
        obj._key = doc["_id"]
        obj._apply_document(doc)
        return obj

    def _apply_document(self, doc):
        self._data = {name: column.type.from_db(doc.get(column.db_name)) for name, column in self._columns.items()}
        self._rel, self._dirty, self._expired = {}, set(), False

    def _load_if_expired(self):
        if self._expired:
            self._session.reload(self)

    def _expire(self):
        if self._persistent and not self._deleted:
            self._expired = True
            self._rel, self._dirty = {}, set()

    def _mark_dirty(self, column):
        if column.foreign_key:
            for name, rel in self._relationships.items():
                _, key, many_to_one = rel.resolve()
                if many_to_one and key is column:
                    self._rel.pop(name, None)
        if self._persistent and self._session is not None:
            self._dirty.add(column.name)
            self._session.mark_dirty(self)

    def __repr__(self):
        return f"<{type(self).__name__} {self._key}>"
