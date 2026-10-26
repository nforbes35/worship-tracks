from fastapi.testclient import TestClient
from worship_tracks.app import create_app


def client(tmp_path, passcode=""):
    return TestClient(create_app(data_dir=tmp_path, soundfont=tmp_path / "x.sf2", passcode=passcode, start_worker=False))


def test_open_when_no_passcode(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/session").json() == {"passcodeRequired": False, "signedIn": True}
    assert c.get("/api/songs").json() == []
    assert "Worship Tracks" in c.get("/").text


def test_passcode_required(tmp_path):
    c = client(tmp_path, passcode="amazing grace")
    assert c.get("/api/songs").status_code == 401
    assert c.get("/").status_code == 200  # the page itself loads, to show the passcode screen
    assert c.post("/api/login", json={"passcode": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"passcode": " amazing grace "}).status_code == 200
    assert c.get("/api/songs").status_code == 200
    assert c.get("/api/session").json()["signedIn"] is True


def test_changing_passcode_signs_everyone_out(tmp_path):
    c = client(tmp_path, passcode="one")
    c.post("/api/login", json={"passcode": "one"})
    token = c.cookies.get("wt_session")
    c2 = client(tmp_path, passcode="two")
    c2.cookies.set("wt_session", token)
    assert c2.get("/api/songs").status_code == 401


def test_rejects_bad_ids(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/songs/../../etc").status_code == 404
    assert c.get("/api/songs/zzzzzzzzzzzz").status_code == 404
    assert c.delete("/api/songs/0123456789ab").status_code == 404
