from flask_smorest import Blueprint

from app.permissions import current_user, roles_required
from app.services import rubric_service
from app.utils.responses import ok

bp = Blueprint("rubrics", __name__, url_prefix="/api/rubrics", description="Domain evaluation rubrics")
SEC = [{"bearerAuth": []}]


@bp.get("")
@bp.doc(security=SEC, description="Common and track rubrics. Admins get every track; others only those of their own domains.")
@roles_required("admin", "domain_lead", "mentor", "student")
def get_rubrics():
    return ok(rubric_service.rubrics_for(current_user()))
