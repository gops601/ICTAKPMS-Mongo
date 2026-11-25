from flask import Blueprint

from app.extensions import db
from app.utils.responses import ok, fail

bp = Blueprint("health", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    try:
        db.ping()
    except Exception:
        return fail("Database unavailable", 503)
    return ok({"status": "healthy"})
