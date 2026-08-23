"""API surface: FHIR endpoints, incidents, recovery, baselines, review, audit."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aegis_care.api.app import app, state


@pytest.fixture
def client():
    client = TestClient(app)
    client.post("/api/system/reset")
    return client


@pytest.fixture
def incident(client):
    response = client.post("/api/incidents", json={
        "family": "F1", "depth": 4, "provenance": "targeted", "n_controls": 1})
    assert response.status_code == 200, response.text
    return response.json()


class TestSystem:
    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"

    def test_system_description(self, client):
        body = client.get("/api/system").json()
        assert body["stats"]["fhir"]["Patient"] == 100
        assert len(body["tasks"]) == 24
        assert set(body["families"]) == {"F1", "F2", "F3", "F4"}
        assert len(body["conditions"]) == 9

    def test_coordinator_has_no_field_rights(self, client):
        body = client.get("/api/system").json()
        assert body["roles"]["coordinator"]["fields"] == []

    def test_dashboard_serves(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "AEGIS" in response.text

    def test_static_assets_serve(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200


class TestFHIREndpoints:
    def test_read_patient(self, client):
        body = client.get("/fhir/Patient/S1000").json()
        assert body["resourceType"] == "Patient"

    def test_missing_resource_404s(self, client):
        assert client.get("/fhir/Patient/NOPE").status_code == 404

    def test_search_returns_bundle(self, client):
        body = client.get("/fhir/Observation", params={"patient": "S1000"}).json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] > 0

    def test_search_by_identifier(self, client):
        mrn = client.get("/fhir/Patient/S1005").json()["identifier"][0]["value"]
        body = client.get("/fhir/Patient", params={"identifier": mrn}).json()
        assert body["total"] == 1


class TestIncidents:
    def test_create(self, incident):
        assert incident["family"] == "F1"
        assert incident["true_contaminated"]
        assert incident["provenance"]["edges_removed"] > 0

    def test_detail_includes_trajectory_and_controls(self, client, incident):
        body = client.get(f"/api/incidents/{incident['incident_id']}").json()
        assert len(body["trajectory"]) == 5
        assert body["controls"]
        assert any(n["contaminated"] for n in body["trajectory"])

    def test_unknown_family_rejected(self, client):
        response = client.post("/api/incidents", json={"family": "F9"})
        assert response.status_code == 400
        assert "unknown family" in response.json()["detail"]

    def test_depth_shallower_than_seed_rejected(self, client):
        response = client.post("/api/incidents",
                               json={"family": "F2", "depth": 1, "provenance": "complete"})
        assert response.status_code == 400
        assert "depth" in response.json()["detail"]

    def test_list(self, client, incident):
        body = client.get("/api/incidents").json()
        assert any(i["incident_id"] == incident["incident_id"] for i in body["incidents"])


class TestRecovery:
    def test_full_care(self, client, incident):
        body = client.post("/api/recover",
                           json={"incident_id": incident["incident_id"]}).json()
        assert body["closure_reached"]
        assert body["repaired"]
        assert body["metrics"]["descendant_recall"] == 1.0
        assert body["metrics"]["bsr"] == 1.0
        assert body["metrics"]["rwh"] == 0.0
        assert "AEGIS-CARE RECOVERY CERTIFICATE" in body["certificate_text"]

    def test_capsules_expose_no_content(self, client, incident):
        body = client.post("/api/recover",
                           json={"incident_id": incident["incident_id"]}).json()
        for capsule in body["capsules"]:
            assert "content" not in capsule
            assert "sketch" not in capsule    # only the dimension is reported

    def test_verdicts_carry_bands_not_text(self, client, incident):
        body = client.post("/api/recover",
                           json={"incident_id": incident["incident_id"]}).json()
        assert body["verdicts"]
        for verdict in body["verdicts"]:
            assert verdict["influence_band"] in ("none", "low", "medium", "high")
            assert "content" not in verdict

    def test_ablation_changes_outcome(self, client, incident):
        """Disabling latent discovery under a targeted mask must hurt recall."""
        full = client.post("/api/recover",
                           json={"incident_id": incident["incident_id"]}).json()
        client.post("/api/system/reset")
        again = client.post("/api/incidents", json={
            "family": "F1", "depth": 4, "provenance": "targeted", "n_controls": 1}).json()
        ablated = client.post("/api/recover", json={
            "incident_id": again["incident_id"], "use_sketch": False}).json()
        assert len(ablated["confirmed"]) < len(full["confirmed"])

    def test_certificate_endpoint(self, client, incident):
        client.post("/api/recover", json={"incident_id": incident["incident_id"]})
        text = client.get(
            f"/api/recover/{incident['incident_id']}/certificate").text
        assert "SAFE RESUME" in text

    def test_unknown_incident_404s(self, client):
        assert client.post("/api/recover",
                           json={"incident_id": "nope"}).status_code == 404


class TestBaselinesEndpoint:
    def test_all_conditions(self, client, incident):
        body = client.post("/api/baselines",
                           json={"incident_id": incident["incident_id"]}).json()
        results = {r["condition"]: r for r in body["results"] if "condition" in r}
        assert len(results) == 9
        assert results["I"]["rwh"] <= results["A"]["rwh"]
        assert results["I"]["bsr"] > results["C"]["bsr"]


class TestMemoryEndpoints:
    def test_public_view_excludes_content(self, client, incident):
        body = client.get("/api/memory", params={"include_content": False}).json()
        assert body["count"] > 0
        assert all("content" not in m for m in body["memories"])

    def test_local_view_includes_content(self, client, incident):
        body = client.get("/api/memory").json()
        assert any(m.get("content") for m in body["memories"])

    def test_graph_marks_masked_edges(self, client, incident):
        body = client.get("/api/memory/any/graph").json()
        assert body["nodes"]
        assert any(not e["observed"] for e in body["edges"]), (
            "targeted masking should leave ground-truth-only edges in the graph")


class TestReviewAndAudit:
    def test_review_queue_reachable(self, client, incident):
        assert "count" in client.get("/api/review/queue").json()

    def test_review_decision_applies(self, client, incident):
        client.post("/api/recover", json={"incident_id": incident["incident_id"]})
        queue = client.get("/api/review/queue").json()
        if not queue["count"]:
            pytest.skip("no artifact required escalation in this incident")
        key = f"{queue['items'][0]['memory_id']}@v{queue['items'][0]['version']}"
        body = client.post("/api/review",
                           json={"memory_key": key, "decision": "reject"}).json()
        assert body["state"] == "tombstoned"

    def test_events_recorded(self, client, incident):
        client.post("/api/recover", json={"incident_id": incident["incident_id"]})
        kinds = {e["kind"] for e in client.get("/api/events").json()["events"]}
        assert "recovery_started" in kinds

    def test_privacy_audit_endpoint(self, client, incident):
        client.post("/api/recover", json={"incident_id": incident["incident_id"]})
        body = client.get(f"/api/privacy/{incident['incident_id']}").json()
        assert body["released_fields"]["raw_content_exported"] is False
        assert "membership" in body


class TestExperimentEndpoint:
    def test_run_small_experiment(self, client):
        body = client.post("/api/experiment", json={
            "families": ["F1"], "depths": [4],
            "provenance_conditions": ["complete"], "tasks_per_family": 1}).json()
        assert body["status"] == "complete"
        assert body["by_condition"]

    def test_report_available_after_run(self, client):
        client.post("/api/experiment", json={
            "families": ["F1"], "depths": [4],
            "provenance_conditions": ["complete"], "tasks_per_family": 1})
        text = client.get("/api/experiment/report").text
        assert "AEGIS-Care experimental report" in text
