"""Weekly reviews: dynamic criteria, scoring, submission, audited re-opening and final sign-off.

Nothing about criteria is hardcoded: evaluators define them per review (optionally from shared templates).
Every mentor of the project and its Domain Lead evaluate on their own (`ReviewEvaluation`); the shared review shows
the AVERAGE of the submitted evaluations. A week opens only after the previous week is evaluated, and evaluation opens
on the scheduled review date. A submitted evaluation is immutable until it is re-opened with a reason; the superseded
state is snapshotted in `evaluation_revisions` so the evaluation history is complete.
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.extensions import db
from app.models import (DomainLead, EvaluationCriterion, EvaluationRevision, EvaluationScore, EvaluationTemplate, Mentor, Project,
                        ProjectReview, ReviewEvaluation, ReviewStudentRemark)
from app.models.base import utcnow
from app.permissions.scope import mentor_profile
from app.services import lifecycle
from app.services.fields import parse_date
from app.services.notification_service import domain_lead_user_ids, notify
from app.services.metrics_service import project_weeks
from app.services.project_service import member_user_ids, mentor_user_ids
from app.utils.audit import audit
from app.utils.errors import AppError, Forbidden, NotFound

CENT = Decimal("0.01")
MAX_CRITERIA = 20
MAX_MARKS = Decimal("1000")
EDITABLE = ("pending", "draft", "reopened")
FEEDBACK_FIELDS = ("remarks", "strengths", "improvements", "recommendations")
STATUS_LABEL = {"pending": "Pending", "draft": "In progress", "reopened": "Under revision", "submitted": "Submitted"}

# Example criteria offered by "Add sample templates". Purely illustrative data the mentor can edit or delete;
# no code path depends on these names.
SAMPLE_TEMPLATES = {
    1: ("Week 1 – SRS & Analysis (sample)", [("Problem Understanding", 20), ("Requirement Analysis", 20), ("SRS Quality", 30), ("Technical Feasibility", 30)]),
    2: ("Week 2 – Design & Progress (sample)", [("System Design", 20), ("Database Design", 20), ("Implementation Progress", 30), ("Code Quality", 30)]),
    3: ("Week 3 – Build & Test (sample)", [("Functional Implementation", 30), ("Code Quality", 20), ("Testing", 20), ("Scrum Progress", 30)]),
    4: ("Week 4 – Final (sample)", [("Final Implementation", 30), ("Testing and Validation", 20), ("Documentation", 20), ("Presentation and Demonstration", 30)]),
}


# ------------------------------------------------------------- numbers
def to_decimal(value, label):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise AppError(f"{label} must be a number", 422)
    if not d.is_finite():
        raise AppError(f"{label} must be a number", 422)
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _f(d):
    return float(d) if d is not None else None


def summarize(review):
    """Totals for one review. Percentage is based on max marks of ALL criteria (unscored ones count as 0)."""
    total_max = sum((c.max_marks for c in review.criteria), Decimal("0"))
    scored = [c for c in review.criteria if c.score is not None]
    obtained = sum((c.score.marks_obtained for c in scored), Decimal("0"))
    pct = (obtained * 100 / total_max).quantize(CENT, rounding=ROUND_HALF_UP) if total_max else Decimal("0.00")
    return {"total_obtained": _f(obtained), "total_max": _f(total_max), "percentage": _f(pct),
            "criteria_count": len(review.criteria), "scored_count": len(scored)}


def overall_performance(project_id, include_scores=True):
    """Project-level performance across SUBMITTED reviews only. Students never receive scores (include_scores=False)."""
    reviews = (db.session.query(ProjectReview).filter_by(project_id=project_id, status="submitted")
               .order_by(ProjectReview.week_no).all())
    weeks = project_weeks(db.session.get(Project, project_id))
    if not include_scores:
        return {"reviews_submitted": len(reviews), "reviews_total": weeks}
    rows = [{"week_no": r.week_no, **summarize(r)} for r in reviews]
    total_max = sum(Decimal(str(x["total_max"])) for x in rows)
    obtained = sum(Decimal(str(x["total_obtained"])) for x in rows)
    return {
        "reviews_submitted": len(rows), "reviews_total": weeks, "reviews": rows,
        "total_obtained": _f(obtained), "total_max": _f(total_max),
        "percentage": _f((obtained * 100 / total_max).quantize(CENT, rounding=ROUND_HALF_UP)) if total_max else 0.0,
        "average_percentage": round(sum(x["percentage"] for x in rows) / len(rows), 2) if rows else 0.0,
    }


# ------------------------------------------------------------ permissions
def week_gate(project, week_no, sequential=True):
    """Why this week cannot be evaluated yet (None when it can). Weeks open strictly one after another."""
    order = lifecycle.ORDER[project.stage]
    if project.stage == "completed":
        return "This project is completed. Reopen it before changing evaluations."
    if week_no == 1 and order < lifecycle.ORDER["srs_approved"]:
        return "Review 1 opens once the SRS is approved."
    if week_no > 1 and order < lifecycle.ORDER["implementation"]:
        return f"Weeks 2–{project_weeks(project)} can be evaluated once implementation has started (SRS approved)."
    if week_no > 1 and sequential:
        prev = db.session.query(ProjectReview).filter_by(project_id=project.id, week_no=week_no - 1).first()
        if prev is not None and prev.status != "submitted":
            return f"Week {week_no - 1} must be evaluated before Week {week_no} opens."
    return None


def date_gate(review):
    """Evaluation opens on the scheduled review date (None when open, or when no date is scheduled yet)."""
    if review.review_date and date.today() < review.review_date:
        return f"This review is scheduled for {review.review_date:%d %b %Y}. Evaluation opens on that date."
    return None


def is_assigned_mentor(user, project):
    from app.services.project_service import mentors_for_project
    m = mentor_profile(user)
    return bool(m and any(x.id == m.id for x in mentors_for_project(project)))


def evaluation_of(review, user):
    """The caller's own evaluation of this review (None until they first save)."""
    m = mentor_profile(user)
    return next((e for e in review.evaluations if m and e.mentor_id == m.id), None) if m else None


def review_permissions(user, review):
    project = db.session.get(Project, review.project_id)
    gate = week_gate(project, review.week_no)
    assigned = is_assigned_mentor(user, project)          # the Domain Lead and every mentor of the project evaluate
    mine = evaluation_of(review, user) if assigned else None
    my_status = mine.status if mine else "pending"
    dgate = date_gate(review)
    can_edit = assigned and my_status in EDITABLE and gate is None and dgate is None
    submitted_any = any(e.status == "submitted" for e in review.evaluations)
    can_reopen = project.stage != "completed" and (
        (assigned and my_status == "submitted") or (not assigned and user.role_name in ("admin", "domain_lead") and submitted_any))
    reason = None
    if not can_edit:
        reason = gate or (dgate if assigned else None) or (
            "Your evaluation is submitted and read-only. Reopen it to correct it." if my_status == "submitted" else None)
    return {"can_edit": can_edit, "can_submit": can_edit, "can_reopen": can_reopen, "reason": reason,
            "can_schedule": bool(assigned and my_status in EDITABLE and week_gate(project, review.week_no, sequential=False) is None)}


# --------------------------------------------------------- serialisation
def criterion_to_dict(c):
    return {"id": c.id, "name": c.name, "max_marks": _f(c.max_marks), "display_order": c.display_order,
            "marks_obtained": _f(c.score.marks_obtained) if c.score else None, "remarks": c.score.remarks if c.score else None}


def _eval_criteria_view(evl):
    if not evl:
        return []
    return [{"id": c["id"], "name": c["name"], "max_marks": c["max_marks"], "display_order": i,
             "marks_obtained": c.get("marks_obtained"), "remarks": c.get("remarks")} for i, c in enumerate(evl.criteria or [])]


def summarize_eval(evl):
    """Totals for one evaluator's own criteria (same shape as `summarize`)."""
    crit = (evl.criteria or []) if evl else []
    total_max = sum((Decimal(str(c["max_marks"])) for c in crit), Decimal("0"))
    scored = [c for c in crit if c.get("marks_obtained") is not None]
    obtained = sum((Decimal(str(c["marks_obtained"])) for c in scored), Decimal("0"))
    pct = (obtained * 100 / total_max).quantize(CENT, rounding=ROUND_HALF_UP) if total_max else Decimal("0.00")
    return {"total_obtained": _f(obtained), "total_max": _f(total_max), "percentage": _f(pct),
            "criteria_count": len(crit), "scored_count": len(scored)}


def student_remark_rows(review, only_student_id=None, include_marks=True, evl=None):
    """One row per project member (the individual evaluation: remark + marks). A student only ever gets their own
    row and never the marks. With `evl`, the rows are that evaluator's own; otherwise the shared (averaged) ones."""
    project = db.session.get(Project, review.project_id)
    if evl is not None:
        existing = {r["student_id"]: r for r in (evl.student_remarks or [])}

        def get(row):
            return (row.get("remarks"), row.get("marks_obtained")) if row else (None, None)
    else:
        existing = {r.student_id: r for r in review.student_remarks}

        def get(row):
            return (row.remarks, _f(row.marks_obtained)) if row else (None, None)
    rows = []
    for m in project.members:
        remarks, marks = get(existing.get(m.student_id))
        item = {"student_id": m.student_id, "name": m.student.user.name, "registration_no": m.student.registration_no,
                "remarks": remarks or ""}
        if include_marks:
            item["marks_obtained"] = marks
        rows.append(item)
    return [r for r in rows if only_student_id is None or r["student_id"] == only_student_id]


def revert_note(review, mentor_id=None):
    """Why/who sent a review back (shown to the evaluator while their evaluation is under revision)."""
    from app.models import User
    revs = db.session.query(EvaluationRevision).filter_by(review_id=review.id).order_by(EvaluationRevision.id.desc()).all()
    rev = next((r for r in revs if mentor_id is None or (r.snapshot or {}).get("mentor_id") in (None, mentor_id)), None)
    if not rev:
        return None
    u = db.session.get(User, rev.changed_by) if rev.changed_by else None
    return {"reason": rev.reason, "by": u.name if u else "Unknown", "by_role": u.role_name if u else None, "at": rev.created_at.isoformat()}


def _evaluator_name(evl):
    m = db.session.get(Mentor, evl.mentor_id)
    return m.user.name if m else "Unknown"


def evaluations_view(review, viewer_mentor_id=None, detail=True):
    """Every evaluator's evaluation of this review, for evaluators and monitors (never students)."""
    out = []
    for e in sorted(review.evaluations, key=lambda x: x.id):
        s = summarize_eval(e)
        item = {"mentor_id": e.mentor_id, "mentor": _evaluator_name(e), "status": e.status, "status_label": STATUS_LABEL[e.status],
                "percentage": s["percentage"], "total_obtained": s["total_obtained"], "total_max": s["total_max"],
                "submitted_at": e.submitted_at.isoformat() if e.submitted_at else None,
                "is_mine": e.mentor_id == viewer_mentor_id}
        if detail and e.status == "submitted":
            item.update(criteria=_eval_criteria_view(e), **{f: getattr(e, f) for f in FEEDBACK_FIELDS})
        out.append(item)
    return out


def review_to_dict(review, role, viewer_student_id=None, user=None):
    """Full view for evaluators/monitors; students only ever receive submitted evaluations (and only their own remark).

    For an evaluator (`user` is an assigned mentor / Domain Lead) the criteria, feedback and status are THEIR OWN
    evaluation and `summary` is theirs too; `overall` is the average of every submitted evaluation."""
    from app.services.metrics_service import phase_for
    base = {"id": review.id, "project_id": review.project_id, "week_no": review.week_no, "phase": phase_for(review.week_no),
            "due_date": review.due_date.isoformat() if review.due_date else None,
            "is_overdue": bool(review.due_date and review.status != "submitted" and review.due_date < date.today()),
            "status": review.status, "status_label": STATUS_LABEL[review.status], "version": review.version}
    if role == "student" and review.status != "submitted":
        return {**base, "visible": False, "status": "pending", "status_label": "Pending" if review.status != "reopened" else "Under revision",
                "review_date": review.review_date.isoformat() if review.review_date else None}
    submitted = [e for e in review.evaluations if e.status == "submitted"]
    names = ", ".join(_evaluator_name(e) for e in submitted)
    if not names:
        m = db.session.get(Mentor, review.mentor_id) if review.mentor_id else None
        names = m.user.name if m else None
    date_s = review.review_date.isoformat() if review.review_date else None
    submitted_s = review.submitted_at.isoformat() if review.submitted_at else None
    if role == "student":
        # Students see the mentors' words only: no marks, totals or percentages, and only their own remark.
        return {**base, "visible": True, "review_date": date_s, "mentor": names, "submitted_at": submitted_s,
                **{f: getattr(review, f) for f in FEEDBACK_FIELDS},
                "criteria": [{"id": c.id, "name": c.name, "remarks": c.score.remarks if c.score else None} for c in review.criteria],
                "summary": None, "student_remarks": student_remark_rows(review, viewer_student_id, include_marks=False),
                "revert_note": None}
    project = db.session.get(Project, review.project_id)
    me = mentor_profile(user) if user is not None and is_assigned_mentor(user, project) else None
    mine = next((e for e in review.evaluations if me and e.mentor_id == me.id), None)
    common = {"visible": True, "review_date": date_s, "mentor": names, "submitted_at": submitted_s,
              "overall": summarize(review), "evaluations": evaluations_view(review, me.id if me else None),
              "evaluators_total": len(mentors_for_project(project))}
    if me is not None:        # the evaluator works on their own evaluation
        status = mine.status if mine else "pending"
        return {**base, **common, "status": status, "status_label": STATUS_LABEL[status],
                "version": mine.version if mine else 1,
                "submitted_at": mine.submitted_at.isoformat() if mine and mine.submitted_at else None,
                **{f: (getattr(mine, f) if mine else None) for f in FEEDBACK_FIELDS},
                "criteria": _eval_criteria_view(mine), "summary": summarize_eval(mine),
                "individual_max_marks": _f(mine.individual_max_marks) if mine else 10.0,
                "student_remarks": student_remark_rows(review, evl=mine),
                "revert_note": revert_note(review, me.id) if status == "reopened" else None}
    if review.status != "submitted" and review.evaluations:     # monitors can follow work in progress
        first = sorted(review.evaluations, key=lambda e: e.id)[0]
        return {**base, **common, **{f: getattr(first, f) for f in FEEDBACK_FIELDS},
                "criteria": _eval_criteria_view(first), "summary": summarize_eval(first),
                "individual_max_marks": _f(first.individual_max_marks),
                "student_remarks": student_remark_rows(review, evl=first),
                "revert_note": revert_note(review) if review.status == "reopened" else None}
    return {**base, **common, **{f: getattr(review, f) for f in FEEDBACK_FIELDS},
            "criteria": [criterion_to_dict(c) for c in review.criteria], "summary": summarize(review),
            "individual_max_marks": _f(review.individual_max_marks),
            "student_remarks": student_remark_rows(review),
            "revert_note": revert_note(review) if review.status == "reopened" else None}


# --------------------------------------------------------------- lookups
def get_review_or_404(review_id):
    r = db.session.get(ProjectReview, review_id)
    if not r:
        raise NotFound("Review not found")
    return r


def _lock(project_id):
    return db.session.query(Project).filter_by(id=project_id).with_for_update().one()


def mentors_for_project(project):
    from app.services.project_service import mentors_for_project as _m
    return _m(project)


# ------------------------------------------------------------ validation
def _clean_criteria(items):
    if not isinstance(items, list):
        raise AppError("criteria must be a list", 422)
    if len(items) > MAX_CRITERIA:
        raise AppError(f"At most {MAX_CRITERIA} criteria per review", 422)
    out, seen, errors = [], set(), {}
    for i, it in enumerate(items):
        name = (it.get("name") or "").strip()
        key = f"criteria.{i}"
        if not name:
            errors[f"{key}.name"] = "Criterion name is required"
        elif len(name) > 150:
            errors[f"{key}.name"] = "Criterion name is too long (max 150)"
        elif name.lower() in seen:
            errors[f"{key}.name"] = f"Duplicate criterion '{name}'"
        seen.add(name.lower())
        try:
            mx = to_decimal(it.get("max_marks"), "Maximum marks")
            if mx <= 0 or mx > MAX_MARKS:
                errors[f"{key}.max_marks"] = f"Maximum marks must be between 0.01 and {MAX_MARKS}"
        except AppError as e:
            errors[f"{key}.max_marks"], mx = e.message, None
        marks = None
        if it.get("marks_obtained") not in (None, ""):
            try:
                marks = to_decimal(it["marks_obtained"], "Marks")
                if marks < 0:
                    errors[f"{key}.marks_obtained"] = "Marks cannot be negative"
                elif mx is not None and marks > mx:
                    errors[f"{key}.marks_obtained"] = f"Marks cannot exceed the maximum ({mx})"
            except AppError as e:
                errors[f"{key}.marks_obtained"] = e.message
        remarks = (it.get("remarks") or "").strip()[:2000] or None
        out.append({"id": it.get("id"), "name": name, "max_marks": mx, "marks": marks, "remarks": remarks})
    if errors:
        raise AppError("Validation failed", 422, errors)
    return out


def _apply_criteria(evl, cleaned):
    """Replace the evaluator's own criteria list. Existing ids are kept, so a client can rename or re-score a criterion."""
    existing = {c["id"]: c for c in (evl.criteria or [])}
    next_id = max(existing, default=0) + 1
    out = []
    for item in cleaned:
        cid = item["id"]
        if cid is not None:
            if cid not in existing:
                raise AppError("A criterion does not belong to this review", 422)
        else:
            cid, next_id = next_id, next_id + 1
        out.append({"id": cid, "name": item["name"], "max_marks": _f(item["max_marks"]),
                    "marks_obtained": _f(item["marks"]), "remarks": item["remarks"]})
    evl.criteria = out


def _apply_feedback(evl, payload):
    for f in FEEDBACK_FIELDS:
        if f in payload:
            text = (payload[f] or "").strip()
            if len(text) > 5000:
                raise AppError(f"{f.capitalize()} is too long (max 5000 characters)", 422, {f: "Too long"})
            setattr(evl, f, text or None)


def _apply_review_date(review, payload):
    """The scheduled date belongs to the review itself (shared by every evaluator)."""
    if "review_date" not in payload:
        return
    try:
        new_date = parse_date(payload["review_date"]) if payload["review_date"] else None
    except ValueError as e:
        raise AppError(str(e), 422, {"review_date": str(e)})
    if review.review_date is not None and new_date != review.review_date:
        # Scheduled dates move only through an approved request (services/review_schedule_service.py).
        raise AppError("The review date is already scheduled. Request a change – your Domain Lead must approve it.", 422,
                       {"review_date": "Already scheduled; request a change for approval"})
    first_time = review.review_date is None and new_date is not None
    review.review_date = new_date
    if first_time:        # students hear about the schedule as soon as the mentor sets it
        project = db.session.get(Project, review.project_id)
        notify(member_user_ids(project), "review_scheduled", f"Week {review.week_no} review scheduled",
               f"Your Week {review.week_no} review for “{project.title}” is scheduled for {new_date}.", "/student/reviews")


def _apply_individual_max(evl, payload):
    if "individual_max_marks" not in payload or payload["individual_max_marks"] in (None, ""):
        return
    mx = to_decimal(payload["individual_max_marks"], "Individual maximum marks")
    if mx <= 0 or mx > MAX_MARKS:
        raise AppError("Validation failed", 422, {"individual_max_marks": f"Must be between 0.01 and {MAX_MARKS}"})
    evl.individual_max_marks = mx


def _apply_student_remarks(evl, project, items):
    """items: [{student_id, remarks, marks_obtained}] - the evaluator's individual evaluation of each member.
    Only members of this project; a row with neither remark nor marks is removed."""
    if items is None:
        return
    members = {m.student_id for m in project.members}
    rows = {r["student_id"]: r for r in (evl.student_remarks or [])}
    errors = {}
    for i, it in enumerate(items):
        sid = it.get("student_id")
        key = f"student_remarks.{i}"
        if sid not in members:
            errors[key] = "Not a member of this project"
            continue
        text = (it.get("remarks") or "").strip()
        if len(text) > 2000:
            errors[key] = "Remarks are too long (max 2000 characters)"
            continue
        marks = None
        if it.get("marks_obtained") not in (None, ""):
            try:
                marks = to_decimal(it["marks_obtained"], "Individual marks")
            except AppError as e:
                errors[key] = e.message
                continue
            if marks < 0 or marks > evl.individual_max_marks:
                errors[key] = f"Individual marks must be between 0 and {evl.individual_max_marks:g}"
                continue
        if not text and marks is None:
            rows.pop(sid, None)
        else:
            rows[sid] = {"student_id": sid, "remarks": text or None, "marks_obtained": _f(marks)}
    if errors:
        raise AppError("Validation failed", 422, errors)
    evl.student_remarks = list(rows.values())


# ------------------------------------------------------------ aggregation
def _mean(values):
    return (sum(values, Decimal("0")) / len(values)).quantize(CENT, rounding=ROUND_HALF_UP)


def _joined(subs, field, with_names):
    parts = [(e, (getattr(e, field) or "").strip()) for e in subs]
    parts = [(e, t) for e, t in parts if t]
    if not parts:
        return None
    if len(parts) == 1 or not with_names:
        return parts[0][1]
    return "\n\n".join(f"{_evaluator_name(e)}: {t}" for e, t in parts)


def _aggregate(review):
    """Rebuild the shared review from the evaluators who have SUBMITTED: every criterion's marks (and maximum) is the
    average over the evaluators who scored it, individual marks likewise, feedback is combined. A week therefore counts
    as evaluated once one evaluator submits, and later submissions refine the average."""
    for c in list(review.criteria):
        db.session.delete(c)
    for r in list(review.student_remarks):
        db.session.delete(r)
    db.session.flush()
    db.session.expire(review, ["criteria", "student_remarks"])
    evals = list(review.evaluations)
    subs = sorted((e for e in evals if e.status == "submitted"), key=lambda e: (e.submitted_at or utcnow(), e.id))
    if not subs:
        review.status = ("reopened" if any(e.status == "reopened" for e in evals) else "draft" if evals else "pending")
        review.submitted_at, review.mentor_id = None, None
        for f in FEEDBACK_FIELDS:
            setattr(review, f, None)
        review.version += 1
        db.session.flush()
        return review
    names = {}                                                   # criterion name -> [(max, marks, remarks), ...]
    for e in subs:
        for c in e.criteria or []:
            names.setdefault(c["name"].strip().lower(), []).append(
                (c["name"], Decimal(str(c["max_marks"])), None if c.get("marks_obtained") is None else Decimal(str(c["marks_obtained"])),
                 c.get("remarks"), e))
    for order, rows in enumerate(names.values()):
        crit = EvaluationCriterion(review_id=review.id, name=rows[0][0], max_marks=_mean([r[1] for r in rows]), display_order=order)
        db.session.add(crit)
        db.session.flush()
        marks = [r[2] for r in rows if r[2] is not None]
        notes = [(r[4], r[3]) for r in rows if r[3]]
        if marks or notes:
            remark = (notes[0][1] if len(notes) == 1 else "\n".join(f"{_evaluator_name(e)}: {t}" for e, t in notes)) if notes else None
            db.session.add(EvaluationScore(criterion_id=crit.id, marks_obtained=_mean(marks) if marks else Decimal("0"), remarks=remark))
    review.individual_max_marks = max(e.individual_max_marks for e in subs)
    per_student = {}
    for e in subs:
        for r in e.student_remarks or []:
            per_student.setdefault(r["student_id"], []).append((e, r))
    for sid, rows in per_student.items():
        marks = [Decimal(str(r["marks_obtained"])) * review.individual_max_marks / e.individual_max_marks
                 for e, r in rows if r.get("marks_obtained") is not None]
        notes = [(e, (r.get("remarks") or "").strip()) for e, r in rows if (r.get("remarks") or "").strip()]
        text = None
        if notes:
            text = notes[0][1] if len(notes) == 1 else "\n\n".join(f"{_evaluator_name(e)}: {t}" for e, t in notes)
        db.session.add(ReviewStudentRemark(review_id=review.id, student_id=sid, remarks=text,
                                           marks_obtained=_mean(marks) if marks else None))
    with_names = len(subs) > 1
    for f in FEEDBACK_FIELDS:
        setattr(review, f, _joined(subs, f, with_names))
    review.status = "submitted"
    review.submitted_at = max((e.submitted_at for e in subs if e.submitted_at), default=utcnow())
    review.mentor_id = subs[0].mentor_id
    review.version += 1
    db.session.flush()
    db.session.expire(review, ["criteria", "student_remarks"])
    return review


# ---------------------------------------------------------------- writing
def _prepare(review, user, payload=None, for_submit=False):
    """Common preconditions for writing to a review; returns the locked project and the caller's own evaluation."""
    project = _lock(review.project_id)
    db.session.refresh(review, with_for_update=True)
    if user.role_name not in ("mentor", "domain_lead"):
        raise Forbidden("Only the project's mentors and Domain Lead can evaluate")
    if not is_assigned_mentor(user, project):
        if user.role_name == "domain_lead":
            raise Forbidden("Only the project's mentors and Domain Lead can evaluate")
        raise NotFound("Review not found")
    only_schedule = payload is not None and not for_submit and set(payload) <= {"review_date", "version"}
    gate = week_gate(project, review.week_no, sequential=not only_schedule)      # dates can be planned for every week up front
    if gate:
        raise AppError(gate, 409)
    dgate = date_gate(review)
    if dgate and not only_schedule:
        raise AppError(dgate, 409)
    evl = evaluation_of(review, user)
    if evl is None:
        evl = ReviewEvaluation(review_id=review.id, mentor_id=mentor_profile(user).id, status="draft", criteria=[],
                               student_remarks=[], version=1)
        db.session.add(evl)
        db.session.flush()
        db.session.expire(review, ["evaluations"])
    elif evl.status not in EDITABLE:
        raise AppError("This review was already submitted. Reopen it (with a reason) to make changes.", 409)
    return project, evl


def _check_version(evl, payload):
    expected = payload.get("version")
    if expected is not None and expected != evl.version:
        raise AppError("This evaluation was changed elsewhere. Reload and try again.", 409)


def _write(review, project, evl, payload):
    if "criteria" in payload:
        _apply_criteria(evl, _clean_criteria(payload["criteria"]))
    _apply_feedback(evl, payload)
    _apply_individual_max(evl, payload)
    _apply_student_remarks(evl, project, payload.get("student_remarks"))
    _apply_review_date(review, payload)


# ------------------------------------------------------------- operations
def save_draft(review, payload, user):
    project, evl = _prepare(review, user, payload)
    _check_version(evl, payload)
    _write(review, project, evl, payload)
    evl.version += 1
    if review.status != "submitted":
        _aggregate(review)
    lifecycle.log_activity(project.id, user.id, "review.saved", {"week": review.week_no})
    return review


def submit(review, payload, user):
    project, evl = _prepare(review, user, payload, for_submit=True)
    _check_version(evl, payload)
    _write(review, project, evl, payload)
    errors = {}
    if not evl.criteria:
        errors["criteria"] = "Add at least one evaluation criterion"
    missing = [c["name"] for c in evl.criteria or [] if c.get("marks_obtained") is None]
    if missing:
        errors["criteria"] = "Score every criterion before submitting: " + ", ".join(missing)
    if not (evl.remarks or "").strip():
        errors["remarks"] = "Overall feedback is required to submit"
    if len(project.members) > 1:  # a team review also evaluates every member individually
        rows = {r["student_id"]: r for r in (evl.student_remarks or [])}
        incomplete = [m.student.user.name for m in project.members
                      if m.student_id not in rows or rows[m.student_id].get("marks_obtained") is None
                      or not (rows[m.student_id].get("remarks") or "").strip()]
        if incomplete:
            errors["student_remarks"] = ("Evaluate every team member (individual marks and remarks) before submitting: "
                                         + ", ".join(incomplete))
    if errors:
        raise AppError(next(iter(errors.values())), 422, errors)
    first = review.status != "submitted"
    was_reopened = evl.status == "reopened"
    evl.status, evl.submitted_at = "submitted", utcnow()
    evl.version += 1
    review.review_date = review.review_date or date.today()
    _aggregate(review)
    s = summarize_eval(evl)
    lifecycle.log_activity(project.id, user.id, "review.submitted", {"week": review.week_no, "percentage": s["percentage"]})
    audit(user.id, "review.submit", "project_review", review.id, {"project_id": project.id, "week": review.week_no})
    db.session.flush()
    if was_reopened:      # changed marks are for the Domain Leads only; students are not told
        notify(domain_lead_user_ids(project.batch.domain_id) - {user.id}, "review_updated", f"Week {review.week_no} marks updated",
               f"{user.name} changed and resubmitted the Week {review.week_no} marks of “{project.title}”.", f"/reviews/{review.id}")
    if first and not was_reopened:
        notify(member_user_ids(project), "review_completed", f"Week {review.week_no} review completed",
               f"Your Week {review.week_no} evaluation is ready. Open it to read your mentor's remarks.", "/student/reviews")
    return review


def reopen(review, reason, user, mentor_id=None):
    """Send submitted evaluation(s) back for correction. An evaluator reopens their own; a Domain Lead / admin who is not
    an evaluator of the project reverts every submitted evaluation (or just `mentor_id`'s)."""
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise AppError("A reason (at least 5 characters) is required to reopen a review", 422, {"reason": "Reason required"})
    project = _lock(review.project_id)
    db.session.refresh(review, with_for_update=True)
    assigned = is_assigned_mentor(user, project)
    if assigned:
        mine = evaluation_of(review, user)
        targets = [mine] if mine is not None and mine.status == "submitted" else []
    elif user.role_name == "mentor":
        raise NotFound("Review not found")
    elif user.role_name not in ("admin", "domain_lead"):    # domain-lead scope was already enforced by the route
        raise Forbidden("Only the project's mentors, its Domain Lead or an administrator can reopen a review")
    else:
        targets = [e for e in review.evaluations if e.status == "submitted" and (mentor_id is None or e.mentor_id == mentor_id)]
    if not targets:
        raise AppError("Only submitted reviews can be reopened", 409)
    if project.stage == "completed":
        raise AppError("The project is completed. Reopen the project first.", 409)
    was_submitted = review.status == "submitted"
    for evl in targets:
        snapshot = {"mentor_id": evl.mentor_id, "mentor": _evaluator_name(evl), "status": evl.status,
                    "criteria": _eval_criteria_view(evl), "summary": summarize_eval(evl),
                    "student_remarks": evl.student_remarks or [], "version": evl.version,
                    **{f: getattr(evl, f) for f in FEEDBACK_FIELDS}}
        db.session.add(EvaluationRevision(review_id=review.id, snapshot=snapshot, reason=reason, changed_by=user.id, created_at=utcnow()))
        evl.status, evl.submitted_at = "reopened", None
        evl.version += 1
    _aggregate(review)
    lifecycle.log_activity(project.id, user.id, "review.reopened", {"week": review.week_no, "reason": reason})
    audit(user.id, "review.reopen", "project_review", review.id, {"project_id": project.id, "week": review.week_no, "reason": reason})
    db.session.flush()
    if assigned:          # a mentor opening their own review for changes: Domain Leads only are alerted
        notify(domain_lead_user_ids(project.batch.domain_id) - {user.id}, "review_reopened_by_mentor",
               f"Week {review.week_no} review opened for changes",
               f"{user.name} opened their Week {review.week_no} evaluation of “{project.title}” to change marks. Reason: {reason}",
               f"/reviews/{review.id}")
    elif was_submitted and review.status != "submitted":
        notify(member_user_ids(project), "review_reopened", f"Week {review.week_no} review under revision",
               "This evaluation was reopened for correction. You'll be notified when it is resubmitted.", "/student/reviews")
    if not assigned:   # tell the evaluator(s) their score was sent back, with the reason
        who = "Your Domain Lead" if user.role_name == "domain_lead" else "An administrator"
        owners = {db.session.get(Mentor, e.mentor_id).user_id for e in targets}
        notify(owners, "review_reverted", f"Week {review.week_no} score reverted for update",
               f"{who} ({user.name}) reverted “{project.title}” Week {review.week_no} to you. Reason: {reason}", f"/reviews/{review.id}")
    return review


def revisions(review):
    from app.models import User
    out = []
    for rev in sorted(review.revisions, key=lambda r: r.id, reverse=True):
        u = db.session.get(User, rev.changed_by) if rev.changed_by else None
        out.append({"id": rev.id, "reason": rev.reason, "changed_by": u.name if u else "Unknown",
                    "at": rev.created_at.isoformat(), "snapshot": rev.snapshot})
    return out


def final_signoff(project, remarks, user):
    remarks = (remarks or "").strip()
    if len(remarks) < 3:
        raise AppError("Final evaluation remarks are required", 422, {"remarks": "Remarks required"})
    project = _lock(project.id)
    if is_assigned_mentor(user, project):
        pass
    elif user.role_name == "mentor":
        raise NotFound("Project not found")
    elif user.role_name != "admin":
        raise Forbidden("Only the project's mentor or an administrator can sign off the final evaluation")
    if project.stage != "final_evaluation":
        raise AppError("The final evaluation can only be signed off while the project is in the Final Evaluation stage", 409)
    if project.final_signed_off_by:
        raise AppError("The final evaluation was already signed off", 409)
    submitted = db.session.query(ProjectReview).filter_by(project_id=project.id, status="submitted").count()
    if submitted < project_weeks(project):
        raise AppError(f"All {'three' if project_weeks(project) == 3 else 'four'} weekly reviews must be submitted first", 409)
    project.final_signed_off_by, project.final_remarks = user.id, remarks
    lifecycle.log_activity(project.id, user.id, "final.signed_off", {})
    audit(user.id, "project.final_signoff", "project", project.id)
    notify(member_user_ids(project) | domain_lead_user_ids(project.batch.domain_id), "final_signed_off", "Final evaluation completed",
           f"The final evaluation of “{project.title}” was signed off.", f"/projects/{project.id}")
    return project


# --------------------------------------------------------------- templates
def template_owner_ids(user):
    """Whose criteria templates the caller can see and use: their own plus every mentor / Domain Lead of their domain(s)."""
    from app.models import User
    from app.permissions.scope import lead_domain_ids
    me = mentor_profile(user)
    domains = set(lead_domain_ids(user) or ()) | ({me.domain_id} if me and me.domain_id else set())
    owners = {user.id}
    if domains:
        owners |= {m.user_id for m in db.session.query(Mentor).filter(Mentor.domain_id.in_(list(domains))).all()}
        owners |= {row[0] for row in db.session.query(DomainLead.user_id).filter(DomainLead.domain_id.in_(list(domains))).all()}
    return owners


def template_owner_name(template):
    from app.models import User
    u = db.session.get(User, template.owner_id)
    return u.name if u else "Unknown"


def clean_template_criteria(items):
    cleaned = _clean_criteria([{"name": i.get("name"), "max_marks": i.get("max_marks")} for i in items])
    return [{"name": c["name"], "max_marks": float(c["max_marks"])} for c in cleaned]


def apply_template(review, template, user):
    _project, evl = _prepare(review, user)
    _apply_criteria(evl, [{"id": None, "name": c["name"], "max_marks": Decimal(str(c["max_marks"])), "marks": None, "remarks": None}
                          for c in template.criteria])
    evl.version += 1
    if review.status != "submitted":
        _aggregate(review)
    return review


def create_samples(user):
    created = []
    existing = {t.name for t in db.session.query(EvaluationTemplate).filter_by(owner_id=user.id)}
    for week, (name, crit) in SAMPLE_TEMPLATES.items():
        if name in existing:
            continue
        t = EvaluationTemplate(owner_id=user.id, name=name, week_no=week,
                               criteria=[{"name": n, "max_marks": m} for n, m in crit])
        db.session.add(t)
        created.append(t)
    db.session.flush()
    return created
