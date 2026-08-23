"""Empirical leakage measurement (proposal Sections 7.2 and 10).

Section 7.2 is explicit: "A compact sketch is not inherently private ...
The system guarantees only that raw content is not intentionally exported
through the defined recovery interface. Residual leakage is measured
empirically."

So we attack our own capsules:

  * protected-attribute inference  - can an adversary predict gender / a
    restricted-condition flag from the sketch alone?
  * membership inference           - can it tell whether a given memory was in
    the incident's candidate set?
  * linkability                    - can repeated recovery events be joined back
    to the same patient across recipients?
  * released-field audit           - exactly which fields ever left a runtime

Advantage is reported over the majority-class baseline, so 0.0 means "no better
than guessing". Nothing here claims formal differential privacy (Section 11.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..care.capsule import ALLOWED_CAPSULE_FIELDS, FORBIDDEN_CAPSULE_FIELDS, CapsuleMinter
from ..memory.sketch import SketchEncoder
from ..policy.rbac import Role


@dataclass
class AttackResult:
    name: str
    n: int
    accuracy: float
    baseline: float
    advantage: float
    detail: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return (f"{self.name}: acc={self.accuracy:.3f} baseline={self.baseline:.3f} "
                f"advantage={self.advantage:+.3f} (n={self.n})")


class PrivacyAuditor:
    """Runs the leakage attacks against an environment's recovery interface."""

    def __init__(self, env) -> None:
        self.env = env
        self.encoder: SketchEncoder = env.encoder

    # ==================================================================
    def attribute_inference(self, *, recipient: str = "nursing",
                            incident_id: str = "PRIVACY-PROBE",
                            attribute: str = "gender") -> AttackResult:
        """Train a classifier on (sketch -> protected attribute).

        The attacker is given the *strongest realistic* position: labelled
        sketches for half the patients, predicting the other half.
        """
        sketches, labels = [], []
        for pid in self.env.fhir.patient_ids():
            patient = self.env.fhir.read("Patient", pid)
            if patient is None:
                continue
            text = self._memory_like_text(pid)
            sketches.append(self.encoder.sketch(
                text, recipient=recipient, purpose="incident_recovery",
                incident_id=incident_id))
            if attribute == "gender":
                labels.append(patient.get("gender", "unknown"))
            elif attribute == "restricted_flag":
                restricted = [o for o in self.env.fhir.observations_for(pid, restricted_ok=True)
                              if o.get("_aegisRestricted")]
                labels.append("yes" if restricted else "no")
            else:
                raise ValueError(f"unknown attribute {attribute}")

        return self._fit_and_score(f"attribute_inference[{attribute}]", sketches, labels)

    # ==================================================================
    def membership_inference(self, incident, capsules,
                             *, recipient: str = "nursing") -> AttackResult:
        """Can an adversary decide whether a memory was a recovery candidate?

        The attacker sees the capsule sketch and each memory's own sketch, and
        thresholds on similarity - the natural attack against this interface.
        """
        if not capsules:
            return AttackResult("membership_inference", 0, 0.0, 0.0, 0.0,
                                {"note": "no capsules issued"})
        capsule = capsules[0]
        members = set(incident.true_contaminated) | {incident.seed_key}

        sims, truth = [], []
        for artifact in self.env.all_artifacts():
            local = self.encoder.sketch(
                artifact.content, recipient=capsule.recipient,
                purpose=capsule.purpose, incident_id=capsule.incident_id)
            sims.append(self.encoder.similarity(local, capsule.sketch))
            truth.append(artifact.key in members)

        if not sims or len(set(truth)) < 2:
            return AttackResult("membership_inference", len(sims), 0.0, 0.0, 0.0,
                                {"note": "degenerate label set"})

        sims_arr = np.asarray(sims)
        truth_arr = np.asarray(truth, dtype=bool)
        # Best achievable threshold: an upper bound on this attack.
        best_acc = 0.0
        for threshold in np.unique(sims_arr):
            acc = float(((sims_arr >= threshold) == truth_arr).mean())
            best_acc = max(best_acc, acc)
        baseline = float(max(truth_arr.mean(), 1 - truth_arr.mean()))
        return AttackResult(
            "membership_inference", len(sims), round(best_acc, 4), round(baseline, 4),
            round(best_acc - baseline, 4),
            {"note": "best-threshold upper bound on the attack"})

    # ==================================================================
    def linkability(self, *, n_patients: int = 40,
                    incident_id: str = "PRIVACY-PROBE") -> AttackResult:
        """Can an honest-but-curious coordinator join two recipients' capsules
        for the same patient?

        With receiver scoping this should be at chance; without it, trivial.
        """
        patient_ids = self.env.fhir.patient_ids()[:n_patients]
        scoped_hits, unscoped_hits = 0, 0

        for pid in patient_ids:
            text = self._memory_like_text(pid)
            a = self.encoder.sketch(text, recipient="nursing",
                                    purpose="incident_recovery", incident_id=incident_id)
            # Scoped: match against every candidate under a *different* recipient.
            best_scoped, best_scoped_pid = -1.0, None
            best_unscoped, best_unscoped_pid = -1.0, None
            for other in patient_ids:
                other_text = self._memory_like_text(other)
                b = self.encoder.sketch(other_text, recipient="clinical_summary",
                                        purpose="incident_recovery",
                                        incident_id=incident_id)
                s = self.encoder.similarity(a, b)
                if s > best_scoped:
                    best_scoped, best_scoped_pid = s, other
                # Unscoped control: one global projection for everyone.
                u_a = self.encoder.sketch(text, recipient="global", purpose="global",
                                          incident_id="global")
                u_b = self.encoder.sketch(other_text, recipient="global",
                                          purpose="global", incident_id="global")
                su = self.encoder.similarity(u_a, u_b)
                if su > best_unscoped:
                    best_unscoped, best_unscoped_pid = su, other
            scoped_hits += int(best_scoped_pid == pid)
            unscoped_hits += int(best_unscoped_pid == pid)

        n = len(patient_ids)
        baseline = 1.0 / n if n else 0.0
        return AttackResult(
            "linkability[cross-recipient]", n,
            round(scoped_hits / n, 4) if n else 0.0, round(baseline, 4),
            round((scoped_hits / n) - baseline, 4) if n else 0.0,
            {"unscoped_ablation_accuracy": round(unscoped_hits / n, 4) if n else 0.0,
             "note": "unscoped ablation shows what scoping prevents"},
        )

    # ==================================================================
    def released_field_audit(self, incident_id: str) -> Dict[str, Any]:
        """Exact logging of fields released to the coordinator (Section 7.2)."""
        stats = self.env.ledger.capsule_stats(incident_id)
        released = set(stats["distinct_fields_released"])
        return {
            "capsules": stats["capsules"],
            "total_bytes": stats["total_bytes"],
            "fields_released": sorted(released),
            "undeclared_fields": sorted(released - ALLOWED_CAPSULE_FIELDS),
            "raw_content_fields": sorted(released & FORBIDDEN_CAPSULE_FIELDS),
            "raw_content_exported": bool(released & FORBIDDEN_CAPSULE_FIELDS),
        }

    # ==================================================================
    def full_audit(self, incident, capsules) -> Dict[str, Any]:
        return {
            "attribute_gender": self.attribute_inference(attribute="gender").__dict__,
            "attribute_restricted": self.attribute_inference(
                attribute="restricted_flag").__dict__,
            "membership": self.membership_inference(incident, capsules).__dict__,
            "linkability": self.linkability().__dict__,
            "released_fields": self.released_field_audit(incident.incident_id),
        }

    # ==================================================================
    def _memory_like_text(self, patient_id: str) -> str:
        """Reconstruct the kind of text a memory would contain for this patient,
        so the attack operates on realistic sketches."""
        display = self.env.fhir.patient_display(patient_id)
        mrn = self.env.fhir.patient_mrn(patient_id)
        obs = self.env.fhir.observations_for(patient_id)[:6]
        lines = [f"Shift handover - {display} (record {patient_id}, MRN {mrn})."]
        lines += [
            f"  - {o['code']['text']} {o['valueQuantity']['value']} {o['valueQuantity']['unit']}"
            for o in obs
        ]
        return "\n".join(lines)

    @staticmethod
    def _fit_and_score(name: str, sketches: List[List[int]],
                       labels: List[str]) -> AttackResult:
        if len(set(labels)) < 2 or len(sketches) < 8:
            return AttackResult(name, len(sketches), 0.0, 0.0, 0.0,
                                {"note": "insufficient data"})
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score

        X = np.asarray(sketches, dtype=float)
        y = np.asarray(labels)
        counts = {label: int((y == label).sum()) for label in set(labels)}
        baseline = max(counts.values()) / len(y)

        model = LogisticRegression(max_iter=2000)
        n_splits = min(5, min(counts.values()))
        if n_splits < 2:
            return AttackResult(name, len(y), baseline, baseline, 0.0,
                                {"note": "class too small for cross-validation"})
        scores = cross_val_score(model, X, y, cv=n_splits, scoring="accuracy")
        accuracy = float(scores.mean())
        return AttackResult(name, len(y), round(accuracy, 4), round(baseline, 4),
                            round(accuracy - baseline, 4), {"class_counts": counts})


__all__ = ["PrivacyAuditor", "AttackResult"]
