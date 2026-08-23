"""The recovery coordinator: the CARE loop end to end.

Implements the algorithm sketch of Section 6.5:

  1. Mark each seed non-servable and issue signed, receiver-scoped capsules.
  2. In each local runtime, collect exact descendants and latent candidates.
  3. For every non-exact candidate, replay locally without the suspected influence.
  4. If influence is not confirmed, retain the memory and record the negative verdict.
  5. If confirmed and trusted support is sufficient, rebuild and verify a new version.
  6. If confirmed but reconstruction is unsafe or impossible, quarantine and escalate.
  7. Treat each confirmed descendant as a new frontier node; repeat until closure.
  8. Commit tombstones, repaired versions, signed verdicts, and revocation fingerprints.
  9. Run clean follow-up tasks plus resurrection probes before returning to service.

The coordinator is honest-but-curious by assumption and holds no clinical read
rights (see PolicyEngine): everything it learns arrives as commitments, counts,
bands, and signed verdicts.
"""
from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..config import AegisConfig, CONFIG
from ..memory.models import ArtifactType, MemoryArtifact, MemoryState
from ..policy.rbac import Role
from .attribution import AttributionEngine, InfluenceReport
from .candidate import Candidate, CandidateDiscoverer
from .capsule import CapsuleMinter, RecoveryCapsule, SignedVerdict
from .certificate import RecoveryCertificate, build_certificate
from .enforcement import ResurrectionFirewall
from .recompile import Recompiler, RepairOutcome


@dataclass
class CAREOptions:
    """Ablation switches (Section 9.2, "critical ablations")."""

    use_sketch: bool = True              # remove latent sketch
    use_explicit_lineage: bool = True    # explicit-lineage-only comparison
    use_counterfactual: bool = True      # remove counterfactual replay
    use_recompilation: bool = True       # quarantine instead of recompile
    use_enforcement: bool = True         # remove resurrection firewall
    use_scoping: bool = True             # remove purpose/recipient scoping
    max_rounds: int = 8

    def label(self) -> str:
        flags = [
            ("sketch", self.use_sketch), ("lineage", self.use_explicit_lineage),
            ("counterfactual", self.use_counterfactual), ("recompile", self.use_recompilation),
            ("enforce", self.use_enforcement), ("scope", self.use_scoping),
        ]
        return "+".join(n for n, on in flags if on) or "none"


@dataclass
class RecoveryResult:
    incident_id: str
    seeds: List[str]
    candidates_considered: List[Dict[str, Any]] = field(default_factory=list)
    confirmed: List[str] = field(default_factory=list)
    cleared: List[str] = field(default_factory=list)
    repaired: List[Dict[str, Any]] = field(default_factory=list)
    quarantined: List[Dict[str, Any]] = field(default_factory=list)
    verdicts: List[SignedVerdict] = field(default_factory=list)
    capsules: List[RecoveryCapsule] = field(default_factory=list)
    rounds: int = 0
    closure_reached: bool = False
    enforcement: Dict[str, Any] = field(default_factory=dict)
    resurrection_probe: Dict[str, Any] = field(default_factory=dict)
    overhead: Dict[str, Any] = field(default_factory=dict)
    certificate: Optional[RecoveryCertificate] = None
    options: Dict[str, Any] = field(default_factory=dict)

    def touched_destructively(self) -> Set[str]:
        """Everything acted on destructively: the precision denominator."""
        return ({r["memory_key"] for r in self.repaired}
                | {q["memory_key"] for q in self.quarantined})


class RecoveryCoordinator:
    """Orchestrates recovery without ever holding patient content."""

    def __init__(self, env, config: Optional[AegisConfig] = None) -> None:
        self.env = env
        self.config = config or CONFIG
        self.minter = CapsuleMinter(env.keyring, env.encoder, self.config.capsule)
        self.discoverer = CandidateDiscoverer(env.encoder, self.config.candidate)
        self.attributor = AttributionEngine(env.encoder, env.keyring, self.config.influence)
        self.recompiler = Recompiler(self.config.repair)
        self.firewall = ResurrectionFirewall(env.encoder, env.keyring)

    # ==================================================================
    def recover(
        self,
        incident_id: str,
        seed_keys: Sequence[str],
        *,
        options: Optional[CAREOptions] = None,
        followup_tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> RecoveryResult:
        options = options or CAREOptions()
        started = time.perf_counter()
        env = self.env
        ledger = env.ledger

        seeds: List[Tuple[Any, MemoryArtifact]] = []
        for key in seed_keys:
            artifact = env.find_artifact(key)
            if artifact is None:
                raise ValueError(f"unknown seed {key}")
            seeds.append((env.runtime(artifact.owner), artifact))

        result = RecoveryResult(incident_id=incident_id, seeds=list(seed_keys),
                                options=asdict(options))
        ledger.log_event(incident_id, "coordinator", "recovery_started", None,
                         {"seeds": list(seed_keys), "options": options.label()})

        # --- step 1: barrier. Seeds become non-servable immediately. ------
        for runtime, seed in seeds:
            runtime.vault.set_state(seed.key, MemoryState.SUSPECTED, incident_id,
                                    "confirmed compromised seed")
            runtime.vault.index.remove(seed.key)

        known_bad: Set[str] = {s.commitment() for _, s in seeds}
        incident_key = env.keyring.incident_key(incident_id)

        # --- steps 2-7: frontier loop to closure --------------------------
        frontier: List[Tuple[Any, MemoryArtifact]] = list(seeds)
        processed: Set[str] = {s.key for _, s in seeds}
        confirmed_pairs: List[Tuple[Any, MemoryArtifact]] = []
        rounds = 0

        while frontier and rounds < options.max_rounds:
            rounds += 1
            next_frontier: List[Tuple[Any, MemoryArtifact]] = []

            for _, frontier_artifact in frontier:
                capsules = self._issue_capsules(
                    incident_id, frontier_artifact, options, ledger)
                result.capsules.extend(capsules)

                for capsule in capsules:
                    recipient_role = Role(capsule.recipient)
                    runtime = env.runtime(recipient_role)
                    try:
                        self.minter.verify(capsule, expected_recipient=capsule.recipient)
                    except Exception as exc:  # capsule rejected -> no discovery
                        ledger.log_event(incident_id, capsule.recipient,
                                         "capsule_rejected", None, {"error": str(exc)})
                        continue

                    candidates = self.discoverer.discover(
                        runtime, capsule,
                        incident_key=incident_key,
                        known_bad_commitments=known_bad,
                        use_sketch=options.use_sketch,
                        use_explicit=options.use_explicit_lineage,
                        already_seen=processed,
                    )
                    if not self.minter.spend_query(capsule, len(candidates)):
                        ledger.log_event(incident_id, capsule.recipient,
                                         "query_budget_exhausted", None, {})

                    for cand in candidates:
                        if cand.memory_key in processed:
                            continue
                        processed.add(cand.memory_key)
                        result.candidates_considered.append({
                            **cand.to_public_dict(), "memory_key": cand.memory_key,
                        })

                        artifact = runtime.vault.get(cand.memory_key)
                        if artifact is None:
                            continue

                        decision, report = self._attribute(
                            runtime, artifact, known_bad, options, cand)

                        if not decision:
                            result.cleared.append(artifact.key)
                            verdict = self.attributor.sign_verdict(
                                runtime, incident_id, artifact, report, "retain")
                            result.verdicts.append(verdict)
                            ledger.record_verdict(
                                incident_id, artifact.key, runtime.role.value,
                                verdict.influence_band, verdict.influence_score,
                                "retain", verdict.signature)
                            continue

                        result.confirmed.append(artifact.key)
                        confirmed_pairs.append((runtime, artifact))
                        known_bad.add(artifact.commitment())
                        runtime.vault.set_state(artifact.key, MemoryState.SUSPECTED,
                                                incident_id, "influence confirmed")
                        next_frontier.append((runtime, artifact))

                        verdict = self.attributor.sign_verdict(
                            runtime, incident_id, artifact, report,
                            "repair" if options.use_recompilation else "quarantine")
                        result.verdicts.append(verdict)
                        ledger.record_verdict(
                            incident_id, artifact.key, runtime.role.value,
                            verdict.influence_band, verdict.influence_score,
                            verdict.disposition, verdict.signature)

            frontier = next_frontier

        result.rounds = rounds
        result.closure_reached = not frontier

        # --- step 5/6: repair or quarantine, deepest artifacts first -------
        # Depth ordering matters: a handover must be repaired before the summary
        # that derives from it, so the summary rebuilds on clean support.
        for runtime, artifact in self._order_for_repair(confirmed_pairs):
            task = self._task_for(artifact)
            if options.use_recompilation:
                outcome = self.recompiler.recompile(
                    runtime, artifact, task, incident_id, known_bad,
                    message=self._clean_message_for(runtime, artifact, task, known_bad))
            else:
                runtime.vault.set_state(artifact.key, MemoryState.QUARANTINED,
                                        incident_id, "quarantine-only mode")
                outcome = RepairOutcome(memory_key=artifact.key, action="quarantined",
                                        reason="recompilation disabled")
            record = {"memory_key": outcome.memory_key, "new_key": outcome.new_key,
                      "confidence": round(outcome.confidence, 3), "reason": outcome.reason,
                      "checks": outcome.checks}
            (result.repaired if outcome.action == "repaired" else
             result.quarantined).append(record)

        # --- step 8: tombstones + revocation fingerprints ------------------
        withdrawn: List[Tuple[Any, MemoryArtifact]] = list(seeds)
        for runtime, artifact in confirmed_pairs:
            current = runtime.vault.get(artifact.key)
            if current is not None:
                withdrawn.append((runtime, current))

        enforcement = self.firewall.enforce(
            env.runtimes, incident_id, withdrawn, ledger,
            enabled=options.use_enforcement)
        result.enforcement = {
            "tombstones": len(enforcement.tombstones),
            "revoked_commitments": len(enforcement.revoked_commitments),
            "revoked_sketches": enforcement.revoked_sketches,
            "enabled": options.use_enforcement,
        }

        # --- step 9: resurrection probes ----------------------------------
        result.resurrection_probe = self.firewall.probe(
            env, incident_id, [s for _, s in seeds], followup_tasks or [])

        # --- certificate ---------------------------------------------------
        elapsed = time.perf_counter() - started
        result.overhead = {
            **env.overhead(),
            "wall_seconds": round(elapsed, 4),
            "capsules": len(result.capsules),
            "capsule_bytes": sum(c.size_bytes() for c in result.capsules),
            "rounds": result.rounds,
        }
        result.certificate = build_certificate(incident_id, result, env, options)
        ledger.log_event(incident_id, "coordinator", "recovery_complete", None,
                         {"confirmed": len(result.confirmed),
                          "repaired": len(result.repaired),
                          "quarantined": len(result.quarantined),
                          "closure": result.closure_reached})
        return result

    # ==================================================================
    def _issue_capsules(self, incident_id: str, seed: MemoryArtifact,
                        options: CAREOptions, ledger) -> List[RecoveryCapsule]:
        """One capsule per recipient runtime, each in its own scope."""
        capsules = []
        for role in (Role.REGISTRATION, Role.NURSING, Role.CLINICAL_SUMMARY):
            purpose = "incident_recovery"
            capsule = self.minter.mint(
                seed, incident_id=incident_id, recipient=role.value,
                issuer="coordinator", purpose=purpose)
            if not options.use_scoping:
                # Ablation: unscoped capsules reuse a single global projection,
                # which is what makes cross-recipient linkage possible again.
                capsule.sketch = self.env.encoder.sketch(
                    seed.content, recipient="global", purpose="global",
                    incident_id="global")
                capsule.patient_token = f"pt_raw_{seed.patient_scope}"
                capsule.signature = self.env.keyring.sign("coordinator", capsule.signable())
            capsules.append(capsule)
            ledger.record_capsule(incident_id, role.value, seed.commitment(),
                                  capsule.size_bytes(), capsule.released_fields())
        return capsules

    def _attribute(self, runtime, artifact: MemoryArtifact, known_bad: Set[str],
                   options: CAREOptions, cand: Candidate):
        """Confirm or clear a candidate."""
        task = self._task_for(artifact)
        if not options.use_counterfactual:
            # Sketch-only ablation: every candidate is treated as causal, which
            # is exactly the failure mode Section 9.2 asks us to quantify.
            report = InfluenceReport(
                memory_key=artifact.key, influence_score=cand.score,
                semantic_delta=1.0 - cand.similarity, patient_change=0.0,
                resource_change=0.0, predicate_changed=False, confirmed=True,
                original_patient=artifact.patient_scope, counterfactual_patient=None,
                replay_available=False, detail={"mode": "sketch_only"},
            )
            return True, report

        if cand.explicit:
            # An exact lineage edge bypasses the similarity threshold, but we
            # still replay so the verdict carries real evidence.
            report = self.attributor.assess(runtime, artifact, task, known_bad)
            return True, report

        report = self.attributor.assess(runtime, artifact, task, known_bad)
        return report.confirmed, report

    # ------------------------------------------------------------------
    def _task_for(self, artifact: MemoryArtifact) -> Dict[str, Any]:
        """Recover the originating task definition from the pinned recipe."""
        recipe = artifact.replay_recipe
        base = None
        if recipe is not None:
            base = next((t for t in self.env.tasks if t["task_id"] == recipe.task_id), None)
        task = dict(base) if base else {
            "task_id": recipe.task_id if recipe else "unknown",
            "kind": recipe.task_kind if recipe else "unknown",
            "query": {}, "label": "recovered task",
        }
        task["purpose"] = artifact.purpose
        return task

    def _clean_message_for(self, runtime, artifact: MemoryArtifact,
                           task: Dict[str, Any], known_bad: Set[str]):
        """Supply a repaired upstream context if one now exists.

        This is how repair propagates: once a handover has been rebuilt, the
        summary that derives from it rebuilds against the repaired version
        rather than falling all the way back to a bare FHIR lookup.
        """
        from ..agents.runtime import AgentMessage

        best: Optional[MemoryArtifact] = None
        for other in self.env.all_artifacts():
            if other.state != MemoryState.REPAIRED:
                continue
            if other.commitment() in known_bad:
                continue
            if other.memory_id.rsplit("-", 1)[0] != artifact.memory_id.rsplit("-", 1)[0]:
                continue
            if other.key == artifact.key:
                continue
            if best is None or other.created_at > best.created_at:
                best = other
        if best is None or not best.structured_facts.get("patient_id"):
            return None
        pid = best.structured_facts["patient_id"]
        return AgentMessage(
            from_role=best.owner, to_role=runtime.role, patient_id=pid,
            patient_display=self.env.fhir.patient_display(pid),
            mrn=self.env.fhir.patient_mrn(pid),
            source_commitment=best.commitment(), route="repaired_upstream",
        )

    @staticmethod
    def _order_for_repair(pairs: List[Tuple[Any, MemoryArtifact]]):
        """Shallowest first, so parents are rebuilt before their children."""
        stage_rank = {
            ArtifactType.IDENTITY_HINT: 0,
            ArtifactType.LOOKUP_STRATEGY: 1,
            ArtifactType.HANDOVER: 2,
            ArtifactType.OBSERVATION_SUMMARY: 2,
            ArtifactType.CLINICAL_SUMMARY: 3,
            ArtifactType.AGGREGATE: 4,
        }
        return sorted(pairs, key=lambda ra: (stage_rank.get(ra[1].artifact_type, 9),
                                             ra[1].key))


__all__ = ["RecoveryCoordinator", "CAREOptions", "RecoveryResult"]
