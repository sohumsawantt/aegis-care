"""The AEGIS-Care environment: three role-separated runtimes over one FHIR sandbox.

This module executes multi-session trajectories, records the *private
ground-truth graph* used only for scoring (Section 8.4), and exposes the
snapshot/restore needed so every recovery condition runs against paired,
identical state (Section 9.1 step 1).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .agents.model import ClinicalModel, get_model
from .agents.runtime import AgentMessage, AgentRuntime, DerivationResult
from .config import CONFIG, AegisConfig
from .fhir.store import FHIRStore
from .incident.tasks import CHAIN, build_task_manifest, purpose_for_role, select_tasks
from .memory.models import ArtifactType, MemoryArtifact, MemoryState
from .memory.sketch import SketchEncoder
from .memory.store import LedgerStore
from .policy.rbac import PolicyEngine, Role
from .util.crypto import KeyRing


@dataclass
class TrajectoryNode:
    """One artifact produced by one step of a trajectory."""

    key: str
    role: Role
    artifact_type: ArtifactType
    depth: int
    true_parent_keys: List[str] = field(default_factory=list)
    true_patient: Optional[str] = None
    intended_patient: Optional[str] = None
    contaminated: bool = False


@dataclass
class Trajectory:
    """A multi-session run of one task through the derivation chain."""

    trajectory_id: str
    task_id: str
    family: str
    intended_patient: str
    nodes: List[TrajectoryNode] = field(default_factory=list)
    is_contaminated: bool = False
    is_control: bool = False
    seed_key: Optional[str] = None

    def node_keys(self) -> List[str]:
        return [n.key for n in self.nodes]


class GroundTruthGraph:
    """Private instrumentation (Section 8.4).

    Exists only for scoring. No recovery code path may read it; the evaluation
    harness is the only consumer.
    """

    def __init__(self) -> None:
        self.edges: Set[Tuple[str, str]] = set()          # (parent_key, child_key)
        self.nodes: Dict[str, TrajectoryNode] = {}
        self.contaminated: Set[str] = set()
        self.seeds: Set[str] = set()

    def add_node(self, node: TrajectoryNode) -> None:
        self.nodes[node.key] = node
        for parent in node.true_parent_keys:
            self.edges.add((parent, node.key))
        if node.contaminated:
            self.contaminated.add(node.key)

    def descendants_of(self, seed_key: str) -> Set[str]:
        out: Set[str] = set()
        frontier = [seed_key]
        while frontier:
            cur = frontier.pop()
            for parent, child in self.edges:
                if parent == cur and child not in out:
                    out.add(child)
                    frontier.append(child)
        return out

    def true_contaminated_descendants(self, seed_key: str) -> Set[str]:
        """Descendants that are genuinely contaminated - the recall denominator."""
        return {k for k in self.descendants_of(seed_key) if k in self.contaminated}

    def stats(self) -> Dict[str, int]:
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "contaminated": len(self.contaminated), "seeds": len(self.seeds)}


class AegisEnvironment:
    """Wires the sandbox, policy, runtimes, and ledger into one system."""

    def __init__(
        self,
        config: Optional[AegisConfig] = None,
        model_spec: Optional[str] = None,
        ledger_path: Optional[Path] = None,
    ) -> None:
        self.config = config or CONFIG
        self.fhir = FHIRStore(self.config.n_patients, self.config.seed)
        self.policy = PolicyEngine()
        self.ledger = LedgerStore(ledger_path)
        self.keyring = KeyRing()
        self.encoder = SketchEncoder(self.config.sketch)
        self.model: ClinicalModel = get_model(model_spec)

        self.runtimes: Dict[Role, AgentRuntime] = {
            role: AgentRuntime(role, self.fhir, self.policy, self.ledger,
                               self.keyring, self.encoder, self.model)
            for role in (Role.REGISTRATION, Role.NURSING, Role.CLINICAL_SUMMARY)
        }

        self.tasks = select_tasks(
            build_task_manifest(self.fhir.patient_ids(), self.fhir),
            self.config.n_base_tasks,
        )
        self.truth = GroundTruthGraph()
        self.trajectories: Dict[str, Trajectory] = {}
        self._session_counter = 0

    # ==================================================================
    def runtime(self, role: Role) -> AgentRuntime:
        return self.runtimes[role]

    def find_artifact(self, key: str) -> Optional[MemoryArtifact]:
        for rt in self.runtimes.values():
            found = rt.vault.get(key)
            if found is not None:
                return found
        return None

    def artifact_by_commitment(self, commitment: str) -> Optional[MemoryArtifact]:
        for rt in self.runtimes.values():
            found = rt.vault.by_commitment(commitment)
            if found is not None:
                return found
        return None

    def all_artifacts(self) -> List[MemoryArtifact]:
        out: List[MemoryArtifact] = []
        for rt in self.runtimes.values():
            out.extend(rt.vault.all())
        return out

    # ==================================================================
    # Trajectory execution
    # ==================================================================
    def run_trajectory(
        self,
        task: Dict[str, Any],
        *,
        trajectory_id: Optional[str] = None,
        depth: Optional[int] = None,
        forced_seed_patient: Optional[str] = None,
        seed_depth: int = 0,
        launder_restricted: bool = False,
        is_control: bool = False,
        record_truth: bool = True,
    ) -> Trajectory:
        """Execute one task across the derivation chain.

        `forced_seed_patient` injects the wrong record association at
        `seed_depth`, which is how every contamination family plants its seed:
        family F1 seeds at depth 0 (a registration alias), F2 at the handover
        step (a wrong-chart copied fact), and so on.

        `launder_restricted` additionally pulls physician-only fields into the
        seed's content, which is the access-scope laundering family F3.
        """
        self._session_counter += 1
        session_id = f"S{self._session_counter:04d}"
        depth = depth if depth is not None else task.get("depth", 4)
        tid = trajectory_id or f"TR-{task['task_id']}-{session_id}"
        intended = task["patient_id"]

        traj = Trajectory(trajectory_id=tid, task_id=task["task_id"], family=task["family"],
                          intended_patient=intended, is_control=is_control)

        message: Optional[AgentMessage] = None
        prev_key: Optional[str] = None

        for step, (role, artifact_type) in enumerate(CHAIN[: depth + 1]):
            rt = self.runtimes[role]
            step_task = dict(task)
            step_task["purpose"] = purpose_for_role(role, task)
            poisoning_here = forced_seed_patient is not None and step == seed_depth

            if poisoning_here and step == 0:
                # Poisoned seed: the alias asserts the wrong record for this query.
                result = self._forge_identity_hint(rt, step_task, forced_seed_patient)
            elif poisoning_here:
                # Contamination enters mid-chain: the step receives a context
                # item naming the wrong record, exactly as it would if an
                # upstream lookup had been mis-associated.
                poisoned_message = AgentMessage(
                    from_role=CHAIN[step - 1][0], to_role=role,
                    patient_id=forced_seed_patient,
                    patient_display=self.fhir.patient_display(forced_seed_patient),
                    mrn=self.fhir.patient_mrn(forced_seed_patient),
                    source_commitment=message.source_commitment if message else None,
                    route="poisoned_context",
                )
                result = rt.derive(artifact_type, step_task, poisoned_message)
                result.route = "poisoned_context"
            else:
                result = rt.derive(artifact_type, step_task, message)

            if launder_restricted and step == seed_depth:
                result = self._launder_restricted(rt, result)

            memory_id = f"{tid}-{artifact_type.value}"
            artifact = rt.write_memory(
                memory_id, artifact_type, result, step_task,
                session_id=session_id,
                true_parents=list(result.parent_commitments),
            )

            resolved = artifact.structured_facts.get("patient_id")
            node = TrajectoryNode(
                key=artifact.key,
                role=role,
                artifact_type=artifact_type,
                depth=step,
                true_parent_keys=[prev_key] if prev_key else [],
                true_patient=resolved,
                intended_patient=intended,
                contaminated=bool(resolved is not None and resolved != intended),
            )
            traj.nodes.append(node)
            if record_truth:
                self.truth.add_node(node)

            if poisoning_here or (launder_restricted and step == seed_depth):
                traj.seed_key = artifact.key
                traj.is_contaminated = True
                node.contaminated = True
                if record_truth:
                    self.truth.seeds.add(artifact.key)
                    self.truth.contaminated.add(artifact.key)

            # Hand context to the next role in the chain.
            if resolved is not None:
                payload: Dict[str, Any] = {}
                if artifact.structured_facts.get("laundered_restricted"):
                    # Copy-forward carries the restricted fragment onward. It
                    # travels on the message, so withholding the message during
                    # replay removes it.
                    carried = artifact.content.split(
                        "Carried-forward screening detail: ")[-1].rstrip(".")
                    payload["carried_forward"] = carried
                    payload["restricted_ids"] = artifact.structured_facts.get(
                        "restricted_ids", [])
                message = AgentMessage(
                    from_role=role,
                    to_role=CHAIN[step + 1][0] if step + 1 < len(CHAIN) else role,
                    patient_id=resolved,
                    patient_display=self.fhir.patient_display(resolved),
                    mrn=self.fhir.patient_mrn(resolved),
                    source_commitment=artifact.commitment(),
                    payload=payload,
                )
            prev_key = artifact.key

        traj.is_contaminated = traj.is_contaminated or any(n.contaminated for n in traj.nodes)
        self.trajectories[tid] = traj
        self.ledger.log_event(None, "environment", "trajectory_complete", tid,
                              {"task": task["task_id"], "nodes": len(traj.nodes),
                               "contaminated": traj.is_contaminated})
        return traj

    def _forge_identity_hint(self, rt: AgentRuntime, task: Dict[str, Any],
                             wrong_patient: str) -> DerivationResult:
        """Write the alias that associates the queried patient with a wrong record.

        This is the only place contamination is introduced, and it uses the
        agent's ordinary write path - which is the point: the poisoned write is
        indistinguishable from a legitimate one at write time.
        """
        query = task["query"]
        patient = self.fhir.read("Patient", wrong_patient)
        ctx = {
            "query_text": query["query_text"],
            "patient_id": wrong_patient,
            "patient_display": self.fhir.patient_display(wrong_patient),
            "mrn": self.fhir.patient_mrn(wrong_patient),
            "birth_date": patient.get("birthDate") if patient else None,
            "route": "memory_hint",
        }
        rt.counters["model_calls"] += 1
        return DerivationResult(
            content=rt.model.compose_identity(ctx),
            structured_facts={
                "patient_id": wrong_patient,
                "mrn": ctx["mrn"],
                "query_key": query["query_key"],
                "route": "memory_hint",
                "resolved": True,
            },
            parent_commitments=[],
            route="poisoned_alias",
            model_calls=1,
        )

    def _launder_restricted(self, rt: AgentRuntime,
                            result: DerivationResult) -> DerivationResult:
        """Family F3: a physician-only field reaches a memory whose owner has no
        rights over it.

        The policy engine blocks *direct* reads of restricted resources, so the
        only way this can happen is through a memory path - which is precisely
        the laundering mechanism the proposal describes ("restricted source ->
        authorized summary -> shared memory").
        """
        pid = result.structured_facts.get("patient_id")
        if not pid:
            return result
        restricted = [o for o in self.fhir.observations_for(pid, restricted_ok=True)
                      if o.get("_aegisRestricted")]
        if not restricted:
            return result
        fragments = [
            f"{o['code']['text']} {o['valueQuantity']['value']} {o['valueQuantity']['unit']}"
            for o in restricted
        ]
        result.content += "\nCarried-forward screening detail: " + "; ".join(fragments) + "."
        result.structured_facts = dict(result.structured_facts)
        result.structured_facts["restricted_ids"] = [o["id"] for o in restricted]
        result.structured_facts["laundered_restricted"] = True
        return result

    # ==================================================================
    # Follow-up probes used to score recovery (Section 9.1 step 6)
    # ==================================================================
    def run_followup_task(self, task: Dict[str, Any], depth: int = 4) -> Dict[str, Any]:
        """Run a clean follow-up task *without writing memory*, and report which
        patient the system would act on now."""
        message: Optional[AgentMessage] = None
        selected: Optional[str] = None
        route = "none"
        for step, (role, artifact_type) in enumerate(CHAIN[: depth + 1]):
            rt = self.runtimes[role]
            step_task = dict(task)
            step_task["purpose"] = purpose_for_role(role, task)
            result = rt.derive(artifact_type, step_task, message)
            selected = result.structured_facts.get("patient_id")
            route = result.route
            if selected is None:
                break
            message = AgentMessage(
                from_role=role,
                to_role=CHAIN[step + 1][0] if step + 1 < len(CHAIN) else role,
                patient_id=selected,
                patient_display=self.fhir.patient_display(selected),
                mrn=self.fhir.patient_mrn(selected),
                source_commitment=None,
            )
        return {
            "task_id": task["task_id"],
            "intended_patient": task["patient_id"],
            "selected_patient": selected,
            "correct": selected == task["patient_id"],
            "route": route,
        }

    # ==================================================================
    # Snapshots
    # ==================================================================
    def snapshot(self) -> Dict[str, Any]:
        return {
            "fhir": self.fhir.snapshot(),
            "runtimes": {role.value: rt.snapshot() for role, rt in self.runtimes.items()},
            "trajectories": copy.deepcopy(self.trajectories),
            "session_counter": self._session_counter,
        }

    def restore(self, snap: Dict[str, Any]) -> None:
        self.fhir.restore(snap["fhir"])
        for role, rt in self.runtimes.items():
            rt.restore(snap["runtimes"][role.value])
        self.trajectories = copy.deepcopy(snap["trajectories"])
        self._session_counter = snap["session_counter"]

    # ==================================================================
    def overhead(self) -> Dict[str, int]:
        total: Dict[str, int] = {}
        for rt in self.runtimes.values():
            for k, v in rt.counters.items():
                total[k] = total.get(k, 0) + v
        return total

    def reset_counters(self) -> None:
        for rt in self.runtimes.values():
            for k in rt.counters:
                rt.counters[k] = 0

    def stats(self) -> Dict[str, Any]:
        return {
            "fhir": self.fhir.stats(),
            "tasks": len(self.tasks),
            "trajectories": len(self.trajectories),
            "memory": {r.value: rt.vault.stats() for r, rt in self.runtimes.items()},
            "truth": self.truth.stats(),
        }


__all__ = ["AegisEnvironment", "Trajectory", "TrajectoryNode", "GroundTruthGraph"]
