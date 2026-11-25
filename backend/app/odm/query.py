"""Single-collection queries."""
from .columns import Column, Condition, Order, and_


class NoResultFound(Exception):
    pass


class MultipleResultsFound(Exception):
    pass


class Row(tuple):
    """A tuple of column values that also allows attribute access."""

    def __new__(cls, values, names):
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getattr__(self, name):
        try:
            return self[self.__dict__["_names"].index(name)]
        except (KeyError, ValueError):
            raise AttributeError(name)


class Results(list):
    def all(self):
        return list(self)

    def first(self):
        return self[0] if self else None


class Pagination:
    def __init__(self, items, page, per_page, total):
        self.items, self.page, self.per_page, self.total = items, page, per_page, total
        self.pages = -(-total // per_page) if per_page else 0


class Query:
    def __init__(self, session_factory, entities):
        self._session_factory = session_factory
        first = entities[0]
        if isinstance(first, Column):
            self.model, self.columns = first.model, tuple(entities)
        else:
            self.model, self.columns = first, ()
        self._where = []
        self._order = []
        self._stages = []
        self._stage_sort = []
        self._limit = None
        self._distinct = False
        self._for_update = False

    def _clone(self, **changes):
        q = Query.__new__(Query)
        q.__dict__ = {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}
        q.__dict__.update(changes)
        return q

    @property
    def _session(self):
        return self._session_factory()

    # ------------------------------------------------------------- building
    def where(self, *conditions):
        return self._clone(_where=self._where + list(conditions))

    filter = where

    def filter_by(self, **kwargs):
        return self.where(*[Condition(self.model._columns[k], "$eq", v) for k, v in kwargs.items()])

    def order_by(self, *orders):
        added = [o if isinstance(o, Order) else Order(o, 1) for o in orders]
        for o in added:
            if o.column.model is not self.model:
                raise TypeError(f"Cannot order {self.model.__name__} by {o.column!r}; use order_by_related()")
        return self._clone(_order=self._order + added)

    def order_by_related(self, key, column, direction=1):
        """Order by a column of the document that `key` (a foreign key of this model) points at."""
        alias = f"_sort{len(self._stage_sort)}"
        stages = [
            {"$lookup": {"from": column.model.__tablename__, "localField": key.db_name, "foreignField": "_id",
                         "as": f"{alias}_doc"}},
            {"$addFields": {alias: {"$first": f"${alias}_doc.{column.db_name}"}}},
        ]
        return self._clone(_stages=self._stages + stages, _stage_sort=self._stage_sort + [(alias, direction)])

    def order_by_expression(self, expression, direction=1):
        """Order by a computed MongoDB aggregation expression."""
        alias = f"_sort{len(self._stage_sort)}"
        return self._clone(_stages=self._stages + [{"$addFields": {alias: expression}}],
                           _stage_sort=self._stage_sort + [(alias, direction)])

    def limit(self, n):
        return self._clone(_limit=n)

    def distinct(self):
        return self._clone(_distinct=True)

    def with_for_update(self):
        return self._clone(_for_update=True)

    def with_only_columns(self, *columns):
        return self._clone(columns=tuple(columns))

    # ------------------------------------------------------------ execution
    def compile_filter(self):
        if not self._where:
            return {}
        if len(self._where) == 1:
            return self._where[0].compile(self.model)
        return and_(*self._where).compile(self.model)

    def _documents(self, skip=0, limit=None):
        session = self._session
        session.autoflush()
        flt = self.compile_filter()
        if self._for_update:
            session.lock_matching(self.model, flt)
        limit = self._limit if limit is None else limit
        projection = {c.db_name: 1 for c in self.columns} if self.columns else None
        sort = self._stage_sort + [(o.column.db_name, o.direction) for o in self._order]
        if not any(name == "_id" for name, _ in sort):
            sort.append(("_id", 1))
        collection = session.collection(self.model)
        key = ("find", flt, projection, sort, skip, limit, self._stages)

        def load():
            if self._stages:
                pipeline = [{"$match": flt}, *self._stages, {"$sort": dict(sort)}]
                if skip:
                    pipeline.append({"$skip": skip})
                if limit:
                    pipeline.append({"$limit": limit})
                return list(collection.aggregate(pipeline, session=session.client_session))
            return list(collection.find(flt, projection, sort=sort, skip=skip, limit=limit or 0,
                                        session=session.client_session))
        if self._for_update or self._stages:      # a $lookup also depends on another collection
            return load()
        return session.cached_read(self.model, key, load)

    def _materialise(self, docs):
        if not self.columns:
            return [self._session.materialise(self.model, d) for d in docs]
        names = [c.name for c in self.columns]
        rows = [Row([c.type.from_db(d.get(c.db_name)) for c in self.columns], names) for d in docs]
        if self._distinct:
            seen, unique = set(), []
            for row in rows:
                if tuple(row) not in seen:
                    seen.add(tuple(row))
                    unique.append(row)
            rows = unique
        return rows

    def all(self):
        if self.columns and self._limit == 0:
            return []
        return self._materialise(self._documents())

    def __iter__(self):
        return iter(self.all())

    def first(self):
        rows = self._materialise(self._documents(limit=1))
        return rows[0] if rows else None

    def one_or_none(self):
        rows = self._materialise(self._documents(limit=2))
        if len(rows) > 1:
            raise MultipleResultsFound(self.model.__name__)
        return rows[0] if rows else None

    def one(self):
        row = self.one_or_none()
        if row is None:
            raise NoResultFound(self.model.__name__)
        return row

    def scalars(self):
        if self.columns:
            return Results(row[0] for row in self.all())
        return Results(self.all())

    def scalar(self):
        row = self.first()
        return row[0] if row is not None and self.columns else row

    def values_list(self, column=None):
        """The values of one column of the matching documents (also what ``in_(query)`` uses)."""
        q = self if column is None else self.with_only_columns(column)
        if len(q.columns) != 1:
            raise TypeError("A sub-query must select exactly one column")
        return list(q.scalars())

    def value_set(self, column=None):
        return set(self.values_list(column))

    def count(self):
        if self.columns and self._distinct:
            return len(self.all())
        session = self._session
        session.autoflush()
        flt = self.compile_filter()
        return session.cached_read(self.model, ("count", flt), lambda: session.collection(self.model).count_documents(
            flt, session=session.client_session))

    def count_by(self, *columns):
        """{value (or tuple of values): number of matching documents}."""
        session = self._session
        session.autoflush()
        pipeline = [{"$match": self.compile_filter()},
                    {"$group": {"_id": {c.name: f"${c.db_name}" for c in columns}, "n": {"$sum": 1}}}]
        groups = session.cached_read(self.model, ("count_by", pipeline), lambda: list(
            session.collection(self.model).aggregate(pipeline, session=session.client_session)))
        out = {}
        for d in groups:
            key = tuple(c.type.from_db(d["_id"].get(c.name)) for c in columns)
            out[key[0] if len(columns) == 1 else key] = d["n"]
        return out

    def paginate(self, page=1, per_page=20):
        page, per_page = max(page or 1, 1), max(per_page or 20, 1)
        total = self.count()
        docs = self._documents(skip=(page - 1) * per_page, limit=per_page)
        return Pagination(self._materialise(docs), page, per_page, total)

    # ---------------------------------------------------------- bulk writes
    def update(self, values, synchronize_session=None):
        session = self._session
        session.autoflush()
        return session.bulk_update(self.model, self.compile_filter(), values)

    def delete(self, synchronize_session=None):
        session = self._session
        session.autoflush()
        return session.bulk_delete(self.model, self.compile_filter())
