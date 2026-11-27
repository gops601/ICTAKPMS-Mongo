from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token, get_jwt_identity, jwt_required
from flask_smorest import Blueprint
from marshmallow import EXCLUDE, Schema, fields, validate

from app.extensions import db, limiter
from app.models import User
from app.permissions import current_user, login_required
from app.schemas.auth import (ChangePasswordSchema, ForgotPasswordSchema, LoginSchema,
                              ResetPasswordSchema, user_to_dict)
from app.schemas.org import mentor_to_dict
from app.services import auth_service, user_service
from app.utils.errors import AppError, NotFound
from app.utils.responses import ok

bp = Blueprint("auth", __name__, url_prefix="/api/auth", description="Authentication")


def _login_limit():
    return current_app.config["LOGIN_RATE_LIMIT"]


def _tokens(user):
    claims = {"role": user.role_name}
    return {"access_token": create_access_token(str(user.id), additional_claims=claims),
            "refresh_token": create_refresh_token(str(user.id), additional_claims=claims)}


@bp.post("/login")
@limiter.limit(_login_limit)
@bp.arguments(LoginSchema)
def login(data):
    user = auth_service.authenticate(data["email"], data["password"])
    return ok({**_tokens(user), "user": user_to_dict(user)}, "Login successful")


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user or not user.is_active:
        raise AppError("Account is inactive", 401)
    return ok({"access_token": create_access_token(str(user.id), additional_claims={"role": user.role_name})})


@bp.get("/me")
@bp.doc(security=[{"bearerAuth": []}])
@login_required
def me():
    return ok(user_to_dict(current_user()))


class MentorProfileSchema(Schema):
    class Meta:
        unknown = EXCLUDE
    name = fields.String(validate=validate.Length(min=2, max=150))
    phone = fields.String(allow_none=True, validate=validate.Length(max=30))
    designation = fields.String(allow_none=True, validate=validate.Length(max=120))
    expertise = fields.String(allow_none=True, validate=validate.Length(max=255))
    company = fields.String(allow_none=True, validate=validate.Length(max=150))
    industry_sector = fields.String(allow_none=True, validate=validate.Length(max=120))
    experience_years = fields.Integer(allow_none=True, validate=validate.Range(min=0, max=70))
    linkedin_url = fields.String(allow_none=True, validate=validate.Length(max=255))
    bio = fields.String(allow_none=True, validate=validate.Length(max=5000))


def _own_mentor():
    from app.permissions.scope import mentor_profile
    m = mentor_profile(current_user())
    if not m:
        raise NotFound("You do not have a mentor profile")
    return m


@bp.get("/mentor-profile")
@bp.doc(security=[{"bearerAuth": []}])
@login_required
def get_mentor_profile():
    return ok(mentor_to_dict(_own_mentor()))


@bp.put("/mentor-profile")
@bp.doc(security=[{"bearerAuth": []}], description="A mentor (or domain lead) edits their own profile. Type and domain are set by an admin.")
@bp.arguments(MentorProfileSchema)
@login_required
def update_mentor_profile(data):
    m = _own_mentor()
    keys = ("designation", "company", "industry_sector", "experience_years", "linkedin_url", "bio")
    merged = {k: data.get(k, getattr(m, k)) for k in keys}
    clean = user_service.clean_mentor_profile(m.mentor_type, **merged)
    clean.pop("mentor_type")
    for k, v in clean.items():
        setattr(m, k, v)
    if "expertise" in data:
        m.expertise = (data["expertise"] or "").strip() or None
    if data.get("name"):
        m.user.name = data["name"].strip()
    if "phone" in data:
        m.user.phone = (data["phone"] or "").strip() or None
    db.session.commit()
    return ok(mentor_to_dict(m), "Profile updated")


@bp.post("/change-password")
@bp.doc(security=[{"bearerAuth": []}])
@bp.arguments(ChangePasswordSchema)
@login_required
def change_password(data):
    auth_service.change_password(current_user(), data["current_password"], data["new_password"])
    return ok(message="Password changed successfully")


@bp.post("/forgot-password")
@limiter.limit(_login_limit)
@bp.arguments(ForgotPasswordSchema)
def forgot_password(data):
    token = auth_service.request_password_reset(data["email"])
    payload = {"token": token} if token else None  # token exposed only under TESTING
    return ok(payload, "If the account exists, a reset link has been sent")


@bp.post("/reset-password")
@limiter.limit(_login_limit)
@bp.arguments(ResetPasswordSchema)
def reset_password(data):
    auth_service.reset_password(data["token"], data["new_password"])
    return ok(message="Password reset successful")
