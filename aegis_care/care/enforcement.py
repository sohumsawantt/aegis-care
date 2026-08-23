"""CARE stage E: version enforcement and resurrection prevention.

Proposal Section 5.5.4. Superseded and poisoned versions remain as signed
tombstones in the audit log but are excluded from retrieval. Future writes and
retrievals are checked against revocation commitments and candidate sketches.

"This converts recovery from a one-time cleanup into an enforced state
transition."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..memory.models import MemoryArtifact, MemoryState
from ..memory.sketch import SketchEncoder
from ..util.crypto import KeyRing

#: Similarity at or above which a new write is treated as reintroducing
#: withdrawn influence. Deliberately high: the firewall must not block ordinary
#: clinical writes that merely mention the same patient.
RESURRECTION_SIMILARITY = 0.93


@dataclass
class Tombstone:
    memory_key: str
    incident_id: str
    commitment: str
    reason: str
    signature: str = ""

    def signable(self) -> Dict[str, Any]:
        return {"memory_key": self.memory_key, "incident_id": self.incident_id,
                "commitment": self.commitment, "reason": self.reason}


@dataclass
class EnforcementReport:
    tombstones: List[Tombstone] = field(default_factory=list)
    revoked_commitments: Set[str] = field(default_factory=set)
    revoked_sketches: int = 0
    blocked_writes: List[Dict[str, Any]] = field(default_factory=list)
    probes_run: int = 0
    probes_blocked: int = 0


class ResurrectionFirewall:
    """Installs revocation state into every runtime and runs re-entry probes."""

    def __init__(self, encoder: SketchEncoder, keyring: KeyRing,
                 similarity_threshold: float = RESURRECTION_SIMILARITY) -> None:
        self.encoder = encoder
        self.keyring = keyring
        self.threshold = similarity_threshold

    # ------------------------------------------------------------------
    def enforce(
        self,
        runtimes: Dict[Any, Any],
        incident_id: str,
        withdrawn: List[Tuple[Any, MemoryArtifact]],
        ledger,
        *,
        enabled: bool = True,
    ) -> EnforcementReport:
        """Tombstone every withdrawn version and arm the firewall.

        `withdrawn` is a list of (runtime, artifact) pairs covering seeds,
        confirmed contaminated descendants, and superseded versions.
        """
        report = EnforcementReport()
        revoked: Set[str] = set()
        sketches: List[Tuple[List[int], float]] = []

        for runtime, artifact in withdrawn:
            commitment = artifact.commitment()
            revoked.add(commitment)

            tomb = Tombstone(
                memory_key=artifact.key, incident_id=incident_id,
                commitment=commitment,
                reason=artifact.quarantine_reason or "withdrawn during recovery",
            )
            tomb.signature = self.keyring.sign("coordinator", tomb.signable())
            ledger.add_tombstone(tomb.memory_key, incident_id, commitment,
                                 tomb.reason, tomb.signature)
            report.tombstones.append(tomb)

            # Ensure the artifact is genuinely non-servable.
            if artifact.state not in (MemoryState.SUPERSEDED, MemoryState.QUARANTINED,
                                      MemoryState.TOMBSTONED):
                runtime.vault.set_state(artifact.key, MemoryState.TOMBSTONED,
                                        incident_id, "withdrawn during recovery")
            else:
                runtime.vault.index.remove(artifact.key)

            # Revocation fingerprint for content-level re-entry detection.
            sketches.append((
                self.encoder.local_sketch(artifact.content, runtime.role.value),
                self.threshold,
            ))

        if enabled:
            for runtime in runtimes.values():
                # Each runtime gets fingerprints projected into its own scope.
                own = [
                    (self.encoder.local_sketch(a.content, runtime.role.value), self.threshold)
                    for _, a in withdrawn
                ]
                runtime.install_revocations(revoked, own)

        report.revoked_commitments = revoked
        report.revoked_sketches = len(sketches)
        ledger.log_event(incident_id, "coordinator", "enforcement_armed", None,
                         {"tombstones": len(report.tombstones),
                          "revoked_commitments": len(revoked), "enabled": enabled})
        return report

    # ------------------------------------------------------------------
    def probe(self, env, incident_id: str, seed_artifacts: List[MemoryArtifact],
              tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Resurrection probes (Section 9.1 step 6).

        Attempts to reintroduce withdrawn influence by re-writing the seed's own
        content through the ordinary write path. A correctly armed firewall
        blocks every attempt.
        """
        attempts, blocked = 0, 0
        details: List[Dict[str, Any]] = []

        for seed in seed_artifacts:
            runtime = env.runtime(seed.owner)
            attempts += 1
            probe_artifact = MemoryArtifact(
                memory_id=f"probe-{seed.memory_id}",
                version=1,
                owner=seed.owner,
                artifact_type=seed.artifact_type,
                content=seed.content,
                explicit_parent_commitments=[seed.commitment()],
                patient_scope=seed.patient_scope,
                purpose=seed.purpose,
                structured_facts=dict(seed.structured_facts),
            )
            verdict = runtime.firewall_check(probe_artifact)
            if verdict is not None:
                blocked += 1
            details.append({
                "seed": seed.key,
                "blocked": verdict is not None,
                "reason": (verdict or {}).get("reason", "not_blocked"),
            })

            # Second probe: same influence, laundered through paraphrase so the
            # explicit ancestor edge is gone and only the sketch can catch it.
            attempts += 1
            laundered = MemoryArtifact(
                memory_id=f"probe-launder-{seed.memory_id}",
                version=1,
                owner=seed.owner,
                artifact_type=seed.artifact_type,
                content=seed.content.replace("Resolution route", "Route"),
                explicit_parent_commitments=[],
                patient_scope=seed.patient_scope,
                purpose=seed.purpose,
                structured_facts=dict(seed.structured_facts),
            )
            verdict2 = runtime.firewall_check(laundered)
            if verdict2 is not None:
                blocked += 1
            details.append({
                "seed": seed.key,
                "probe": "laundered",
                "blocked": verdict2 is not None,
                "reason": (verdict2 or {}).get("reason", "not_blocked"),
            })

        return {
            "attempts": attempts,
            "blocked": blocked,
            "resurrection_rate": round(1.0 - (blocked / attempts), 4) if attempts else 0.0,
            "details": details,
        }


__all__ = ["ResurrectionFirewall", "Tombstone", "EnforcementReport", "RESURRECTION_SIMILARITY"]
