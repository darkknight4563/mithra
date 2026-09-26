"""Island Lab static route: page, film and redirect."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_lab_redirects_to_trailing_slash():
    """Bare /lab redirects so relative assets resolve."""
    r = client.get("/lab", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/lab/"


def test_lab_page_served():
    """The lab page is served as HTML with its title."""
    r = client.get("/lab/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<title>Mithra Island Lab</title>" in r.text


def test_lab_film_supports_range_requests():
    """The MP4 answers range requests, which Safari needs for playback."""
    r = client.get("/lab/mithra-film.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-type"] == "video/mp4"
    assert len(r.content) == 100


def test_dashboard_still_at_root():
    """Mounting the lab leaves the main dashboard and health untouched."""
    assert client.get("/").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
