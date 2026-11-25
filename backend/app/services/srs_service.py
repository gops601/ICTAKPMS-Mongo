"""SRS submission, mentor review and revision history. Drives the SRS part of the lifecycle."""
import re
from datetime import date, timedelta

from app.extensions import db
from app.models import Project, ProjectGithubRepository, SrsReviewHistory, SrsSubmission
from app.models.base import utcnow
from app.permissions.scope import student_profile
from app.services import file_service, lifecycle
from app.services.notification_service import notify
from app.services.project_service import member_user_ids, mentor_user_ids
from app.utils.errors import AppError, Forbidden

SUBMITTABLE_STAGES = ("allocated", "srs_preparation", "srs_changes_requested")
REVIEWABLE_STATUSES = ("submitted", "under_review")
# API decision -> (submission status, history decision, target lifecycle stage)
DECISIONS = {
    "start_review": ("under_review", "under_review", "srs_under_review"),
    "approve": ("approved", "approved", "srs_approved"),
    "request_changes": ("changes_requested", "changes_requested", "srs_changes_requested"),
    "reject": ("rejected", "rejected", "srs_changes_requested"),  # rejected SRS must be resubmitted
}
REMARKS_REQUIRED = ("request_changes", "reject")
LABELS = {"not_submitted": "Not Submitted", "submitted": "Submitted", "under_review": "Under Review",
          "changes_requested": "Changes Requested", "approved": "Approved"}


def _lock(project_id):
    """Write lock so concurrent submissions/reviews on one project serialise."""
    return db.session.query(Project).filter_by(id=project_id).with_for_update().one()


def srs_status(project):
    """Student-facing SRS status derived from the lifecycle stage."""
    order = lifecycle.ORDER[project.stage]
    if order >= lifecycle.ORDER["srs_approved"]:
        return "approved"
    return {"srs_submitted": "submitted", "srs_under_review": "under_review",
            "srs_changes_requested": "changes_requested"}.get(project.stage, "not_submitted")


def latest_submission(project_id, for_update=False):
    q = db.session.query(SrsSubmission).filter_by(project_id=project_id).order_by(SrsSubmission.version.desc())
    return (q.with_for_update() if for_update else q).first()      # locking read avoids stale-snapshot versions


def implementation_unlocked(project):
    return lifecycle.ORDER[project.stage] >= lifecycle.ORDER["srs_approved"]


def first_week_due(project):
    """The SRS and the GitHub repository are due one week after the approved project starts."""
    return project.start_date + timedelta(weeks=1) if project.start_date else None


def _week_one_item(done_at, due, today):
    if done_at:
        state = "late" if due and done_at.date() > due else "submitted"
    else:
        state = "overdue" if due and today > due else "pending"
    return {"state": state, "submitted_at": done_at.isoformat() if done_at else None}


def first_week_status(project, first_srs_at, repo, today=None):
    """Week 1 checklist. `first_srs_at`: when version 1 of the SRS arrived; `repo`: the repository record or None."""
    today, due = today or date.today(), first_week_due(project)
    return {"due_date": due.isoformat() if due else None,
            "srs": _week_one_item(first_srs_at, due, today),
            "github": _week_one_item(repo.created_at if repo else None, due, today)}


def project_first_week(project):
    first = db.session.query(SrsSubmission).filter_by(project_id=project.id).order_by(SrsSubmission.version.asc()).first()
    repo = db.session.query(ProjectGithubRepository).filter_by(project_id=project.id).first()
    return first_week_status(project, first.submitted_at if first else None, repo)


def github_unlocked(project):
    """The repository opens as soon as the project is approved; it does not wait for the SRS review."""
    return project.problem_statement.approval_status == "approved"


def submit(project, file_storage, description, user, url=None):
    student = student_profile(user)
    member = student and any(pm.student_id == student.id for pm in project.members)
    if user.role_name != "student" or not member:
        raise Forbidden("Only the project's students can submit the SRS")
    project = _lock(project.id)
    if project.problem_statement.approval_status != "approved":
        raise AppError("The SRS can only be started once a mentor has approved the problem statement.", 409)
    if project.stage not in SUBMITTABLE_STAGES:
        hint = {"srs_submitted": "Your SRS is awaiting review.",
                "srs_under_review": "Your SRS is being reviewed."}.get(project.stage, "The SRS is already approved.")
        raise AppError(f"An SRS cannot be submitted right now. {hint}", 409)

    attachment = file_service.save_source(file_storage, url, "srs", user.id)
    if project.stage == "allocated":              # uploading the SRS is itself the start of SRS preparation
        lifecycle.transition(project, "srs_preparation", user, "SRS added")
    last = latest_submission(project.id, for_update=True)
    sub = SrsSubmission(project_id=project.id, version=(last.version + 1) if last else 1, file_id=attachment.id,
                        description=(description or "").strip()[:2000] or None, status="submitted",
                        submitted_by=user.id, submitted_at=utcnow())
    db.session.add(sub)
    db.session.flush()
    lifecycle.transition(project, "srs_submitted", user, system=True)
    lifecycle.log_activity(project.id, user.id, "srs.submitted", {"version": sub.version})
    notify(mentor_user_ids(project), "srs_submitted", "SRS submitted for review",
           f"“{project.title}” – SRS v{sub.version} was submitted.", f"/srs/{project.id}")
    return sub


def review(submission, decision, remarks, reviewer):
    if decision not in DECISIONS:
        raise AppError(f"decision must be one of: {', '.join(DECISIONS)}", 422)
    remarks = (remarks or "").strip()
    if decision in REMARKS_REQUIRED and len(remarks) < 3:
        raise AppError("Comments are required when rejecting an SRS or requesting modifications", 422,
                       {"remarks": "Comments are required"})
    project = _lock(submission.project_id)
    db.session.refresh(submission, with_for_update=True)
    latest = latest_submission(project.id, for_update=True)
    if latest.id != submission.id:
        raise AppError("Only the latest SRS version can be reviewed", 409)
    if submission.status not in REVIEWABLE_STATUSES:
        raise AppError(f"This SRS was already {submission.status.replace('_', ' ')}", 409)
    if decision == "start_review" and submission.status == "under_review":
        raise AppError("This SRS is already under review", 409)

    sub_status, history_decision, stage = DECISIONS[decision]
    lifecycle.transition(project, stage, reviewer, system=True)
    submission.status = sub_status
    db.session.add(SrsReviewHistory(srs_submission_id=submission.id, reviewer_id=reviewer.id,
                                    decision=history_decision, remarks=remarks or None))
    lifecycle.log_activity(project.id, reviewer.id, "srs.reviewed",
                           {"version": submission.version, "decision": history_decision})
    db.session.flush()
    if decision != "start_review":
        titles = {"approve": "SRS approved", "request_changes": "SRS changes requested", "reject": "SRS rejected"}
        body = {"approve": "Your SRS was approved – implementation is now unlocked.",
                "request_changes": "Your mentor requested changes to the SRS.",
                "reject": "Your SRS was rejected. Please revise and resubmit."}[decision]
        notify(member_user_ids(project), f"srs_{history_decision}", titles[decision],
               body + (f" “{remarks}”" if remarks else ""), "/student/srs")
    return submission


# ------------------------------------------------------------------ GitHub
_GITHUB_RE = re.compile(r"^https://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


def normalise_github_url(url):
    """Accept only https://github.com/<owner>/<repo>; return the canonical form."""
    m = _GITHUB_RE.match((url or "").strip())
    if not m or m.group(2) in (".", ".."):
        raise AppError("Enter a valid GitHub repository URL, e.g. https://github.com/owner/repo", 422,
                       {"url": "Not a valid GitHub repository URL"})
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def set_github(project, url, description, user):
    student = student_profile(user)
    if user.role_name != "student" or not (student and any(pm.student_id == student.id for pm in project.members)):
        raise Forbidden("Only the project's students can set the repository")
    if not github_unlocked(project):
        raise AppError("The repository can be added once your project is approved", 409)
    clean = normalise_github_url(url)
    repo = db.session.query(ProjectGithubRepository).filter_by(project_id=project.id).first()
    created = repo is None
    if created:
        repo = ProjectGithubRepository(project_id=project.id)
        db.session.add(repo)
    repo.url, repo.description, repo.updated_by = clean, (description or "").strip()[:2000] or None, user.id
    lifecycle.log_activity(project.id, user.id, "github.set" if created else "github.updated", {"url": clean})
    return repo
