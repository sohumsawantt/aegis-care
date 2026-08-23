"""The core invariants of proposal Section 7.1, plus the termination and safety
properties of Section 6.6.

These are the claims the system makes about itself, so each one gets an
executable check rather than prose.
"""
from __future__ import annotations

import pytest

from aegis_care.care.capsule import FORBIDDEN_CAPSULE_FIELDS
from aegis_care.care.coordinator import CAREOptions, RecoveryCoordinator
from aegis_care.eval.privacy import PrivacyAuditor
from aegis_care.memory.models import SERVABLE_STATES, MemoryState
from aegis_care.policy.rbac import ROLE_FIELD_MATRIX, FieldCategory, Role


class TestSection71Invariants:
    """Section 7.1, one test per bullet."""

    def test_seed_and_confirmed_descendants_are_non_servable(self, env, recovered):
        """"A confirmed seed and every confirmed contaminated descendant are
        non-servable during recovery." """
        incident, result = recovered
        seed = env.find_artifact(incident.seed_key)
        assert not seed.is_servable()
        for key in result.confirmed:
            artifact = env.find_artifact(key)
            assert artifact.state not in SERVABLE_STATES, (
                f"{key} remained servable in state {artifact.state}")

    def test_repaired_memory_does_not_cite_a_tombstoned_version(self, env, recovered):
        """"No repaired memory may cite a tombstoned version as trusted support." """
        incident, result = recovered
        tombstoned = set(env.ledger.tombstone_commitments(incident.incident_id))
        for record in result.repaired:
            repaired = env.find_artifact(record["new_key"])
            assert not (set(repaired.explicit_parent_commitments) & tombstoned), (
                f"{repaired.key} cites a tombstoned ancestor")

    def test_no_role_receives_data_outside_its_policy(self, env, recovered):
        """"No role may receive a raw record, sketch, or verdict outside the
        request's patient, role, purpose, recipient, and expiry policy." """
        incident, result = recovered
        for capsule in result.capsules:
            assert capsule.recipient in {r.value for r in env.runtimes}
            assert capsule.purpose == "incident_recovery"
            assert set(capsule.released_fields()) & FORBIDDEN_CAPSULE_FIELDS == set()

    def test_no_role_holds_a_field_it_cannot_read(self, env, recovered):
        """After recovery no servable memory may carry a restricted field in a
        role without rights over it."""
        _, _ = recovered
        for role, runtime in env.runtimes.items():
            if FieldCategory.RESTRICTED in ROLE_FIELD_MATRIX[role]:
                continue
            for artifact in runtime.vault.servable():
                assert not artifact.structured_facts.get("laundered_restricted"), (
                    f"{artifact.key} holds restricted material in role {role.value}")

    def test_similarity_alone_cannot_trigger_destructive_repair(self, env, builder):
        """"Latent similarity alone cannot trigger destructive repair; exact
        lineage or counterfactual confirmation is required." """
        incident = builder.build("F1", env.tasks[0], depth=4, n_controls=1)
        result = RecoveryCoordinator(env).recover(
            incident.incident_id, [incident.seed_key], options=CAREOptions())
        for key in result.confirmed:
            artifact = env.find_artifact(key)
            verdict = next((v for v in result.verdicts
                            if v.memory_commitment == artifact.commitment()), None)
            assert verdict is not None, f"{key} was acted on with no signed verdict"
            # Either a hard predicate changed, or replay produced real evidence.
            assert verdict.predicate_changed or verdict.influence_score > 0.0

    def test_missing_recipe_causes_quarantine_not_guessing(self, env, builder):
        """"A missing replay recipe, missing trusted source, or policy conflict
        causes quarantine or human review." """
        incident = builder.build("F1", env.tasks[0], depth=4)
        # Strip the recipe from a descendant so it cannot be rebuilt.
        victim_key = sorted(incident.true_contaminated)[0]
        victim = env.find_artifact(victim_key)
        victim.replay_recipe = None

        result = RecoveryCoordinator(env).recover(
            incident.incident_id, [incident.seed_key], options=CAREOptions())
        after = env.find_artifact(victim_key)
        assert after.state in (MemoryState.QUARANTINED, MemoryState.TOMBSTONED,
                               MemoryState.SUSPECTED), (
            "an unreconstructable artifact must never be silently repaired")
        assert not after.is_servable()

    def test_completion_requires_probes_and_checks(self, recovered):
        """"Recovery completion requires safety follow-up tasks, privacy checks,
        and resurrection probes." """
        _, result = recovered
        cert = result.certificate
        assert result.resurrection_probe["attempts"] > 0
        assert "capsules" in cert.privacy
        assert cert.safe_resume is (
            result.closure_reached
            and result.resurrection_probe["resurrection_rate"] == 0.0)


class TestSection66Properties:
    """Termination and safety properties."""

    def test_monotone_incident_frontier(self, env, recovered):
        """No artifact returns to ACTIVE during an incident."""
        incident, result = recovered
        touched = set(result.confirmed) | {incident.seed_key}
        for key in touched:
            assert env.find_artifact(key).state != MemoryState.ACTIVE

    def test_finite_termination(self, recovered):
        _, result = recovered
        assert result.closure_reached
        assert result.rounds <= CAREOptions().max_rounds

    def test_each_artifact_processed_at_most_once(self, recovered):
        _, result = recovered
        keys = [c["memory_key"] for c in result.candidates_considered]
        assert len(keys) == len(set(keys)), "an artifact entered the frontier twice"

    def test_no_raw_content_centralisation(self, env, recovered):
        """All content-dependent matching and replay stays in the owning runtime."""
        incident, result = recovered
        auditor = PrivacyAuditor(env)
        audit = auditor.released_field_audit(incident.incident_id)
        assert audit["raw_content_exported"] is False
        assert audit["undeclared_fields"] == []
        assert audit["raw_content_fields"] == []

    def test_fail_closed_reconstruction(self, env, recovered):
        """Repairs must clear tau_r and their mandatory checks; anything that
        does not is quarantined instead of guessed."""
        from aegis_care.config import CONFIG

        _, result = recovered
        for record in result.repaired:
            assert record["confidence"] >= CONFIG.repair.tau_r, (
                f"{record['memory_key']} was published below the repair-confidence floor")
            assert all(record["checks"].get(k, True) for k in ("resolved", "policy_ok"))
        for record in result.quarantined:
            assert record["reason"], "a quarantine must carry an auditable reason"

    def test_closed_publication(self, env, recovered):
        """A repaired artifact becomes retrievable only after checks pass."""
        _, result = recovered
        for record in result.repaired:
            repaired = env.find_artifact(record["new_key"])
            assert repaired.state == MemoryState.REPAIRED
            assert repaired.is_servable()
            assert repaired.replay_recipe is not None
            assert repaired.signature


class TestAuditTrail:
    def test_no_destructive_deletion(self, env, recovered):
        """Section 11.2: superseded content is made non-servable, never erased."""
        incident, result = recovered
        for record in result.repaired:
            original = env.find_artifact(record["memory_key"])
            assert original is not None, "an original version was deleted outright"
            assert original.content, "original content was erased"

    def test_tombstones_are_signed(self, env, recovered):
        incident, _ = recovered
        rows = list(env.ledger.conn.execute(
            "SELECT * FROM tombstones WHERE incident_id = ?", (incident.incident_id,)))
        assert rows
        for row in rows:
            assert row["signature"]

    def test_every_verdict_is_signed_and_verifiable(self, env, recovered):
        _, result = recovered
        for verdict in result.verdicts:
            assert env.keyring.verify(verdict.runtime, verdict.signable(),
                                      verdict.signature)

    def test_events_recorded_for_the_incident(self, env, recovered):
        incident, _ = recovered
        kinds = {e["kind"] for e in env.ledger.events(incident.incident_id)}
        assert "recovery_started" in kinds
        assert "recovery_complete" in kinds
