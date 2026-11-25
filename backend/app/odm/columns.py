"""Column types, column descriptors and the filter expressions they build.

``Batch.domain_id == 3`` returns an expression that compiles to a MongoDB filter. An expression always belongs to one
collection: using another model's column in a query raises, because MongoDB queries do not join implicitly.
"""
import copy
import re
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from bson.decimal128 import Decimal128


# ------------------------------------------------------------------ types
class ColumnType:
    def coerce(self, value):
        """Normalise a value assigned in Python (so it reads back the same before and after a round trip)."""
        return value

    def to_db(self, value):
        return value

    def from_db(self, value):
        return value


class Integer(ColumnType):
    pass


class BigInteger(Integer):
    pass


class Boolean(ColumnType):
    def coerce(self, value):
        return None if value is None else bool(value)


class String(ColumnType):
    def __init__(self, length=None):
        self.length = length


class Text(String):
    pass


class Date(ColumnType):
    """MongoDB has no date-only type: stored as midnight."""

    def coerce(self, value):
        return value.date() if isinstance(value, datetime) else value

    def to_db(self, value):
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.min)
        return value

    def from_db(self, value):
        return value.date() if isinstance(value, datetime) else value


class DateTime(ColumnType):
    """Naive UTC; MongoDB keeps milliseconds."""

    def coerce(self, value):
        if isinstance(value, datetime):
            return value.replace(microsecond=value.microsecond // 1000 * 1000, tzinfo=None)
        return value


class Numeric(ColumnType):
    def __init__(self, precision=10, scale=2):
        self.quantum = Decimal(1).scaleb(-scale)

    def coerce(self, value):
        if value is None:
            return None
        return Decimal(str(value)).quantize(self.quantum, rounding=ROUND_HALF_UP)

    def to_db(self, value):
        return None if value is None else Decimal128(self.coerce(value))

    def from_db(self, value):
        if value is None:
            return None
        return self.coerce(value.to_decimal() if isinstance(value, Decimal128) else value)


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class JSON(ColumnType):
    def to_db(self, value):
        return _plain(value)

    def from_db(self, value):
        return copy.deepcopy(value)


# ------------------------------------------------------------------ expressions
class Expr:
    def compile(self, model):
        raise NotImplementedError


class Condition(Expr):
    def __init__(self, column, op, value):
        self.column, self.op, self.value = column, op, value

    def compile(self, model):
        col = self.column
        if col.model is not model:
            raise TypeError(f"{col!r} cannot filter a {model.__name__} query: MongoDB queries do not join")
        value = self.value
        if self.op in ("$in", "$nin"):
            if hasattr(value, "values_list"):            # a column query used as a sub-query
                value = value.values_list()
            value = [col.type.to_db(v) for v in value]
        elif self.op != "$regex":
            value = col.type.to_db(value)
        if self.op == "$eq":
            return {col.db_name: value}
        if self.op == "$regex":
            return {col.db_name: {"$regex": value, "$options": "is"}}
        return {col.db_name: {self.op: value}}


class BoolExpr(Expr):
    def __init__(self, op, parts):
        self.op, self.parts = op, parts

    def compile(self, model):
        return {self.op: [p.compile(model) for p in self.parts]}


def or_(*parts):
    return BoolExpr("$or", parts)


def and_(*parts):
    return BoolExpr("$and", parts)


class Order:
    def __init__(self, column, direction):
        self.column, self.direction = column, direction


def _like_regex(pattern):
    body = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return f"^{body}$"


# ------------------------------------------------------------------ columns
class ForeignKey:
    def __init__(self, target, ondelete="RESTRICT"):
        self.table, _, self.column = target.partition(".")
        self.ondelete = (ondelete or "RESTRICT").upper()


class Column:
    def __init__(self, type_, *args, primary_key=False, default=None, onupdate=None, nullable=True, unique=False,
                 index=False, server_default=None):
        self.type = type_() if isinstance(type_, type) else type_
        self.foreign_key = next((a for a in args if isinstance(a, ForeignKey)), None)
        self.primary_key, self.default, self.onupdate = primary_key, default, onupdate
        self.nullable = nullable and not primary_key
        self.unique, self.index = unique, index
        self.name = self.model = None

    def bind(self, model, name):
        """A mixin's column is shared by many models, so every model gets its own copy."""
        column = copy.copy(self)
        column.model, column.name = model, name
        return column

    @property
    def db_name(self):
        return "_id" if self.primary_key else self.name

    def __repr__(self):
        return f"{self.model.__name__}.{self.name}"

    # descriptor
    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        obj._load_if_expired()
        return obj._data.get(self.name)

    def __set__(self, obj, value):
        obj._load_if_expired()
        value = self.type.coerce(value)
        old = obj._data.get(self.name)
        changed = isinstance(self.type, JSON) or self.name not in obj._data or old != value
        obj._data[self.name] = value
        if changed:
            obj._mark_dirty(self)

    def default_value(self, default):
        return self.type.coerce(default() if callable(default) else copy.deepcopy(default))

    # expressions
    __hash__ = object.__hash__

    def __eq__(self, other):
        return Condition(self, "$eq", other)

    def __ne__(self, other):
        return Condition(self, "$ne", other)

    def __lt__(self, other):
        return Condition(self, "$lt", other)

    def __le__(self, other):
        return Condition(self, "$lte", other)

    def __gt__(self, other):
        return Condition(self, "$gt", other)

    def __ge__(self, other):
        return Condition(self, "$gte", other)

    def in_(self, values):
        return Condition(self, "$in", values)

    def notin_(self, values):
        return Condition(self, "$nin", values)

    def is_(self, value):
        return Condition(self, "$eq", value)

    def is_not(self, value):
        return Condition(self, "$ne", value)

    isnot = is_not

    def like(self, pattern):
        """SQL LIKE semantics, case-insensitive."""
        return Condition(self, "$regex", _like_regex(pattern))

    def startswith(self, prefix):
        return Condition(self, "$regex", "^" + re.escape(prefix))

    def asc(self):
        return Order(self, 1)

    def desc(self):
        return Order(self, -1)
