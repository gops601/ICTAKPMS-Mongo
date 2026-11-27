"""Every mentor of a project and its Domain Lead evaluate on the review date; the score is their average; weeks open in order."""
from datetime import date, timedelta

from app.extensions import db
from app.models import (BatchMentor, DomainLead, EvaluationTemplate, Mentor, Notification, ProjectMentor, ProjectReview, User)
from tests.test_reviews import CRIT, domain, full, put, rid, submit, world  # noqa: F401


def scaled(factor):
    """CRIT with every score multiplied (CRIT scores 84/100)."""
    return [{**c, "marks_obtained": round(c["marks_obtained"] * factor, 2)} for c in CRIT]


def add_evaluators(world):                                                         # noqa: F811
    """m2 joins the project's mentors; the AIML Domain Lead becomes a mentor of the batch too."""
    m2 = db.session.query(Mentor).filter(Mentor.user_id.in_(db.select(User.id).where(User.email == "m2@x.com"))).one()
    db.session.add_all([ProjectMentor(project_id=world["main"].id, mentor_id=m2.id),
                        ProjectMentor(project_id=world["main"].id, mentor_id=world["m1"].id)])      # explicit mentors replace the batch's
    la = db.session.query(User).filter_by(email="la@x.com").one()
    lead_m = Mentor(user_id=la.id, domain_id=domain("AIML").id, designation="Domain Lead")
    db.session.add(lead_m)
    db.session.flush()
    db.session.add(ProjectMentor(project_id=world["main"].id, mentor_id=lead_m.id))
    db.session.commit()


def test_overall_score_is_the_average_of_all_evaluators(client, world):            # noqa: F811
    add_evaluators(world)
    rv = rid(world["main"], 1)
    assert submit(client, world, "m1", rv, **full(scaled(1.0))).status_code == 200            # 84 %
    d = client.get(f"/api/reviews/{rv}", headers=world["H"]["m2"]).get_json()["data"]
    assert d["status"] == "pending" and d["permissions"]["can_edit"] is True                  # m2 still has their own evaluation to do
    assert submit(client, world, "m2", rv, **full(scaled(0.5))).status_code == 200            # 42 %
    assert submit(client, world, "la", rv, **full(scaled(0.75))).status_code == 200           # 63 %
    overall = client.get(f"/api/reviews/{rv}", headers=world["H"]["admin"]).get_json()["data"]
    assert overall["summary"]["percentage"] == 63.0 and overall["status"] == "submitted"        # (84 + 42 + 63) / 3
    assert sorted(e["percentage"] for e in overall["evaluations"]) == [42.0, 63.0, 84.0]
    # each evaluator keeps their own evaluation
    mine = client.get(f"/api/reviews/{rv}", headers=world["H"]["m2"]).get_json()["data"]
    assert mine["summary"]["percentage"] == 42.0 and mine["overall"]["percentage"] == 63.0
    # the project's overall performance uses the average too
    perf = client.get(f"/api/reviews/projects/{world['main'].id}", headers=world["H"]["admin"]).get_json()["data"]["overall"]
    assert perf["reviews"][0]["percentage"] == 63.0


def test_a_missing_evaluator_is_ignored_in_the_average(client, world):             # noqa: F811
    add_evaluators(world)
    rv = rid(world["main"], 1)
    submit(client, world, "m1", rv, **full(scaled(1.0)))
    assert db.session.get(ProjectReview, rv).status == "submitted"                          # one submission completes the week
    assert client.get(f"/api/reviews/{rv}", headers=world["H"]["admin"]).get_json()["data"]["summary"]["percentage"] == 84.0
    submit(client, world, "m2", rv, **full(scaled(0.5)))
    assert client.get(f"/api/reviews/{rv}", headers=world["H"]["admin"]).get_json()["data"]["summary"]["percentage"] == 63.0


def test_one_evaluator_reopening_only_removes_their_own_marks(client, world):      # noqa: F811
    add_evaluators(world)
    rv = rid(world["main"], 1)
    submit(client, world, "m1", rv, **full(scaled(1.0)))
    submit(client, world, "m2", rv, **full(scaled(0.5)))
    r = client.post(f"/api/reviews/{rv}/reopen", headers=world["H"]["m2"], json={"reason": "Correcting my marks"})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "reopened"
    d = client.get(f"/api/reviews/{rv}", headers=world["H"]["admin"]).get_json()["data"]
    assert d["status"] == "submitted" and d["summary"]["percentage"] == 84.0                  # m1's evaluation still stands


def test_weeks_open_one_after_another(client, world):                               # noqa: F811
    main = world["main"]
    r = submit(client, world, "m1", rid(main, 2), **full())
    assert r.status_code == 409 and "Week 1 must be evaluated" in r.get_json()["message"]
    d = client.get(f"/api/reviews/{rid(main, 2)}", headers=world["H"]["m1"]).get_json()["data"]
    assert d["permissions"]["can_edit"] is False and "Week 1" in d["permissions"]["reason"]
    assert submit(client, world, "m1", rid(main, 1), **full()).status_code == 200
    assert client.get(f"/api/reviews/{rid(main, 2)}", headers=world["H"]["m1"]).get_json()["data"]["permissions"]["can_edit"] is True
    r = submit(client, world, "m1", rid(main, 3), **full())
    assert r.status_code == 409 and "Week 2 must be evaluated" in r.get_json()["message"]
    assert submit(client, world, "m1", rid(main, 2), **full()).status_code == 200
    assert submit(client, world, "m1", rid(main, 3), **full()).status_code == 200


def test_dates_can_be_planned_for_every_week_before_the_week_opens(client, world):  # noqa: F811
    assert put(client, world, "m1", rid(world["main"], 3), review_date=(date.today() + timedelta(days=14)).isoformat()).status_code == 200


def test_evaluation_opens_on_the_review_date(client, world):                        # noqa: F811
    rv = rid(world["main"], 1)
    put(client, world, "m1", rv, review_date=(date.today() + timedelta(days=3)).isoformat())
    r = submit(client, world, "m1", rv, **full())
    assert r.status_code == 409 and "Evaluation opens on that date" in r.get_json()["message"]
    assert put(client, world, "m1", rv, remarks="early").status_code == 409
    d = client.get(f"/api/reviews/{rv}", headers=world["H"]["m1"]).get_json()["data"]
    assert d["permissions"]["can_edit"] is False and d["permissions"]["can_schedule"] is True
    db.session.get(ProjectReview, rv).review_date = date.today()
    db.session.commit()
    assert submit(client, world, "m1", rv, **full()).status_code == 200


def test_domain_lead_and_mentors_share_criteria_templates(client, world):           # noqa: F811
    add_evaluators(world)
    m2 = db.session.query(Mentor).filter(Mentor.user_id.in_(db.select(User.id).where(User.email == "m2@x.com"))).one()
    m2.domain_id = domain("AIML").id
    db.session.commit()
    body = {"name": "Shared", "week_no": 1, "criteria": [{"name": "Design", "max_marks": 40}, {"name": "Code", "max_marks": 60}]}
    t = client.post("/api/evaluation-criteria/templates", headers=world["H"]["la"], json=body)
    assert t.status_code == 201
    seen = client.get("/api/evaluation-criteria/templates", headers=world["H"]["m2"]).get_json()["data"]
    assert [x["name"] for x in seen] == ["Shared"] and seen[0]["is_mine"] is False
    rv = rid(world["main"], 1)
    r = client.post(f"/api/reviews/{rv}/apply-template", headers=world["H"]["m2"], json={"template_id": t.get_json()["data"]["id"]})
    assert r.status_code == 200 and [c["name"] for c in r.get_json()["data"]["criteria"]] == ["Design", "Code"]
    # only the owner edits it
    tid = t.get_json()["data"]["id"]
    assert client.delete(f"/api/evaluation-criteria/templates/{tid}", headers=world["H"]["m2"]).status_code == 404


def test_clear_history_removes_only_the_callers_notifications(client, world):       # noqa: F811
    db.session.add_all([Notification(user_id=world["m1u"].id, type="x", title="a"), Notification(user_id=world["m1u"].id, type="x", title="b"),
                        Notification(user_id=world["s1u"].id, type="x", title="c")])
    db.session.commit()
    r = client.delete("/api/notifications", headers=world["H"]["m1"])
    assert r.status_code == 200 and r.get_json()["data"]["deleted"] == 2
    assert client.get("/api/notifications", headers=world["H"]["m1"]).get_json()["data"] == []
    assert len(client.get("/api/notifications", headers=world["H"]["s1"]).get_json()["data"]) == 1
    assert client.delete("/api/notifications").status_code == 401
