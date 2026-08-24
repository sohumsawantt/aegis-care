"""Durable storage for operators, sessions, and incidents.

The research environment is deliberately in-memory so experiments are
reproducible. An operational console is the opposite: an incident raised on a
night shift must still be there in the morning, and the audit trail has to
outlive the process.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import Operator, OpsRole, Session, hash_password
from .models import IncidentNote, IncidentStatus, IssueKind, OpsIncident, Severity

SCHEMA = """
CREATE TABLE IF NOT EXISTS operators (
    username      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    salt          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    unit          TEXT DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    issued_at  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    patient_id  TEXT,
    reported_at TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_audit (
    row_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action     TEXT NOT NULL,
    subject    TEXT,
    detail     TEXT
);

CREATE INDEX IF NOT EXISTS idx_inc_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_inc_patient ON incidents(patient_id);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON ops_audit(subject);
"""

#: Seeded accounts so the console is usable immediately. A real deployment
#: disables these and federates to the hospital identity provider.
DEFAULT_OPERATORS = [
    ("r.mehta", "Dr. Riya Mehta", OpsRole.CLINICIAN, "Internal Medicine", "clinician123"),
    ("s.nair", "Sunita Nair, RN", OpsRole.CLINICIAN, "Ward 4B", "nurse123"),
    ("a.khan", "Arif Khan", OpsRole.SAFETY_OFFICER, "Clinical Informatics", "safety123"),
    ("p.desai", "Priya Desai", OpsRole.REVIEWER, "Health Information Mgmt", "review123"),
    ("m.rao", "Meera Rao", OpsRole.AUDITOR, "Compliance", "audit123"),
    ("admin", "System Administrator", OpsRole.ADMIN, "IT", "admin123"),
]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class OpsStore:
    """SQLite-backed operational store."""

    def __init__(self, path: Optional[Path] = None, seed_defaults: bool = True) -> None:
        self.path = str(path) if path else ":memory:"
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        if seed_defaults:
            self.seed_default_operators()

    # ==================================================================
    # Operators
    # ==================================================================
    def seed_default_operators(self) -> None:
        existing = self.conn.execute("SELECT COUNT(*) AS n FROM operators").fetchone()["n"]
        if existing:
            return
        for username, display, role, unit, password in DEFAULT_OPERATORS:
            salt, digest = hash_password(password)
            self.conn.execute(
                """INSERT INTO operators
                   (username, display_name, role, salt, password_hash, unit, active, created_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (username, display, role.value, salt, digest, unit, _now()))
        self.conn.commit()

    def add_operator(self, username: str, display_name: str, role: OpsRole,
                     password: str, unit: str = "") -> Operator:
        salt, digest = hash_password(password)
        self.conn.execute(
            """INSERT INTO operators
               (username, display_name, role, salt, password_hash, unit, active, created_at)
               VALUES (?,?,?,?,?,?,1,?)""",
            (username, display_name, role.value, salt, digest, unit, _now()))
        self.conn.commit()
        return self.get_operator(username)  # type: ignore[return-value]

    def get_operator(self, username: str) -> Optional[Operator]:
        row = self.conn.execute(
            "SELECT * FROM operators WHERE username = ?", (username,)).fetchone()
        if row is None:
            return None
        return Operator(
            username=row["username"], display_name=row["display_name"],
            role=OpsRole(row["role"]), salt=row["salt"],
            password_hash=row["password_hash"], unit=row["unit"] or "",
            active=bool(row["active"]), created_at=row["created_at"])

    def list_operators(self) -> List[Operator]:
        return [self.get_operator(r["username"])  # type: ignore[misc]
                for r in self.conn.execute(
                    "SELECT username FROM operators ORDER BY role, username")]

    def set_operator_active(self, username: str, active: bool) -> None:
        self.conn.execute("UPDATE operators SET active = ? WHERE username = ?",
                          (1 if active else 0, username))
        self.conn.commit()

    # ==================================================================
    # Sessions
    # ==================================================================
    def save_session(self, session: Session) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions (token, username, issued_at, expires_at) "
            "VALUES (?,?,?,?)",
            (session.token, session.username, session.issued_at, session.expires_at))
        self.conn.commit()

    def get_session(self, token: str) -> Optional[Session]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        return Session(token=row["token"], username=row["username"],
                       issued_at=row["issued_at"], expires_at=row["expires_at"])

    def delete_session(self, token: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self.conn.commit()

    def purge_expired_sessions(self) -> int:
        cursor = self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        self.conn.commit()
        return cursor.rowcount

    # ==================================================================
    # Incidents
    # ==================================================================
    def save_incident(self, incident: OpsIncident) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO incidents
               (incident_id, payload, status, severity, patient_id, reported_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (incident.incident_id, json.dumps(_encode(incident), default=str),
             incident.status.value, incident.severity.value, incident.patient_id,
             incident.reported_at, incident.updated_at))
        self.conn.commit()

    def get_incident(self, incident_id: str) -> Optional[OpsIncident]:
        row = self.conn.execute(
            "SELECT payload FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        return _decode(json.loads(row["payload"])) if row else None

    def list_incidents(self, *, open_only: bool = False, patient_id: Optional[str] = None,
                       assigned_to: Optional[str] = None,
                       limit: int = 200) -> List[OpsIncident]:
        clauses, params = [], []
        if open_only:
            clauses.append("status NOT IN ('closed','dismissed')")
        if patient_id:
            clauses.append("patient_id = ?")
            params.append(patient_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT payload FROM incidents {where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit))
        incidents = [_decode(json.loads(r["payload"])) for r in rows]
        if assigned_to:
            incidents = [i for i in incidents if i.assigned_to == assigned_to]
        return incidents

    def next_incident_id(self) -> str:
        year = _dt.datetime.now(_dt.timezone.utc).year
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM incidents WHERE incident_id LIKE ?",
            (f"AC-{year}-%",)).fetchone()
        return f"AC-{year}-{row['n'] + 1:04d}"

    def counts_by_status(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM incidents GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def counts_by_severity(self, open_only: bool = True) -> Dict[str, int]:
        where = "WHERE status NOT IN ('closed','dismissed')" if open_only else ""
        rows = self.conn.execute(
            f"SELECT severity, COUNT(*) AS n FROM incidents {where} GROUP BY severity")
        return {r["severity"]: r["n"] for r in rows}

    # ==================================================================
    # Operational audit trail
    # ==================================================================
    def audit(self, actor: str, actor_role: str, action: str,
              subject: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "INSERT INTO ops_audit (at, actor, actor_role, action, subject, detail) "
            "VALUES (?,?,?,?,?,?)",
            (_now(), actor, actor_role, action, subject,
             json.dumps(detail or {}, default=str)))
        self.conn.commit()

    def audit_trail(self, subject: Optional[str] = None,
                    limit: int = 300) -> List[Dict[str, Any]]:
        if subject:
            rows = self.conn.execute(
                "SELECT * FROM ops_audit WHERE subject = ? ORDER BY row_id DESC LIMIT ?",
                (subject, limit))
        else:
            rows = self.conn.execute(
                "SELECT * FROM ops_audit ORDER BY row_id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            item = dict(r)
            item["detail"] = json.loads(item["detail"] or "{}")
            out.append(item)
        return out

    def close(self) -> None:
        self.conn.close()


# ----------------------------------------------------------------------
def _encode(incident: OpsIncident) -> Dict[str, Any]:
    from dataclasses import asdict
    data = asdict(incident)
    data["issue_kind"] = incident.issue_kind.value
    data["severity"] = incident.severity.value
    data["status"] = incident.status.value
    return data


def _decode(data: Dict[str, Any]) -> OpsIncident:
    notes = [IncidentNote(**n) for n in data.pop("notes", [])]
    data["issue_kind"] = IssueKind(data["issue_kind"])
    data["severity"] = Severity(data["severity"])
    data["status"] = IncidentStatus(data["status"])
    incident = OpsIncident(**data)
    incident.notes = notes
    return incident


__all__ = ["OpsStore", "DEFAULT_OPERATORS"]
