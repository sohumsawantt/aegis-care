"""Recovery certificate (functional requirement F9).

"Machine-readable and human-readable incident summary with counts and
unresolved risk." The certificate is signed by the coordinator and contains no
patient content - it is exactly the artifact an auditor could be handed.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class RecoveryCertificate:
    incident_id: str
    issued_at: str
    issuer: str
    seeds: List[str]
    counts: Dict[str, int]
    closure_reached: bool
    enforcement: Dict[str, Any]
    resurrection: Dict[str, Any]
    privacy: Dict[str, Any]
    overhead: Dict[str, Any]
    unresolved_risk: List[str]
    safe_resume: bool
    options: Dict[str, Any]
    signature: str = ""

    def signable(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("signature", None)
        return data

    def to_text(self) -> str:
        """Human-readable rendering for the reviewer UI and the report."""
        lines = [
            "=" * 68,
            f"AEGIS-CARE RECOVERY CERTIFICATE   incident {self.incident_id}",
            "=" * 68,
            f"Issued      : {self.issued_at} by {self.issuer}",
            f"Seeds       : {', '.join(self.seeds)}",
            f"Configuration: {self.options.get('label', 'full CARE')}",
            "",
            "OUTCOME",
            f"  candidates considered : {self.counts.get('candidates', 0)}",
            f"  influence confirmed   : {self.counts.get('confirmed', 0)}",
            f"  cleared (retained)    : {self.counts.get('cleared', 0)}",
            f"  repaired              : {self.counts.get('repaired', 0)}",
            f"  quarantined           : {self.counts.get('quarantined', 0)}",
            f"  closure reached       : {self.closure_reached}",
            "",
            "ENFORCEMENT",
            f"  tombstones            : {self.enforcement.get('tombstones', 0)}",
            f"  revoked commitments   : {self.enforcement.get('revoked_commitments', 0)}",
            f"  firewall armed        : {self.enforcement.get('enabled', False)}",
            f"  resurrection blocked  : {self.resurrection.get('blocked', 0)}"
            f"/{self.resurrection.get('attempts', 0)}",
            "",
            "PRIVACY",
            f"  capsules issued       : {self.privacy.get('capsules', 0)}",
            f"  bytes released        : {self.privacy.get('total_bytes', 0)}",
            f"  raw content exported  : {self.privacy.get('raw_content_exported', 'none')}",
            f"  fields released       : {', '.join(self.privacy.get('fields', []))}",
            "",
            "OVERHEAD",
            f"  local replays         : {self.overhead.get('replays', 0)}",
            f"  model calls           : {self.overhead.get('model_calls', 0)}",
            f"  FHIR reads            : {self.overhead.get('fhir_reads', 0)}",
            f"  wall seconds          : {self.overhead.get('wall_seconds', 0)}",
            "",
            "UNRESOLVED RISK",
        ]
        lines += [f"  - {r}" for r in self.unresolved_risk] or ["  - none recorded"]
        lines += [
            "",
            f"SAFE RESUME: {'APPROVED' if self.safe_resume else 'BLOCKED - review required'}",
            "=" * 68,
        ]
        return "\n".join(lines)


def build_certificate(incident_id: str, result, env, options) -> RecoveryCertificate:
    """Assemble and sign the certificate from a completed recovery."""
    capsule_stats = env.ledger.capsule_stats(incident_id)

    unresolved: List[str] = []
    if not result.closure_reached:
        unresolved.append(
            "frontier not exhausted within round budget; descendants may remain active")
    for q in result.quarantined:
        unresolved.append(f"quarantined {q['memory_key']}: {q['reason']}")
    if result.resurrection_probe.get("resurrection_rate", 0.0) > 0.0:
        unresolved.append(
            f"resurrection probes not fully blocked "
            f"({result.resurrection_probe.get('blocked')}/"
            f"{result.resurrection_probe.get('attempts')} blocked)")
    if not options.use_counterfactual:
        unresolved.append(
            "counterfactual confirmation disabled: destructive actions rest on "
            "similarity alone and precision is not defensible")
    if not options.use_enforcement:
        unresolved.append("resurrection firewall disabled: withdrawn influence may return")

    # Safe resume requires closure, a fully blocked probe set, and no reliance
    # on similarity-only evidence (Section 7.1 invariants).
    safe_resume = bool(
        result.closure_reached
        and result.resurrection_probe.get("resurrection_rate", 1.0) == 0.0
        and options.use_counterfactual
    )

    cert = RecoveryCertificate(
        incident_id=incident_id,
        issued_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        issuer="coordinator",
        seeds=list(result.seeds),
        counts={
            "candidates": len(result.candidates_considered),
            "confirmed": len(result.confirmed),
            "cleared": len(result.cleared),
            "repaired": len(result.repaired),
            "quarantined": len(result.quarantined),
            "verdicts": len(result.verdicts),
        },
        closure_reached=result.closure_reached,
        enforcement=dict(result.enforcement),
        resurrection={k: v for k, v in result.resurrection_probe.items() if k != "details"},
        privacy={
            "capsules": capsule_stats["capsules"],
            "total_bytes": capsule_stats["total_bytes"],
            "fields": capsule_stats["distinct_fields_released"],
            "raw_content_exported": "none",
            "scoping_enabled": options.use_scoping,
        },
        overhead=dict(result.overhead),
        unresolved_risk=unresolved,
        safe_resume=safe_resume,
        options={**{k: v for k, v in result.options.items()}, "label": options.label()},
    )
    cert.signature = env.keyring.sign("coordinator", cert.signable())
    return cert


__all__ = ["RecoveryCertificate", "build_certificate"]
