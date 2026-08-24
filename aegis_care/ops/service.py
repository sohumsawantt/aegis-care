"""The operational service: intake, triage, recovery, review, closure.

This is the layer a clinical safety team actually drives. It translates between
the vocabulary of the ward ("this handover names the wrong patient") and the
vocabulary of the recovery engine (seeds, descendants, counterfactual replay),
and it keeps a durable record of who did what.
"""
from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..care.coordinator import CAREOptions, RecoveryCoordinator
from ..config import CONFIG
from ..environment import AegisEnvironment
from ..memory.models import ArtifactType, MemoryArtifact, MemoryState
from ..policy.rbac import ROLE_FIELD_MATRIX, FieldCategory, Role
from .auth import (
    AuthError,
    Operator,
    OpsRole,
    Permission,
    Session,
    new_session,
    verify_password,
)
from .models import (
    ISSUE_KIND_INFO,
    IncidentStatus,
    IssueKind,
    OpsIncident,
    Severity,
)
from .store import OpsStore


class OpsError(Exception):
    """A workflow rule was violated."""


class OpsService:
    """Everything the console needs, with authorisation enforced at each call."""

    def __init__(self, env: Optional[AegisEnvironment] = None,
                 store: Optional[OpsStore] = None,
                 db_path: Optional[Path] = None) -> None:
        self.lock = threading.RLock()
        self.env = env or AegisEnvironment()
        self.store = store or OpsStore(db_path)
        #: Task ids whose memory already exists. A drill must use a fresh task,
        #: otherwise the agents reuse the cached clean descendants and nothing
        #: actually propagates.
        self._used_tasks: set = set()

    # ==================================================================
    # Authentication
    # ==================================================================
    def sign_in(self, username: str, password: str) -> Dict[str, Any]:
        operator = self.store.get_operator(username)
        if operator is None or not operator.active:
            raise AuthError("Invalid username or password.")
        if not verify_password(password, operator.salt, operator.password_hash):
            self.store.audit(username, "unknown", "sign_in_failed")
            raise AuthError("Invalid username or password.")
        session = new_session(operator.username)
        self.store.save_session(session)
        self.store.audit(operator.username, operator.role.value, "sign_in")
        return {
            "token": session.token,
            "expires_at": session.expires_at,
            "operator": operator.to_public(),
        }

    def sign_out(self, token: str) -> None:
        session = self.store.get_session(token)
        if session:
            operator = self.store.get_operator(session.username)
            self.store.audit(session.username,
                             operator.role.value if operator else "unknown", "sign_out")
        self.store.delete_session(token)

    def operator_for_token(self, token: str) -> Operator:
        session = self.store.get_session(token)
        if session is None:
            raise AuthError("Not signed in.")
        if session.is_expired():
            self.store.delete_session(token)
            raise AuthError("Session expired. Please sign in again.")
        operator = self.store.get_operator(session.username)
        if operator is None or not operator.active:
            raise AuthError("Account is no longer active.")
        return operator

    @staticmethod
    def require(operator: Operator, permission: Permission) -> None:
        if not operator.can(permission):
            raise OpsError(
                f"{operator.display_name} ({operator.role.value}) is not authorised to "
                f"{permission.value.replace('_', ' ')}.")

    # ==================================================================
    # Intake
    # ==================================================================
    def report_incident(
        self,
        operator: Operator,
        *,
        title: str,
        issue_kind: str,
        description: str,
        patient_id: Optional[str] = None,
        observed_in: str = "",
        suspected_memory_key: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> OpsIncident:
        self.require(operator, Permission.REPORT_INCIDENT)
        try:
            kind = IssueKind(issue_kind)
        except ValueError:
            raise OpsError(f"Unknown issue type '{issue_kind}'.")
        if not title.strip():
            raise OpsError("A short title is required.")
        if not description.strip():
            raise OpsError("Please describe what you observed.")

        info = ISSUE_KIND_INFO[kind]
        chosen_severity = Severity(severity) if severity else info["default_severity"]

        with self.lock:
            incident = OpsIncident(
                incident_id=self.store.next_incident_id(),
                title=title.strip(),
                issue_kind=kind,
                description=description.strip(),
                reported_by=operator.username,
                reported_by_role=operator.role.value,
                unit=operator.unit,
                patient_id=patient_id or None,
                patient_display=(self.env.fhir.patient_display(patient_id)
                                 if patient_id else ""),
                observed_in=observed_in.strip(),
                severity=chosen_severity,
                suspected_memory_key=suspected_memory_key,
            )
            incident.add_note(
                operator.username, operator.role.value,
                f"Reported as {info['label'].lower()}"
                + (f" for patient {incident.patient_display or patient_id}" if patient_id else "")
                + ".", kind="system")
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value, "report_incident",
                             incident.incident_id,
                             {"issue_kind": kind.value, "severity": chosen_severity.value})
        return incident

    # ==================================================================
    # Triage and workflow
    # ==================================================================
    def get_incident(self, operator: Operator, incident_id: str) -> OpsIncident:
        self.require(operator, Permission.VIEW_INCIDENT)
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise OpsError(f"Incident {incident_id} not found.")
        return incident

    def worklist(self, operator: Operator, *, open_only: bool = True,
                 mine: bool = False, patient_id: Optional[str] = None) -> List[OpsIncident]:
        self.require(operator, Permission.VIEW_WORKLIST)
        incidents = self.store.list_incidents(open_only=open_only, patient_id=patient_id)
        if mine:
            incidents = [i for i in incidents
                         if i.assigned_to == operator.username
                         or i.reported_by == operator.username]
        # A clinician sees what they raised, not the whole hospital's queue.
        if operator.role == OpsRole.CLINICIAN:
            incidents = [i for i in incidents if i.reported_by == operator.username]
        incidents.sort(key=lambda i: (i.severity.rank, i.reported_at))
        return incidents

    def triage(self, operator: Operator, incident_id: str, *,
               severity: Optional[str] = None, assign_to: Optional[str] = None,
               note: str = "") -> OpsIncident:
        self.require(operator, Permission.TRIAGE_INCIDENT)
        with self.lock:
            incident = self.get_incident(operator, incident_id)
            if severity:
                incident.severity = Severity(severity)
            if assign_to:
                if self.store.get_operator(assign_to) is None:
                    raise OpsError(f"No operator named {assign_to}.")
                incident.assigned_to = assign_to
            if incident.status == IncidentStatus.REPORTED:
                incident.status = IncidentStatus.TRIAGED
            incident.add_note(operator.username, operator.role.value,
                              note or f"Triaged as {incident.severity.value}"
                              + (f", assigned to {assign_to}" if assign_to else "") + ".",
                              kind="status")
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value, "triage",
                             incident_id, {"severity": incident.severity.value,
                                           "assigned_to": incident.assigned_to})
        return incident

    def add_note(self, operator: Operator, incident_id: str, body: str) -> OpsIncident:
        self.require(operator, Permission.VIEW_INCIDENT)
        if not body.strip():
            raise OpsError("Note cannot be empty.")
        with self.lock:
            incident = self.get_incident(operator, incident_id)
            incident.add_note(operator.username, operator.role.value, body.strip())
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value, "note", incident_id)
        return incident

    # ==================================================================
    # Confirming the compromised memory
    # ==================================================================
    def candidate_seeds(self, operator: Operator, incident_id: str) -> List[Dict[str, Any]]:
        """Servable memories the officer should consider as the compromised one.

        Filtering strictly by the reported patient would be wrong, and in the
        worst possible way: a wrong-patient memory is *filed under the wrong
        patient*. The very artifact being looked for would be hidden. So three
        things are surfaced, each labelled with why it appeared:

          scoped   filed under the reported patient
          mentions names the reported patient in its text but is filed elsewhere
          recent   neither, but recent enough to be worth a look

        The officer still confirms by hand; this only narrows the haystack.
        """
        self.require(operator, Permission.CONFIRM_SEED)
        incident = self.get_incident(operator, incident_id)

        needles: List[str] = []
        if incident.patient_id:
            needles = [
                incident.patient_id,
                self.env.fhir.patient_display(incident.patient_id),
                self.env.fhir.patient_mrn(incident.patient_id),
            ]
            needles = [n for n in needles if n and n != "UNKNOWN"]

        scoped, mentions, recent = [], [], []
        for runtime in self.env.runtimes.values():
            for artifact in runtime.vault.servable():
                entry = {
                    "memory_key": artifact.key,
                    "owner": artifact.owner.value,
                    "artifact_type": artifact.artifact_type.value,
                    "patient_id": artifact.patient_scope,
                    "patient_display": (self.env.fhir.patient_display(artifact.patient_scope)
                                        if artifact.patient_scope else ""),
                    "created_at": artifact.created_at,
                    "state": artifact.state.value,
                    "preview": artifact.content[:220],
                    "restricted": bool(
                        artifact.structured_facts.get("laundered_restricted")),
                }
                if incident.patient_id and artifact.patient_scope == incident.patient_id:
                    entry["match"] = "scoped"
                    entry["why"] = "Filed under the reported patient."
                    scoped.append(entry)
                elif needles and any(n in artifact.content for n in needles):
                    entry["match"] = "mentions"
                    entry["why"] = (
                        f"Names the reported patient but is filed under "
                        f"{entry['patient_display'] or artifact.patient_scope}. "
                        f"A mis-association looks exactly like this.")
                    mentions.append(entry)
                else:
                    entry["match"] = "recent"
                    entry["why"] = "Recent memory, not linked to the reported patient."
                    recent.append(entry)

        for group in (scoped, mentions, recent):
            group.sort(key=lambda a: (a["artifact_type"], a["created_at"]))
        # Mis-associations first: they are the ones a patient filter would hide.
        return mentions + scoped + recent[:40]

    def confirm_seed(self, operator: Operator, incident_id: str,
                     memory_keys: List[str], note: str = "") -> OpsIncident:
        self.require(operator, Permission.CONFIRM_SEED)
        if not memory_keys:
            raise OpsError("Select at least one memory to confirm as compromised.")
        with self.lock:
            incident = self.get_incident(operator, incident_id)
            for key in memory_keys:
                if self.env.find_artifact(key) is None:
                    raise OpsError(f"Memory {key} not found.")
            incident.seed_keys = list(dict.fromkeys(memory_keys))
            if incident.status in (IncidentStatus.REPORTED, IncidentStatus.TRIAGED):
                incident.status = IncidentStatus.CONFIRMED
            incident.add_note(
                operator.username, operator.role.value,
                note or f"Confirmed {len(incident.seed_keys)} compromised memory "
                        f"version(s) as the incident seed.", kind="status")
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value, "confirm_seed",
                             incident_id, {"seeds": incident.seed_keys})
        return incident

    # ==================================================================
    # Recovery
    # ==================================================================
    def run_recovery(self, operator: Operator, incident_id: str,
                     options: Optional[Dict[str, bool]] = None) -> OpsIncident:
        self.require(operator, Permission.RUN_RECOVERY)
        with self.lock:
            incident = self.get_incident(operator, incident_id)
            if not incident.seed_keys:
                raise OpsError(
                    "Confirm the compromised memory before running recovery. "
                    "The system will not act on an unconfirmed suspicion.")
            if incident.status in (IncidentStatus.CLOSED, IncidentStatus.DISMISSED):
                raise OpsError("This incident is closed.")

            incident.status = IncidentStatus.RECOVERING
            self.store.save_incident(incident)

            coordinator = RecoveryCoordinator(self.env)
            result = coordinator.recover(
                incident.incident_id, incident.seed_keys,
                options=CAREOptions(**(options or {})))

            certificate = result.certificate
            incident.recovery_summary = {
                "candidates": len(result.candidates_considered),
                "confirmed": len(result.confirmed),
                "cleared": len(result.cleared),
                "repaired": len(result.repaired),
                "quarantined": len(result.quarantined),
                "rounds": result.rounds,
                "closure_reached": result.closure_reached,
                "tombstones": result.enforcement.get("tombstones", 0),
                "resurrection": result.resurrection_probe,
                "overhead": result.overhead,
                "repaired_detail": result.repaired,
                "quarantined_detail": result.quarantined,
            }
            incident.quarantined_keys = [q["memory_key"] for q in result.quarantined]
            if certificate is not None:
                from dataclasses import asdict
                incident.certificate = asdict(certificate)
                incident.certificate_text = certificate.to_text()

            incident.status = (IncidentStatus.REVIEW_REQUIRED
                               if incident.quarantined_keys
                               else IncidentStatus.RECOVERED)
            incident.add_note(
                operator.username, operator.role.value,
                f"Recovery complete: {len(result.repaired)} memory version(s) rebuilt "
                f"from the trusted record, {len(result.quarantined)} held for review, "
                f"{result.enforcement.get('tombstones', 0)} tombstoned.", kind="status")
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value, "run_recovery",
                             incident_id, incident.recovery_summary)
        return incident

    # ==================================================================
    # Review queue (functional requirement F10)
    # ==================================================================
    def review_queue(self, operator: Operator) -> List[Dict[str, Any]]:
        self.require(operator, Permission.REVIEW_QUARANTINE)
        items: List[Dict[str, Any]] = []
        incidents = {i.incident_id: i for i in self.store.list_incidents(open_only=False)}
        for runtime in self.env.runtimes.values():
            for artifact in runtime.vault.all():
                if artifact.state != MemoryState.QUARANTINED:
                    continue
                related = next(
                    (i for i in incidents.values() if artifact.key in i.quarantined_keys),
                    None)
                items.append({
                    "memory_key": artifact.key,
                    "owner": artifact.owner.value,
                    "artifact_type": artifact.artifact_type.value,
                    "patient_id": artifact.patient_scope,
                    "patient_display": (self.env.fhir.patient_display(artifact.patient_scope)
                                        if artifact.patient_scope else ""),
                    "reason": artifact.quarantine_reason or "",
                    "content": artifact.content,
                    "created_at": artifact.created_at,
                    "incident_id": related.incident_id if related else None,
                })
        items.sort(key=lambda i: i["created_at"])
        return items

    def review_decision(self, operator: Operator, memory_key: str, decision: str,
                        note: str = "") -> Dict[str, Any]:
        self.require(operator, Permission.REVIEW_QUARANTINE)
        if decision not in ("approve", "reject", "hold"):
            raise OpsError(f"Unknown decision '{decision}'.")
        with self.lock:
            artifact = self.env.find_artifact(memory_key)
            if artifact is None:
                raise OpsError(f"Memory {memory_key} not found.")
            runtime = self.env.runtime(artifact.owner)

            if decision == "reject":
                runtime.vault.set_state(artifact.key, MemoryState.TOMBSTONED,
                                        "human_review",
                                        f"rejected by {operator.username}: {note}")
                outcome = "tombstoned"
            elif decision == "approve":
                runtime.vault.set_state(artifact.key, MemoryState.REPAIRED,
                                        "human_review",
                                        f"approved by {operator.username}: {note}")
                runtime.vault.index.add(artifact.key, artifact.content)
                outcome = "returned to service"
            else:
                outcome = "kept in quarantine"

            self.store.audit(operator.username, operator.role.value, "review_decision",
                             memory_key, {"decision": decision, "note": note})

            # Close the loop on any incident that was waiting on this artifact.
            for incident in self.store.list_incidents(open_only=True):
                if memory_key not in incident.quarantined_keys:
                    continue
                incident.add_note(
                    operator.username, operator.role.value,
                    f"Review decision on {memory_key}: {decision} ({outcome})."
                    + (f" {note}" if note else ""), kind="status")
                if decision != "hold":
                    incident.quarantined_keys = [
                        k for k in incident.quarantined_keys if k != memory_key]
                    if not incident.quarantined_keys and \
                            incident.status == IncidentStatus.REVIEW_REQUIRED:
                        incident.status = IncidentStatus.RECOVERED
                        incident.add_note(
                            operator.username, operator.role.value,
                            "All quarantined artifacts resolved.", kind="system")
                self.store.save_incident(incident)

        return {"memory_key": memory_key, "decision": decision, "outcome": outcome,
                "state": artifact.state.value}

    # ==================================================================
    # Closure
    # ==================================================================
    def close_incident(self, operator: Operator, incident_id: str, *,
                       resolution: str, dismiss: bool = False) -> OpsIncident:
        self.require(operator, Permission.CLOSE_INCIDENT)
        if not resolution.strip():
            raise OpsError("A resolution summary is required to close an incident.")
        with self.lock:
            incident = self.get_incident(operator, incident_id)
            target = IncidentStatus.DISMISSED if dismiss else IncidentStatus.CLOSED
            if not dismiss and incident.quarantined_keys:
                raise OpsError(
                    f"{len(incident.quarantined_keys)} artifact(s) still await review. "
                    "Resolve them before closing.")
            if not dismiss and incident.status not in (
                    IncidentStatus.RECOVERED, IncidentStatus.REVIEW_REQUIRED):
                raise OpsError(
                    "Run recovery before closing, or dismiss the incident if no "
                    "contamination was found.")
            incident.status = target
            incident.resolution = resolution.strip()
            incident.closed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
            incident.closed_by = operator.username
            incident.add_note(operator.username, operator.role.value,
                              f"{'Dismissed' if dismiss else 'Closed'}: {resolution.strip()}",
                              kind="status")
            self.store.save_incident(incident)
            self.store.audit(operator.username, operator.role.value,
                             "dismiss_incident" if dismiss else "close_incident",
                             incident_id, {"resolution": resolution})
        return incident

    # ==================================================================
    # Patient-centred view
    # ==================================================================
    def patient_memory(self, operator: Operator, patient_id: str) -> Dict[str, Any]:
        """Everything durable agent memory holds about one patient.

        This is the question a clinician actually asks after an incident:
        "what does the assistant still believe about my patient?"
        """
        self.require(operator, Permission.VIEW_PATIENT_MEMORY)
        patient = self.env.fhir.read("Patient", patient_id)
        if patient is None:
            raise OpsError(f"Patient {patient_id} not found.")

        by_role: Dict[str, List[Dict[str, Any]]] = {}
        for role, runtime in self.env.runtimes.items():
            entries = []
            for artifact in runtime.vault.all():
                if artifact.patient_scope != patient_id:
                    continue
                entries.append({
                    "memory_key": artifact.key,
                    "artifact_type": artifact.artifact_type.value,
                    "version": artifact.version,
                    "state": artifact.state.value,
                    "servable": artifact.is_servable(),
                    "created_at": artifact.created_at,
                    "content": artifact.content,
                    "supersedes": artifact.supersedes,
                    "quarantine_reason": artifact.quarantine_reason,
                    "restricted": bool(
                        artifact.structured_facts.get("laundered_restricted")),
                })
            entries.sort(key=lambda e: (e["artifact_type"], e["version"]))
            by_role[role.value] = entries

        incidents = self.store.list_incidents(open_only=False, patient_id=patient_id)
        return {
            "patient": {
                "id": patient_id,
                "display": self.env.fhir.patient_display(patient_id),
                "mrn": self.env.fhir.patient_mrn(patient_id),
                "birth_date": patient.get("birthDate"),
                "gender": patient.get("gender"),
            },
            "memory_by_role": by_role,
            "totals": {
                "artifacts": sum(len(v) for v in by_role.values()),
                "servable": sum(1 for v in by_role.values() for e in v if e["servable"]),
                "quarantined": sum(1 for v in by_role.values() for e in v
                                   if e["state"] == "quarantined"),
            },
            "incidents": [i.to_summary() for i in incidents],
        }

    def search_patients(self, operator: Operator, query: str,
                        limit: int = 20) -> List[Dict[str, Any]]:
        self.require(operator, Permission.VIEW_PATIENT_MEMORY)
        query = (query or "").strip().lower()
        if not query:
            return []
        out = []
        for pid in self.env.fhir.patient_ids():
            display = self.env.fhir.patient_display(pid)
            mrn = self.env.fhir.patient_mrn(pid)
            if query in display.lower() or query in mrn.lower() or query in pid.lower():
                out.append({"patient_id": pid, "display": display, "mrn": mrn})
            if len(out) >= limit:
                break
        return out

    # ==================================================================
    # Dashboard
    # ==================================================================
    def dashboard(self, operator: Operator) -> Dict[str, Any]:
        self.require(operator, Permission.VIEW_WORKLIST)
        open_incidents = self.worklist(operator, open_only=True)
        severity = {}
        for incident in open_incidents:
            severity[incident.severity.value] = severity.get(incident.severity.value, 0) + 1

        awaiting_review = 0
        if operator.can(Permission.REVIEW_QUARANTINE):
            awaiting_review = len(self.review_queue(operator))

        exposures = 0
        for role, runtime in self.env.runtimes.items():
            if FieldCategory.RESTRICTED in ROLE_FIELD_MATRIX.get(role, frozenset()):
                continue
            exposures += sum(1 for a in runtime.vault.servable()
                             if a.structured_facts.get("laundered_restricted"))

        return {
            "open_incidents": len(open_incidents),
            "by_severity": severity,
            "by_status": self.store.counts_by_status(),
            "awaiting_review": awaiting_review,
            "active_exposures": exposures,
            "memory_totals": {
                role.value: runtime.vault.stats()
                for role, runtime in self.env.runtimes.items()
            },
            "recent": [i.to_summary() for i in open_incidents[:8]],
        }

    # ==================================================================
    # Environment bootstrap and drills
    # ==================================================================
    def bootstrap_workload(self, n_tasks: int = 8) -> int:
        """Populate memory with ordinary, uncontaminated agent activity.

        In a live deployment the agents are already running and their vaults are
        already full; the console simply attaches to them. Against the sandbox
        we have to generate that history so there is something real to inspect.
        """
        with self.lock:
            if any(runtime.vault.all() for runtime in self.env.runtimes.values()):
                return 0
            written = 0
            for task in self.env.tasks[:n_tasks]:
                # Full depth, so every role holds memory. Running each task at
                # its manifest depth would leave the clinical-summary vault
                # empty and make the system look inert.
                self.env.run_trajectory(task, depth=4)
                self._used_tasks.add(task["task_id"])
                written += 1
            return written

    def simulate_contamination(self, operator: Operator, *, issue_kind: str = "wrong_patient",
                               patient_id: Optional[str] = None) -> Dict[str, Any]:
        """Plant a real contamination event in the sandbox for drills and training.

        This exists because the console cannot wait for a genuine poisoning to
        occur. The contamination it creates is not staged: it runs the ordinary
        agent write path, propagates through the real derivation chain, and must
        be found by the same recovery loop as any other incident. A live
        deployment would disable this endpoint.
        """
        self.require(operator, Permission.CONFIRM_SEED)
        from ..incident.scenarios import FAMILY_INFO, ScenarioBuilder

        family = ISSUE_KIND_INFO[IssueKind(issue_kind)]["family"]
        with self.lock:
            builder = ScenarioBuilder(self.env)
            task = None
            if patient_id:
                task = next((t for t in self.env.tasks
                             if t["patient_id"] == patient_id
                             and t["task_id"] not in self._used_tasks), None)
            if task is None:
                task = next((t for t in self.env.tasks
                             if t["task_id"] not in self._used_tasks), None)
            if task is None:
                raise OpsError(
                    "Every task in this sandbox already has agent memory. Restart the "
                    "service to run another drill.")
            self._used_tasks.add(task["task_id"])
            depth = max(4, FAMILY_INFO[family]["seed_depth"] + 1)
            incident = builder.build(family, task, depth=depth, n_controls=1)
            # A drill that did not actually propagate would teach the wrong thing.
            if not incident.true_contaminated:
                raise OpsError(
                    "The drill did not propagate in this sandbox state; try another "
                    "issue type.")

            self.store.audit(operator.username, operator.role.value,
                             "simulate_contamination", incident.seed_key,
                             {"family": family, "patient_id": task["patient_id"]})
            return {
                "family": family,
                "family_name": FAMILY_INFO[family]["name"],
                "patient_id": task["patient_id"],
                "patient_display": self.env.fhir.patient_display(task["patient_id"]),
                "seed_key": incident.seed_key,
                "affected_count": len(incident.true_contaminated),
                "observed_in": task["label"],
            }

    # ==================================================================
    # Evidence
    # ==================================================================
    def evidence_pack(self, operator: Operator, incident_id: str) -> Dict[str, Any]:
        """Everything an auditor or a governance committee would ask for."""
        self.require(operator, Permission.EXPORT_EVIDENCE)
        incident = self.get_incident(operator, incident_id)
        return {
            "incident": incident.to_detail(),
            "certificate_text": incident.certificate_text,
            "operational_audit": self.store.audit_trail(subject=incident_id),
            "engine_events": self.env.ledger.events(incident_id, limit=1000),
            "verdicts": self.env.ledger.verdicts(incident_id),
            "capsule_disclosure": self.env.ledger.capsule_stats(incident_id),
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "generated_by": operator.username,
        }


__all__ = ["OpsService", "OpsError"]
