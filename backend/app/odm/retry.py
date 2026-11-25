"""MongoDB does not queue writers: a transaction that collides with another fails at once with a transient error.
The whole request is then replayed, which gives the same "one after the other" behaviour a row lock gave."""
import random
import time

from flask import Flask, request
from pymongo.errors import PyMongoError

MAX_ATTEMPTS = 20


class TransactionalFlask(Flask):
    def dispatch_request(self, *args, **kwargs):
        from app.extensions import db
        commits = db.session.commits
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return super().dispatch_request(*args, **kwargs)
            except PyMongoError as exc:
                # Never replay a request that has already committed something.
                if (attempt == MAX_ATTEMPTS or db.session.commits != commits
                        or not exc.has_error_label("TransientTransactionError")):
                    raise
                db.session.rollback()
                for upload in request.files.values():
                    upload.stream.seek(0)
                time.sleep(random.uniform(0.005, 0.02) * attempt)
