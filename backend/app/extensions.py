from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_smorest import Api

from app.odm import Database, IntegrityError  # noqa: F401

db = Database()
jwt = JWTManager()
cors = CORS()
api = Api()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
