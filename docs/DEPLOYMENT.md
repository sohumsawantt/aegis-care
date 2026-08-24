# Operations console — deployment status

The console is a working application, not a mockup: real authentication, a real
permission model, durable incidents, and a real recovery engine underneath. This
document states plainly what is production-shaped, what is still sandbox, and what a
hospital would have to do before clinical use.

Read this before showing the console to anyone who might mistake it for a deployable
clinical system.

---

## Running it

```bash
# Sandbox: synthetic records, in-memory incidents
python -m aegis_care.cli console

# Persistent incidents
python -m aegis_care.cli console --db ./aegis-ops.sqlite

# Against a real FHIR R4 server
python -m aegis_care.cli console \
  --fhir-url https://hapi.fhir.org/baseR4 \
  --db ./aegis-ops.sqlite
```

Equivalent environment variables: `AEGIS_FHIR_URL`, `AEGIS_FHIR_TOKEN`, `AEGIS_OPS_DB`.

When `AEGIS_FHIR_URL` is set the header badge turns green and reads **CONNECTED EHR**;
the drill endpoint is refused with HTTP 403 so nobody can plant synthetic contamination
against a live record source.

---

## What is genuinely production-shaped

| Area | Implementation |
| --- | --- |
| Credentials | PBKDF2-HMAC-SHA256, 240 000 rounds, per-operator salt, constant-time compare |
| Sessions | 32-byte URL-safe tokens, server-side, 60-minute expiry, revoked on sign-out |
| Authorisation | Five operator roles → twelve permissions, enforced in the service layer, not the UI |
| Durability | SQLite; incidents, notes, operators, sessions and the operational audit trail survive restart |
| Audit | Every sign-in, failed sign-in, triage, confirmation, recovery, review decision and closure is recorded with actor, role, subject and timestamp |
| Workflow integrity | Explicit state machine; recovery is refused without a human-confirmed seed; closure is refused while artifacts await review |
| Record access | Read-only FHIR client; `correct_observation` and `snapshot` raise rather than touching a live server |
| Evidence | One-click JSON pack: incident, signed certificate, operational audit, engine events, verdicts, disclosure accounting |

The clinical safety properties are inherited from the engine and covered by the existing
invariant tests: role separation, no raw-content centralisation, fail-closed
reconstruction, monotone incident frontier, and the resurrection firewall.

---

## What is still sandbox

**Synthetic records by default.** Without `--fhir-url` the console runs against 100
generated patients. Nothing about the workflow changes when you point it at a real
server, but the data is fabricated.

**The agents are simulated.** The three role-separated runtimes are part of this project.
In a real deployment they would be *your* clinical assistants, and AEGIS-Care would attach
to their memory stores. That integration does not exist — see below.

**Drills plant contamination.** `POST /api/ops/drill` creates a genuine poisoning event so
the workflow can be practised. It runs the real agent write path and must be found by the
real recovery loop, but it exists because we cannot wait for an authentic incident. It is
disabled automatically against a live EHR.

**Seeded accounts.** Six demo operators with printed passwords, shown on the sign-in
screen. Delete them before any real use.

---

## Required before clinical deployment

Ordered by how hard they are to retrofit.

1. **Identity federation.** Replace the local password store with the hospital's OIDC/SAML
   provider. `OpsService.sign_in()` is the only seam that needs to change; everything above
   it works against `Operator` records regardless of how the credential check happened.
2. **Agent integration.** The real work. `AgentRuntime` must be reimplemented against your
   assistants' actual memory stores, with their write path emitting parent commitments and
   replay recipes. Without recipes, recompilation cannot run and every affected artifact
   falls through to quarantine — safe, but far less useful.
3. **Transport and storage security.** TLS termination, encryption at rest for the
   incident database, secret management for `AEGIS_FHIR_TOKEN`, and key custody for the
   Ed25519 signing identities (currently derived deterministically from a fixed root seed,
   which is correct for reproducible research and wrong for production).
4. **Sensitivity labelling.** `RESTRICTED_CODES` in `fhir/remote.py` is a placeholder list.
   Map it to your site's own security labels or value sets.
5. **Governance.** Institutional review, clinical safety case (DCB0129/0160 or local
   equivalent), data protection impact assessment, and an incident escalation policy that
   says who is allowed to confirm a seed and who signs off a safe resume.
6. **Operational hardening.** Rate limiting on sign-in, session binding, structured logging
   to your SIEM, backup and retention policy for the audit trail, and load testing against
   realistic memory volumes.

Items 1 and 3 are ordinary engineering. Item 2 is the substantial one. Items 5 and 6 are
organisational and cannot be shortcut by code.

---

## What this system still does not claim

Unchanged from the research scope, and worth repeating because a polished interface
invites over-reading:

- No diagnosis, prognosis, treatment planning, medication ordering, or patient-facing advice
- No claim of improved morbidity, mortality, or clinical outcomes
- No HIPAA / DPDP Act / GDPR certification
- No claim that recovery sketches are confidential — the measured membership-inference
  advantage is reported in the research results, not hidden
- Mechanism-level improvement demonstrated in simulation only

The console makes the workflow usable. It does not make the underlying claims stronger.
