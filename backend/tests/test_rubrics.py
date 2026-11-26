from app.extensions import db
from app.models import Batch, Domain, Mentor, OrganisationalUnit, Student
from app.services import rubric_data
from app.services.org_service import set_domain_leads
from app.services.rubric_service import detect_track


def tracks(client, headers):
    r = client.get("/api/rubrics", headers=headers)
    assert r.status_code == 200, r.get_json()
    return [t["key"] for t in r.get_json()["data"]["tracks"]]


def domain(code):
    return db.session.query(Domain).filter_by(code=code).first()


def test_every_rubric_adds_up_to_100_and_every_band_is_filled(app):
    assert sum(c["marks"] for c in rubric_data.COMMON_CRITERIA) == 40
    for track in rubric_data.TRACKS.values():
        assert sum(c["marks"] for c in track["criteria"]) == 60, track["name"]
        assert all(len(c["levels"]) == len(rubric_data.LEVELS) for c in track["criteria"])


def test_track_is_recognised_from_name_or_code(app):
    assert detect_track("AI & ML") == "aiml" and detect_track("Cyber Security") == "cyber"
    assert detect_track("Full-Stack Development") == "fsd" and detect_track("Something", "DS") == "ds"
    assert detect_track("Digital Marketing", "DIGMKT") is None


def test_admin_sees_all_tracks(client, make_user, login):
    make_user("admin")
    h = login("admin@test.local")
    assert tracks(client, h) == ["fsd", "ds", "aiml", "cyber"]
    data = client.get("/api/rubrics", headers=h).get_json()["data"]
    assert "IoT" in [d["name"] for d in data["unmapped_domains"]]


def test_lead_mentor_and_student_see_only_their_domain(client, make_user, login):
    lead, mentor, student = make_user("domain_lead"), make_user("mentor"), make_user("student")
    set_domain_leads(domain("AIML"), [lead.id])
    db.session.add(Mentor(user_id=mentor.id, domain_id=domain("CYBER").id))
    batch = Batch(code="B1", name="B1", ou_id=db.session.query(OrganisationalUnit).first().id, domain_id=domain("CYBER").id)
    db.session.add(batch)
    db.session.flush()
    db.session.add(Student(user_id=student.id, batch_id=batch.id, registration_no="R1"))
    db.session.commit()

    assert tracks(client, login("domain_lead@test.local")) == ["aiml"]
    assert tracks(client, login("mentor@test.local")) == ["cyber"]
    assert tracks(client, login("student@test.local")) == ["cyber"]


def test_admin_chooses_a_domains_rubric(client, make_user, login):
    make_user("admin")
    lead = make_user("domain_lead")
    d = domain("ENTERPRISE")
    set_domain_leads(d, [lead.id])
    db.session.commit()
    admin, lead_h = login("admin@test.local"), login("domain_lead@test.local")
    assert tracks(client, lead_h) == []

    r = client.put(f"/api/domains/{d.id}", headers=admin, json={"rubric_track": "fsd"})
    assert r.get_json()["data"]["effective_rubric_track"] == "fsd"
    assert tracks(client, lead_h) == ["fsd"]

    client.put(f"/api/domains/{d.id}", headers=admin, json={"description": "unrelated edit"})
    assert tracks(client, lead_h) == ["fsd"]
    client.put(f"/api/domains/{d.id}", headers=admin, json={"rubric_track": None})
    assert tracks(client, lead_h) == []
    assert client.put(f"/api/domains/{d.id}", headers=admin, json={"rubric_track": "nope"}).status_code == 422


def test_rubrics_need_a_login(client):
    assert client.get("/api/rubrics").status_code == 401
