"""The operations console: authentication, authorisation, and the clinical workflow."""
from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from aegis_care.api.app import app
import aegis_care.api.ops_routes as ops_routes
from aegis_care.ops.auth import (
    AuthError,
    OpsRole,
    Permission,
    hash_password,
    new_session,
    verify_password,
)
from aegis_care.ops.models import IncidentStatus, IssueKind, Severity
from aegis_care.ops.service import OpsError, OpsService
from aegis_care.ops.store import OpsStore


# ======================================================================
class TestCredentials:
    def test_password_roundtrip(self):
        salt, digest = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", salt, digest)

    def test_wrong_password_rejected(self):
        salt, digest = hash_password("s3cret")
        assert not verify_password("S3cret", salt, digest)

    def test_password_is_salted(self):
        a_salt, a_hash = hash_password("same")
        b_salt, b_hash = hash_password("same")
        assert a_salt != b_salt and a_hash != b_hash

    def test_password_never_stored_in_clear(self):
        store = OpsStore()
        operator = store.get_operator("a.khan")
        assert operator is not None
        assert "safety123" not in operator.password_hash
        assert len(operator.password_hash) == 64

    def test_session_expiry(self):
        session = new_session("a.khan", ttl_minutes=-1)
        assert session.is_expired()

    def test_tokens_are_unpredictable(self):
        tokens = {new_session("x").token for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(t) > 30 for t in tokens)


class TestPermissionModel:
    def test_clinician_cannot_run_recovery(self):
        store = OpsStore()
        assert not store.get_operator("s.nair").can(Permission.RUN_RECOVERY)

    def test_auditor_is_read_only(self):
        auditor = OpsStore().get_operator("m.rao")
        assert auditor.can(Permission.VIEW_AUDIT)
        for permission in (Permission.RUN_RECOVERY, Permission.CONFIRM_SEED,
                           Permission.REVIEW_QUARANTINE, Permission.REPORT_INCIDENT):
            assert not auditor.can(permission)

    def test_reviewer_cannot_confirm_seeds(self):
        reviewer = OpsStore().get_operator("p.desai")
        assert reviewer.can(Permission.REVIEW_QUARANTINE)
        assert not reviewer.can(Permission.CONFIRM_SEED)

    def test_admin_has_everything(self):
        admin = OpsStore().get_operator("admin")
        assert set(admin.permissions()) == set(Permission)


# ======================================================================
@pytest.fixture
def svc():
    service = OpsService()
    service.bootstrap_workload()
    return service


@pytest.fixture
def nurse(svc):
    return svc.operator_for_token(svc.sign_in("s.nair", "nurse123")["token"])


@pytest.fixture
def officer(svc):
    return svc.operator_for_token(svc.sign_in("a.khan", "safety123")["token"])


@pytest.fixture
def reviewer(svc):
    return svc.operator_for_token(svc.sign_in("p.desai", "review123")["token"])


class TestServiceAuth:
    def test_bad_password_raises(self, svc):
        with pytest.raises(AuthError):
            svc.sign_in("a.khan", "nope")

    def test_unknown_user_raises(self, svc):
        with pytest.raises(AuthError):
            svc.sign_in("nobody", "x")

    def test_sign_out_invalidates_token(self, svc):
        token = svc.sign_in("a.khan", "safety123")["token"]
        svc.sign_out(token)
        with pytest.raises(AuthError):
            svc.operator_for_token(token)

    def test_failed_sign_in_is_audited(self, svc):
        with pytest.raises(AuthError):
            svc.sign_in("a.khan", "wrong")
        assert any(e["action"] == "sign_in_failed" for e in svc.store.audit_trail())


class TestIntake:
    def test_report_creates_incident(self, svc, nurse):
        incident = svc.report_incident(
            nurse, title="Wrong patient in handover", issue_kind="wrong_patient",
            description="Vitals do not match this patient.", patient_id="S1000")
        assert incident.incident_id.startswith("AC-")
        assert incident.status == IncidentStatus.REPORTED
        assert incident.severity == Severity.CRITICAL   # default for wrong patient
        assert incident.patient_display

    def test_report_requires_description(self, svc, nurse):
        with pytest.raises(OpsError, match="describe"):
            svc.report_incident(nurse, title="x", issue_kind="wrong_patient",
                                description="   ")

    def test_report_rejects_unknown_kind(self, svc, nurse):
        with pytest.raises(OpsError, match="Unknown issue type"):
            svc.report_incident(nurse, title="x", issue_kind="aliens",
                                description="y")

    def test_incident_ids_increment(self, svc, nurse):
        a = svc.report_incident(nurse, title="a", issue_kind="wrong_patient", description="a")
        b = svc.report_incident(nurse, title="b", issue_kind="wrong_patient", description="b")
        assert a.incident_id != b.incident_id

    def test_clinician_worklist_is_scoped_to_own_reports(self, svc, nurse, officer):
        svc.report_incident(nurse, title="mine", issue_kind="wrong_patient", description="x")
        doctor = svc.operator_for_token(svc.sign_in("r.mehta", "clinician123")["token"])
        svc.report_incident(doctor, title="theirs", issue_kind="wrong_patient", description="y")
        titles = {i.title for i in svc.worklist(nurse)}
        assert titles == {"mine"}
        assert len(svc.worklist(officer)) == 2


class TestAuthorisationEnforced:
    def test_clinician_cannot_run_recovery(self, svc, nurse):
        incident = svc.report_incident(nurse, title="x", issue_kind="wrong_patient",
                                       description="y")
        with pytest.raises(OpsError, match="not authorised"):
            svc.run_recovery(nurse, incident.incident_id)

    def test_clinician_cannot_confirm_seed(self, svc, nurse):
        incident = svc.report_incident(nurse, title="x", issue_kind="wrong_patient",
                                       description="y")
        with pytest.raises(OpsError, match="not authorised"):
            svc.confirm_seed(nurse, incident.incident_id, ["anything"])

    def test_reviewer_cannot_close(self, svc, nurse, reviewer):
        incident = svc.report_incident(nurse, title="x", issue_kind="wrong_patient",
                                       description="y")
        with pytest.raises(OpsError, match="not authorised"):
            svc.close_incident(reviewer, incident.incident_id, resolution="done")


class TestRecoveryWorkflow:
    def _staged(self, svc, nurse, officer):
        drill = svc.simulate_contamination(officer, issue_kind="wrong_patient")
        incident = svc.report_incident(
            nurse, title="Wrong patient in handover", issue_kind="wrong_patient",
            description="Observations do not match.", patient_id=drill["patient_id"])
        return drill, incident

    def test_recovery_requires_a_confirmed_seed(self, svc, nurse, officer):
        _, incident = self._staged(svc, nurse, officer)
        with pytest.raises(OpsError, match="Confirm the compromised memory"):
            svc.run_recovery(officer, incident.incident_id)

    def test_confirm_rejects_unknown_memory(self, svc, nurse, officer):
        _, incident = self._staged(svc, nurse, officer)
        with pytest.raises(OpsError, match="not found"):
            svc.confirm_seed(officer, incident.incident_id, ["nope@v1"])

    def test_full_workflow(self, svc, nurse, officer):
        drill, incident = self._staged(svc, nurse, officer)
        svc.triage(officer, incident.incident_id, severity="critical", assign_to="a.khan")
        assert svc.get_incident(officer, incident.incident_id).status == IncidentStatus.TRIAGED

        svc.confirm_seed(officer, incident.incident_id, [drill["seed_key"]])
        assert svc.get_incident(officer, incident.incident_id).status == IncidentStatus.CONFIRMED

        recovered = svc.run_recovery(officer, incident.incident_id)
        assert recovered.status in (IncidentStatus.RECOVERED, IncidentStatus.REVIEW_REQUIRED)
        assert recovered.recovery_summary["repaired"] == drill["affected_count"]
        assert recovered.certificate_text
        assert recovered.certificate["safe_resume"] is True

        closed = svc.close_incident(officer, incident.incident_id,
                                    resolution="Rebuilt and verified.")
        assert closed.status == IncidentStatus.CLOSED
        assert closed.closed_by == "a.khan"

    def test_recovery_leaves_unrelated_memory_alone(self, svc, nurse, officer):
        """Memory belonging to other workflows must survive untouched.

        Note the affected artifacts are filed under the *wrong* patient, so
        "unrelated" cannot be defined by patient scope. It is defined by
        trajectory: everything the bootstrap workload wrote before the drill.
        """
        before = {a.key for rt in svc.env.runtimes.values() for a in rt.vault.servable()}
        drill, incident = self._staged(svc, nurse, officer)
        affected = {a.key for rt in svc.env.runtimes.values()
                    for a in rt.vault.all()} - before
        unrelated = before - affected
        assert unrelated, "nothing to protect in this fixture"

        svc.confirm_seed(officer, incident.incident_id, [drill["seed_key"]])
        svc.run_recovery(officer, incident.incident_id)

        disturbed = [k for k in unrelated
                     if not (svc.env.find_artifact(k)
                             and svc.env.find_artifact(k).is_servable())]
        assert not disturbed, f"recovery disturbed unrelated memory: {disturbed[:5]}"

    def test_cannot_close_before_recovery(self, svc, nurse, officer):
        _, incident = self._staged(svc, nurse, officer)
        with pytest.raises(OpsError, match="Run recovery before closing"):
            svc.close_incident(officer, incident.incident_id, resolution="x")

    def test_dismiss_is_allowed_without_recovery(self, svc, nurse, officer):
        _, incident = self._staged(svc, nurse, officer)
        dismissed = svc.close_incident(officer, incident.incident_id,
                                       resolution="No contamination found.", dismiss=True)
        assert dismissed.status == IncidentStatus.DISMISSED

    def test_closing_requires_a_resolution(self, svc, nurse, officer):
        _, incident = self._staged(svc, nurse, officer)
        with pytest.raises(OpsError, match="resolution"):
            svc.close_incident(officer, incident.incident_id, resolution="  ", dismiss=True)


class TestPatientView:
    def test_patient_memory_groups_by_role(self, svc, officer):
        view = svc.patient_memory(officer, "S1000")
        assert view["patient"]["display"]
        assert set(view["memory_by_role"]) == {
            "registration", "nursing", "clinical_summary"}
        assert view["totals"]["artifacts"] > 0

    def test_unknown_patient_raises(self, svc, officer):
        with pytest.raises(OpsError, match="not found"):
            svc.patient_memory(officer, "S9999")

    def test_search_matches_name_and_mrn(self, svc, officer):
        mrn = svc.env.fhir.patient_mrn("S1004")
        assert any(r["patient_id"] == "S1004"
                   for r in svc.search_patients(officer, mrn))


class TestPersistence:
    def test_incidents_survive_a_restart(self, tmp_path, monkeypatch):
        db = tmp_path / "ops.sqlite"
        first = OpsService(db_path=db)
        nurse = first.operator_for_token(first.sign_in("s.nair", "nurse123")["token"])
        incident = first.report_incident(nurse, title="persisted",
                                         issue_kind="wrong_patient", description="x")
        first.store.close()

        second = OpsService(db_path=db)
        reloaded = second.store.get_incident(incident.incident_id)
        assert reloaded is not None
        assert reloaded.title == "persisted"
        assert reloaded.severity == Severity.CRITICAL
        assert reloaded.notes


# ======================================================================
class TestHTTPSurface:
    @pytest.fixture
    def client(self):
        ops_routes.service = OpsService()
        ops_routes.service.bootstrap_workload()
        return TestClient(app)

    @staticmethod
    def _auth(client, username, password):
        out = client.post("/api/ops/session",
                          json={"username": username, "password": password})
        assert out.status_code == 200, out.text
        return {"Authorization": f"Bearer {out.json()['token']}"}

    def test_meta_is_public(self, client):
        body = client.get("/api/ops/meta").json()
        assert body["environment"]["label"]
        assert body["issue_kinds"]

    def test_protected_routes_need_a_session(self, client):
        for path in ("/api/ops/dashboard", "/api/ops/incidents",
                     "/api/ops/review", "/api/ops/audit"):
            assert client.get(path).status_code == 401

    def test_garbage_token_rejected(self, client):
        assert client.get("/api/ops/dashboard",
                          headers={"Authorization": "Bearer nonsense"}).status_code == 401

    def test_bad_credentials_rejected(self, client):
        assert client.post("/api/ops/session",
                           json={"username": "a.khan", "password": "x"}).status_code == 401

    def test_sign_in_returns_permissions(self, client):
        body = client.post("/api/ops/session",
                           json={"username": "a.khan", "password": "safety123"}).json()
        assert "run_recovery" in body["operator"]["permissions"]
        assert body["operator"]["role"] == "safety_officer"

    def test_clinician_recovery_is_forbidden_over_http(self, client):
        nurse = self._auth(client, "s.nair", "nurse123")
        inc = client.post("/api/ops/incidents", headers=nurse, json={
            "title": "x", "issue_kind": "wrong_patient", "description": "y"}).json()
        response = client.post(
            f"/api/ops/incidents/{inc['incident_id']}/recover", headers=nurse, json={})
        assert response.status_code == 400
        assert "not authorised" in response.json()["detail"]

    def test_end_to_end_over_http(self, client):
        officer = self._auth(client, "a.khan", "safety123")
        nurse = self._auth(client, "s.nair", "nurse123")

        drill = client.post("/api/ops/drill", headers=officer,
                            json={"issue_kind": "wrong_patient"}).json()
        assert drill["affected_count"] > 0

        inc = client.post("/api/ops/incidents", headers=nurse, json={
            "title": "Wrong patient in handover", "issue_kind": "wrong_patient",
            "description": "Observations do not match.",
            "patient_id": drill["patient_id"]}).json()
        iid = inc["incident_id"]

        candidates = client.get(f"/api/ops/incidents/{iid}/candidates",
                                headers=officer).json()["candidates"]
        assert candidates

        client.post(f"/api/ops/incidents/{iid}/confirm", headers=officer,
                    json={"memory_keys": [drill["seed_key"]]})
        recovered = client.post(f"/api/ops/incidents/{iid}/recover",
                                headers=officer, json={}).json()
        assert recovered["recovery_summary"]["repaired"] == drill["affected_count"]
        assert recovered["certificate"]["safe_resume"] is True

        closed = client.post(f"/api/ops/incidents/{iid}/close", headers=officer,
                             json={"resolution": "Rebuilt and verified."}).json()
        assert closed["status"] == "closed"

        evidence = client.get(f"/api/ops/incidents/{iid}/evidence", headers=officer).json()
        assert evidence["certificate_text"]
        assert evidence["operational_audit"]

    def test_auditor_cannot_report_or_recover(self, client):
        auditor = self._auth(client, "m.rao", "audit123")
        assert client.post("/api/ops/incidents", headers=auditor, json={
            "title": "x", "issue_kind": "wrong_patient",
            "description": "y"}).status_code == 400

    def test_console_is_the_root_page(self, client):
        assert "Operations Console" in client.get("/").text

    def test_other_interfaces_still_reachable(self, client):
        assert client.get("/present").status_code == 200
        assert client.get("/research").status_code == 200


class TestDemoSeeding:
    """The console must open with something in it, and everything in it must be
    a genuine contamination that propagated through the real derivation chain."""

    @pytest.fixture(scope="class")
    def seeded(self):
        service = OpsService()
        service.bootstrap_workload()
        service.seed_demo_incidents()
        return service

    def test_incidents_are_created(self, seeded):
        assert len(seeded.store.list_incidents(open_only=False)) >= 3

    def test_incidents_span_several_workflow_stages(self, seeded):
        statuses = {i.status for i in seeded.store.list_incidents(open_only=False)}
        assert IncidentStatus.CLOSED in statuses
        assert len(statuses) >= 3, "a single-stage queue teaches nothing"

    def test_at_least_one_incident_remains_open_to_work(self, seeded):
        assert any(i.status.is_open
                   for i in seeded.store.list_incidents(open_only=False))

    def test_closed_incident_carries_a_signed_certificate(self, seeded):
        closed = [i for i in seeded.store.list_incidents(open_only=False)
                  if i.status == IncidentStatus.CLOSED]
        assert closed
        assert closed[0].certificate_text
        assert closed[0].certificate["safe_resume"] is True

    def test_every_seeded_incident_actually_propagated(self, seeded):
        """Nothing here may be a cosmetic row: each names a real patient and a
        contamination that reached the agents' memory."""
        for incident in seeded.store.list_incidents(open_only=False):
            assert incident.patient_id
            assert incident.patient_display
            assert incident.notes

    def test_all_three_roles_hold_memory(self, seeded):
        totals = {role.value: rt.vault.stats().get("total", 0)
                  for role, rt in seeded.env.runtimes.items()}
        assert all(v > 0 for v in totals.values()), totals

    def test_every_family_can_propagate_back_to_back(self):
        """A drill of each type must work even after earlier drills and a
        recovery have run - control trajectories consume sibling tasks, which
        previously made later drills fail silently."""
        service = OpsService()
        service.bootstrap_workload()
        officer = service.operator_for_token(
            service.sign_in("a.khan", "safety123")["token"])
        for kind in ("wrong_patient", "wrong_record_content",
                     "restricted_disclosure", "stale_after_correction"):
            drill = service.simulate_contamination(officer, issue_kind=kind)
            assert drill["affected_count"] > 0, f"{kind} did not propagate"

    def test_seeding_is_skipped_when_incidents_already_exist(self, tmp_path):
        db = tmp_path / "ops.sqlite"
        first = OpsService(db_path=db)
        first.bootstrap_workload()
        first.seed_demo_incidents()
        count = len(first.store.list_incidents(open_only=False))
        first.store.close()

        second = OpsService(db_path=db)
        assert len(second.store.list_incidents(open_only=False)) == count


class TestSpreadTree:
    """The spread view is how a clinician understands blast radius, so it must
    be accurate about what was reached and how it was found."""

    @pytest.fixture(scope="class")
    def spread(self):
        service = OpsService()
        service.bootstrap_workload()
        officer = service.operator_for_token(
            service.sign_in("a.khan", "safety123")["token"])
        nurse = service.operator_for_token(
            service.sign_in("s.nair", "nurse123")["token"])
        drill = service.simulate_contamination(officer, issue_kind="wrong_patient")
        incident = service.report_incident(
            nurse, title="Wrong patient", issue_kind="wrong_patient",
            description="x", patient_id=drill["patient_id"])
        service.confirm_seed(officer, incident.incident_id, [drill["seed_key"]])
        service.run_recovery(officer, incident.incident_id)
        return service, officer, incident, drill

    def test_empty_before_recovery(self):
        service = OpsService()
        service.bootstrap_workload()
        officer = service.operator_for_token(
            service.sign_in("a.khan", "safety123")["token"])
        nurse = service.operator_for_token(
            service.sign_in("s.nair", "nurse123")["token"])
        incident = service.report_incident(
            nurse, title="x", issue_kind="wrong_patient", description="y")
        tree = service.spread_tree(officer, incident.incident_id)
        assert tree["nodes"] == []
        assert tree["recovered"] is False

    def test_covers_every_affected_memory(self, spread):
        service, officer, incident, drill = spread
        tree = service.spread_tree(officer, incident.incident_id)
        # the origin plus each descendant the drill created
        assert tree["stats"]["affected"] == drill["affected_count"] + 1

    def test_origin_is_marked_and_unique(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        seeds = [n for n in tree["nodes"] if n["is_seed"]]
        assert len(seeds) == 1
        assert seeds[0]["depth"] == 0
        assert seeds[0]["discovery"] == "seed"

    def test_tree_is_connected(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        keys = {n["key"] for n in tree["nodes"]}
        seed = next(n["key"] for n in tree["nodes"] if n["is_seed"])
        reachable, frontier = {seed}, [seed]
        while frontier:
            current = frontier.pop()
            for edge in tree["edges"]:
                if edge["from"] == current and edge["to"] not in reachable:
                    reachable.add(edge["to"])
                    frontier.append(edge["to"])
        assert reachable == keys, "some affected memory is not reachable from the origin"

    def test_records_how_each_hop_was_discovered(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        for node in tree["nodes"]:
            assert node["discovery"] in ("seed", "lineage", "latent_sketch")

    def test_crosses_role_boundaries(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        assert tree["stats"]["roles"] == 3, "the drill should reach all three roles"

    def test_every_hop_named_the_wrong_patient(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        assert all(n["wrong_patient"] for n in tree["nodes"])

    def test_repairs_name_the_intended_patient(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        repaired = [n for n in tree["nodes"] if n["outcome"] == "repaired"]
        assert repaired
        for node in repaired:
            assert node["repaired_patient"] == incident.patient_id

    def test_reports_untouched_clean_memory(self, spread):
        service, officer, incident, _ = spread
        tree = service.spread_tree(officer, incident.incident_id)
        assert tree["stats"]["cleared"] > 0

    def test_clinician_may_see_the_spread_of_their_own_report(self, spread):
        service, _, incident, _ = spread
        nurse = service.operator_for_token(
            service.sign_in("s.nair", "nurse123")["token"])
        assert service.spread_tree(nurse, incident.incident_id)["nodes"]

    def test_auditor_may_see_the_spread(self, spread):
        service, _, incident, _ = spread
        auditor = service.operator_for_token(
            service.sign_in("m.rao", "audit123")["token"])
        assert service.spread_tree(auditor, incident.incident_id)["nodes"]


class TestAccessSurface:
    @pytest.fixture
    def client(self):
        ops_routes.service = OpsService()
        ops_routes.service.bootstrap_workload()
        return TestClient(app)

    def test_meta_exposes_the_full_permission_matrix(self, client):
        meta = client.get("/api/ops/meta").json()
        matrix = meta["role_permissions"]
        assert set(matrix) == {r.value for r in OpsRole}
        assert "run_recovery" in matrix["safety_officer"]
        assert "run_recovery" not in matrix["clinician"]
        assert "review_quarantine" in matrix["reviewer"]
        assert "confirm_seed" not in matrix["reviewer"]
        assert matrix["auditor"] == sorted(
            ["export_evidence", "view_audit", "view_incident", "view_worklist"])

    def test_spread_route_requires_a_session(self, client):
        assert client.get("/api/ops/incidents/AC-1/spread").status_code == 401

    def test_views_asset_is_served(self, client):
        response = client.get("/static/ops-views.js")
        assert response.status_code == 200
        assert "pageSpread" in response.text
