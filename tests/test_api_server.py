import pandas as pd
from fastapi.testclient import TestClient

from objaverse import api


def test_health_and_sources():
    client = TestClient(api.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    sources = client.get("/v1/sources")
    assert sources.status_code == 200
    assert set(sources.json()["sources"]) == {
        "github",
        "thingiverse",
        "smithsonian",
        "sketchfab",
    }


def test_annotations_filtering(monkeypatch):
    sample = pd.DataFrame(
        [
            {
                "fileIdentifier": "one",
                "source": "github",
                "license": "cc-by",
                "fileType": "glb",
                "sha256": "hash-one",
                "metadata": "{}",
            },
            {
                "fileIdentifier": "two",
                "source": "sketchfab",
                "license": "cc0",
                "fileType": "obj",
                "sha256": "hash-two",
                "metadata": "{}",
            },
        ]
    )
    monkeypatch.setattr(api, "_load_annotations", lambda download_dir, refresh: sample)
    client = TestClient(api.app)

    response = client.get("/v1/annotations?source=github&file_type=glb&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_matching"] == 1
    assert payload["records"][0]["fileIdentifier"] == "one"


def test_download_job(monkeypatch, tmp_path):
    sample = pd.DataFrame(
        [
            {
                "fileIdentifier": "one",
                "source": "github",
                "license": "cc-by",
                "fileType": "glb",
                "sha256": "hash-one",
                "metadata": "{}",
            }
        ]
    )
    monkeypatch.setattr(api, "_load_annotations", lambda download_dir, refresh: sample)
    monkeypatch.setattr(api.oxl, "download_objects", lambda **kwargs: {"one": str(tmp_path / "one.glb")})
    client = TestClient(api.app)

    response = client.post(
        "/v1/downloads",
        json={"source": "github", "limit": 1, "download_dir": str(tmp_path), "processes": 1},
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = client.get(f"/v1/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert job.json()["result"] == {"one": str(tmp_path / "one.glb")}
