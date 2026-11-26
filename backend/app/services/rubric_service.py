"""Which evaluation rubric belongs to which domain, and which rubrics a user may see.

Admins see every track. Everyone else sees the tracks of their own domains: a student's batch domain, the domains a
lead leads, and the domains a mentor serves (their profile's, plus those of the batches they are assigned to).
"""
import re

from app.extensions import db
from app.models import Domain
from app.models.user import ROLE_ADMIN, ROLE_STUDENT
from app.permissions.scope import lead_domain_ids, mentor_domain_ids, mentor_profile, student_profile
from app.services import rubric_data

# Spellings of a domain's name or code (lower case, letters and digits only) that identify its track.
_ALIASES = {
    "fsd": {"fsd", "fullstack", "fullstackdevelopment", "fullstackdeveloper", "mern", "mernstack", "mean", "meanstack"},
    "ds": {"ds", "datascience", "datasciences"},
    "aiml": {"ai", "ml", "aiml", "aiandml", "machinelearning", "artificialintelligence",
             "artificialintelligenceandmachinelearning", "artificialintelligencemachinelearning"},
    "cyber": {"cyber", "cybersec", "cybersecurity"},
}


def detect_track(*labels):
    for label in labels:
        key = re.sub(r"[^a-z0-9]", "", (label or "").lower().replace("&", "and"))
        for track, aliases in _ALIASES.items():
            if key in aliases:
                return track
    return None


def domain_track(domain):
    """The administrator's choice wins; otherwise it is recognised from the domain's name or code."""
    if domain.rubric_track in rubric_data.TRACKS:
        return domain.rubric_track
    return detect_track(domain.name, domain.code)


def _own_domain_ids(user):
    if user.role_name == ROLE_STUDENT:
        s = student_profile(user)
        return {s.batch.domain_id} - {None} if s else set()
    ids = lead_domain_ids(user) | mentor_domain_ids(user)
    m = mentor_profile(user)
    if m and m.domain_id:
        ids.add(m.domain_id)
    return ids


def rubrics_for(user):
    is_admin = user.role_name == ROLE_ADMIN
    q = db.select(Domain).order_by(Domain.name)
    if not is_admin:
        q = q.where(Domain.id.in_(_own_domain_ids(user) or {-1}))

    by_track, unmapped = {}, []
    for d in db.session.scalars(q):
        ref = {"id": d.id, "name": d.name}
        track = domain_track(d)
        if track:
            by_track.setdefault(track, []).append(ref)
        else:
            unmapped.append(ref)

    tracks = [{"key": key, **track, "domains": by_track.get(key, [])}
              for key, track in rubric_data.TRACKS.items() if is_admin or key in by_track]
    return {"title": rubric_data.TITLE, "version": rubric_data.VERSION, "levels": rubric_data.LEVELS,
            "common_criteria": rubric_data.COMMON_CRITERIA, "tracks": tracks, "unmapped_domains": unmapped,
            "scoring_principles": rubric_data.SCORING_PRINCIPLES, "code_quality": rubric_data.CODE_QUALITY,
            "grades": rubric_data.GRADES, "eligibility": rubric_data.ELIGIBILITY}
