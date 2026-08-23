"""Role-local agent runtimes.

Each runtime owns one role's vault, its sketch index, its FHIR access (filtered
by policy), and - critically - its own replay engine. Section 6.6 requires that
"all content-dependent matching, replay, and verification occurs in the owning
runtime", so every method here is designed to be callable *only* by its owner.

The derivation functions are deliberately written so that a memory can be
rebuilt from either
  (a) an upstream memory / inter-agent message  (the normal, poisonable path)
  (b) trusted FHIR resources                    (the clean-room path)
which is exactly what makes counterfactual replay and recompilation possible.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..config import CONFIG
from ..fhir.store import FHIRStore
from ..memory.models import ArtifactType, MemoryArtifact, MemoryState, ReplayRecipe
from ..memory.sketch import SketchEncoder
from ..memory.store import LedgerStore, MemoryVault
from ..policy.rbac import (
    FieldCategory,
    Operation,
    PolicyEngine,
    Role,
    categorize_resource,
)
from ..util.crypto import KeyRing, commit, commit_text
from .model import PROMPT_VERSION, TOOL_SCHEMA_VERSION, ClinicalModel, DeterministicClinicalModel

# Which FHIR observation codes each task family pulls.
VITAL_CODES = ["8867-4", "8480-6", "8462-4", "8310-5", "2708-6"]
LAB_CODES = ["2160-0", "2345-7", "6690-2", "718-7", "2951-2"]

#: A memory write is authorised against the field category the artifact carries,
#: not a blanket "note" right. This is what keeps the registration desk from
#: writing clinical material at all.
ARTIFACT_CATEGORY = {
    ArtifactType.IDENTITY_HINT: FieldCategory.IDENTITY,
    ArtifactType.LOOKUP_STRATEGY: FieldCategory.IDENTITY,
    ArtifactType.HANDOVER: FieldCategory.VITALS,
    ArtifactType.OBSERVATION_SUMMARY: FieldCategory.VITALS,
    ArtifactType.AGGREGATE: FieldCategory.LABORATORY,
    ArtifactType.CLINICAL_SUMMARY: FieldCategory.NOTE,
    ArtifactType.WORKFLOW_NOTE: FieldCategory.NOTE,
    ArtifactType.PROCEDURE: FieldCategory.NOTE,
}


@dataclass
class AgentMessage:
    """Context handed from one role to the next.

    `source_commitment` is the lineage edge. If provenance masking drops it, the
    receiving runtime still consumed the content but no longer has the explicit
    edge - which is precisely the incomplete-provenance condition under study.
    """

    from_role: Role
    to_role: Role
    patient_id: str
    patient_display: str
    mrn: str
    source_commitment: Optional[str]
    route: str = "upstream_agent"
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DerivationResult:
    content: str
    structured_facts: Dict[str, Any]
    parent_commitments: List[str]
    route: str
    fhir_reads: int = 0
    model_calls: int = 0


class AgentRuntime:
    """One role-separated clinical agent."""

    def __init__(
        self,
        role: Role,
        fhir: FHIRStore,
        policy: PolicyEngine,
        ledger: LedgerStore,
        keyring: KeyRing,
        encoder: SketchEncoder,
        model: Optional[ClinicalModel] = None,
    ) -> None:
        self.role = role
        self.fhir = fhir
        self.policy = policy
        self.ledger = ledger
        self.keyring = keyring
        self.encoder = encoder
        self.model = model or DeterministicClinicalModel()
        self.vault = MemoryVault(role, encoder, ledger, keyring)

        # Enforcement state (Section 5.5.4). Populated by the firewall.
        self.revoked_commitments: Set[str] = set()
        self.revoked_sketches: List[Tuple[List[int], float]] = []
        self.blocked_writes: List[Dict[str, Any]] = []

        # Overhead accounting (Section 10, "Overhead").
        self.counters: Dict[str, int] = {
            "fhir_reads": 0, "model_calls": 0, "replays": 0, "writes": 0,
        }

    # ==================================================================
    # Policy-checked FHIR access
    # ==================================================================
    def _read_patient(self, patient_id: str, purpose: str) -> Optional[Dict[str, Any]]:
        decision = self.policy.check(self.role, Operation.READ, FieldCategory.IDENTITY,
                                     purpose=purpose, patient_id=patient_id)
        if not decision:
            return None
        self.counters["fhir_reads"] += 1
        return self.fhir.read("Patient", patient_id)

    def _search_patients(self, purpose: str, **params: Any) -> List[Dict[str, Any]]:
        decision = self.policy.check(self.role, Operation.SEARCH, FieldCategory.IDENTITY,
                                     purpose=purpose)
        if not decision:
            return []
        self.counters["fhir_reads"] += 1
        return self.fhir.search("Patient", **params)

    def _read_observations(self, patient_id: str, codes: Sequence[str],
                           purpose: str) -> List[Dict[str, Any]]:
        self.counters["fhir_reads"] += 1
        obs = self.fhir.search("Observation", patient=patient_id, code=",".join(codes))
        # Policy filtering is what prevents a role from ever holding a field it
        # has no rights over. Access-scope laundering must therefore come from
        # a *memory* path, not a direct read.
        return self.policy.filter_resources(self.role, obs, purpose=purpose)

    def _read_conditions(self, patient_id: str, purpose: str) -> List[Dict[str, Any]]:
        self.counters["fhir_reads"] += 1
        conds = self.fhir.search("Condition", patient=patient_id)
        return self.policy.filter_resources(self.role, conds, purpose=purpose)

    # ==================================================================
    # Identity resolution: the poisonable step
    # ==================================================================
    def _matching_identity_hint(self, query_key: str,
                                exclude: Set[str]) -> Optional[MemoryArtifact]:
        """Reuse of a stored identity hint is the memory shortcut that a
        poisoned alias hijacks (Section 1.2 / MPBench "explicit write" channel)."""
        candidates = [
            a for a in self.vault.servable_of_type(ArtifactType.IDENTITY_HINT)
            if a.structured_facts.get("query_key") == query_key
            and a.commitment() not in exclude
            and a.commitment() not in self.revoked_commitments
        ]
        if not candidates:
            return None
        # Most recent hint wins, matching a real cache's behaviour.
        return sorted(candidates, key=lambda a: (a.created_at, a.key))[-1]

    def _matching_cached(self, artifact_type: ArtifactType, cache_key: str,
                         exclude: Set[str]) -> Optional[MemoryArtifact]:
        """Find a servable derived memory that this task would reuse instead of
        recomputing.

        This is the mechanism that makes descendants *harmful* rather than
        merely present: a stale handover or summary is not inert, it is a
        retrieval cue that later sessions consume. Deleting only the seed leaves
        these alive, which is precisely the gap in Section 1.2.

        `exclude` may hold commitments or memory ids; a replay of an artifact
        always blocks its own memory id so it can never justify itself.
        """
        out = [
            a for a in self.vault.servable_of_type(artifact_type)
            if a.structured_facts.get("cache_key") == cache_key
            and a.commitment() not in exclude
            and a.memory_id not in exclude
            and a.commitment() not in self.revoked_commitments
        ]
        if not out:
            return None
        return sorted(out, key=lambda a: (a.created_at, a.version, a.key))[-1]

    @staticmethod
    def cache_key_for(task: Dict[str, Any]) -> str:
        return f"{task.get('task_id', 'unknown')}"

    def _reuse_cached(self, artifact_type: ArtifactType, task: Dict[str, Any],
                      exclude: Set[str]) -> Optional[DerivationResult]:
        """Return a derivation that simply reuses a cached descendant, if one
        is servable for this task."""
        cached = self._matching_cached(artifact_type, self.cache_key_for(task), exclude)
        if cached is None:
            return None
        facts = dict(cached.structured_facts)
        return DerivationResult(
            content=cached.content,
            structured_facts=facts,
            parent_commitments=[cached.commitment()],
            route="memory_cache",
        )

    def resolve_identity(self, query: Dict[str, Any], purpose: str,
                         exclude: Optional[Set[str]] = None) -> DerivationResult:
        exclude = exclude or set()
        query_key = query["query_key"]
        query_text = query["query_text"]

        hint = self._matching_identity_hint(query_key, exclude)
        parents: List[str] = []
        if hint is not None:
            patient_id = hint.structured_facts["patient_id"]
            route = "memory_hint"
            parents.append(hint.commitment())
        else:
            # Clean-room path: trusted FHIR lookup.
            params = {k: v for k, v in query.items()
                      if k in ("identifier", "family", "given", "birthdate")}
            matches = self._search_patients(purpose, **params)
            if not matches:
                return DerivationResult(
                    content=f"No patient matched query '{query_text}'.",
                    structured_facts={"patient_id": None, "query_key": query_key,
                                      "route": "fhir_lookup", "resolved": False},
                    parent_commitments=[], route="fhir_lookup",
                )
            patient_id = matches[0]["id"]
            route = "fhir_lookup"

        patient = self._read_patient(patient_id, purpose)
        if patient is None:
            return DerivationResult(
                content=f"Identity resolution blocked by policy for '{query_text}'.",
                structured_facts={"patient_id": None, "query_key": query_key,
                                  "route": "policy_denied", "resolved": False},
                parent_commitments=parents, route="policy_denied",
            )

        ctx = {
            "query_text": query_text,
            "patient_id": patient_id,
            "patient_display": self.fhir.patient_display(patient_id),
            "mrn": self.fhir.patient_mrn(patient_id),
            "birth_date": patient.get("birthDate"),
            "route": route,
        }
        self.counters["model_calls"] += 1
        return DerivationResult(
            content=self.model.compose_identity(ctx),
            structured_facts={
                "patient_id": patient_id,
                "mrn": ctx["mrn"],
                "query_key": query_key,
                "route": route,
                "resolved": True,
            },
            parent_commitments=parents,
            route=route,
            model_calls=1,
        )

    # ==================================================================
    # Derivations for the remaining artifact types
    # ==================================================================
    def _patient_from_message(
        self, message: Optional[AgentMessage], query: Dict[str, Any],
        purpose: str, exclude: Set[str],
    ) -> Tuple[Optional[str], str, List[str], bool]:
        """Take the patient from upstream context unless that context is
        excluded/revoked, in which case fall back to a trusted FHIR resolution.

        This single helper is what makes every downstream artifact both
        poisonable and clean-room rebuildable. The final element reports whether
        the upstream context was actually used, which decides whether
        copy-forward material travels with it."""
        usable = (
            message is not None
            and (message.source_commitment is None
                 or (message.source_commitment not in exclude
                     and message.source_commitment not in self.revoked_commitments))
        )
        if usable and message is not None:
            parents = [message.source_commitment] if message.source_commitment else []
            return message.patient_id, "upstream_agent", parents, True

        resolution = self.resolve_identity(query, purpose, exclude=exclude)
        return resolution.structured_facts.get("patient_id"), "fhir_rebuild", [], False

    @staticmethod
    def _carry_forward(result: "DerivationResult", message: Optional[AgentMessage],
                       used_upstream: bool) -> "DerivationResult":
        """Propagate copied-forward material from upstream context.

        Copy-forward is a documented clinical failure mode (Joint Commission
        copy-and-paste guidance, reference [3]) and it is how an access-scope
        violation launders itself downstream. Because it rides on the *message*,
        withholding that message during replay removes it - which is what lets
        counterfactual replay detect it and recompilation drop it."""
        if not used_upstream or message is None:
            return result
        carried = message.payload.get("carried_forward")
        if not carried:
            return result
        result.content += "\nCarried-forward screening detail: " + carried + "."
        result.structured_facts = dict(result.structured_facts)
        result.structured_facts["restricted_ids"] = list(
            message.payload.get("restricted_ids", []))
        result.structured_facts["laundered_restricted"] = True
        return result

    def derive_lookup_cue(self, task: Dict[str, Any], message: Optional[AgentMessage],
                          exclude: Optional[Set[str]] = None) -> DerivationResult:
        exclude = exclude or set()
        purpose = task["purpose"]
        cached = self._reuse_cached(ArtifactType.LOOKUP_STRATEGY, task, exclude)
        if cached is not None:
            return cached
        pid, route, parents, used = self._patient_from_message(
            message, task["query"], purpose, exclude)
        if pid is None:
            return DerivationResult("Unresolvable lookup cue.",
                                    {"patient_id": None, "resolved": False}, parents, route)
        ctx = {
            "task_label": task["label"],
            "patient_id": pid,
            "patient_display": self.fhir.patient_display(pid),
            "mrn": self.fhir.patient_mrn(pid),
            "route": route,
        }
        self.counters["model_calls"] += 1
        derived = DerivationResult(
            content=self.model.compose_lookup_cue(ctx),
            structured_facts={"patient_id": pid, "mrn": ctx["mrn"],
                              "task_label": task["label"], "resolved": True},
            parent_commitments=parents, route=route, model_calls=1,
        )
        return self._carry_forward(derived, message, used)

    def derive_handover(self, task: Dict[str, Any], message: Optional[AgentMessage],
                        exclude: Optional[Set[str]] = None) -> DerivationResult:
        exclude = exclude or set()
        purpose = task["purpose"]
        cached = self._reuse_cached(ArtifactType.HANDOVER, task, exclude)
        if cached is not None:
            return cached
        pid, route, parents, used = self._patient_from_message(
            message, task["query"], purpose, exclude)
        if pid is None:
            return DerivationResult("Unresolvable handover.",
                                    {"patient_id": None, "resolved": False}, parents, route)
        codes = task.get("codes", VITAL_CODES)
        observations = self._read_observations(pid, codes, purpose)
        ctx = {
            "patient_id": pid,
            "patient_display": self.fhir.patient_display(pid),
            "mrn": self.fhir.patient_mrn(pid),
            "observations": observations,
            "encounter_class": task.get("encounter_class", "ambulatory"),
            "outstanding": task.get("outstanding", "routine monitoring"),
        }
        self.counters["model_calls"] += 1
        derived = DerivationResult(
            content=self.model.compose_handover(ctx),
            structured_facts={
                "patient_id": pid,
                "mrn": ctx["mrn"],
                "observation_ids": [o["id"] for o in observations],
                "values": {o["code"]["coding"][0]["code"]: o["valueQuantity"]["value"]
                           for o in observations},
                "resolved": True,
            },
            parent_commitments=parents, route=route, model_calls=1,
        )
        return self._carry_forward(derived, message, used)

    def derive_summary(self, task: Dict[str, Any], message: Optional[AgentMessage],
                       exclude: Optional[Set[str]] = None) -> DerivationResult:
        exclude = exclude or set()
        purpose = task["purpose"]
        cached = self._reuse_cached(ArtifactType.CLINICAL_SUMMARY, task, exclude)
        if cached is not None:
            return cached
        pid, route, parents, used = self._patient_from_message(
            message, task["query"], purpose, exclude)
        if pid is None:
            return DerivationResult("Unresolvable summary.",
                                    {"patient_id": None, "resolved": False}, parents, route)
        observations = self._read_observations(pid, task.get("codes", LAB_CODES), purpose)
        conditions = self._read_conditions(pid, purpose)
        ctx = {
            "patient_id": pid,
            "patient_display": self.fhir.patient_display(pid),
            "mrn": self.fhir.patient_mrn(pid),
            "observations": observations,
            "conditions": [c["code"]["text"] for c in conditions],
            "purpose_label": task.get("purpose_label", "authorized care overview"),
        }
        self.counters["model_calls"] += 1
        derived = DerivationResult(
            content=self.model.compose_summary(ctx),
            structured_facts={
                "patient_id": pid,
                "mrn": ctx["mrn"],
                "observation_ids": [o["id"] for o in observations],
                "condition_ids": [c["id"] for c in conditions],
                # The summary asserts these measurements, so they belong in the
                # structured facts a replay can be compared against. Without
                # them a stale-value descendant looks unchanged.
                "values": {o["code"]["coding"][0]["code"]: o["valueQuantity"]["value"]
                           for o in observations},
                "resolved": True,
            },
            parent_commitments=parents, route=route, model_calls=1,
        )
        return self._carry_forward(derived, message, used)

    def derive_aggregate(self, task: Dict[str, Any], message: Optional[AgentMessage],
                         exclude: Optional[Set[str]] = None) -> DerivationResult:
        exclude = exclude or set()
        purpose = task["purpose"]
        cached = self._reuse_cached(ArtifactType.AGGREGATE, task, exclude)
        if cached is not None:
            return cached
        pid, route, parents, used = self._patient_from_message(
            message, task["query"], purpose, exclude)
        if pid is None:
            return DerivationResult("Unresolvable aggregate.",
                                    {"patient_id": None, "resolved": False}, parents, route)
        code = task.get("metric_code", "2345-7")
        observations = self._read_observations(pid, [code], purpose)
        values = [o["valueQuantity"]["value"] for o in observations]
        mean = round(sum(values) / len(values), 2) if values else None
        unit = observations[0]["valueQuantity"]["unit"] if observations else ""
        ctx = {
            "patient_id": pid,
            "patient_display": self.fhir.patient_display(pid),
            "metric": task.get("metric_label", "mean value"),
            "value": mean, "n": len(values), "unit": unit,
        }
        self.counters["model_calls"] += 1
        derived = DerivationResult(
            content=self.model.compose_aggregate(ctx),
            structured_facts={"patient_id": pid, "metric_code": code, "value": mean,
                              "n": len(values), "resolved": True},
            parent_commitments=parents, route=route, model_calls=1,
        )
        return self._carry_forward(derived, message, used)

    # ==================================================================
    # Dispatch
    # ==================================================================
    DERIVERS = {
        ArtifactType.LOOKUP_STRATEGY: "derive_lookup_cue",
        ArtifactType.HANDOVER: "derive_handover",
        ArtifactType.CLINICAL_SUMMARY: "derive_summary",
        ArtifactType.OBSERVATION_SUMMARY: "derive_handover",
        ArtifactType.AGGREGATE: "derive_aggregate",
    }

    def derive(self, artifact_type: ArtifactType, task: Dict[str, Any],
               message: Optional[AgentMessage] = None,
               exclude: Optional[Set[str]] = None) -> DerivationResult:
        if artifact_type == ArtifactType.IDENTITY_HINT:
            return self.resolve_identity(task["query"], task["purpose"], exclude=exclude)
        method = self.DERIVERS.get(artifact_type)
        if method is None:
            raise ValueError(f"no deriver for {artifact_type}")
        return getattr(self, method)(task, message, exclude)

    # ==================================================================
    # Writing memory
    # ==================================================================
    def write_memory(
        self,
        memory_id: str,
        artifact_type: ArtifactType,
        result: DerivationResult,
        task: Dict[str, Any],
        *,
        session_id: str = "",
        true_parents: Optional[List[str]] = None,
        version: Optional[int] = None,
        supersedes: Optional[str] = None,
        state: MemoryState = MemoryState.ACTIVE,
    ) -> MemoryArtifact:
        """Persist a derived artifact with its replay recipe.

        Passes through the resurrection firewall first (Section 5.5.4)."""
        category = ARTIFACT_CATEGORY.get(artifact_type, FieldCategory.NOTE)
        decision = self.policy.check(self.role, Operation.WRITE_MEMORY, category,
                                     purpose=task["purpose"],
                                     patient_id=result.structured_facts.get("patient_id"))
        if not decision:
            raise PermissionError(f"{self.role.value} may not write memory: {decision.reason}")

        recipe = ReplayRecipe(
            task_id=task["task_id"],
            task_kind=task["kind"],
            model_name=self.model.name,
            prompt_version=PROMPT_VERSION,
            tool_schema_version=TOOL_SCHEMA_VERSION,
            input_commitments={
                "query": commit(task["query"], domain="task-query"),
                "parents": commit(sorted(result.parent_commitments), domain="parents"),
            },
            task_predicate={
                "selected_patient": result.structured_facts.get("patient_id"),
                "resource_ids": sorted(result.structured_facts.get("observation_ids", [])
                                       + result.structured_facts.get("condition_ids", [])),
            },
            parameters={k: task[k] for k in ("codes", "metric_code", "label", "purpose")
                        if k in task},
        )

        facts = dict(result.structured_facts)
        facts.setdefault("cache_key", self.cache_key_for(task))

        artifact = MemoryArtifact(
            memory_id=memory_id,
            version=version if version is not None else self.vault.next_version(memory_id),
            owner=self.role,
            artifact_type=artifact_type,
            content=result.content,
            explicit_parent_commitments=list(result.parent_commitments),
            _true_parents=list(true_parents if true_parents is not None
                               else result.parent_commitments),
            patient_scope=result.structured_facts.get("patient_id"),
            role_scope=self.role,
            purpose=task["purpose"],
            replay_recipe=recipe,
            structured_facts=facts,
            session_id=session_id,
            state=state,
            supersedes=supersedes,
        )

        blocked = self.firewall_check(artifact)
        if blocked is not None:
            self.blocked_writes.append(blocked)
            self.ledger.log_event(blocked.get("incident_id"), self.role.value,
                                  "write_blocked_by_firewall", artifact.key, blocked)
            artifact.state = MemoryState.QUARANTINED
            artifact.quarantine_reason = blocked["reason"]

        self.counters["writes"] += 1
        return self.vault.put(artifact)

    # ==================================================================
    # Replay (Section 5.5.2 / 5.5.3)
    # ==================================================================
    def replay(self, artifact: MemoryArtifact, task: Dict[str, Any],
               message: Optional[AgentMessage] = None,
               exclude: Optional[Set[str]] = None) -> DerivationResult:
        """Recompute an artifact under its pinned recipe.

        `exclude` is the set of parent commitments treated as unavailable. With
        `exclude=set()` this reproduces the artifact; with the suspected seed in
        `exclude` it produces the counterfactual."""
        self.counters["replays"] += 1
        recipe = artifact.replay_recipe
        if recipe is None:
            raise ValueError(f"{artifact.key} has no replay recipe")
        replay_task = dict(task)
        replay_task.update(recipe.parameters)
        replay_task.setdefault("task_id", recipe.task_id)
        replay_task.setdefault("kind", recipe.task_kind)
        # Block this artifact's own memory id: a replay that reused the very
        # version under test would always look uninfluenced.
        blocked = set(exclude or set()) | {artifact.memory_id, artifact.commitment()}
        return self.derive(artifact.artifact_type, replay_task, message, blocked)

    # ==================================================================
    # Resurrection firewall (Section 5.5.4)
    # ==================================================================
    def install_revocations(self, commitments: Set[str],
                            sketches: Sequence[Tuple[List[int], float]]) -> None:
        self.revoked_commitments |= set(commitments)
        self.revoked_sketches.extend(sketches)

    def firewall_check(self, artifact: MemoryArtifact) -> Optional[Dict[str, Any]]:
        """Block a write that reintroduces withdrawn influence.

        Two triggers: citing a revoked ancestor directly, or matching a revoked
        sketch above threshold while carrying the same patient association."""
        for parent in artifact.explicit_parent_commitments:
            if parent in self.revoked_commitments:
                return {"reason": "cites_revoked_ancestor", "parent": parent}
        if artifact.write_context_sketch is None:
            probe = self.encoder.local_sketch(artifact.content, self.role.value)
        else:
            probe = artifact.write_context_sketch
        for sketch, threshold in self.revoked_sketches:
            sim = self.encoder.similarity(probe, sketch)
            if sim >= threshold:
                return {"reason": "matches_revoked_sketch", "similarity": round(sim, 4),
                        "threshold": threshold}
        return None

    def clear_revocations(self) -> None:
        self.revoked_commitments.clear()
        self.revoked_sketches.clear()
        self.blocked_writes.clear()

    # ==================================================================
    def snapshot(self) -> Dict[str, Any]:
        return {
            "vault": self.vault.snapshot(),
            "revoked_commitments": set(self.revoked_commitments),
            "revoked_sketches": list(self.revoked_sketches),
            "counters": dict(self.counters),
        }

    def restore(self, snap: Dict[str, Any]) -> None:
        self.vault.restore(snap["vault"])
        self.revoked_commitments = set(snap["revoked_commitments"])
        self.revoked_sketches = list(snap["revoked_sketches"])
        self.counters = dict(snap["counters"])
        self.blocked_writes = []


__all__ = ["AgentRuntime", "AgentMessage", "DerivationResult", "VITAL_CODES", "LAB_CODES"]
