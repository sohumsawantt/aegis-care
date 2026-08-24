"""FastAPI application: the live AEGIS-Care prototype.

Exposes the FHIR sandbox, the three role-separated agent runtimes, incident
construction, the CARE recovery loop, human review (requirement F10), recovery
certificates (F9), the privacy audit, and the experiment runner - plus the
reviewer dashboard.

Role separation is enforced at the API boundary too: the coordinator endpoints
never return memory content, and content endpoints require the owning role.
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..care.coordinator import CAREOptions, RecoveryCoordinator
from ..config import CONFIG, PROJECT_ROOT, RESULTS_DIR
from ..environment import AegisEnvironment
from ..eval.baselines import CONDITION_INFO, BaselineRunner
from ..eval.metrics import MetricsEvaluator
from ..eval.privacy import PrivacyAuditor
from ..eval.report import build_report, make_figures
from ..eval.runner import ExperimentRunner
from ..incident.masks import ProvenanceMask
from ..incident.scenarios import FAMILIES, FAMILY_INFO, Incident, ScenarioBuilder
from ..memory.models import MemoryState
from .demo import session as demo_session
from .ops_routes import router as ops_router
from ..policy.rbac import ROLE_FIELD_MATRIX, Role

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class AppState:
    """Holds the live environment and everything derived from it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.env = AegisEnvironment()
        self.builder = ScenarioBuilder(self.env)
        self.incidents: Dict[str, Incident] = {}
        self.recoveries: Dict[str, Any] = {}
        self.snapshots: Dict[str, Any] = {}
        self.masks: Dict[str, Any] = {}
        self.experiment: Optional[Dict[str, Any]] = None
        self.experiment_status = "idle"
        self.experiment_log: List[str] = []

    def reset(self) -> None:
        self.env = AegisEnvironment()
        self.builder = ScenarioBuilder(self.env)
        self.incidents.clear()
        self.recoveries.clear()
        self.snapshots.clear()
        self.masks.clear()


state = AppState()

app = FastAPI(
    title="AEGIS-Care",
    version="4.0",
    description="A privacy-bounded memory recompiler for recovering poisoned "
                "clinical AI agents. Simulated FHIR sandbox only - not for "
                "clinical use.",
)


# ======================================================================
# Models
# ======================================================================
class IncidentRequest(BaseModel):
    family: str = Field("F1", description="F1 | F2 | F3 | F4")
    task_id: Optional[str] = None
    depth: int = Field(4, ge=1, le=4)
    n_controls: int = Field(1, ge=0, le=3)
    provenance: str = Field("complete",
                            description="complete | random20 | random40 | random60 | targeted")


class RecoveryRequest(BaseModel):
    incident_id: str
    use_sketch: bool = True
    use_explicit_lineage: bool = True
    use_counterfactual: bool = True
    use_recompilation: bool = True
    use_enforcement: bool = True
    use_scoping: bool = True


class BaselineRequest(BaseModel):
    incident_id: str
    conditions: List[str] = Field(default_factory=lambda: list(CONDITION_INFO.keys()))


class ReviewRequest(BaseModel):
    memory_key: str
    decision: str = Field(..., description="approve | reject | keep_quarantined")
    reviewer: str = "reviewer"
    note: str = ""


class ExperimentRequest(BaseModel):
    families: List[str] = Field(default_factory=lambda: list(FAMILIES))
    depths: List[int] = Field(default_factory=lambda: [2, 3, 4])
    provenance_conditions: List[str] = Field(
        default_factory=lambda: ["complete", "random40", "targeted"])
    conditions: List[str] = Field(default_factory=lambda: list(CONDITION_INFO.keys()))
    tasks_per_family: int = 2
    n_controls: int = 1


# ======================================================================
# System
# ======================================================================
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": app.version,
            "model": state.env.model.name, "config_seed": CONFIG.seed}


@app.get("/api/system")
def system() -> Dict[str, Any]:
    env = state.env
    return {
        "stats": env.stats(),
        "roles": {
            role.value: {
                "fields": sorted(f.value for f in ROLE_FIELD_MATRIX[role]),
                "memory": env.runtime(role).vault.stats() if role in env.runtimes else {},
            }
            for role in (Role.REGISTRATION, Role.NURSING, Role.CLINICAL_SUMMARY,
                         Role.COORDINATOR)
        },
        "families": FAMILY_INFO,
        "conditions": {k: {"name": v[0], "purpose": v[1]}
                       for k, v in CONDITION_INFO.items()},
        "provenance_conditions": list(ProvenanceMask.CONDITIONS),
        "tasks": [{"task_id": t["task_id"], "family": t["family"],
                   "label": t["label"], "patient_id": t["patient_id"],
                   "depth": t["depth"]} for t in env.tasks],
        "sketch": {"dim": CONFIG.sketch.sketch_dim, "bits": CONFIG.sketch.quant_bits,
                   "bytes": env.encoder.bytes_per_sketch()},
    }


@app.post("/api/system/reset")
def reset_system() -> Dict[str, Any]:
    with state.lock:
        state.reset()
    return {"status": "reset", "stats": state.env.stats()}


# ======================================================================
# FHIR sandbox
# ======================================================================
@app.get("/fhir/{resource_type}/{resource_id}")
def fhir_read(resource_type: str, resource_id: str) -> Dict[str, Any]:
    found = state.env.fhir.read(resource_type, resource_id)
    if found is None:
        raise HTTPException(404, f"{resource_type}/{resource_id} not found")
    return found


@app.get("/fhir/{resource_type}")
def fhir_search(resource_type: str, identifier: Optional[str] = None,
                family: Optional[str] = None, given: Optional[str] = None,
                birthdate: Optional[str] = None, patient: Optional[str] = None,
                code: Optional[str] = None, category: Optional[str] = None) -> Dict[str, Any]:
    params = {k: v for k, v in {
        "identifier": identifier, "family": family, "given": given,
        "birthdate": birthdate, "patient": patient, "code": code,
        "category": category}.items() if v}
    results = state.env.fhir.search(resource_type, **params)
    return state.env.fhir.bundle(resource_type, results)


# ======================================================================
# Memory
# ======================================================================
@app.get("/api/memory")
def list_memory(role: Optional[str] = None, state_filter: Optional[str] = None,
                include_content: bool = True) -> Dict[str, Any]:
    """Memory listing. `include_content=false` gives the coordinator-safe view."""
    env = state.env
    roles = [Role(role)] if role else list(env.runtimes.keys())
    out: List[Dict[str, Any]] = []
    for r in roles:
        if r not in env.runtimes:
            continue
        for artifact in env.runtime(r).vault.all():
            if state_filter and artifact.state.value != state_filter:
                continue
            out.append(artifact.to_local_dict() if include_content
                       else artifact.to_public_dict())
    out.sort(key=lambda a: (a["owner"], a["memory_id"], a["version"]))
    return {"count": len(out), "memories": out}


@app.get("/api/memory/{memory_key}")
def get_memory(memory_key: str) -> Dict[str, Any]:
    artifact = state.env.find_artifact(memory_key)
    if artifact is None:
        raise HTTPException(404, f"memory {memory_key} not found")
    data = artifact.to_local_dict()
    data["history"] = state.env.ledger.version_history(artifact.memory_id)
    return data


@app.get("/api/memory/{memory_key}/graph")
def memory_graph(memory_key: str) -> Dict[str, Any]:
    """Derivation graph around one artifact, for the dashboard visualiser."""
    env = state.env
    nodes, edges = [], []
    for artifact in env.all_artifacts():
        nodes.append({
            "key": artifact.key, "type": artifact.artifact_type.value,
            "owner": artifact.owner.value, "state": artifact.state.value,
            "patient": artifact.patient_scope,
            "focus": artifact.key == memory_key,
        })
        for parent in artifact.explicit_parent_commitments:
            parent_artifact = env.artifact_by_commitment(parent)
            if parent_artifact is not None:
                edges.append({"from": parent_artifact.key, "to": artifact.key,
                              "observed": True})
    # Ground-truth edges the operational system cannot see (masked).
    observed = {(e["from"], e["to"]) for e in edges}
    for parent, child in env.truth.edges:
        if (parent, child) not in observed:
            edges.append({"from": parent, "to": child, "observed": False})
    return {"nodes": nodes, "edges": edges}


# ======================================================================
# Incidents
# ======================================================================
@app.post("/api/incidents")
def create_incident(req: IncidentRequest) -> Dict[str, Any]:
    if req.family not in FAMILIES:
        raise HTTPException(400, f"unknown family {req.family}")
    env = state.env
    with state.lock:
        task = next((t for t in env.tasks if t["task_id"] == req.task_id), None) \
            if req.task_id else env.tasks[0]
        if task is None:
            raise HTTPException(400, f"unknown task {req.task_id}")
        seed_depth = FAMILY_INFO[req.family]["seed_depth"]
        if req.depth < seed_depth + 1:
            raise HTTPException(
                400, f"family {req.family} seeds at depth {seed_depth}; "
                     f"requires depth >= {seed_depth + 1}")

        incident = state.builder.build(req.family, task, depth=req.depth,
                                       n_controls=req.n_controls)
        mask = ProvenanceMask(env, CONFIG.seed).apply(req.provenance)
        state.incidents[incident.incident_id] = incident
        state.snapshots[incident.incident_id] = env.snapshot()
        state.masks[incident.incident_id] = mask

    return _incident_payload(incident, mask)


@app.get("/api/incidents")
def list_incidents() -> Dict[str, Any]:
    return {"incidents": [
        _incident_payload(inc, state.masks.get(inc.incident_id))
        for inc in state.incidents.values()]}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str) -> Dict[str, Any]:
    incident = state.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, f"incident {incident_id} not found")
    return _incident_payload(incident, state.masks.get(incident_id), detailed=True)


def _incident_payload(incident: Incident, mask=None,
                      detailed: bool = False) -> Dict[str, Any]:
    env = state.env
    payload: Dict[str, Any] = {
        "incident_id": incident.incident_id,
        "family": incident.family,
        "family_info": FAMILY_INFO[incident.family],
        "task_id": incident.task["task_id"],
        "task_label": incident.task["label"],
        "intended_patient": incident.task["patient_id"],
        "wrong_patient": incident.wrong_patient,
        "depth": incident.depth,
        "seed_key": incident.seed_key,
        "true_contaminated": sorted(incident.true_contaminated),
        "clean_keys": sorted(incident.clean_keys),
        "recovered": incident.incident_id in state.recoveries,
    }
    if mask is not None:
        payload["provenance"] = {
            "condition": mask.condition,
            "edges_before": mask.edges_before,
            "edges_removed": mask.edges_removed,
            "loss_fraction": round(mask.loss_fraction, 3),
            "description": ProvenanceMask.describe(mask.condition),
        }
    if detailed:
        payload["trajectory"] = [
            {"key": n.key, "depth": n.depth, "role": n.role.value,
             "type": n.artifact_type.value, "patient": n.true_patient,
             "contaminated": n.contaminated,
             "state": (env.find_artifact(n.key).state.value
                       if env.find_artifact(n.key) else "missing")}
            for n in incident.contaminated.nodes
        ]
        payload["controls"] = [
            {"trajectory_id": c.trajectory_id,
             "nodes": [{"key": n.key, "type": n.artifact_type.value,
                        "patient": n.true_patient,
                        "state": (env.find_artifact(n.key).state.value
                                  if env.find_artifact(n.key) else "missing")}
                       for n in c.nodes]}
            for c in incident.controls
        ]
    return payload


# ======================================================================
# Recovery
# ======================================================================
@app.post("/api/recover")
def recover(req: RecoveryRequest) -> Dict[str, Any]:
    incident = state.incidents.get(req.incident_id)
    if incident is None:
        raise HTTPException(404, f"incident {req.incident_id} not found")

    options = CAREOptions(
        use_sketch=req.use_sketch,
        use_explicit_lineage=req.use_explicit_lineage,
        use_counterfactual=req.use_counterfactual,
        use_recompilation=req.use_recompilation,
        use_enforcement=req.use_enforcement,
        use_scoping=req.use_scoping,
    )
    env = state.env
    with state.lock:
        coordinator = RecoveryCoordinator(env)
        result = coordinator.recover(incident.incident_id, [incident.seed_key],
                                     options=options, followup_tasks=[incident.task])
        state.recoveries[incident.incident_id] = result

        evaluator = MetricsEvaluator(env)
        from ..eval.baselines import ConditionOutcome
        repaired = {r["memory_key"] for r in result.repaired}
        quarantined = {q["memory_key"] for q in result.quarantined}
        outcome = ConditionOutcome(
            condition="I", incident_id=incident.incident_id,
            withdrawn={incident.seed_key} | repaired | quarantined,
            repaired=repaired, touched=repaired | quarantined | {incident.seed_key},
            cleared=set(result.cleared), overhead=dict(result.overhead),
            certificate=result.certificate, recovery=result)
        metrics = evaluator.evaluate(outcome, incident,
                                     provenance=state.masks[incident.incident_id].condition
                                     if incident.incident_id in state.masks else "complete",
                                     followup_tasks=[incident.task])

    return {
        "incident_id": incident.incident_id,
        "options": asdict(options),
        "rounds": result.rounds,
        "closure_reached": result.closure_reached,
        "candidates": result.candidates_considered,
        "confirmed": result.confirmed,
        "cleared": result.cleared,
        "repaired": result.repaired,
        "quarantined": result.quarantined,
        "verdicts": [v.signable() | {"signature": v.signature[:24] + "..."}
                     for v in result.verdicts],
        "capsules": [
            {**{k: v for k, v in asdict(c).items() if k != "sketch"},
             "sketch_dim": len(c.sketch), "size_bytes": c.size_bytes(),
             "signature": c.signature[:24] + "..."}
            for c in result.capsules[:6]
        ],
        "enforcement": result.enforcement,
        "resurrection_probe": result.resurrection_probe,
        "overhead": result.overhead,
        "certificate": asdict(result.certificate) if result.certificate else None,
        "certificate_text": result.certificate.to_text() if result.certificate else "",
        "metrics": metrics.to_row(),
    }


@app.get("/api/recover/{incident_id}/certificate", response_class=PlainTextResponse)
def certificate_text(incident_id: str) -> str:
    result = state.recoveries.get(incident_id)
    if result is None or result.certificate is None:
        raise HTTPException(404, f"no certificate for {incident_id}")
    return result.certificate.to_text()


@app.post("/api/baselines")
def run_baselines(req: BaselineRequest) -> Dict[str, Any]:
    incident = state.incidents.get(req.incident_id)
    if incident is None:
        raise HTTPException(404, f"incident {req.incident_id} not found")
    snapshot = state.snapshots.get(req.incident_id)
    if snapshot is None:
        raise HTTPException(400, "no frozen snapshot for this incident")

    env = state.env
    provenance = (state.masks[req.incident_id].condition
                  if req.incident_id in state.masks else "complete")
    rows = []
    with state.lock:
        evaluator = MetricsEvaluator(env)
        for condition in req.conditions:
            env.restore(snapshot)
            try:
                outcome = BaselineRunner(env).run(condition, incident,
                                                  followup_tasks=[incident.task])
            except Exception as exc:
                rows.append({"condition": condition, "error": str(exc)})
                continue
            metrics = evaluator.evaluate(outcome, incident, provenance=provenance,
                                         followup_tasks=[incident.task])
            row = metrics.to_row()
            row["name"] = CONDITION_INFO[condition][0]
            row["purpose"] = CONDITION_INFO[condition][1]
            rows.append(row)
        env.restore(snapshot)
    return {"incident_id": req.incident_id, "provenance": provenance, "results": rows}


# ======================================================================
# Human review (requirement F10)
# ======================================================================
@app.get("/api/review/queue")
def review_queue() -> Dict[str, Any]:
    env = state.env
    items = []
    for runtime in env.runtimes.values():
        for artifact in runtime.vault.all():
            if artifact.state == MemoryState.QUARANTINED:
                items.append({
                    **artifact.to_local_dict(),
                    "quarantine_reason": artifact.quarantine_reason,
                })
    return {"count": len(items), "items": items}


@app.post("/api/review")
def review(req: ReviewRequest) -> Dict[str, Any]:
    env = state.env
    artifact = env.find_artifact(req.memory_key)
    if artifact is None:
        raise HTTPException(404, f"memory {req.memory_key} not found")
    if req.decision not in ("approve", "reject", "keep_quarantined"):
        raise HTTPException(400, f"unknown decision {req.decision}")

    runtime = env.runtime(artifact.owner)
    if req.decision == "reject":
        runtime.vault.set_state(artifact.key, MemoryState.TOMBSTONED,
                                "human_review", f"rejected by {req.reviewer}: {req.note}")
        result = "tombstoned"
    elif req.decision == "approve":
        # Safe resume requires follow-up validation, so an approved artifact is
        # republished as a repaired version rather than silently reactivated.
        runtime.vault.set_state(artifact.key, MemoryState.REPAIRED,
                                "human_review", f"approved by {req.reviewer}: {req.note}")
        runtime.vault.index.add(artifact.key, artifact.content)
        result = "repaired"
    else:
        result = "kept_quarantined"

    env.ledger.log_event("human_review", req.reviewer, "review_decision",
                         artifact.key, {"decision": req.decision, "note": req.note})
    return {"memory_key": artifact.key, "decision": req.decision, "result": result,
            "state": artifact.state.value}


# ======================================================================
# Privacy audit
# ======================================================================
@app.get("/api/privacy/{incident_id}")
def privacy_audit(incident_id: str) -> Dict[str, Any]:
    incident = state.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, f"incident {incident_id} not found")
    result = state.recoveries.get(incident_id)
    capsules = result.capsules if result else []
    auditor = PrivacyAuditor(state.env)
    return auditor.full_audit(incident, capsules)


# ======================================================================
# Audit log
# ======================================================================
@app.get("/api/events")
def events(incident_id: Optional[str] = None, limit: int = Query(200, le=2000)) -> Dict[str, Any]:
    return {"events": state.env.ledger.events(incident_id, limit)}


@app.get("/api/verdicts/{incident_id}")
def verdicts(incident_id: str) -> Dict[str, Any]:
    return {"verdicts": state.env.ledger.verdicts(incident_id)}


# ======================================================================
# Experiments
# ======================================================================
@app.post("/api/experiment")
async def run_experiment(req: ExperimentRequest) -> Dict[str, Any]:
    if state.experiment_status == "running":
        raise HTTPException(409, "an experiment is already running")

    state.experiment_status = "running"
    state.experiment_log = []

    def progress(message: str) -> None:
        state.experiment_log.append(message)

    def work() -> Dict[str, Any]:
        runner = ExperimentRunner(progress=progress)
        return runner.run(
            families=tuple(req.families), depths=tuple(req.depths),
            provenance_conditions=tuple(req.provenance_conditions),
            conditions=tuple(req.conditions),
            tasks_per_family=req.tasks_per_family, n_controls=req.n_controls)

    try:
        results = await asyncio.to_thread(work)
    except Exception as exc:
        state.experiment_status = "failed"
        raise HTTPException(500, f"experiment failed: {exc}")

    state.experiment = results
    state.experiment_status = "complete"
    ExperimentRunner.save(results, RESULTS_DIR)
    make_figures(results, RESULTS_DIR)
    build_report(results, out_dir=RESULTS_DIR)

    return {
        "status": "complete",
        "wall_seconds": results["wall_seconds"],
        "incidents": len(results["incidents"]),
        "runs": len(results["rows"]),
        "by_condition": results["by_condition"],
        "by_condition_provenance": results["by_condition_provenance"],
        "oracle_regret": results["oracle_regret"],
        "verification_failures": results["verification_failures"],
    }


@app.get("/api/experiment/status")
def experiment_status() -> Dict[str, Any]:
    return {"status": state.experiment_status,
            "log": state.experiment_log[-50:],
            "has_results": state.experiment is not None}


@app.get("/api/experiment/results")
def experiment_results() -> Dict[str, Any]:
    if state.experiment is None:
        raise HTTPException(404, "no experiment results yet")
    return {k: v for k, v in state.experiment.items() if k != "metrics"}


@app.get("/api/experiment/report", response_class=PlainTextResponse)
def experiment_report() -> str:
    if state.experiment is None:
        raise HTTPException(404, "no experiment results yet")
    privacy = None
    if state.incidents and state.recoveries:
        incident_id = next(iter(state.recoveries))
        incident = state.incidents[incident_id]
        privacy = PrivacyAuditor(state.env).full_audit(
            incident, state.recoveries[incident_id].capsules)
    return build_report(state.experiment, privacy=privacy)


# ======================================================================
# Guided presentation
# ======================================================================
class DemoConfigRequest(BaseModel):
    family: str = Field("F1", description="F1 | F2 | F3 | F4")
    task_id: Optional[str] = None
    depth: int = Field(4, ge=1, le=4)
    provenance: str = Field("targeted")


class DemoCareRequest(BaseModel):
    use_sketch: bool = True
    use_explicit_lineage: bool = True
    use_counterfactual: bool = True
    use_recompilation: bool = True
    use_enforcement: bool = True
    use_scoping: bool = True


@app.get("/api/demo/state")
def demo_state() -> Dict[str, Any]:
    return demo_session.state()


@app.post("/api/demo/configure")
def demo_configure(req: DemoConfigRequest) -> Dict[str, Any]:
    if req.family not in FAMILIES:
        raise HTTPException(400, f"unknown family {req.family}")
    try:
        return demo_session.configure(family=req.family, task_id=req.task_id,
                                      depth=req.depth, provenance=req.provenance)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/demo/system")
def demo_system() -> Dict[str, Any]:
    return demo_session.act_system()


@app.post("/api/demo/clean")
def demo_clean() -> Dict[str, Any]:
    return demo_session.act_clean()


@app.post("/api/demo/poison")
def demo_poison() -> Dict[str, Any]:
    try:
        return demo_session.act_poison()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/demo/condition/{condition}")
def demo_condition(condition: str) -> Dict[str, Any]:
    if condition not in CONDITION_INFO:
        raise HTTPException(400, f"unknown condition {condition}")
    try:
        return demo_session.act_condition(condition)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/demo/care")
def demo_care(req: DemoCareRequest) -> Dict[str, Any]:
    try:
        return demo_session.act_care(req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/demo/compare")
def demo_compare() -> Dict[str, Any]:
    try:
        return demo_session.act_compare()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/demo/privacy")
def demo_privacy() -> Dict[str, Any]:
    try:
        return demo_session.act_privacy()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ======================================================================
# Operations console
# ======================================================================
app.include_router(ops_router)


# ======================================================================
# Interfaces
# ======================================================================
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _page(name: str) -> str:
    page = WEB_DIR / name
    if not page.exists():
        return f"<h1>AEGIS-Care</h1><p>{name} not found.</p>"
    return page.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def operations_console() -> str:
    """The operations console - the product a clinical team signs in to."""
    return _page("ops.html")


@app.get("/present", response_class=HTMLResponse)
def presentation() -> str:
    """Guided walkthrough for demonstrating the method to an audience."""
    return _page("presentation.html")


@app.get("/research", response_class=HTMLResponse)
def research_console() -> str:
    """The analyst console - experiments, baselines, and ablations."""
    return _page("index.html")


@app.get("/console", response_class=HTMLResponse)
def console_alias() -> str:
    """Kept so existing links to the analyst console still resolve."""
    return _page("index.html")


__all__ = ["app", "state", "demo_session"]
