"""Connector for a real FHIR R4 server.

`FHIRStore` is the synthetic sandbox used for research and reproducibility.
This is the same interface backed by an actual FHIR endpoint - a hospital's
server, a HAPI FHIR instance, or a public R4 test server - so the operations
console can run against real infrastructure without any change above this layer.

    from aegis_care.fhir.remote import RemoteFHIRStore
    store = RemoteFHIRStore("https://hapi.fhir.org/baseR4")

What this deliberately does NOT do:

* It never writes. Clean-room recompilation reads the trusted record and rebuilds
  agent memory; it does not modify the chart. `correct_observation` therefore
  raises rather than silently no-opping.
* It has no snapshot semantics. A live server is not frozen, so the paired
  experimental protocol (Section 9.1) is only valid against the sandbox. The
  console works fine; the experiment runner should not be pointed here.

Both limits are enforced in code rather than left to documentation.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

from ..policy.rbac import FieldCategory, categorize_resource

#: Codes the policy layer treats as physician-only. A real deployment maps this
#: to the site's own sensitivity labelling (e.g. a security label on the
#: resource, or a local value set), not to a hard-coded list.
RESTRICTED_CODES = {"75626-2", "44261-6"}
RESTRICTED_CATEGORIES = {"social-history", "survey"}


class RemoteFHIRError(RuntimeError):
    pass


class RemoteFHIRStore:
    """Read-only FHIR R4 client with the same surface as :class:`FHIRStore`."""

    def __init__(self, base_url: str, *, token: Optional[str] = None,
                 timeout: float = 20.0, verify: bool = True,
                 page_size: int = 50) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify = verify
        self.page_size = page_size
        self.read_count = 0
        self._patient_cache: Dict[str, Dict[str, Any]] = {}
        self._client = None

    # ------------------------------------------------------------------
    def _http(self):
        if self._client is None:
            import httpx

            headers = {"Accept": "application/fhir+json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.Client(base_url=self.base_url, headers=headers,
                                        timeout=self.timeout, verify=self.verify)
        return self._client

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.read_count += 1
        try:
            response = self._http().get(path, params=params or {})
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RemoteFHIRError(f"FHIR request to {path} failed: {exc}") from exc

    @staticmethod
    def _entries(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def _annotate(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Attach the sensitivity flag the policy engine relies on."""
        restricted = False
        for coding in resource.get("code", {}).get("coding", []):
            if coding.get("code") in RESTRICTED_CODES:
                restricted = True
        for cat in resource.get("category", []):
            for coding in cat.get("coding", []):
                if coding.get("code") in RESTRICTED_CATEGORIES:
                    restricted = True
        # Honour an explicit security label if the server sets one.
        for label in resource.get("meta", {}).get("security", []):
            if label.get("code") in ("R", "V", "RESTRICTED", "VERY_RESTRICTED"):
                restricted = True
        resource["_aegisRestricted"] = restricted
        return resource

    # ------------------------------------------------------------------
    # Read API (mirrors FHIRStore)
    # ------------------------------------------------------------------
    def read(self, resource_type: str, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            resource = self._get(f"/{resource_type}/{resource_id}")
        except RemoteFHIRError:
            return None
        if resource.get("resourceType") != resource_type:
            return None
        if resource_type == "Patient":
            self._patient_cache[resource_id] = resource
        return self._annotate(resource)

    def search(self, resource_type: str, **params: Any) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"_count": self.page_size}
        mapping = {
            "identifier": "identifier", "family": "family", "given": "given",
            "birthdate": "birthdate", "patient": "subject", "code": "code",
            "category": "category",
        }
        for key, value in params.items():
            if value in (None, ""):
                continue
            target = mapping.get(key, key)
            query[target] = f"Patient/{value}" if target == "subject" else value
        bundle = self._get(f"/{resource_type}", query)
        return [self._annotate(r) for r in self._entries(bundle)]

    def bundle(self, resource_type: str, entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        entries = list(entries)
        return {
            "resourceType": "Bundle", "type": "searchset", "total": len(entries),
            "entry": [{"fullUrl": f"{resource_type}/{e.get('id')}", "resource": e}
                      for e in entries],
        }

    # ------------------------------------------------------------------
    # Convenience accessors used by the agent runtimes
    # ------------------------------------------------------------------
    def patient_ids(self) -> List[str]:
        bundle = self._get("/Patient", {"_count": self.page_size})
        ids = []
        for patient in self._entries(bundle):
            if patient.get("id"):
                ids.append(patient["id"])
                self._patient_cache[patient["id"]] = patient
        return ids

    def _patient(self, patient_id: str) -> Optional[Dict[str, Any]]:
        if patient_id not in self._patient_cache:
            self.read("Patient", patient_id)
        return self._patient_cache.get(patient_id)

    def patient_display(self, patient_id: str) -> str:
        patient = self._patient(patient_id)
        if not patient or not patient.get("name"):
            return "UNKNOWN"
        name = patient["name"][0]
        given = " ".join(name.get("given", []))
        return f"{given} {name.get('family', '')}".strip() or "UNKNOWN"

    def patient_mrn(self, patient_id: str) -> str:
        patient = self._patient(patient_id)
        for identifier in (patient or {}).get("identifier", []):
            if identifier.get("value"):
                return identifier["value"]
        return "UNKNOWN"

    def observations_for(self, patient_id: str,
                         restricted_ok: bool = False) -> List[Dict[str, Any]]:
        obs = self.search("Observation", patient=patient_id)
        obs = [o for o in obs if o.get("valueQuantity")]
        if not restricted_ok:
            obs = [o for o in obs if not o.get("_aegisRestricted")]
        return sorted(obs, key=lambda o: str(o.get("id")))

    def conditions_for(self, patient_id: str,
                       restricted_ok: bool = False) -> List[Dict[str, Any]]:
        conds = self.search("Condition", patient=patient_id)
        if not restricted_ok:
            conds = [c for c in conds if not c.get("_aegisRestricted")]
        return sorted(conds, key=lambda c: str(c.get("id")))

    def ensure_restricted_observation(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """Drill support only. Never fabricates data on a live server."""
        existing = [o for o in self.search("Observation", patient=patient_id)
                    if o.get("_aegisRestricted")]
        return sorted(existing, key=lambda o: str(o.get("id")))[0] if existing else None

    # ------------------------------------------------------------------
    # Write / snapshot: refused by design
    # ------------------------------------------------------------------
    def correct_observation(self, obs_id: str, new_value: float) -> bool:
        raise RemoteFHIRError(
            "AEGIS-Care never writes to the clinical record. Corrections belong in the "
            "EHR; this system rebuilds agent memory from the corrected record.")

    def snapshot(self) -> Dict[str, Any]:
        raise RemoteFHIRError(
            "A live FHIR server cannot be frozen, so the paired experimental protocol "
            "is not valid against it. Use the synthetic sandbox for experiments.")

    def restore(self, snapshot: Dict[str, Any]) -> None:
        raise RemoteFHIRError("Cannot restore a snapshot onto a live FHIR server.")

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for rtype in ("Patient", "Observation", "Condition", "Encounter"):
            try:
                bundle = self._get(f"/{rtype}", {"_summary": "count"})
                out[rtype] = int(bundle.get("total", 0))
            except RemoteFHIRError:
                out[rtype] = 0
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["RemoteFHIRStore", "RemoteFHIRError"]
