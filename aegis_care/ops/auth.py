"""Operator authentication and authorisation.

Real credential handling, not a placeholder: PBKDF2-HMAC-SHA256 with a
per-operator salt, constant-time verification, and expiring bearer sessions.

Deployment note: a hospital would federate this to the existing identity
provider (SAML/OIDC via the trust domain that already governs EHR access) rather
than hold its own password store. `authenticate()` is the single seam where that
swap happens - everything above it works against `Operator` records regardless
of where the credential check came from.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

PBKDF2_ROUNDS = 240_000
SESSION_TTL_MINUTES = 60


class OpsRole(str, Enum):
    """What a person does with this tool, distinct from the *agent* roles the
    recovery engine reasons about."""

    CLINICIAN = "clinician"              # reports suspected memory errors
    SAFETY_OFFICER = "safety_officer"    # triages and runs recovery
    REVIEWER = "reviewer"                # decides on quarantined artifacts
    AUDITOR = "auditor"                  # read-only, compliance and evidence
    ADMIN = "admin"                      # operator and policy administration


class Permission(str, Enum):
    REPORT_INCIDENT = "report_incident"
    VIEW_WORKLIST = "view_worklist"
    VIEW_INCIDENT = "view_incident"
    TRIAGE_INCIDENT = "triage_incident"
    CONFIRM_SEED = "confirm_seed"
    RUN_RECOVERY = "run_recovery"
    REVIEW_QUARANTINE = "review_quarantine"
    VIEW_PATIENT_MEMORY = "view_patient_memory"
    VIEW_AUDIT = "view_audit"
    EXPORT_EVIDENCE = "export_evidence"
    CLOSE_INCIDENT = "close_incident"
    MANAGE_OPERATORS = "manage_operators"


ROLE_PERMISSIONS: Dict[OpsRole, FrozenSet[Permission]] = {
    OpsRole.CLINICIAN: frozenset({
        Permission.REPORT_INCIDENT,
        Permission.VIEW_WORKLIST,
        Permission.VIEW_INCIDENT,
        Permission.VIEW_PATIENT_MEMORY,
    }),
    OpsRole.SAFETY_OFFICER: frozenset({
        Permission.REPORT_INCIDENT,
        Permission.VIEW_WORKLIST,
        Permission.VIEW_INCIDENT,
        Permission.TRIAGE_INCIDENT,
        Permission.CONFIRM_SEED,
        Permission.RUN_RECOVERY,
        Permission.VIEW_PATIENT_MEMORY,
        Permission.VIEW_AUDIT,
        Permission.EXPORT_EVIDENCE,
        Permission.CLOSE_INCIDENT,
    }),
    OpsRole.REVIEWER: frozenset({
        Permission.VIEW_WORKLIST,
        Permission.VIEW_INCIDENT,
        Permission.REVIEW_QUARANTINE,
        Permission.VIEW_PATIENT_MEMORY,
        Permission.VIEW_AUDIT,
    }),
    OpsRole.AUDITOR: frozenset({
        Permission.VIEW_WORKLIST,
        Permission.VIEW_INCIDENT,
        Permission.VIEW_AUDIT,
        Permission.EXPORT_EVIDENCE,
    }),
    OpsRole.ADMIN: frozenset(Permission),
}

#: Human-readable role descriptions surfaced in the sign-in screen.
ROLE_INFO = {
    OpsRole.CLINICIAN: (
        "Clinician",
        "Report a suspected wrong-patient or wrong-record memory and track it."),
    OpsRole.SAFETY_OFFICER: (
        "Clinical safety officer",
        "Triage reports, confirm the compromised memory, and run recovery."),
    OpsRole.REVIEWER: (
        "Memory reviewer",
        "Decide on artifacts the system quarantined instead of rebuilding."),
    OpsRole.AUDITOR: (
        "Compliance auditor",
        "Read-only access to certificates, the audit trail, and evidence packs."),
    OpsRole.ADMIN: (
        "Administrator",
        "Manage operators and recovery policy thresholds."),
}


# ----------------------------------------------------------------------
def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Return (salt_hex, hash_hex)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return hmac.compare_digest(digest.hex(), hash_hex)


# ----------------------------------------------------------------------
@dataclass
class Operator:
    username: str
    display_name: str
    role: OpsRole
    salt: str
    password_hash: str
    #: Clinical unit / department, shown on incidents this operator touches.
    unit: str = ""
    active: bool = True
    created_at: str = ""

    def permissions(self) -> FrozenSet[Permission]:
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions()

    def to_public(self) -> Dict[str, object]:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role.value,
            "role_label": ROLE_INFO[self.role][0],
            "unit": self.unit,
            "active": self.active,
            "permissions": sorted(p.value for p in self.permissions()),
        }


@dataclass
class Session:
    token: str
    username: str
    issued_at: str
    expires_at: str

    def is_expired(self, at: Optional[_dt.datetime] = None) -> bool:
        at = at or _dt.datetime.now(_dt.timezone.utc)
        return at >= _dt.datetime.fromisoformat(self.expires_at)


class AuthError(Exception):
    """Raised for a failed sign-in or an invalid session."""


def new_session(username: str, ttl_minutes: int = SESSION_TTL_MINUTES) -> Session:
    now = _dt.datetime.now(_dt.timezone.utc)
    return Session(
        token=secrets.token_urlsafe(32),
        username=username,
        issued_at=now.isoformat(),
        expires_at=(now + _dt.timedelta(minutes=ttl_minutes)).isoformat(),
    )


__all__ = [
    "OpsRole", "Permission", "ROLE_PERMISSIONS", "ROLE_INFO",
    "Operator", "Session", "AuthError",
    "hash_password", "verify_password", "new_session",
    "SESSION_TTL_MINUTES",
]
