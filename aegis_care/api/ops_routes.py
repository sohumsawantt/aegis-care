"""HTTP surface for the operations console.

Every route below the sign-in pair requires a bearer session, and every handler
goes through `OpsService`, which enforces the permission model. There is no path
that reaches memory content without an authenticated operator holding the right
permission.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..environment import AegisEnvironment
from ..ops.auth import (
    ROLE_INFO,
    ROLE_PERMISSIONS,
    AuthError,
    Operator,
    OpsRole,
    Permission,
)
from ..ops.models import ISSUE_KIND_INFO, IncidentStatus, IssueKind, Severity
from ..ops.service import OpsError, OpsService
from ..ops.store import DEFAULT_OPERATORS

router = APIRouter(prefix="/api/ops", tags=["operations"])


# ======================================================================
# Service wiring
# ======================================================================
def _build_service() -> OpsService:
    """Construct the service, pointing at a real FHIR server when configured.

    AEGIS_FHIR_URL   base URL of an R4 server; omit to use the synthetic sandbox
    AEGIS_FHIR_TOKEN bearer token for that server
    AEGIS_OPS_DB     path to the operational database; omit for in-memory
    """
    fhir_url = os.environ.get("AEGIS_FHIR_URL")
    env = AegisEnvironment()
    if fhir_url:
        from ..fhir.remote import RemoteFHIRStore

        remote = RemoteFHIRStore(fhir_url, token=os.environ.get("AEGIS_FHIR_TOKEN"))
        env.fhir = remote
        for runtime in env.runtimes.values():
            runtime.fhir = remote

    db_path = os.environ.get("AEGIS_OPS_DB")
    service = OpsService(env=env, db_path=Path(db_path) if db_path else None)
    if not fhir_url:
        # Give the console realistic prior agent activity to operate on.
        service.bootstrap_workload()
        # Stage a few incidents so the console opens with something in it.
        # Never against a live EHR, never over an existing incident store.
        if os.environ.get("AEGIS_SEED_DEMO", "1") != "0" and                 not service.store.list_incidents(open_only=False):
            service.seed_demo_incidents()
    return service


service: OpsService = _build_service()


def _connected_to_live_fhir() -> bool:
    return bool(os.environ.get("AEGIS_FHIR_URL"))


# ======================================================================
# Auth dependency
# ======================================================================
def current_operator(authorization: Optional[str] = Header(None)) -> Operator:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return service.operator_for_token(token)
    except AuthError as exc:
        raise HTTPException(401, str(exc))


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OpsError as exc:
        raise HTTPException(400, str(exc))
    except AuthError as exc:
        raise HTTPException(401, str(exc))


# ======================================================================
# Request models
# ======================================================================
class SignInRequest(BaseModel):
    username: str
    password: str


class ReportRequest(BaseModel):
    title: str
    issue_kind: str
    description: str
    patient_id: Optional[str] = None
    observed_in: str = ""
    suspected_memory_key: Optional[str] = None
    severity: Optional[str] = None


class TriageRequest(BaseModel):
    severity: Optional[str] = None
    assign_to: Optional[str] = None
    note: str = ""


class NoteRequest(BaseModel):
    body: str


class ConfirmSeedRequest(BaseModel):
    memory_keys: List[str]
    note: str = ""


class RecoveryRequest(BaseModel):
    use_sketch: bool = True
    use_explicit_lineage: bool = True
    use_counterfactual: bool = True
    use_recompilation: bool = True
    use_enforcement: bool = True
    use_scoping: bool = True


class ReviewRequest(BaseModel):
    memory_key: str
    decision: str = Field(..., description="approve | reject | hold")
    note: str = ""


class CloseRequest(BaseModel):
    resolution: str
    dismiss: bool = False


class DrillRequest(BaseModel):
    issue_kind: str = "wrong_patient"
    patient_id: Optional[str] = None


# ======================================================================
# Session
# ======================================================================
@router.post("/session")
def sign_in(req: SignInRequest) -> Dict[str, Any]:
    try:
        return service.sign_in(req.username, req.password)
    except AuthError as exc:
        raise HTTPException(401, str(exc))


@router.delete("/session")
def sign_out(authorization: Optional[str] = Header(None)) -> Dict[str, str]:
    if authorization and authorization.lower().startswith("bearer "):
        service.sign_out(authorization.split(" ", 1)[1].strip())
    return {"status": "signed out"}


@router.get("/session")
def whoami(operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return {"operator": operator.to_public()}


@router.get("/meta")
def meta() -> Dict[str, Any]:
    """Everything the sign-in screen and forms need, before authentication."""
    return {
        "roles": [
            {"role": r.value, "label": ROLE_INFO[r][0], "description": ROLE_INFO[r][1]}
            for r in OpsRole
        ],
        "issue_kinds": [
            {"kind": k.value, "label": v["label"], "help": v["help"],
             "default_severity": v["default_severity"].value}
            for k, v in ISSUE_KIND_INFO.items()
        ],
        "role_permissions": {
            r.value: sorted(p.value for p in ROLE_PERMISSIONS.get(r, frozenset()))
            for r in OpsRole
        },
        "severities": [s.value for s in Severity],
        "statuses": [s.value for s in IncidentStatus],
        "environment": {
            "live_fhir": _connected_to_live_fhir(),
            "label": "CONNECTED EHR" if _connected_to_live_fhir()
                     else "SANDBOX · synthetic records",
            "persistent": bool(os.environ.get("AEGIS_OPS_DB")),
        },
        "demo_accounts": [] if _connected_to_live_fhir() else [
            {"username": u, "display_name": d, "role": r.value, "unit": unit,
             "password": p}
            for u, d, r, unit, p in DEFAULT_OPERATORS
        ],
    }


# ======================================================================
# Dashboard and worklist
# ======================================================================
@router.get("/dashboard")
def dashboard(operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.dashboard, operator)


@router.get("/incidents")
def worklist(open_only: bool = True, mine: bool = False,
             patient_id: Optional[str] = None,
             operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    incidents = _handle(service.worklist, operator, open_only=open_only,
                        mine=mine, patient_id=patient_id)
    return {"incidents": [i.to_summary() for i in incidents]}


@router.post("/incidents")
def report(req: ReportRequest,
           operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    incident = _handle(service.report_incident, operator, title=req.title,
                       issue_kind=req.issue_kind, description=req.description,
                       patient_id=req.patient_id, observed_in=req.observed_in,
                       suspected_memory_key=req.suspected_memory_key,
                       severity=req.severity)
    return incident.to_detail()


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str,
                    operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.get_incident, operator, incident_id).to_detail()


@router.post("/incidents/{incident_id}/triage")
def triage(incident_id: str, req: TriageRequest,
           operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.triage, operator, incident_id, severity=req.severity,
                   assign_to=req.assign_to, note=req.note).to_detail()


@router.post("/incidents/{incident_id}/notes")
def add_note(incident_id: str, req: NoteRequest,
             operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.add_note, operator, incident_id, req.body).to_detail()


@router.get("/incidents/{incident_id}/candidates")
def candidate_seeds(incident_id: str,
                    operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return {"candidates": _handle(service.candidate_seeds, operator, incident_id)}


@router.post("/incidents/{incident_id}/confirm")
def confirm_seed(incident_id: str, req: ConfirmSeedRequest,
                 operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.confirm_seed, operator, incident_id,
                   req.memory_keys, req.note).to_detail()


@router.post("/incidents/{incident_id}/recover")
def run_recovery(incident_id: str, req: RecoveryRequest,
                 operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.run_recovery, operator, incident_id,
                   req.model_dump()).to_detail()


@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id: str, req: CloseRequest,
                   operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.close_incident, operator, incident_id,
                   resolution=req.resolution, dismiss=req.dismiss).to_detail()


@router.get("/incidents/{incident_id}/spread")
def spread(incident_id: str,
           operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.spread_tree, operator, incident_id)


@router.get("/incidents/{incident_id}/evidence")
def evidence(incident_id: str,
             operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.evidence_pack, operator, incident_id)


# ======================================================================
# Review queue
# ======================================================================
@router.get("/review")
def review_queue(operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return {"items": _handle(service.review_queue, operator)}


@router.post("/review")
def review_decision(req: ReviewRequest,
                    operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.review_decision, operator, req.memory_key,
                   req.decision, req.note)


# ======================================================================
# Patients
# ======================================================================
@router.get("/patients/search")
def search_patients(q: str = Query(""),
                    operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return {"results": _handle(service.search_patients, operator, q)}


@router.get("/patients/{patient_id}")
def patient_memory(patient_id: str,
                   operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    return _handle(service.patient_memory, operator, patient_id)


# ======================================================================
# Audit
# ======================================================================
@router.get("/audit")
def audit_trail(subject: Optional[str] = None, limit: int = Query(200, le=1000),
                operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    OpsService.require(operator, Permission.VIEW_AUDIT)
    return {"entries": service.store.audit_trail(subject=subject, limit=limit)}


# ======================================================================
# Drill (sandbox only)
# ======================================================================
@router.post("/drill")
def drill(req: DrillRequest,
          operator: Operator = Depends(current_operator)) -> Dict[str, Any]:
    if _connected_to_live_fhir():
        raise HTTPException(
            403, "Drills are disabled when connected to a live EHR.")
    return _handle(service.simulate_contamination, operator,
                   issue_kind=req.issue_kind, patient_id=req.patient_id)


__all__ = ["router", "service", "current_operator"]
