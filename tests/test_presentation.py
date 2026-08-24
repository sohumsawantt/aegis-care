"""The guided presentation: every act, in order, driving the real system.

These tests double as a rehearsal check. If they pass, the live demonstration
will produce the narrative beats it is supposed to produce.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aegis_care.api.app import app
from aegis_care.api.demo import ACTS


@pytest.fixture
def client():
    client = TestClient(app)
    client.post("/api/demo/configure",
                json={"family": "F1", "depth": 4, "provenance": "targeted"})
    return client


class TestPresentationShell:
    def test_presentation_is_the_root_page(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Live Demonstration" in response.text
        assert "presentation.js" in response.text

    def test_analyst_console_still_available(self, client):
        response = client.get("/console")
        assert response.status_code == 200
        assert "Incident Lab" in response.text

    def test_presentation_assets_serve(self, client):
        for asset in ("presentation.js", "presentation.css"):
            assert client.get(f"/static/{asset}").status_code == 200

    def test_acts_are_ordered_and_unique(self):
        ids = [a["id"] for a in ACTS]
        assert len(ids) == len(set(ids))
        assert ids[0] == "intro" and ids[-1] == "close"

    def test_state_reports_acts(self, client):
        body = client.get("/api/demo/state").json()
        assert len(body["acts"]) == len(ACTS)
        assert body["settings"]["family"] == "F1"


class TestConfiguration:
    def test_configure_pins_the_scenario(self, client):
        body = client.post("/api/demo/configure", json={
            "family": "F3", "depth": 4, "provenance": "random40"}).json()
        assert body["settings"]["family"] == "F3"
        assert body["settings"]["provenance"] == "random40"

    def test_unknown_family_rejected(self, client):
        assert client.post("/api/demo/configure",
                           json={"family": "F9"}).status_code == 400

    def test_depth_below_seed_rejected(self, client):
        response = client.post("/api/demo/configure",
                               json={"family": "F2", "depth": 1})
        assert response.status_code == 400
        assert "depth" in response.json()["detail"]

    def test_configure_clears_previous_run(self, client):
        client.post("/api/demo/poison", json={})
        client.post("/api/demo/configure", json={"family": "F1", "depth": 4})
        state = client.get("/api/demo/state").json()
        assert state["completed"] == []
        assert state["has_incident"] is False

    def test_acts_before_poisoning_are_refused(self, client):
        client.post("/api/demo/configure", json={"family": "F1", "depth": 4})
        for path in ("/api/demo/condition/B", "/api/demo/care", "/api/demo/compare"):
            assert client.post(path, json={}).status_code == 400


class TestNarrativeArc:
    """The demonstration only works if each act lands its point."""

    def test_act_system_reports_coordinator_has_no_rights(self, client):
        body = client.post("/api/demo/system", json={}).json()
        coordinator = next(r for r in body["roles"] if r["role"] == "coordinator")
        assert coordinator["authorized"] == []
        assert body["fhir"]["Patient"] == 100

    def test_act_clean_selects_the_right_patient(self, client):
        body = client.post("/api/demo/clean", json={}).json()
        assert body["probe"]["correct"] is True
        assert len(body["chain"]) == 5
        assert all(not n["wrong_patient"] for n in body["chain"])

    def test_act_poison_contaminates_every_hop(self, client):
        body = client.post("/api/demo/poison", json={}).json()
        assert body["probe"]["correct"] is False, "the poisoning must actually break the task"
        assert len(body["true_contaminated"]) == 4
        assert all(n["wrong_patient"] for n in body["chain"])
        assert body["provenance"]["edges_removed"] > 0
        assert body["controls"], "a matched clean control is required"

    def test_act_deletion_is_the_motivating_failure(self, client):
        """Baseline B must leave the system broken - that is the whole point."""
        client.post("/api/demo/poison", json={})
        body = client.post("/api/demo/condition/B", json={}).json()
        assert body["metrics"]["descendant_recall"] == 0.0
        assert body["probe"]["correct"] is False
        assert body["metrics"]["rwh"] > 0

    def test_act_reset_destroys_clean_state(self, client):
        client.post("/api/demo/poison", json={})
        body = client.post("/api/demo/condition/C", json={}).json()
        assert body["metrics"]["descendant_recall"] == 1.0
        assert body["metrics"]["bsr"] == 0.0

    def test_act_care_recovers_cleanly(self, client):
        client.post("/api/demo/poison", json={})
        body = client.post("/api/demo/care", json={}).json()
        m = body["metrics"]
        assert m["descendant_recall"] == 1.0
        assert m["descendant_precision"] == 1.0
        assert m["bsr"] == 1.0
        assert m["rwh"] == 0.0
        assert m["drr"] == 0.0
        assert body["probe"]["correct"] is True
        assert body["closure_reached"] is True

    def test_care_exposes_four_stages_in_order(self, client):
        client.post("/api/demo/poison", json={})
        stages = client.post("/api/demo/care", json={}).json()["stages"]
        assert list(stages) == ["C", "A", "R", "E"]
        for letter, stage in stages.items():
            assert stage["name"] and stage["explain"]
            assert stage["headline"] is not None

    def test_care_marks_which_candidates_were_true_descendants(self, client):
        client.post("/api/demo/poison", json={})
        detail = client.post("/api/demo/care", json={}).json()["stages"]["C"]["detail"]
        assert detail
        assert any(c["is_true_descendant"] for c in detail)

    def test_act_compare_runs_all_nine(self, client):
        client.post("/api/demo/poison", json={})
        results = client.post("/api/demo/compare", json={}).json()["results"]
        by_id = {r["condition"]: r for r in results if "condition" in r}
        assert len(by_id) == 9
        assert by_id["I"]["rwh"] <= by_id["B"]["rwh"]
        assert by_id["I"]["bsr"] > by_id["C"]["bsr"]
        assert by_id["I"]["uer"] < by_id["G"]["uer"]

    def test_act_privacy_reports_the_scoping_ablation(self, client):
        client.post("/api/demo/poison", json={})
        client.post("/api/demo/care", json={})
        body = client.post("/api/demo/privacy", json={}).json()
        assert body["released_fields"]["raw_content_exported"] is False
        assert body["linkability"]["detail"]["unscoped_ablation_accuracy"] > \
            body["linkability"]["accuracy"]


class TestPresentationPrivacy:
    def test_capsule_shown_to_the_audience_has_no_content(self, client):
        client.post("/api/demo/poison", json={})
        capsules = client.post("/api/demo/care", json={}).json()["capsules"]
        assert capsules
        for capsule in capsules:
            assert "content" not in capsule
            assert "sketch" not in capsule          # only a short preview travels
            assert len(capsule["sketch_preview"]) <= 12

    def test_verdicts_shown_carry_bands_not_text(self, client):
        client.post("/api/demo/poison", json={})
        detail = client.post("/api/demo/care", json={}).json()["stages"]["A"]["detail"]
        assert detail
        for verdict in detail:
            assert verdict["influence_band"] in ("none", "low", "medium", "high")
            assert set(verdict) == {
                "memory_commitment", "runtime", "influence_band",
                "influence_score", "predicate_changed", "disposition"}


class TestEveryFamilyPresents:
    """A demonstration must not break if the presenter picks a different family."""

    @pytest.mark.parametrize("family", ["F1", "F2", "F3", "F4"])
    def test_family_runs_end_to_end(self, client, family):
        client.post("/api/demo/configure",
                    json={"family": family, "depth": 4, "provenance": "targeted"})
        client.post("/api/demo/clean", json={})
        poison = client.post("/api/demo/poison", json={}).json()
        assert poison["true_contaminated"], f"{family} produced no descendants"

        care = client.post("/api/demo/care", json={}).json()
        assert care["metrics"]["descendant_recall"] == 1.0, f"{family} did not fully recover"
        assert care["metrics"]["bsr"] == 1.0, f"{family} destroyed clean state"
        assert care["certificate"]["safe_resume"] is True
