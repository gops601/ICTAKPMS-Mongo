import os
from datetime import timedelta


def _bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me-32bytes!!")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "30")))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "7")))

    # MongoDB must be a replica set (every Atlas cluster is one): the app relies on multi-document transactions.
    MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
    MONGODB_DB = os.getenv("MONGODB_DB", "plms")

    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")]

    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.abspath("uploads"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024

    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "10 per minute")
    MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
    LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
    RESET_TOKEN_MINUTES = int(os.getenv("RESET_TOKEN_MINUTES", "30"))
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

    API_TITLE = "ICTAK API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_SWAGGER_UI_PATH = "/docs"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
    API_SPEC_OPTIONS = {
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
        }
    }
    PROPAGATE_EXCEPTIONS = False


class TestConfig(Config):
    TESTING = True
    MONGODB_URI = os.getenv("TEST_MONGODB_URI", "mongodb://localhost:27027/?directConnection=true")
    MONGODB_DB = os.getenv("TEST_MONGODB_DB", "plms_test")      # emptied by every test
    RATELIMIT_ENABLED = False
    UPLOAD_FOLDER = os.path.abspath("test_uploads")
