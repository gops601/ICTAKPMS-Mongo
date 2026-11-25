"""Links added in place of uploaded files: SRS, student proposals and Project Shelf attachments."""
import io

import pytest

from app.extensions import db
from app.models import FileAttachment
from app.services.file_service import clean_url
from app.utils.errors import AppError
from tests.test_industry_proposals import PDF, world  # noqa: F401
from tests.test_portals_datasheet import sheet as filled_sheet

DOC = "https://drive.google.com/file/d/abc123/view?usp=sharing"


@pytest.mark.parametrize("url", ["", "drive.google.com/x", "javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd",
                                 "ftp://example.com/a", "https://", "https://nodot", "https://user:pw@example.com/a",
                                 "https://exa mple.com", 'https://example.com/"<x>', "https://example.com/" + "a" * 2000])
def test_unsafe_or_incomplete_links_are_refused(url):
    with pytest.raises(AppError):
        clean_url(url)


def test_a_normal_link_is_kept_as_typed():
    assert clean_url(f"  {DOC} ") == DOC


def send(client, world, data):          # noqa: F811
    return client.post("/api/proposals", headers=world["H"]["s1"], content_type="multipart/form-data", data=data)



def test_proposal_by_link_with_dataset_links(client, world):     # noqa: F811
    data = {"title": "Linked", "url": DOC, "dataset_links": ["https://example.com/data/train.csv", "https://kaggle.com/d/x"]}
    r = send(client, world, data)
    assert r.status_code == 422 and "data_sheet" in r.get_json()["errors"]        # datasets still need describing
    r = send(client, world, {**data, "data_sheet": (filled_sheet(), "sheet.xlsx")})
    assert r.status_code == 201, r.get_json()
    p = r.get_json()["data"]
    assert p["file"]["url"] == DOC and p["file"]["name"] == "Link (drive.google.com)" and p["file"]["size_bytes"] == 0
    assert [(d["name"], bool(d["url"])) for d in p["datasets"]] == [
        ("train.csv", True), ("Link (kaggle.com)", True), ("sheet.xlsx", False)]
    # there is nothing to download for a link
    assert client.get(f"/api/proposals/{p['id']}/download", headers=world["H"]["s1"]).status_code == 409
    shelf = client.get(f"/api/problem-statements/{p['shelf']['id']}", headers=world["H"]["lead"]).get_json()["data"]
    assert shelf["documents"][0]["url"] == DOC


def test_proposal_needs_exactly_one_of_file_or_link(client, world):   # noqa: F811
    assert send(client, world, {"title": "t"}).status_code == 422
    r = send(client, world, {"title": "t", "url": DOC, "file": (io.BytesIO(PDF), "a.pdf")})
    assert r.status_code == 422 and "url" in r.get_json()["errors"]
    r = send(client, world, {"title": "t", "url": "javascript:alert(1)"})
    assert r.status_code == 422 and db.session.query(FileAttachment).count() == 0
