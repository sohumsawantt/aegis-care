"""Operational incident records.

The research core reasons about *memory artifacts*. This layer reasons about the
human process wrapped around them: someone notices something wrong, it gets
triaged, a compromised memory is confirmed, recovery runs, a person reviews what
could not be rebuilt automatically, and the incident is closed with evidence.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    CRITICAL = "critical"    # wrong patient reached a clinical action or restricted field exposed
    HIGH = "high"            # wrong-patient association in servable memory
    MODERATE = "moderate"    # stale or superseded content still in use
    LOW = "low"              # suspected, not yet demonstrated

    @property
    def rank(self) -> int:
        return {"critical": 0, "high": 1, "moderate": 2, "low": 3}[self.value]


class IncidentStatus(str, Enum):
    REPORTED = "reported"                  # intake complete, awaiting triage
    TRIAGED = "triaged"                    # assessed and assigned
    CONFIRMED = "confirmed"                # a compromised seed has been identified
    RECOVERING = "recovering"              # CARE loop in progress
    REVIEW_REQUIRED = "review_required"    # recovery finished, artifacts need a human
    RECOVERED = "recovered"                # recovery complete, safe resume approved
    CLOSED = "closed"
    DISMISSED = "dismissed"                # investigated, no contamination found

    @property
    def is_open(self) -> bool:
        return self not in (IncidentStatus.CLOSED, IncidentStatus.DISMISSED)


#: Which transitions the workflow permits. Keeping this explicit stops the UI
#: and the API from disagreeing about what is possible.
ALLOWED_TRANSITIONS: Dict[IncidentStatus, set] = {
    IncidentStatus.REPORTED: {IncidentStatus.TRIAGED, IncidentStatus.DISMISSED},
    IncidentStatus.TRIAGED: {IncidentStatus.CONFIRMED, IncidentStatus.DISMISSED},
    IncidentStatus.CONFIRMED: {IncidentStatus.RECOVERING, IncidentStatus.DISMISSED},
    IncidentStatus.RECOVERING: {IncidentStatus.REVIEW_REQUIRED, IncidentStatus.RECOVERED},
    IncidentStatus.REVIEW_REQUIRED: {IncidentStatus.RECOVERED, IncidentStatus.RECOVERING},
    IncidentStatus.RECOVERED: {IncidentStatus.CLOSED, IncidentStatus.RECOVERING},
    IncidentStatus.CLOSED: set(),
    IncidentStatus.DISMISSED: set(),
}


class IssueKind(str, Enum):
    """What the reporter observed. Maps onto the contamination families the
    recovery engine understands, but phrased the way a clinician would say it."""

    WRONG_PATIENT = "wrong_patient"
    WRONG_RECORD_CONTENT = "wrong_record_content"
    RESTRICTED_DISCLOSURE = "restricted_disclosure"
    STALE_AFTER_CORRECTION = "stale_after_correction"
    OTHER = "other"


ISSUE_KIND_INFO = {
    IssueKind.WRONG_PATIENT: {
        "label": "Wrong patient",
        "help": "The assistant referred to, retrieved, or documented against the wrong "
                "person.",
        "family": "F1",
        "default_severity": Severity.CRITICAL,
    },
    IssueKind.WRONG_RECORD_CONTENT: {
        "label": "Content from another chart",
        "help": "Observations, vitals, or history that belong to a different patient "
                "appeared in this patient's material.",
        "family": "F2",
        "default_severity": Severity.HIGH,
    },
    IssueKind.RESTRICTED_DISCLOSURE: {
        "label": "Restricted information disclosed",
        "help": "Protected information (behavioural health, substance use) appeared "
                "somewhere the viewing role is not authorised to see it.",
        "family": "F3",
        "default_severity": Severity.CRITICAL,
    },
    IssueKind.STALE_AFTER_CORRECTION: {
        "label": "Superseded information reused",
        "help": "The record was corrected, but the assistant is still repeating the old "
                "value.",
        "family": "F4",
        "default_severity": Severity.MODERATE,
    },
    IssueKind.OTHER: {
        "label": "Something else",
        "help": "Describe what you observed.",
        "family": "F1",
        "default_severity": Severity.MODERATE,
    },
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


@dataclass
class IncidentNote:
    author: str
    author_role: str
    at: str
    body: str
    kind: str = "note"      # note | status | system

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpsIncident:
    """A safety report and everything that happened to it."""

    incident_id: str
    title: str
    issue_kind: IssueKind
    description: str

    reported_by: str
    reported_by_role: str
    reported_at: str = field(default_factory=_now)
    unit: str = ""

    patient_id: Optional[str] = None
    patient_display: str = ""
    observed_in: str = ""              # where the reporter saw it

    severity: Severity = Severity.MODERATE
    status: IncidentStatus = IncidentStatus.REPORTED
    assigned_to: Optional[str] = None

    #: Confirmed compromised memory versions. Recovery cannot start without one.
    seed_keys: List[str] = field(default_factory=list)
    suspected_memory_key: Optional[str] = None

    #: Populated once recovery has run.
    recovery_summary: Dict[str, Any] = field(default_factory=dict)
    certificate: Dict[str, Any] = field(default_factory=dict)
    certificate_text: str = ""
    quarantined_keys: List[str] = field(default_factory=list)

    notes: List[IncidentNote] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None
    resolution: str = ""

    # ------------------------------------------------------------------
    def add_note(self, author: str, author_role: str, body: str,
                 kind: str = "note") -> IncidentNote:
        note = IncidentNote(author=author, author_role=author_role, at=_now(),
                            body=body, kind=kind)
        self.notes.append(note)
        self.updated_at = note.at
        return note

    def can_transition_to(self, target: IncidentStatus) -> bool:
        return target in ALLOWED_TRANSITIONS.get(self.status, set())

    @property
    def age_hours(self) -> float:
        started = _dt.datetime.fromisoformat(self.reported_at)
        delta = _dt.datetime.now(_dt.timezone.utc) - started
        return round(delta.total_seconds() / 3600.0, 2)

    def to_summary(self) -> Dict[str, Any]:
        """Row shape for the worklist."""
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "issue_kind": self.issue_kind.value,
            "issue_label": ISSUE_KIND_INFO[self.issue_kind]["label"],
            "severity": self.severity.value,
            "status": self.status.value,
            "is_open": self.status.is_open,
            "patient_id": self.patient_id,
            "patient_display": self.patient_display,
            "reported_by": self.reported_by,
            "reported_at": self.reported_at,
            "updated_at": self.updated_at,
            "assigned_to": self.assigned_to,
            "unit": self.unit,
            "age_hours": self.age_hours,
            "seed_count": len(self.seed_keys),
            "quarantined": len(self.quarantined_keys),
            "safe_resume": bool(self.certificate.get("safe_resume")) if self.certificate else None,
        }

    def to_detail(self) -> Dict[str, Any]:
        data = self.to_summary()
        data.update({
            "description": self.description,
            "observed_in": self.observed_in,
            "reported_by_role": self.reported_by_role,
            "seed_keys": list(self.seed_keys),
            "suspected_memory_key": self.suspected_memory_key,
            "recovery_summary": dict(self.recovery_summary),
            "certificate": dict(self.certificate),
            "certificate_text": self.certificate_text,
            "quarantined_keys": list(self.quarantined_keys),
            "notes": [n.to_dict() for n in self.notes],
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "resolution": self.resolution,
            "allowed_transitions": sorted(
                s.value for s in ALLOWED_TRANSITIONS.get(self.status, set())),
            "issue_help": ISSUE_KIND_INFO[self.issue_kind]["help"],
        })
        return data


__all__ = [
    "Severity", "IncidentStatus", "IssueKind", "ISSUE_KIND_INFO",
    "ALLOWED_TRANSITIONS", "OpsIncident", "IncidentNote",
]
