"""One-time copy of the old MySQL database into MongoDB.

    pip install -r requirements-migrate.txt
    python scripts/migrate_mysql_to_mongo.py --source "mysql+pymysql://user:password@host:3306/plms?charset=utf8mb4"

The target is MONGODB_URI / MONGODB_DB (from the environment or .env). Ids are preserved, so links, logins and
uploaded files keep working. The MySQL database is only read. Stop the application while this runs.
The source must be at the last MySQL release's schema (its `flask db upgrade` applied).
"""
import argparse
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

import sqlalchemy as sa  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.odm.columns import JSON  # noqa: E402
from app.odm.model import registry  # noqa: E402

BATCH = 1000


class MigrationError(Exception):
    pass


def _reflect(connection):
    """{table name: reflected table}, after checking that every collection has a complete source table."""
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    problems = []
    for name, model in registry.by_table.items():
        table = metadata.tables.get(name)
        if table is None:
            problems.append(f"table {name} is missing")
            continue
        missing = [c for c in model._columns if c not in table.columns]
        if missing:
            problems.append(f"table {name} lacks column(s) {', '.join(missing)}")
    if problems:
        raise MigrationError("The source database does not match the application:\n  " + "\n  ".join(problems))
    return metadata.tables


def _document(model, row):
    doc = {}
    for name, column in model._columns.items():
        value = row[name]
        if value is not None:
            if isinstance(column.type, JSON) and isinstance(value, (str, bytes)):
                value = json.loads(value)
            value = column.type.coerce(value)
        doc[column.db_name] = column.type.to_db(value)
    return doc


def _next_ids(connection):
    """MySQL's own counters, which are ahead of MAX(id) when the newest rows were deleted."""
    if connection.dialect.name != "mysql":
        return {}
    connection.execute(sa.text("SET SESSION information_schema_stats_expiry = 0"))
    rows = connection.execute(sa.text(
        "SELECT table_name, auto_increment FROM information_schema.tables WHERE table_schema = DATABASE()"))
    return {table: value for table, value in rows if value}


def migrate(source_url, replace=False, out=print):
    """Copy every table; returns {collection: documents copied}. Needs an app context."""
    db.create_all()
    target = db.connection()
    occupied = [t for t in registry.by_table if target.mongo[t].find_one({}, {"_id": 1})]
    if occupied and not replace:
        raise MigrationError(f"MongoDB database '{target.name}' already has data ({', '.join(sorted(occupied)[:5])}, ...)."
                             " Re-run with --replace to delete it and copy again.")
    engine = sa.create_engine(source_url)
    counts = {}
    try:
        with engine.connect() as connection:
            tables = _reflect(connection)
            auto = _next_ids(connection)
            if replace:
                db.clear_all()
            for name, model in sorted(registry.by_table.items()):
                table = tables[name]
                # JSON is read as text and decoded here, the same way for every driver
                selected = [sa.type_coerce(table.c[c], sa.Text).label(c) if isinstance(col.type, JSON) else table.c[c]
                            for c, col in model._columns.items()]
                result = connection.execution_options(stream_results=True).execute(sa.select(*selected))
                copied, highest = 0, 0
                for rows in result.mappings().partitions(BATCH):
                    docs = [_document(model, row) for row in rows]
                    target.mongo[name].insert_many(docs)
                    copied += len(docs)
                    if isinstance(docs[0]["_id"], int):
                        highest = max(highest, *(d["_id"] for d in docs))
                highest = max(highest, auto.get(name, 1) - 1)
                if highest:
                    target.reserve_id(model, highest)
                expected = connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                stored = target.mongo[name].count_documents({})
                if not copied == expected == stored:
                    raise MigrationError(f"{name}: {expected} rows in MySQL but {stored} documents in MongoDB")
                counts[name] = copied
                out(f"  {name}: {copied}")
    finally:
        engine.dispose()
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.getenv("MYSQL_URL"), help="SQLAlchemy URL of MySQL (or $MYSQL_URL)")
    parser.add_argument("--replace", action="store_true", help="delete the data already in the MongoDB database first")
    args = parser.parse_args()
    if not args.source:
        parser.error("give --source or set MYSQL_URL")
    app = create_app()
    with app.app_context():
        try:
            counts = migrate(args.source, args.replace)
        except MigrationError as exc:
            sys.exit(f"Migration stopped: {exc}")
    print(f"Done: {sum(counts.values())} documents in {len(counts)} collections.")


if __name__ == "__main__":
    main()
