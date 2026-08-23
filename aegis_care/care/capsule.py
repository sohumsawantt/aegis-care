"""Scoped recovery capsules (proposal Section 5.4).

When a seed is revoked the coordinator does not broadcast its text. The owning
runtime mints a receiver-specific capsule whose entire contents are listed in
the table below. Patient names, raw MRNs, notes, laboratory values, and
hidden/KV states are structurally absent - there is no field to put them in.

    field                        security / privacy function
    seed_commitment              binds the incident to a version, not content
    recipient + purpose + expiry prevents general reuse of forensic evidence
    receiver-scoped patient token local matching, reduced cross-recipient linkage
    quantized latent sketch      candidate generation under missing lineage
    type/time band               prunes impossible relations without exact times
    signature + nonce            detects tampering and stale-capsule replay
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

from ..config import CapsuleConfig
from ..memory.models import ArtifactType, MemoryArtifact
from ..memory.sketch import SketchEncoder
from ..util.crypto import KeyRing, canonical_bytes, commit, receiver_scoped_token

#: The complete set of fields a capsule may ever contain. The privacy audit
#: asserts equality against this set, so adding a field is a deliberate act.
ALLOWED_CAPSULE_FIELDS = frozenset({
    "capsule_id", "incident_id", "seed_commitment", "recipient", "purpose",
    "issued_at", "expires_at", "nonce", "patient_token", "sketch",
    "artifact_type_band", "time_band", "issuer",
})

#: Fields that would constitute raw-content export. Never populated; the audit
#: checks for their absence.
FORBIDDEN_CAPSULE_FIELDS = frozenset({
    "content", "text", "note", "patient_name", "mrn", "birth_date",
    "observation_values", "hidden_state", "kv_cache", "embedding_raw",
})


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def time_band(timestamp: str, band_hours: int = 24) -> str:
    """Coarsen a timestamp so ordering survives but exact times do not leak."""
    try:
        parsed = _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "unknown"
    epoch = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    bucket = int((parsed - epoch).total_seconds() // (band_hours * 3600))
    return f"tb{bucket}"


def artifact_type_band(artifact_type: ArtifactType) -> str:
    """Coarse workflow stage rather than the exact artifact type."""
    if artifact_type in (ArtifactType.IDENTITY_HINT, ArtifactType.LOOKUP_STRATEGY):
        return "identity_stage"
    if artifact_type in (ArtifactType.HANDOVER, ArtifactType.OBSERVATION_SUMMARY):
        return "handover_stage"
    return "summary_stage"


@dataclass
class RecoveryCapsule:
    capsule_id: str
    incident_id: str
    seed_commitment: str
    recipient: str
    purpose: str
    issued_at: str
    expires_at: str
    nonce: str
    patient_token: str
    sketch: List[int]
    artifact_type_band: str
    time_band: str
    issuer: str
    signature: str = ""

    def signable(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("signature", None)
        return data

    def size_bytes(self) -> int:
        return len(canonical_bytes(asdict(self)))

    def released_fields(self) -> List[str]:
        return sorted(k for k, v in asdict(self).items()
                      if v not in (None, "", []) and k != "signature")

    def is_expired(self, at: Optional[_dt.datetime] = None) -> bool:
        at = at or _now()
        return at > _dt.datetime.fromisoformat(self.expires_at)


class CapsuleError(Exception):
    """Raised when a capsule fails verification."""


class CapsuleMinter:
    """Issues and verifies capsules; tracks replay nonces and query budgets."""

    def __init__(self, keyring: KeyRing, encoder: SketchEncoder,
                 config: Optional[CapsuleConfig] = None) -> None:
        self.keyring = keyring
        self.encoder = encoder
        self.config = config or CapsuleConfig()
        self._seen_nonces: Set[str] = set()
        self._query_budget: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def mint(self, seed: MemoryArtifact, *, incident_id: str, recipient: str,
             issuer: str, purpose: str = "incident_recovery",
             expiry_seconds: Optional[int] = None) -> RecoveryCapsule:
        """Mint a capsule for one recipient. The sketch is projected into the
        recipient's scope, so two recipients receive incomparable sketches."""
        issued = _now()
        expires = issued + _dt.timedelta(
            seconds=expiry_seconds or self.config.default_expiry_seconds)
        nonce = commit({"seed": seed.commitment(), "recipient": recipient,
                        "at": issued.isoformat()}, domain="nonce")[:32]

        incident_key = self.keyring.incident_key(incident_id)
        patient_token = (
            receiver_scoped_token(seed.patient_scope, recipient, incident_key)
            if seed.patient_scope else "pt_unscoped"
        )

        capsule = RecoveryCapsule(
            capsule_id=f"CAP-{nonce[:12]}",
            incident_id=incident_id,
            seed_commitment=seed.commitment(),
            recipient=recipient,
            purpose=purpose,
            issued_at=issued.isoformat(),
            expires_at=expires.isoformat(),
            nonce=nonce,
            patient_token=patient_token,
            sketch=self.encoder.sketch(seed.content, recipient=recipient,
                                       purpose=purpose, incident_id=incident_id),
            artifact_type_band=artifact_type_band(seed.artifact_type),
            time_band=time_band(seed.created_at),
            issuer=issuer,
        )
        capsule.signature = self.keyring.sign(issuer, capsule.signable())
        self._query_budget[capsule.capsule_id] = self.config.max_query_budget
        self._assert_no_raw_content(capsule)
        return capsule

    # ------------------------------------------------------------------
    def verify(self, capsule: RecoveryCapsule, *, expected_recipient: str,
               expected_purpose: str = "incident_recovery",
               at: Optional[_dt.datetime] = None) -> None:
        """Reject tampered, expired, wrong-purpose, wrong-recipient, or replayed
        capsules. Acceptance evidence for functional requirement F3."""
        if not self.keyring.verify(capsule.issuer, capsule.signable(), capsule.signature):
            raise CapsuleError("signature invalid or capsule tampered")
        if capsule.recipient != expected_recipient:
            raise CapsuleError(
                f"capsule addressed to {capsule.recipient}, not {expected_recipient}")
        if capsule.purpose != expected_purpose:
            raise CapsuleError(f"capsule purpose {capsule.purpose} != {expected_purpose}")
        if capsule.is_expired(at):
            raise CapsuleError("capsule expired")
        self._assert_no_raw_content(capsule)

    def consume_nonce(self, capsule: RecoveryCapsule) -> None:
        """Single-use acceptance: a second presentation of the same nonce is a
        replay of stale forensic evidence."""
        if capsule.nonce in self._seen_nonces:
            raise CapsuleError("capsule nonce already consumed (replay)")
        self._seen_nonces.add(capsule.nonce)

    def spend_query(self, capsule: RecoveryCapsule, n: int = 1) -> bool:
        """Query budget limits how much a recipient can probe with one capsule,
        which bounds the linkability attack surface (Section 14, sketch leakage)."""
        remaining = self._query_budget.get(capsule.capsule_id, 0)
        if remaining < n:
            return False
        self._query_budget[capsule.capsule_id] = remaining - n
        return True

    # ------------------------------------------------------------------
    @staticmethod
    def _assert_no_raw_content(capsule: RecoveryCapsule) -> None:
        keys = set(asdict(capsule).keys()) - {"signature"}
        extra = keys - ALLOWED_CAPSULE_FIELDS
        if extra:
            raise CapsuleError(f"capsule carries undeclared fields: {sorted(extra)}")
        leaked = keys & FORBIDDEN_CAPSULE_FIELDS
        if leaked:
            raise CapsuleError(f"capsule carries raw-content fields: {sorted(leaked)}")


@dataclass
class SignedVerdict:
    """What the coordinator receives back: a band and a disposition, never the
    original or counterfactual clinical text (Section 5.5.2)."""

    incident_id: str
    memory_commitment: str
    runtime: str
    influence_band: str          # none | low | medium | high
    influence_score: float
    predicate_changed: bool
    disposition: str             # retain | repair | quarantine
    evidence: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""

    def signable(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("signature", None)
        return data


def band_for(score: float, predicate_changed: bool) -> str:
    if predicate_changed:
        return "high"
    if score >= 0.60:
        return "high"
    if score >= 0.30:
        return "medium"
    if score >= 0.12:
        return "low"
    return "none"


__all__ = [
    "RecoveryCapsule", "CapsuleMinter", "CapsuleError", "SignedVerdict",
    "band_for", "time_band", "artifact_type_band",
    "ALLOWED_CAPSULE_FIELDS", "FORBIDDEN_CAPSULE_FIELDS",
]
