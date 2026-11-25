"""Account provisioning for mentors, leads and students (profile rows included)."""
import secrets
import string

from app.extensions import db
from app.models import (Batch, BatchMentor, DomainLead, Mentor, Notification, PasswordResetToken, Project, ProjectMentor, ProjectStudent,
                        Role, Student, StudentProposal, User)
from app.services.auth_service import create_user, validate_password_strength
from app.utils.errors import AppError, NotFound
from app.utils.ids import student_prefix


def generate_temp_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        if any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw):
            return pw


MENTOR_TYPES = ("internal", "industry")
INDUSTRY_REQUIRED = {"company": "Company", "industry_sector": "Industry / sector", "designation": "Designation",
                     "bio": "Professional profile"}


def clean_mentor_profile(mentor_type, designation, company, industry_sector, experience_years, linkedin_url, bio):
    """Internal mentors are ICT trainers; industry mentors must supply company details and a profile."""
    mentor_type = mentor_type or "internal"
    if mentor_type not in MENTOR_TYPES:
        raise AppError("Mentor type must be internal or industry", 422, {"mentor_type": "Invalid mentor type"})
    strip = lambda v: (v or "").strip() or None  # noqa: E731
    out = {"mentor_type": mentor_type, "designation": strip(designation), "company": None, "industry_sector": None,
           "experience_years": None, "linkedin_url": None, "bio": None}
    if mentor_type == "industry":
        out.update(company=strip(company), industry_sector=strip(industry_sector), experience_years=experience_years,
                   linkedin_url=strip(linkedin_url), bio=strip(bio))
        errors = {k: f"{label} is required for industry mentors" for k, label in INDUSTRY_REQUIRED.items() if not out[k]}
        if errors:
            raise AppError("Industry mentors need company details and a profile", 422, errors)
        if out["linkedin_url"] and not out["linkedin_url"].lower().startswith(("http://", "https://")):
            raise AppError("Profile link must start with http:// or https://", 422,
                           {"linkedin_url": "Must be a full http(s) URL"})
    return out


def create_staff_user(role, name, email, password, phone=None, domain_id=None, designation=None, expertise=None,
                      mentor_type="internal", company=None, industry_sector=None, experience_years=None,
                      linkedin_url=None, bio=None, domain_ids=None, also_mentor=False):
    if role == "student":
        raise AppError("Students are created through the student endpoints (a batch is required)", 422)
    domains = []
    if role == "domain_lead":                   # a lead always leads at least one domain; validate before creating the user
        from app.models import Domain
        ids = list(dict.fromkeys(domain_ids or []))
        if not ids:
            raise AppError("Choose the domain this lead will manage", 422, {"domain_ids": "Select at least one domain"})
        domains = [db.session.get(Domain, i) for i in ids]
        if any(d is None for d in domains):
            raise AppError("Domain not found", 422, {"domain_ids": "Domain not found"})
        if any(not d.is_active for d in domains):
            raise AppError("An inactive domain cannot be assigned", 422, {"domain_ids": "Domain is inactive"})
    profile = clean_mentor_profile(mentor_type, designation, company, industry_sector, experience_years,
                                   linkedin_url, bio) if role == "mentor" or (role == "admin" and also_mentor) else None  # validate before creating the user
    if role == "admin" and also_mentor and not domain_id:
        raise AppError("Choose the domain this administrator will mentor in", 422, {"domain_id": "Domain is required"})
    existing_admin = find_admin_by_email(email) if role in ("mentor", "domain_lead") else None
    if existing_admin:                          # an administrator's email may also hold a mentor / lead profile
        user = existing_admin
        has_mentor = db.session.query(Mentor).filter_by(user_id=user.id).first() is not None
        if role == "mentor" and has_mentor:
            raise AppError("This administrator is already a mentor", 409)
        have = {l.domain_id for l in db.session.query(DomainLead).filter_by(user_id=user.id).all()}
        if role == "domain_lead" and all(d.id in have for d in domains):
            raise AppError("This administrator already leads the selected domain(s)", 409)
        domains = [d for d in domains if d.id not in have]
    else:
        has_mentor = False
        user = create_user(role, name, email, password, phone)
    if role == "admin" and also_mentor:         # an administrator who is also a mentor
        db.session.add(Mentor(user_id=user.id, domain_id=domain_id, expertise=expertise, **profile))
    for i, d in enumerate(domains):
        db.session.add(DomainLead(user_id=user.id, domain_id=d.id, is_primary=(i == 0 and not existing_admin)))
    if role == "mentor":
        db.session.add(Mentor(user_id=user.id, domain_id=domain_id, expertise=expertise, **profile))
    if role == "domain_lead" and not has_mentor:  # every domain lead is also a mentor of the domain(s) they lead
        db.session.add(Mentor(user_id=user.id, domain_id=domains[0].id, designation="Domain Lead", mentor_type="internal"))
    return user


def find_admin_by_email(email):
    user = db.session.query(User).filter(User.email == email.strip().lower()).first()
    return user if user and user.role_name == "admin" else None


def create_student(batch, name, email, phone, registration_no, extra_fields=None, password=None):
    """Create user + student profile. Returns (student, temp_password)."""
    return create_students([{"batch": batch, "name": name, "email": email, "phone": phone,
                             "registration_no": registration_no, "extra_fields": extra_fields, "password": password}])[0]


def create_students(rows):
    """Create many students with a handful of queries. Each row: batch, name, email, phone, registration_no and
    optionally extra_fields, password, password_hash. Returns [(student, temporary password)] in row order."""
    if not rows:
        return []
    role = db.session.query(Role).filter_by(name="student").first()
    if not role:
        raise AppError("Unknown role 'student'", 422)
    emails = [r["email"].strip().lower() for r in rows]
    taken_emails = {e.lower() for e in db.session.query(User.email).filter(User.email.in_(emails)).values_list()}
    given = [(r.get("registration_no") or "").strip() for r in rows]
    taken_regs = {(s.batch_id, s.registration_no.lower()) for s in db.session.query(Student.batch_id, Student.registration_no)
                  .filter(Student.batch_id.in_({r["batch"].id for r in rows}),
                          Student.registration_no.in_([g for g in given if g]))}
    highest, users, profiles = {}, [], []
    for row, email, registration_no in zip(rows, emails, given):
        batch = row["batch"]
        if not registration_no:  # left blank: the system issues <DOMAIN>-ST<NN> (e.g. ML-AI-ST01), numbered within the batch
            prefix = student_prefix(batch)
            if (batch.id, prefix) not in highest:
                existing = db.session.query(Student.registration_no).filter(Student.batch_id == batch.id).values_list()
                typed = [g for g in given if g]
                highest[(batch.id, prefix)] = max((int(v[len(prefix):]) for v in [*existing, *typed]
                                                   if v.upper().startswith(prefix) and v[len(prefix):].isdigit()), default=0)
            highest[(batch.id, prefix)] += 1
            registration_no = f"{prefix}{highest[(batch.id, prefix)]:02d}"
        if (batch.id, registration_no.lower()) in taken_regs:
            raise AppError(f"Registration number '{registration_no}' already exists in this batch", 409)
        taken_regs.add((batch.id, registration_no.lower()))
        if email in taken_emails:
            raise AppError("A user with this email already exists", 409)
        taken_emails.add(email)
        password = row.get("password") or generate_temp_password()
        validate_password_strength(password)
        user = User(role_id=role.id, name=row["name"].strip(), email=email, phone=row.get("phone"),
                    must_change_password=True)
        if row.get("password_hash"):
            user.password_hash = row["password_hash"]
        else:
            user.set_password(password)
        users.append(user)
        profiles.append((batch, registration_no, row.get("extra_fields") or None, password))
    db.session.add_all(users)
    db.session.flush()
    created = [(Student(user_id=u.id, batch_id=batch.id, registration_no=reg, extra_fields=extra), password)
               for u, (batch, reg, extra, password) in zip(users, profiles)]
    db.session.add_all([s for s, _ in created])
    db.session.flush()
    return created


def prehash_passwords(count):
    """[(temporary password, hash)] for a bulk enrolment, computed before its transaction starts: hashing is slow on
    purpose, and MongoDB aborts a transaction that stays open for more than 60 seconds."""
    out = []
    for _ in range(count):
        password = generate_temp_password()
        user = User()
        user.set_password(password)
        out.append((password, user.password_hash))
    return out


def validate_new_students(rows):
    """Check a list of {name,email,registration_no} for a not-yet-created batch; 422 listing every bad row."""
    from app.services.fields import EMAIL_RE
    existing = {e[0].lower() for e in db.session.query(User.email).all()}
    seen_email, seen_reg, errors = {}, {}, {}
    for i, r in enumerate(rows, 1):
        email = (r.get("email") or "").strip().lower()
        reg = (r.get("registration_no") or "").strip().lower()
        r["email"], r["name"] = email, (r.get("name") or "").strip()
        if len(r["name"]) < 2:
            errors[f"students.{i}.name"] = f"Student {i}: name is required"
        if not EMAIL_RE.match(email):
            errors[f"students.{i}.email"] = f"Student {i}: '{email}' is not a valid email"
        elif email in existing:
            errors[f"students.{i}.email"] = f"Student {i}: email '{email}' is already registered"
        elif email in seen_email:
            errors[f"students.{i}.email"] = f"Student {i}: duplicate email (same as student {seen_email[email]})"
        else:
            seen_email[email] = i
        if reg:
            if reg in seen_reg:
                errors[f"students.{i}.registration_no"] = f"Student {i}: duplicate registration number (same as student {seen_reg[reg]})"
            else:
                seen_reg[reg] = i
    if errors:
        raise AppError("; ".join(errors.values()), 422, errors)


def credential_row(student, temporary_password):
    """One shareable login record (never persisted; shown to the admin once)."""
    return {"name": student.user.name, "email": student.user.email, "phone": student.user.phone or "",
            "registration_no": student.registration_no, "batch_code": student.batch.code, "batch_name": student.batch.name,
            "temporary_password": temporary_password}


def issue_batch_credentials(batch, student_ids=None):
    """Fresh temporary passwords for students who have not logged in yet (must_change_password still set).

    Students who already chose their own password are skipped, so this can never lock out or override
    someone who is actively using their account. Returns (issued, skipped).
    """
    q = db.session.query(Student).filter_by(batch_id=batch.id)
    if student_ids:
        q = q.filter(Student.id.in_(student_ids))
    issued, skipped = [], []
    students = db.session.prefetch(q.order_by(Student.registration_no).all(), "user", "batch")
    passwords = iter(prehash_passwords(sum(1 for s in students if s.user.is_active and s.user.must_change_password)))
    for s in students:
        user = s.user
        if not user.is_active:
            skipped.append({"name": user.name, "registration_no": s.registration_no, "reason": "Account is deactivated"})
        elif not user.must_change_password:
            skipped.append({"name": user.name, "registration_no": s.registration_no, "reason": "Already set their own password"})
        else:
            password, password_hash = next(passwords)
            issued.append(credential_row(s, reset_user_password(user, password, password_hash)))
    return issued, skipped


def reset_user_password(user, new_password=None, password_hash=None):
    password = new_password or generate_temp_password()
    validate_password_strength(password)
    if password_hash:
        user.password_hash = password_hash
    else:
        user.set_password(password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    return password


def get_user_or_404(user_id):
    u = db.session.get(User, user_id)
    if not u:
        raise NotFound("User not found")
    return u


def ensure_batch(batch_id):
    b = db.session.get(Batch, batch_id)
    if not b:
        raise AppError("Batch not found", 422, {"batch_id": "Batch not found"})
    return b


# ------------------------------------------------------------- staff credentials
STAFF_ROLES = ("mentor", "domain_lead")


def staff_credential_row(user, temporary_password):
    """One shareable login record for a mentor or domain lead (never persisted)."""
    row = {"name": user.name, "email": user.email, "phone": user.phone or "", "role": user.role_name,
           "temporary_password": temporary_password, "designation": "", "company": "", "details": ""}
    mentor = db.session.query(Mentor).filter_by(user_id=user.id).first()
    if mentor:
        row["designation"] = mentor.designation or ""
        row["company"] = mentor.company or ""
        row["details"] = "Industry mentor" if mentor.mentor_type == "industry" else "Internal mentor (ICT trainer)"
    elif user.role_name == "domain_lead":
        names = [d.domain.name for d in db.session.query(DomainLead).filter_by(user_id=user.id).all()]
        row["details"] = ", ".join(names)
    return row


def issue_staff_credentials(role, user_ids=None, include_active=False):
    """Fresh temporary passwords for mentors / domain leads who have not logged in yet.

    Like the student version, anyone who already chose their own password (or is deactivated) is skipped so an
    active account is never locked out by a bulk download; use "Reset password" for those. Returns (issued, skipped).
    """
    if role not in STAFF_ROLES:
        raise AppError("Credentials can be issued for mentors and domain leads here", 422)
    from app.services.domain_sync_service import users_with_role
    q = users_with_role(role)
    if user_ids:
        q = q.filter(User.id.in_(user_ids))
    issued, skipped = [], []
    people = q.order_by(User.name).all()
    passwords = iter(prehash_passwords(sum(1 for u in people if u.is_active and (u.must_change_password or include_active))))
    for u in people:
        if not u.is_active:
            skipped.append({"name": u.name, "email": u.email, "reason": "Account is deactivated"})
        elif not u.must_change_password and not include_active:
            skipped.append({"name": u.name, "email": u.email, "reason": "Already set their own password"})
        else:  # includes people who chose their own password when include_active: their old password stops working
            password, password_hash = next(passwords)
            issued.append(staff_credential_row(u, reset_user_password(u, password, password_hash)))
    return issued, skipped


# ------------------------------------------------------------------- deletion
def delete_user(user, actor):
    """Permanently delete an account and its profile. Refuses when that would orphan real work.

    Returns a small description for the audit log. Raises AppError(409) with the reason otherwise.
    """
    if user.id == actor.id:
        raise AppError("You cannot delete your own account", 409)
    role = user.role_name
    if role == "admin":
        from app.services.domain_sync_service import users_with_role
        others = users_with_role("admin").filter(User.id != user.id, User.is_active.is_(True)).count()
        if others == 0:
            raise AppError("This is the last active administrator and cannot be deleted", 409)
    elif role == "student":
        s = db.session.query(Student).filter_by(user_id=user.id).first()
        if s and db.session.query(ProjectStudent).filter_by(student_id=s.id).first():
            raise AppError("This student has a project. Deactivate the account instead (deleting would damage the project's records).", 409)
        if s:
            _remove_student_profiles([s])
    elif role in ("mentor", "admin"):           # an admin may also hold a mentor profile
        m = db.session.query(Mentor).filter_by(user_id=user.id).first()
        if m:
            batches = db.session.query(BatchMentor).filter_by(mentor_id=m.id, is_active=True).count()
            overrides = db.session.query(Project.id).filter(db.or_(Project.mentor_id == m.id, Project.id.in_(
                db.select(ProjectMentor.project_id).where(ProjectMentor.mentor_id == m.id)))).count()
            if batches or overrides:
                raise AppError(f"This mentor is assigned to {batches} batch(es) and {overrides} project(s). "
                               "Reassign or remove them first, or deactivate the account.", 409)
            db.session.query(BatchMentor).filter_by(mentor_id=m.id).delete()
            db.session.delete(m)
    elif role == "domain_lead":
        m = db.session.query(Mentor).filter_by(user_id=user.id).first()       # the lead's own mentor profile goes with them
        if m:
            batches = db.session.query(BatchMentor).filter_by(mentor_id=m.id, is_active=True).count()
            overrides = db.session.query(Project.id).filter(db.or_(Project.mentor_id == m.id, Project.id.in_(
                db.select(ProjectMentor.project_id).where(ProjectMentor.mentor_id == m.id)))).count()
            if batches or overrides:
                raise AppError(f"This lead is also a mentor of {batches} batch(es) and {overrides} project(s). "
                               "Remove them as a mentor first, or deactivate the account.", 409)
            db.session.query(BatchMentor).filter_by(mentor_id=m.id).delete()
            db.session.delete(m)
        for link in db.session.query(DomainLead).filter_by(user_id=user.id).all():
            others = db.session.query(DomainLead).filter(DomainLead.domain_id == link.domain_id, DomainLead.user_id != user.id).count()
            if others == 0 and db.session.query(Batch).filter_by(domain_id=link.domain_id).count():
                raise AppError(f"This is the only lead of the domain '{link.domain.name}', which has batches. "
                               "Assign another lead first, or deactivate the account.", 409)
        db.session.query(DomainLead).filter_by(user_id=user.id).delete()
    db.session.query(Notification).filter_by(user_id=user.id).delete()
    db.session.query(PasswordResetToken).filter_by(user_id=user.id).delete()
    info = {"email": user.email, "role": role, "name": user.name}
    db.session.delete(user)
    db.session.flush()
    return info


def _remove_student_profiles(students):
    """Delete student profiles that have no project, with their proposals and any file nothing else uses."""
    from app.models import ProblemStatement
    from app.services import file_service
    ids = [s.id for s in students]
    db.session.query(ProblemStatement).filter(ProblemStatement.proposed_by_student_id.in_(ids)).update(
        {"proposed_by_student_id": None})
    for p in db.session.query(StudentProposal).filter(StudentProposal.student_id.in_(ids)).all():
        files = [p.file] + [d.file for d in p.datasets]
        db.session.delete(p)
        db.session.flush()
        for f in files:
            file_service.delete_file_if_unused(f)
    db.session.query(Student).filter(Student.id.in_(ids)).delete()


def delete_students(students):
    """Delete the accounts of many students who have no project (a batch being removed), in bulk."""
    if db.session.query(ProjectStudent).filter(ProjectStudent.student_id.in_([s.id for s in students])).first():
        raise AppError("A student has a project. Deactivate the account instead (deleting would damage the project's records).", 409)
    user_ids = [s.user_id for s in students]
    _remove_student_profiles(students)
    db.session.query(Notification).filter(Notification.user_id.in_(user_ids)).delete()
    db.session.query(PasswordResetToken).filter(PasswordResetToken.user_id.in_(user_ids)).delete()
    db.session.query(User).filter(User.id.in_(user_ids)).delete()


def set_lead_domains(user, domain_ids):
    """Replace the domains a domain lead leads. A lead always keeps at least one."""
    from app.models import Domain
    if user.role_name != "domain_lead":
        raise AppError("Only domain leads have domains", 422)
    ids = list(dict.fromkeys(domain_ids or []))
    if not ids:
        raise AppError("Choose at least one domain", 422, {"domain_ids": "Select at least one domain"})
    domains = [db.session.get(Domain, i) for i in ids]
    if any(d is None for d in domains):
        raise AppError("Domain not found", 422, {"domain_ids": "Domain not found"})
    current = {l.domain_id: l for l in db.session.query(DomainLead).filter_by(user_id=user.id).all()}
    for did, link in current.items():
        if did not in ids:
            db.session.delete(link)
    for i, d in enumerate(domains):
        if d.id not in current:
            db.session.add(DomainLead(user_id=user.id, domain_id=d.id, is_primary=(i == 0 and not current)))
    db.session.flush()
    mentor = db.session.query(Mentor).filter_by(user_id=user.id).first()
    if mentor is None:
        mentor = Mentor(user_id=user.id, domain_id=domains[0].id, designation="Domain Lead", mentor_type="internal")
        db.session.add(mentor)
        db.session.flush()
    elif mentor.domain_id not in ids:
        mentor.domain_id = domains[0].id
    from app.services import domain_sync_service
    domain_sync_service.auto_assign_mentor_to_batches(mentor, user)
    from app.services.notification_service import notify
    notify({user.id}, "domains_changed", "Your domains were updated",
           "You now lead: " + ", ".join(d.name for d in domains) + ".", "/lead")
    return domains
