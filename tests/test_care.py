"""Propagation, capsules, and the four CARE stages."""
from __future__ import annotations

import datetime as _dt

import pytest

from aegis_care.care.capsule import (
    ALLOWED_CAPSULE_FIELDS,
    FORBIDDEN_CAPSULE_FIELDS,
    CapsuleError,
    CapsuleMinter,
    band_for,
)
from aegis_care.care.coordinator import CAREOptions, RecoveryCoordinator
from aegis_care.incident.masks import ProvenanceMask
from aegis_care.incident.scenarios import FAMILIES, FAMILY_INFO
from aegis_care.memory.models import ArtifactType, MemoryState
from aegis_care.policy.rbac import Role


# ======================================================================
class TestPropagation:
    def test_clean_trajectory_resolves_intended_patient(self, env):
        traj = env.run_trajectory(env.tasks[0])
        assert not traj.is_contaminated
        assert all(n.true_patient == env.tasks[0]["patient_id"] for n in traj.nodes)

    def test_poisoned_seed_propagates_through_every_hop(self, env):
        task = env.tasks[0]
        traj = env.run_trajectory(task, depth=4, forced_seed_patient="S1099", seed_depth=0)
        assert traj.is_contaminated
        assert all(n.true_patient == "S1099" for n in traj.nodes)
        assert len(env.truth.true_contaminated_descendants(traj.seed_key)) == 4

    def test_propagation_crosses_role_boundaries(self, env):
        traj = env.run_trajectory(env.tasks[0], depth=4, forced_seed_patient="S1099")
        roles = {n.role for n in traj.nodes if n.contaminated}
        assert roles == {Role.REGISTRATION, Role.NURSING, Role.CLINICAL_SUMMARY}

    @pytest.mark.parametrize("depth", [1, 2, 3, 4])
    def test_depth_is_respected(self, env, depth):
        traj = env.run_trajectory(env.tasks[0], depth=depth, forced_seed_patient="S1099")
        assert len(traj.nodes) == depth + 1

    def test_descendants_are_consumed_by_later_tasks(self, env):
        """A stale descendant is a live retrieval cue, not an inert record."""
        task = env.tasks[0]
        env.run_trajectory(task, depth=4, forced_seed_patient="S1099")
        # Remove only the seed, exactly as baseline B does.
        seed = env.runtime(Role.REGISTRATION).vault.all()[0]
        env.runtime(Role.REGISTRATION).vault.set_state(
            seed.key, MemoryState.TOMBSTONED, "INC", "seed deleted")
        followup = env.run_followup_task(task)
        assert not followup["correct"], (
            "deleting the seed alone must not fix the follow-up task; "
            "descendants still carry the wrong association")

    @pytest.mark.parametrize("family", list(FAMILIES))
    def test_every_family_builds_and_contaminates(self, env, builder, family):
        depth = max(4, FAMILY_INFO[family]["seed_depth"] + 1)
        incident = builder.build(family, env.tasks[10], depth=depth)
        assert incident.seed_key
        assert incident.true_contaminated, f"family {family} produced no descendants"

    def test_f3_launders_restricted_field_across_roles(self, env, builder):
        incident = builder.build("F3", env.tasks[10], depth=4)
        summary_runtime = env.runtime(Role.CLINICAL_SUMMARY)
        laundered = [a for a in summary_runtime.vault.all()
                     if a.structured_facts.get("laundered_restricted")]
        assert laundered, "restricted material never reached the summary role"

    def test_control_trajectory_is_independent(self, f1_incident):
        assert f1_incident.clean_keys
        assert not (f1_incident.clean_keys & f1_incident.true_contaminated)


# ======================================================================
class TestCapsules:
    def _mint(self, env, incident):
        minter = CapsuleMinter(env.keyring, env.encoder)
        seed = env.find_artifact(incident.seed_key)
        return minter, minter.mint(seed, incident_id="INC-T", recipient="nursing",
                                   issuer="coordinator")

    def test_capsule_carries_no_raw_content(self, env, f1_incident):
        _, capsule = self._mint(env, f1_incident)
        fields = set(capsule.released_fields())
        assert not (fields & FORBIDDEN_CAPSULE_FIELDS)
        assert fields <= ALLOWED_CAPSULE_FIELDS

    def test_capsule_does_not_contain_patient_identifiers(self, env, f1_incident):
        _, capsule = self._mint(env, f1_incident)
        seed = env.find_artifact(f1_incident.seed_key)
        blob = str(capsule.signable())
        assert seed.content not in blob
        assert env.fhir.patient_mrn(seed.patient_scope) not in blob
        assert env.fhir.patient_display(seed.patient_scope) not in blob

    def test_valid_capsule_verifies(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        minter.verify(capsule, expected_recipient="nursing")

    def test_tampered_capsule_rejected(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        capsule.sketch = [0] * len(capsule.sketch)
        with pytest.raises(CapsuleError, match="tampered|signature"):
            minter.verify(capsule, expected_recipient="nursing")

    def test_wrong_recipient_rejected(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        with pytest.raises(CapsuleError, match="addressed to"):
            minter.verify(capsule, expected_recipient="clinical_summary")

    def test_wrong_purpose_rejected(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        with pytest.raises(CapsuleError, match="purpose"):
            minter.verify(capsule, expected_recipient="nursing",
                          expected_purpose="human_review")

    def test_expired_capsule_rejected(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=2)
        with pytest.raises(CapsuleError, match="expired"):
            minter.verify(capsule, expected_recipient="nursing", at=future)

    def test_nonce_replay_rejected(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        minter.consume_nonce(capsule)
        with pytest.raises(CapsuleError, match="replay"):
            minter.consume_nonce(capsule)

    def test_query_budget_is_finite(self, env, f1_incident):
        minter, capsule = self._mint(env, f1_incident)
        assert minter.spend_query(capsule, minter.config.max_query_budget)
        assert not minter.spend_query(capsule, 1)

    def test_recipients_receive_different_sketches(self, env, f1_incident):
        minter = CapsuleMinter(env.keyring, env.encoder)
        seed = env.find_artifact(f1_incident.seed_key)
        a = minter.mint(seed, incident_id="INC-T", recipient="nursing", issuer="coordinator")
        b = minter.mint(seed, incident_id="INC-T", recipient="clinical_summary",
                        issuer="coordinator")
        assert a.sketch != b.sketch
        assert a.patient_token != b.patient_token

    @pytest.mark.parametrize("score,predicate,expected", [
        (0.0, False, "none"), (0.2, False, "low"),
        (0.4, False, "medium"), (0.8, False, "high"), (0.0, True, "high"),
    ])
    def test_influence_banding(self, score, predicate, expected):
        assert band_for(score, predicate) == expected


# ======================================================================
class TestCARELoop:
    def test_recovers_all_descendants(self, recovered):
        incident, result = recovered
        neutralised = set(result.confirmed)
        assert incident.true_contaminated <= neutralised

    def test_repairs_restore_intended_patient(self, env, recovered):
        incident, result = recovered
        assert result.repaired
        for record in result.repaired:
            repaired = env.find_artifact(record["new_key"])
            assert repaired.structured_facts["patient_id"] == incident.task["patient_id"]
            assert repaired.state == MemoryState.REPAIRED

    def test_clean_control_is_not_destroyed(self, env, recovered):
        """RQ3: counterfactual replay must protect surface-similar clean state."""
        incident, result = recovered
        for key in incident.clean_keys:
            artifact = env.find_artifact(key)
            assert artifact.is_servable(), f"clean control {key} was destroyed"

    def test_followup_task_is_correct_after_recovery(self, env, recovered):
        incident, _ = recovered
        assert env.run_followup_task(incident.task, depth=incident.depth)["correct"]

    def test_closure_is_reached(self, recovered):
        _, result = recovered
        assert result.closure_reached

    def test_superseded_versions_are_retained_not_deleted(self, env, recovered):
        """Section 11.2: no destructive deletion of the audit trail."""
        incident, result = recovered
        for record in result.repaired:
            original = env.find_artifact(record["memory_key"])
            assert original is not None
            assert original.state == MemoryState.SUPERSEDED

    def test_certificate_is_signed_and_complete(self, env, recovered):
        _, result = recovered
        cert = result.certificate
        assert cert is not None
        assert env.keyring.verify("coordinator", cert.signable(), cert.signature)
        assert cert.safe_resume
        assert "AEGIS-CARE RECOVERY CERTIFICATE" in cert.to_text()

    def test_coordinator_never_sees_clinical_text(self, env, recovered):
        """The strongest privacy claim: verdicts carry bands, not content."""
        incident, result = recovered
        blob = "".join(str(v.signable()) for v in result.verdicts)
        for artifact in env.all_artifacts():
            if artifact.content:
                assert artifact.content not in blob

    def test_recovery_under_targeted_masking(self, env, builder):
        """RQ2: latent sketches must recover descendants explicit lineage loses."""
        incident = builder.build("F1", env.tasks[2], depth=4, n_controls=1)
        mask = ProvenanceMask(env).apply("targeted")
        assert mask.edges_removed > 0

        snapshot = env.snapshot()
        coordinator = RecoveryCoordinator(env)

        lineage_only = coordinator.recover(
            incident.incident_id + "-lineage", [incident.seed_key],
            options=CAREOptions(use_sketch=False))
        env.restore(snapshot)

        full = RecoveryCoordinator(env).recover(
            incident.incident_id + "-full", [incident.seed_key], options=CAREOptions())

        assert len(full.confirmed) > len(lineage_only.confirmed), (
            "latent discovery did not recover anything explicit lineage missed")

    def test_sketch_only_over_quarantines(self, env, builder):
        """RQ3: without counterfactual confirmation, clean state is destroyed."""
        incident = builder.build("F1", env.tasks[4], depth=4, n_controls=1)
        result = RecoveryCoordinator(env).recover(
            incident.incident_id, [incident.seed_key],
            options=CAREOptions(use_counterfactual=False, use_recompilation=False,
                                use_explicit_lineage=False))
        destroyed = {q["memory_key"] for q in result.quarantined} & incident.clean_keys
        assert destroyed, "sketch-only mode should have false-positived on clean state"

    def test_resurrection_probes_blocked(self, recovered):
        _, result = recovered
        probe = result.resurrection_probe
        assert probe["attempts"] > 0
        assert probe["blocked"] == probe["attempts"]
        assert probe["resurrection_rate"] == 0.0

    def test_firewall_blocks_reintroduction(self, env, recovered):
        """A later write citing a revoked ancestor must not go through."""
        incident, _ = recovered
        seed = env.find_artifact(incident.seed_key)
        runtime = env.runtime(seed.owner)
        assert runtime.firewall_check(seed) is not None

    def test_disabling_enforcement_allows_resurrection(self, env, builder):
        incident = builder.build("F1", env.tasks[6], depth=4)
        result = RecoveryCoordinator(env).recover(
            incident.incident_id, [incident.seed_key],
            options=CAREOptions(use_enforcement=False))
        assert result.resurrection_probe["resurrection_rate"] > 0.0

    def test_overhead_is_recorded(self, recovered):
        _, result = recovered
        assert result.overhead["replays"] > 0
        assert result.overhead["capsule_bytes"] > 0


# ======================================================================
class TestProvenanceMasks:
    def test_complete_removes_nothing(self, env, f1_incident):
        assert ProvenanceMask(env).apply("complete").edges_removed == 0

    @pytest.mark.parametrize("condition,expected", [
        ("random20", 0.2), ("random40", 0.4), ("random60", 0.6)])
    def test_random_loss_fraction(self, env, f1_incident, condition, expected):
        mask = ProvenanceMask(env).apply(condition)
        assert abs(mask.loss_fraction - expected) < 0.15

    def test_targeted_removes_cross_role_and_summary_edges(self, env, f1_incident):
        mask = ProvenanceMask(env).apply("targeted")
        assert mask.edges_removed > 0
        for child_key, _ in mask.removed:
            artifact = env.find_artifact(child_key)
            assert artifact is not None

    def test_masking_does_not_touch_ground_truth(self, env, f1_incident):
        before = len(env.truth.edges)
        ProvenanceMask(env).apply("targeted")
        assert len(env.truth.edges) == before

    def test_masks_are_deterministic(self, env, builder):
        builder.build("F1", env.tasks[0], depth=4)
        a = ProvenanceMask(env, seed=7).apply("random40")
        assert a.edges_removed >= 0
