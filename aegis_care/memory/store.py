"""Append-only version ledger and the role-local memory vault.

Section 12.1: "SQLite ... append-only version and event tables". Section 11.2
forbids destructive deletion of the audit trail: superseded content becomes
non-servable and is represented by policy-controlled tombstones, never erased.
"""
from __future__ import annotations

import json
import sqlite3
import datetime as _dt
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..policy.rbac import Role
from ..util.crypto import KeyRing
from .models import (
    ArtifactType,
    MemoryArtifact,
    MemoryState,
    ReplayRecipe,
    state_transition_allowed,
)
from .sketch import SketchEncoder, SketchIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_versions (
    row_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id         TEXT NOT NULL,
    version           INTEGER NOT NULL,
    owner             TEXT NOT NULL,
    artifact_type     TEXT NOT NULL,
    content_ref       TEXT NOT NULL,
    commitment        TEXT NOT NULL,
    patient_scope     TEXT,
    purpose           TEXT,
    state             TEXT NOT NULL,
    supersedes        TEXT,
    recipe_fp         TEXT,
    signature         TEXT,
    signed_by         TEXT,
    session_id        TEXT,
    created_at        TEXT NOT NULL,
    payload           TEXT NOT NULL,
    UNIQUE (memory_id, version)
);

CREATE TABLE IF NOT EXISTS memory_edges (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    child_key    TEXT NOT NULL,
    parent_commit TEXT NOT NULL,
    observed     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (child_key, parent_commit)
);

CREATE TABLE IF NOT EXISTS events (
    row_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    incident_id TEXT,
    actor      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    subject    TEXT,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS tombstones (
    memory_key  TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    commitment  TEXT NOT NULL,
    reason      TEXT,
    at          TEXT NOT NULL,
    signature   TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    memory_key  TEXT NOT NULL,
    runtime     TEXT NOT NULL,
    influence_band TEXT NOT NULL,
    influence_score REAL NOT NULL,
    disposition TEXT NOT NULL,
    signature   TEXT NOT NULL,
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capsule_log (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    seed_commitment TEXT NOT NULL,
    bytes_released INTEGER NOT NULL,
    fields_released TEXT NOT NULL,
    at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mv_memory ON memory_versions(memory_id);
CREATE INDEX IF NOT EXISTS idx_ev_incident ON events(incident_id);
"""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class LedgerStore:
    """Append-only audit ledger shared by all runtimes and the coordinator."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = str(path) if path else ":memory:"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    def record_version(self, artifact: MemoryArtifact) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO memory_versions
               (memory_id, version, owner, artifact_type, content_ref, commitment,
                patient_scope, purpose, state, supersedes, recipe_fp, signature,
                signed_by, session_id, created_at, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                artifact.memory_id, artifact.version, artifact.owner.value,
                artifact.artifact_type.value, artifact.content_ref, artifact.commitment(),
                artifact.patient_scope, artifact.purpose, artifact.state.value,
                artifact.supersedes,
                artifact.replay_recipe.fingerprint() if artifact.replay_recipe else None,
                artifact.signature, artifact.signed_by, artifact.session_id,
                artifact.created_at, json.dumps(artifact.to_public_dict()),
            ),
        )
        for parent in artifact.explicit_parent_commitments:
            self.conn.execute(
                "INSERT OR IGNORE INTO memory_edges (child_key, parent_commit) VALUES (?,?)",
                (artifact.key, parent),
            )
        self.conn.commit()

    def record_state_change(self, artifact: MemoryArtifact, incident_id: str, reason: str) -> None:
        self.conn.execute(
            """UPDATE memory_versions SET state = ? WHERE memory_id = ? AND version = ?""",
            (artifact.state.value, artifact.memory_id, artifact.version),
        )
        self.log_event(incident_id, artifact.owner.value, "state_change",
                       artifact.key, {"state": artifact.state.value, "reason": reason})

    def log_event(self, incident_id: Optional[str], actor: str, kind: str,
                  subject: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "INSERT INTO events (at, incident_id, actor, kind, subject, detail) VALUES (?,?,?,?,?,?)",
            (_now(), incident_id, actor, kind, subject, json.dumps(detail or {}, default=str)),
        )
        self.conn.commit()

    def add_tombstone(self, memory_key: str, incident_id: str, commitment: str,
                      reason: str, signature: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO tombstones
               (memory_key, incident_id, commitment, reason, at, signature)
               VALUES (?,?,?,?,?,?)""",
            (memory_key, incident_id, commitment, reason, _now(), signature),
        )
        self.conn.commit()

    def tombstone_commitments(self, incident_id: Optional[str] = None) -> List[str]:
        if incident_id:
            rows = self.conn.execute(
                "SELECT commitment FROM tombstones WHERE incident_id = ?", (incident_id,))
        else:
            rows = self.conn.execute("SELECT commitment FROM tombstones")
        return [r["commitment"] for r in rows]

    def record_verdict(self, incident_id: str, memory_key: str, runtime: str,
                       band: str, score: float, disposition: str, signature: str) -> None:
        self.conn.execute(
            """INSERT INTO verdicts (incident_id, memory_key, runtime, influence_band,
               influence_score, disposition, signature, at) VALUES (?,?,?,?,?,?,?,?)""",
            (incident_id, memory_key, runtime, band, score, disposition, signature, _now()),
        )
        self.conn.commit()

    def record_capsule(self, incident_id: str, recipient: str, seed_commitment: str,
                       size_bytes: int, fields: List[str]) -> None:
        self.conn.execute(
            """INSERT INTO capsule_log (incident_id, recipient, seed_commitment,
               bytes_released, fields_released, at) VALUES (?,?,?,?,?,?)""",
            (incident_id, recipient, seed_commitment, size_bytes, json.dumps(sorted(fields)), _now()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    def events(self, incident_id: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        if incident_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE incident_id = ? ORDER BY row_id DESC LIMIT ?",
                (incident_id, limit))
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY row_id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            item = dict(r)
            item["detail"] = json.loads(item["detail"] or "{}")
            out.append(item)
        return out

    def verdicts(self, incident_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM verdicts WHERE incident_id = ? ORDER BY row_id", (incident_id,))
        return [dict(r) for r in rows]

    def capsule_stats(self, incident_id: str) -> Dict[str, Any]:
        rows = list(self.conn.execute(
            "SELECT * FROM capsule_log WHERE incident_id = ?", (incident_id,)))
        fields: set = set()
        for r in rows:
            fields.update(json.loads(r["fields_released"]))
        return {
            "capsules": len(rows),
            "total_bytes": sum(r["bytes_released"] for r in rows),
            "distinct_fields_released": sorted(fields),
        }

    def version_history(self, memory_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memory_versions WHERE memory_id = ? ORDER BY version", (memory_id,))
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


class MemoryVault:
    """A single role's private memory store.

    Raw content never leaves this object; the coordinator only ever receives
    commitments, counts, bands, and signed verdicts (Section 6.6, "no raw-content
    centralization").
    """

    def __init__(self, owner: Role, encoder: SketchEncoder, ledger: LedgerStore,
                 keyring: KeyRing) -> None:
        self.owner = owner
        self.encoder = encoder
        self.ledger = ledger
        self.keyring = keyring
        self.index = SketchIndex(encoder, owner.value)
        self._artifacts: Dict[str, MemoryArtifact] = {}     # key -> artifact
        self._by_commitment: Dict[str, str] = {}            # commitment -> key
        self._latest: Dict[str, int] = {}                   # memory_id -> latest version

    # ------------------------------------------------------------------
    def put(self, artifact: MemoryArtifact) -> MemoryArtifact:
        artifact.signed_by = self.owner.value
        artifact.signature = self.keyring.sign(self.owner.value, artifact.signable_payload())
        if artifact.write_context_sketch is None:
            artifact.write_context_sketch = self.index.add(artifact.key, artifact.content)
        else:
            self.index.add(artifact.key, artifact.content)
        self._artifacts[artifact.key] = artifact
        self._by_commitment[artifact.commitment()] = artifact.key
        self._latest[artifact.memory_id] = max(
            self._latest.get(artifact.memory_id, 0), artifact.version)
        self.ledger.record_version(artifact)
        return artifact

    def get(self, key: str) -> Optional[MemoryArtifact]:
        return self._artifacts.get(key)

    def by_commitment(self, commitment: str) -> Optional[MemoryArtifact]:
        key = self._by_commitment.get(commitment)
        return self._artifacts.get(key) if key else None

    def latest(self, memory_id: str) -> Optional[MemoryArtifact]:
        v = self._latest.get(memory_id)
        return self._artifacts.get(f"{memory_id}@v{v}") if v else None

    def all(self) -> List[MemoryArtifact]:
        return list(self._artifacts.values())

    def servable(self) -> List[MemoryArtifact]:
        """What a task may actually retrieve. The resurrection firewall and the
        recovery barrier both work by shrinking this set."""
        return [a for a in self._artifacts.values() if a.is_servable()]

    def servable_of_type(self, artifact_type: ArtifactType,
                         patient_scope: Optional[str] = None) -> List[MemoryArtifact]:
        out = [a for a in self.servable() if a.artifact_type == artifact_type]
        if patient_scope is not None:
            out = [a for a in out if a.patient_scope == patient_scope]
        return sorted(out, key=lambda a: (a.created_at, a.key))

    def set_state(self, key: str, target: MemoryState, incident_id: str,
                  reason: str) -> bool:
        """Monotone frontier enforcement (Section 6.6)."""
        art = self._artifacts.get(key)
        if art is None:
            return False
        if not state_transition_allowed(art.state, target):
            self.ledger.log_event(incident_id, self.owner.value, "state_change_rejected",
                                  key, {"from": art.state.value, "to": target.value})
            return False
        art.state = target
        art.updated_at = _now()
        if target in (MemoryState.QUARANTINED, MemoryState.TOMBSTONED):
            art.quarantine_reason = reason
            self.index.remove(key)   # non-servable memories leave the retrieval index
        self.ledger.record_state_change(art, incident_id, reason)
        return True

    def next_version(self, memory_id: str) -> int:
        return self._latest.get(memory_id, 0) + 1

    def stats(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for a in self._artifacts.values():
            counts[a.state.value] = counts.get(a.state.value, 0) + 1
        counts["total"] = len(self._artifacts)
        return counts

    def snapshot(self) -> Dict[str, Any]:
        """Deep snapshot so every recovery condition starts from an identical
        memory state (Section 9.1 step 1)."""
        import copy
        return {
            "artifacts": copy.deepcopy(self._artifacts),
            "by_commitment": dict(self._by_commitment),
            "latest": dict(self._latest),
        }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        import copy
        self._artifacts = copy.deepcopy(snapshot["artifacts"])
        self._by_commitment = dict(snapshot["by_commitment"])
        self._latest = dict(snapshot["latest"])
        self.index = SketchIndex(self.encoder, self.owner.value)
        for key, art in self._artifacts.items():
            if art.is_servable():
                self.index.add(key, art.content)


__all__ = ["LedgerStore", "MemoryVault"]
