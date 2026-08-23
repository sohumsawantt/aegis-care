"""Crypto, FHIR sandbox, policy, sketches, and the memory ledger."""
from __future__ import annotations

import pytest

from aegis_care.fhir.store import FHIRStore
from aegis_care.memory.models import (
    ArtifactType,
    MemoryArtifact,
    MemoryState,
    state_transition_allowed,
)
from aegis_care.memory.sketch import SketchEncoder, SketchIndex
from aegis_care.memory.store import LedgerStore, MemoryVault
from aegis_care.policy.rbac import (
    FieldCategory,
    Operation,
    PolicyEngine,
    Role,
    categorize_resource,
)
from aegis_care.util.crypto import KeyRing, commit, receiver_scoped_token


# ======================================================================
class TestCrypto:
    def test_signature_roundtrip(self):
        kr = KeyRing()
        payload = {"verdict": "confirmed", "score": 0.81}
        sig = kr.sign("nursing", payload)
        assert kr.verify("nursing", payload, sig)

    def test_tampering_detected(self):
        kr = KeyRing()
        sig = kr.sign("nursing", {"verdict": "confirmed"})
        assert not kr.verify("nursing", {"verdict": "clean"}, sig)

    def test_wrong_principal_rejected(self):
        kr = KeyRing()
        sig = kr.sign("nursing", {"a": 1})
        assert not kr.verify("registration", {"a": 1}, sig)

    def test_identities_are_deterministic(self):
        assert KeyRing().public_key_b64("nursing") == KeyRing().public_key_b64("nursing")

    def test_commitments_are_stable_and_order_independent(self):
        assert commit({"a": 1, "b": 2}) == commit({"b": 2, "a": 1})
        assert commit({"a": 1}) != commit({"a": 2})

    def test_patient_token_is_receiver_scoped(self):
        """The same patient must not be linkable across recipients."""
        kr = KeyRing()
        key = kr.incident_key("INC-1")
        nursing = receiver_scoped_token("S1001", "nursing", key)
        summary = receiver_scoped_token("S1001", "clinical_summary", key)
        assert nursing != summary
        assert nursing == receiver_scoped_token("S1001", "nursing", key)

    def test_patient_token_differs_across_incidents(self):
        kr = KeyRing()
        a = receiver_scoped_token("S1001", "nursing", kr.incident_key("INC-1"))
        b = receiver_scoped_token("S1001", "nursing", kr.incident_key("INC-2"))
        assert a != b


# ======================================================================
class TestFHIRStore:
    def test_generates_committed_dataset(self):
        store = FHIRStore(100)
        stats = store.stats()
        assert stats["Patient"] == 100
        assert stats["Observation"] > 500

    def test_search_by_identifier(self):
        store = FHIRStore(50)
        mrn = store.patient_mrn("S1007")
        assert [p["id"] for p in store.search("Patient", identifier=mrn)] == ["S1007"]

    def test_restricted_observations_filtered_by_default(self):
        store = FHIRStore(50)
        store.ensure_restricted_observation("S1003")
        assert len(store.observations_for("S1003")) < len(
            store.observations_for("S1003", restricted_ok=True))

    def test_snapshot_restore_is_isolating(self):
        store = FHIRStore(30)
        snap = store.snapshot()
        obs_id = store.observations_for("S1000")[0]["id"]
        store.correct_observation(obs_id, 999.0)
        assert store.read("Observation", obs_id)["valueQuantity"]["value"] == 999.0
        store.restore(snap)
        assert store.read("Observation", obs_id)["valueQuantity"]["value"] != 999.0

    def test_resource_categorisation(self):
        store = FHIRStore(20)
        assert categorize_resource(store.read("Patient", "S1000")) == FieldCategory.IDENTITY
        restricted = store.ensure_restricted_observation("S1001")
        assert categorize_resource(restricted) == FieldCategory.RESTRICTED


# ======================================================================
class TestPolicy:
    def test_registration_cannot_read_clinical_notes(self):
        policy = PolicyEngine()
        assert not policy.check(Role.REGISTRATION, Operation.READ, FieldCategory.NOTE,
                                purpose="patient_registration")

    def test_nursing_can_read_vitals(self):
        policy = PolicyEngine()
        assert policy.check(Role.NURSING, Operation.READ, FieldCategory.VITALS,
                            purpose="shift_handover")

    def test_no_role_may_read_restricted_fields(self):
        policy = PolicyEngine()
        for role, purpose in ((Role.REGISTRATION, "patient_registration"),
                              (Role.NURSING, "shift_handover"),
                              (Role.CLINICAL_SUMMARY, "care_summary")):
            assert not policy.check(role, Operation.READ, FieldCategory.RESTRICTED,
                                    purpose=purpose)

    def test_coordinator_has_no_clinical_rights(self):
        """The honest-but-curious coordinator holds no clinical read rights at all."""
        policy = PolicyEngine()
        for category in FieldCategory:
            assert not policy.check(Role.COORDINATOR, Operation.READ, category,
                                    purpose="incident_recovery")

    def test_purpose_must_match_role(self):
        policy = PolicyEngine()
        assert not policy.check(Role.NURSING, Operation.READ, FieldCategory.VITALS,
                                purpose="patient_registration")

    def test_patient_scope_enforced(self):
        policy = PolicyEngine()
        policy.grant_patient_scope(Role.NURSING, {"S1000"})
        assert policy.check(Role.NURSING, Operation.READ, FieldCategory.VITALS,
                            purpose="shift_handover", patient_id="S1000")
        assert not policy.check(Role.NURSING, Operation.READ, FieldCategory.VITALS,
                                purpose="shift_handover", patient_id="S1099")

    def test_denials_are_recorded(self):
        policy = PolicyEngine()
        policy.check(Role.REGISTRATION, Operation.READ, FieldCategory.NOTE,
                     purpose="patient_registration")
        assert policy.denials


# ======================================================================
class TestSketches:
    def test_deterministic(self):
        enc = SketchEncoder()
        text = "Shift handover - Devraj Alvarez (record S1000, MRN MRN6100000)."
        assert enc.local_sketch(text, "nursing") == enc.local_sketch(text, "nursing")

    def test_related_text_scores_higher_than_unrelated(self):
        enc = SketchEncoder()
        a = enc.local_sketch("handover for S1000 heart rate 88 glucose 140", "nursing")
        b = enc.local_sketch("handover S1000 with heart rate and glucose reviewed", "nursing")
        c = enc.local_sketch("summary for S1077 sodium 138 hemoglobin 12", "nursing")
        assert enc.similarity(a, b) > enc.similarity(a, c)

    def test_receiver_scoping_decorrelates(self):
        """Same text, two recipients: the sketches must not be comparable."""
        enc = SketchEncoder()
        text = "Shift handover - record S1000"
        a = enc.sketch(text, recipient="nursing", incident_id="INC1")
        b = enc.sketch(text, recipient="clinical_summary", incident_id="INC1")
        # 0.5 is the orthogonal point for cosine mapped to [0,1].
        assert abs(enc.similarity(a, b) - 0.5) < 0.15

    def test_quantised_width(self):
        enc = SketchEncoder()
        sketch = enc.local_sketch("anything", "nursing")
        assert len(sketch) == enc.config.sketch_dim
        assert all(-128 <= v <= 127 for v in sketch)

    def test_index_ranks_by_similarity(self):
        enc = SketchEncoder()
        index = SketchIndex(enc, "nursing")
        index.add("m1", "handover for record S1000 heart rate 88")
        index.add("m2", "aggregate for record S1099 sodium 140")
        assert index.query("handover record S1000 heart rate")[0][0] == "m1"


# ======================================================================
class TestMemoryLedger:
    def _vault(self):
        ledger = LedgerStore()
        keyring = KeyRing()
        return MemoryVault(Role.NURSING, SketchEncoder(), ledger, keyring), ledger, keyring

    def _artifact(self, **kw):
        defaults = dict(memory_id="m1", version=1, owner=Role.NURSING,
                        artifact_type=ArtifactType.HANDOVER,
                        content="handover for S1000", patient_scope="S1000",
                        purpose="shift_handover")
        defaults.update(kw)
        return MemoryArtifact(**defaults)

    def test_put_signs_and_indexes(self):
        vault, _, keyring = self._vault()
        artifact = vault.put(self._artifact())
        assert keyring.verify("nursing", artifact.signable_payload(), artifact.signature)
        assert artifact.write_context_sketch is not None

    def test_signature_survives_state_change(self):
        """Lifecycle moves must not invalidate the origin attestation."""
        vault, _, keyring = self._vault()
        artifact = vault.put(self._artifact())
        vault.set_state(artifact.key, MemoryState.QUARANTINED, "INC", "confirmed")
        assert keyring.verify("nursing", artifact.signable_payload(), artifact.signature)

    def test_monotone_frontier(self):
        """Section 6.6: an artifact cannot silently return to active."""
        vault, _, _ = self._vault()
        artifact = vault.put(self._artifact())
        assert vault.set_state(artifact.key, MemoryState.QUARANTINED, "INC", "x")
        assert not vault.set_state(artifact.key, MemoryState.ACTIVE, "INC", "revert")
        assert artifact.state == MemoryState.QUARANTINED

    @pytest.mark.parametrize("current,target,allowed", [
        (MemoryState.ACTIVE, MemoryState.SUSPECTED, True),
        (MemoryState.SUSPECTED, MemoryState.QUARANTINED, True),
        (MemoryState.QUARANTINED, MemoryState.TOMBSTONED, True),
        (MemoryState.TOMBSTONED, MemoryState.ACTIVE, False),
        (MemoryState.REPAIRED, MemoryState.ACTIVE, False),
        (MemoryState.SUPERSEDED, MemoryState.SUSPECTED, False),
    ])
    def test_transition_matrix(self, current, target, allowed):
        assert state_transition_allowed(current, target) is allowed

    def test_non_servable_leaves_retrieval_index(self):
        vault, _, _ = self._vault()
        artifact = vault.put(self._artifact())
        assert len(vault.index) == 1
        vault.set_state(artifact.key, MemoryState.TOMBSTONED, "INC", "withdrawn")
        assert len(vault.index) == 0
        assert vault.servable() == []

    def test_ledger_is_append_only_for_versions(self):
        vault, ledger, _ = self._vault()
        vault.put(self._artifact(version=1))
        vault.put(self._artifact(version=2, content="rebuilt", supersedes="m1@v1"))
        history = ledger.version_history("m1")
        assert [h["version"] for h in history] == [1, 2]

    def test_public_projection_excludes_content(self):
        """Nothing coordinator-visible may carry raw content."""
        artifact = self._artifact()
        public = artifact.to_public_dict()
        assert "content" not in public
        assert "patient_scope" not in public
        assert artifact.content not in str(public)
