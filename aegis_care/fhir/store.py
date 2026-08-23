"""In-process FHIR R4 sandbox: the trusted source of truth.

Clean-room recompilation (Section 5.5.3) rebuilds memories from "trusted,
currently authorized FHIR resources". That makes this store the anchor of the
whole system, so it supports snapshots: an incident freezes the record state,
and a correction (scenario family F4) advances it.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

from .generator import generate_bundle


class FHIRStore:
    """A minimal FHIR R4 server supporting the search parameters this project
    needs: Patient by identifier/name/birthdate, and resources by subject."""

    def __init__(self, n_patients: int = 100, seed: int = 20260729) -> None:
        self._resources: Dict[str, List[Dict[str, Any]]] = generate_bundle(n_patients, seed)
        self._index: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._rebuild_index()
        self.read_count = 0

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def _rebuild_index(self) -> None:
        self._index = {}
        for rtype, items in self._resources.items():
            self._index[rtype] = {item["id"]: item for item in items}

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------
    def read(self, resource_type: str, resource_id: str) -> Optional[Dict[str, Any]]:
        self.read_count += 1
        found = self._index.get(resource_type, {}).get(resource_id)
        return copy.deepcopy(found) if found else None

    def search(self, resource_type: str, **params: Any) -> List[Dict[str, Any]]:
        """Search with the subset of FHIR search semantics used here."""
        self.read_count += 1
        results = self._resources.get(resource_type, [])

        if "identifier" in params and params["identifier"]:
            wanted = str(params["identifier"]).lower()
            results = [
                r for r in results
                if any(str(i.get("value", "")).lower() == wanted for i in r.get("identifier", []))
            ]
        if "family" in params and params["family"]:
            wanted = str(params["family"]).lower()
            results = [
                r for r in results
                if any(str(n.get("family", "")).lower() == wanted for n in r.get("name", []))
            ]
        if "given" in params and params["given"]:
            wanted = str(params["given"]).lower()
            results = [
                r for r in results
                if any(wanted in [str(g).lower() for g in n.get("given", [])]
                       for n in r.get("name", []))
            ]
        if "birthdate" in params and params["birthdate"]:
            wanted = str(params["birthdate"])
            results = [r for r in results if r.get("birthDate") == wanted]
        if "patient" in params and params["patient"]:
            ref = f"Patient/{params['patient']}"
            results = [r for r in results if r.get("subject", {}).get("reference") == ref]
        if "code" in params and params["code"]:
            wanted = {c.strip() for c in str(params["code"]).split(",")}
            results = [
                r for r in results
                if any(c.get("code") in wanted for c in r.get("code", {}).get("coding", []))
            ]
        if "category" in params and params["category"]:
            wanted = str(params["category"])
            results = [
                r for r in results
                if any(c.get("code") == wanted
                       for cat in r.get("category", []) for c in cat.get("coding", []))
            ]

        return copy.deepcopy(results)

    def bundle(self, resource_type: str, entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        entries = list(entries)
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(entries),
            "entry": [{"fullUrl": f"{resource_type}/{e['id']}", "resource": e} for e in entries],
        }

    # ------------------------------------------------------------------
    # Convenience accessors used by the agent runtimes
    # ------------------------------------------------------------------
    def patient_ids(self) -> List[str]:
        return [p["id"] for p in self._resources["Patient"]]

    def patient_display(self, patient_id: str) -> str:
        p = self._index.get("Patient", {}).get(patient_id)
        if not p:
            return "UNKNOWN"
        name = p["name"][0]
        return f"{' '.join(name['given'])} {name['family']}"

    def patient_mrn(self, patient_id: str) -> str:
        p = self._index.get("Patient", {}).get(patient_id)
        return p["identifier"][0]["value"] if p else "UNKNOWN"

    def observations_for(self, patient_id: str, restricted_ok: bool = False) -> List[Dict[str, Any]]:
        obs = self.search("Observation", patient=patient_id)
        if not restricted_ok:
            obs = [o for o in obs if not o.get("_aegisRestricted")]
        return sorted(obs, key=lambda o: o["id"])

    def conditions_for(self, patient_id: str, restricted_ok: bool = False) -> List[Dict[str, Any]]:
        conds = self.search("Condition", patient=patient_id)
        if not restricted_ok:
            conds = [c for c in conds if not c.get("_aegisRestricted")]
        return sorted(conds, key=lambda c: c["id"])

    # ------------------------------------------------------------------
    # Mutation, used only by scenario construction (e.g. F4 stale-corrected-fact)
    # ------------------------------------------------------------------
    def ensure_restricted_observation(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """Guarantee the patient has a physician-only field.

        The access-scope laundering family (F3) needs a restricted resource to
        exist before it can be laundered. Adding one is part of the synthetic
        incident overlay Section 8.1 permits; it does not alter any underlying
        clinical fact used by the other families.
        """
        existing = [o for o in self.search("Observation", patient=patient_id)
                    if o.get("_aegisRestricted")]
        if existing:
            return sorted(existing, key=lambda o: o["id"])[0]

        from .generator import OBSERVATION_CATALOG, sandbox_time

        spec = next(s for s in OBSERVATION_CATALOG if s["restricted"])
        # Deterministic value derived from the patient id, so runs are reproducible.
        digits = int("".join(ch for ch in patient_id if ch.isdigit()) or "0")
        value = round(spec["low"] + (digits % max(1, spec["high"] - spec["low"])), 1)
        obs = {
            "resourceType": "Observation",
            "id": f"{patient_id}-OBSR1",
            "status": "final",
            "category": [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": spec["category"]}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": spec["code"],
                                 "display": spec["display"]}], "text": spec["display"]},
            "subject": {"reference": f"Patient/{patient_id}"},
            "effectiveDateTime": sandbox_time(-5, hour=8),
            "valueQuantity": {"value": value, "unit": spec["unit"],
                              "system": "http://unitsofmeasure.org", "code": spec["unit"]},
            "_aegisRestricted": True,
        }
        self._resources["Observation"].append(obs)
        self._rebuild_index()
        return obs

    def correct_observation(self, obs_id: str, new_value: float) -> bool:
        for obs in self._resources.get("Observation", []):
            if obs["id"] == obs_id:
                obs["valueQuantity"]["value"] = new_value
                obs["status"] = "corrected"
                self._rebuild_index()
                return True
        return False

    def snapshot(self) -> Dict[str, Any]:
        """Freeze the record state so every recovery condition runs against an
        identical sandbox (Section 9.1 step 1)."""
        return copy.deepcopy(self._resources)

    def restore(self, snapshot: Dict[str, Any]) -> None:
        self._resources = copy.deepcopy(snapshot)
        self._rebuild_index()

    def stats(self) -> Dict[str, int]:
        return {rtype: len(items) for rtype, items in self._resources.items()}


__all__ = ["FHIRStore"]
