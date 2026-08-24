# AEGIS-Care

**A privacy-bounded memory recompiler for recovering poisoned clinical AI agents.**

A complete, runnable implementation of the NMIMS MPSTME B.Tech Computer Engineering
capstone proposal *"AEGIS-Care: Latent dependency discovery, local counterfactual
confirmation, clean-room replay, and resurrection-resistant repair under incomplete
provenance"* (v4.0).

> **Core question.** When one clinical AI memory is found to be wrong, can the system
> discover, repair, and verify everything that inherited the error **without opening every
> patient record**?

> ⚠️ **Scope.** Simulated FHIR sandbox with synthetic records only. No diagnosis, treatment,
> medication execution, real EHR connection, or compliance certification. See
> [Non-goals](#non-goals).

---

## The problem in one paragraph

A clinical record assistant stores an incorrect patient association, summarises it into a
nursing handover, and that handover into a clinical summary. When the original alias is
found and deleted, the handover and summary **remain active** — authentic-looking,
well-formed, and still wrong. Wiping all memory fixes it but destroys everything the agent
legitimately learned. A central investigator could reconstruct the truth, but only by
reading patient content they are not authorised to see. AEGIS-Care is the third option.

## The CARE loop

| Stage | What it does | Why it exists |
| --- | --- | --- |
| **C** — Candidate discovery | Traverses exact lineage; where edges are missing, searches a local index using a receiver-scoped latent sketch | Explicit provenance is incomplete in practice |
| **A** — Attribution | Replays each candidate's creation *without* the suspected ancestor, inside the owning runtime | Similarity is not causality |
| **R** — Recompilation | Rebuilds confirmed descendants from trusted FHIR resources; quarantines what it cannot rebuild | Deletion destroys utility; guessing is unsafe |
| **E** — Enforcement | Signs tombstones and arms a resurrection firewall over later writes and retrievals | Cleanup that can be undone is not recovery |

The latent sketch **finds what may have inherited the error**; counterfactual replay **tests
whether it actually did**; recompilation **restores useful state**; enforcement **keeps the
repaired system repaired**.

---

## Quick start

```bash
pip install -r requirements.txt

python -m aegis_care.cli console       # ← the operations console (the product)
python -m aegis_care.cli present       # guided demonstration of the method
python -m aegis_care.cli demo          # one incident end to end + recovery certificate
python -m aegis_care.cli baselines     # all nine recovery conditions, side by side
python -m aegis_care.cli privacy       # empirical leakage attacks on our own interface
python -m aegis_care.cli experiment    # the full paired matrix -> results/
python -m aegis_care.cli serve         # presentation + console + API at :8000
pytest -q                              # 229 tests
```

## Three interfaces

| Route | Interface | For |
| --- | --- | --- |
| `/` | **Operations console** | Clinical safety teams working real incidents |
| `/present` | Guided demonstration | Explaining the method to an audience |
| `/research` | Analyst console | Experiments, baselines, ablations |

## Operations console

The product. A clinical safety team signs in and works incidents end to end.

```bash
python -m aegis_care.cli console                       # synthetic sandbox
python -m aegis_care.cli console --db ./aegis.sqlite   # persistent incidents
python -m aegis_care.cli console --fhir-url https://hapi.fhir.org/baseR4
```

**The workflow**

1. **A clinician reports what they saw** — "the handover lists another patient's vitals".
   No technical vocabulary required; they pick from four plain-language issue types.
2. **A safety officer triages** and identifies the compromised memory. The picker lists
   memories that *name the reported patient but are filed under someone else* first —
   because a wrong-patient memory is filed under the wrong patient, and a naive patient
   filter would hide the very thing being looked for.
3. **Recovery runs** only after a human confirms the seed. Never on suspicion.
4. **A reviewer decides** on anything the system refused to rebuild rather than guess.
5. **The incident closes** with a signed certificate and a downloadable evidence pack.

**Roles and permissions** — five operator roles mapped to twelve permissions, enforced in
the service layer rather than by hiding buttons: clinicians can report but never run
recovery; reviewers decide on quarantined artifacts but cannot confirm seeds; auditors are
strictly read-only.

**Security** — PBKDF2-HMAC-SHA256 credentials (240 000 rounds, per-operator salt), 
server-side sessions with expiry, and a durable audit trail covering every sign-in,
triage, confirmation, recovery, review decision, and closure.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for exactly what is production-shaped, what is
still sandbox, and what a hospital must do before clinical use.

## Presentation mode

`python -m aegis_care.cli present` opens a guided, projector-sized walkthrough that runs
the **real system** act by act — nothing is pre-recorded.

| Act | What the audience sees |
| --- | --- |
| 1 · The System | Live sandbox figures; the coordinator's empty rights list |
| 2 · Normal Operation | The task runs clean; the chain animates left to right |
| 3 · The Poisoning | One corrupted alias propagates hop by hop; lineage edges go dark |
| 4 · Why Deletion Fails | Baseline B: the seed is struck through and the task is **still wrong** |
| 5 · Why Reset Is Worse | Baseline C: safety restored, BSR collapses to 0.000 |
| 6 · The CARE Loop | Four stage cards light up C → A → R → E with live counts and signed verdicts |
| 7 · Verification | The full capsule rendered as JSON, with the fields that *don't exist* struck through; certificate; resurrection probes |
| 8 · All Nine Conditions | The comparison table, computed live on frozen state |
| 9 · What Leaks | The four attacks, including the scoping ablation |

**Controls:** <kbd>←</kbd> <kbd>→</kbd> navigate · <kbd>Space</kbd> runs the current act ·
<kbd>N</kbd> toggles speaker notes. The **Scenario** button switches contamination family,
task, depth, and provenance condition mid-talk; **Restart** gives you a clean run for the
next audience.

Speaker notes are built in — press <kbd>N</kbd> for per-act talking points, including which
number to point at and why it matters.

The analyst console (every control exposed at once) is at **`/research`**.

No GPU, no Docker, no network, and no model download are required: the default clinical
model is a frozen deterministic composer, which also makes every counterfactual replay
byte-reproducible. To run against a real open-weight model instead:

```bash
python -m aegis_care.cli demo --model "openai:qwen3:8b@http://localhost:11434/v1"
```

Structured facts are always computed from FHIR, so swapping the model changes the prose but
can never change a safety predicate.

---

## What `demo` prints

```
── CONTAMINATED TRAJECTORY ────────────────────────────────────────────────────
  depth 0  registration      identity_hint       patient=S1084  CONTAMINATED <- SEED
  depth 1  nursing           lookup_strategy     patient=S1084  CONTAMINATED
  depth 2  nursing           handover            patient=S1084  CONTAMINATED
  depth 3  clinical_summary  clinical_summary    patient=S1084  CONTAMINATED
  depth 4  clinical_summary  aggregate           patient=S1084  CONTAMINATED

── CARE RECOVERY ──────────────────────────────────────────────────────────────
  C  candidates ranked   : 9
  A  confirmed / cleared : 4 / 5
  R  repaired / quarant. : 4 / 0
  E  tombstones          : 5
     closure reached     : True in 2 round(s)

  Clean-room repairs
    ...-lookup_strategy@v1   →  ...-lookup_strategy@v2    patient=S1000
    ...-handover@v1          →  ...-handover@v2           patient=S1000
    ...-clinical_summary@v1  →  ...-clinical_summary@v2   patient=S1000
    ...-aggregate@v1         →  ...-aggregate@v2          patient=S1000

── POST-RECOVERY VERIFICATION ─────────────────────────────────────────────────
  Follow-up task selects : S1000 (intended S1000) — CORRECT
```

Note the five candidates that were **cleared**: those are the matched clean control
trajectory. Counterfactual replay examined them and left them untouched.

---

## Results

Full matrix: 4 families × 3 depths × 5 provenance conditions × 9 recovery conditions
= **900 condition runs over 100 incidents in ~83 s**. Regenerate with
`python -m aegis_care.cli experiment`.

### Primary comparison

| id | Condition | RWH ↓ | Recall ↑ | Precision ↑ | BSR ↑ | UER ↓ | DRR ↓ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | No recovery | 0.267 | 0.000 | 1.000 | 1.000 | 0.111 | 1.00 |
| B | Delete confirmed seed only | 0.267 | 0.000 | 1.000 | 1.000 | 0.082 | 1.00 |
| C | Full memory reset | 0.000 | 1.000 | 0.271 | **0.000** | 0.000 | 1.00 |
| D | Explicit-lineage quarantine | 0.180 | 0.398 | 0.980 | 0.995 | 0.049 | 0.00 |
| E | Explicit-lineage clean replay | 0.180 | 0.398 | 0.980 | 1.000 | 0.047 | 0.00 |
| F | Sketch-only quarantine | 0.000 | 1.000 | **0.336** | **0.241** | 0.000 | 0.00 |
| G | Central raw-content oracle | 0.000 | 1.000 | 0.750 | 0.868 | **1.000** | 1.00 |
| H | Complete private oracle graph | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.00 |
| **I** | **AEGIS-Care / full CARE** | **0.000** | **1.000** | **1.000** | **1.000** | **0.000** | **0.00** |

Reading the table the way the proposal asks:

- **B is the motivating failure.** Deleting the seed leaves recall at 0 and residual harm
  unchanged — the descendants are still live retrieval cues.
- **C buys safety with everything else.** Perfect recall, zero clean state retained.
- **F is what happens without counterfactual confirmation.** Similarity alone finds every
  contaminated descendant *and* destroys three quarters of the clean state.
- **G is the governance problem.** It matches CARE on safety by reading every runtime's raw
  clinical content: UER 1.0.
- **I is the only non-oracle condition** that is simultaneously safe (RWH 0), precise
  (1.000), utility-preserving (BSR 1.000), non-leaking (UER 0), and resurrection-proof
  (DRR 0).

### RQ1 — recovery under provenance loss

Descendant recall as observable lineage is withheld:

| Condition | complete | random20 | random40 | random60 | targeted |
| --- | --- | --- | --- | --- | --- |
| D / E (explicit lineage) | 1.000 | 0.446 | 0.263 | 0.100 | 0.183 |
| F (sketch only) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| **I (full CARE)** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** |
| H (private oracle) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

This is the result the whole design exists to produce: with complete provenance, explicit
lineage is sufficient and the latent component earns nothing. As edges disappear,
provenance-only recovery collapses while CARE holds — **at precision 1.000 and BSR 1.000**,
which is what separates it from the sketch-only baseline that also holds recall.

### A negative result, reported

RQ1 hypothesised that *targeted* removal of cross-role and semantic-summary edges would harm
provenance-only recovery **more** than random removal. Compared at matched realised edge
loss, it did not:

| Condition | loss bucket | recall (targeted) | recall (random) | targeted worse by |
| --- | --- | --- | --- | --- |
| D | 25–50% | 0.500 | 0.260 | **−0.240** |
| D | 50–75% | 0.104 | 0.063 | **−0.042** |

The hypothesis is **not supported** in this benchmark. The explanation is structural: in a
short derivation chain the dominant factor is whether the *first* edge survives. Random
masking sometimes removes it; targeted masking, which prefers summary and cross-role edges,
often leaves it intact. Section 10.2 of the proposal commits to publishing negative results,
so this is reported rather than tuned away.

### Empirical leakage

The proposal explicitly refuses to call sketches private by construction, so the system
attacks its own recovery interface:

| Attack | Accuracy | Baseline | Advantage |
| --- | --- | --- | --- |
| Protected-attribute inference (gender) | 0.230 | 0.340 | −0.110 |
| Protected-attribute inference (restricted flag) | 0.590 | 0.750 | −0.160 |
| Membership inference | 0.929 | 0.643 | **+0.286** |
| Cross-recipient linkability | 0.025 | 0.025 | +0.000 |

Two honest readings:

1. **Membership inference works.** An adversary holding a capsule can tell, well above
   chance, whether a given memory was in the incident's candidate set. This is a real
   residual leak. The system claims only that *raw content* is never exported through the
   defined interface — and the released-field audit confirms exactly which 13 fields ever
   leave a runtime, with zero raw-content fields among them.
2. **Receiver scoping is load-bearing.** Cross-recipient linkage sits at chance (0.025).
   Remove the scoping and the same attack reaches **1.000** — an honest-but-curious
   coordinator could join every recovery event back to one patient. That is the
   "remove purpose/recipient scoping" ablation, measured.

---

## Architecture

```
                      ┌──────────────────────────────────┐
                      │      Recovery Coordinator        │
                      │  honest-but-curious; holds NO    │
                      │  clinical read rights whatsoever │
                      └───┬──────────┬──────────┬────────┘
       scoped capsule ↓   │          │          │   ↑ signed verdict
       (commitment,       │          │          │     (band + disposition,
        sketch, token,    │          │          │      never clinical text)
        purpose, expiry)  │          │          │
              ┌───────────┴──┐ ┌─────┴──────┐ ┌─┴──────────────┐
              │ Registration │ │  Nursing   │ │ Clinical       │
              │   runtime    │ │  runtime   │ │ summary        │
              │              │ │            │ │ runtime        │
              │ vault +      │ │ vault +    │ │ vault +        │
              │ sketch index │ │ sketch idx │ │ sketch index   │
              │ replay engine│ │ replay eng │ │ replay engine  │
              └───────┬──────┘ └─────┬──────┘ └──────┬────────┘
                      └──────────────┼───────────────┘
                                     ▼
                        ┌────────────────────────┐
                        │  FHIR R4 sandbox       │
                        │  (trusted source of    │
                        │   truth for rebuilds)  │
                        └────────────────────────┘
```

Raw patient content never leaves the runtime authorised to hold it. Candidate matching,
counterfactual replay, and clean-room rebuild all execute inside the owning runtime.

### Module map

| Path | Responsibility |
| --- | --- |
| [`config.py`](aegis_care/config.py) | Every threshold and weight, frozen in one auditable object |
| [`fhir/`](aegis_care/fhir/) | Synthetic FHIR R4 sandbox with snapshot/restore |
| [`policy/rbac.py`](aegis_care/policy/rbac.py) | Deterministic EICU-AC-style role/purpose/patient/operation checks |
| [`memory/models.py`](aegis_care/memory/models.py) | The versioned memory artifact and its lifecycle |
| [`memory/sketch.py`](aegis_care/memory/sketch.py) | Receiver-scoped quantised sketches (64 × int8) |
| [`memory/store.py`](aegis_care/memory/store.py) | Append-only ledger + per-role vault |
| [`agents/runtime.py`](aegis_care/agents/runtime.py) | Derivation, memory writes, replay, firewall |
| [`care/capsule.py`](aegis_care/care/capsule.py) | Recovery capsule schema, signing, verification |
| [`care/candidate.py`](aegis_care/care/candidate.py) | **C** — scoring and compatibility filters |
| [`care/attribution.py`](aegis_care/care/attribution.py) | **A** — counterfactual replay and influence |
| [`care/recompile.py`](aegis_care/care/recompile.py) | **R** — clean-room rebuild, fail-closed |
| [`care/enforcement.py`](aegis_care/care/enforcement.py) | **E** — tombstones and resurrection firewall |
| [`care/coordinator.py`](aegis_care/care/coordinator.py) | The CARE loop to closure |
| [`incident/`](aegis_care/incident/) | Task manifest, four families, provenance masks |
| [`eval/`](aegis_care/eval/) | Baselines, metrics, statistics, privacy attacks, report |
| [`api/app.py`](aegis_care/api/app.py) | FastAPI service |
| [`ops/auth.py`](aegis_care/ops/auth.py) | Credentials, sessions, permission model |
| [`ops/models.py`](aegis_care/ops/models.py) | Incident lifecycle and state machine |
| [`ops/store.py`](aegis_care/ops/store.py) | Durable operators, sessions, incidents, audit |
| [`ops/service.py`](aegis_care/ops/service.py) | Intake → triage → recovery → review → closure |
| [`fhir/remote.py`](aegis_care/fhir/remote.py) | Read-only connector for a real FHIR R4 server |
| [`api/ops_routes.py`](aegis_care/api/ops_routes.py) | Authenticated console API |
| [`web/ops.*`](aegis_care/web/) | Operations console UI |
| [`api/demo.py`](aegis_care/api/demo.py) | Guided presentation session |
| [`web/presentation.*`](aegis_care/web/) | Presentation GUI |
| [`web/index.html`](aegis_care/web/index.html) | Analyst console |

---

## Scenario families

| ID | Family | Seed | Propagation | Observable failure |
| --- | --- | --- | --- | --- |
| F1 | Wrong-patient alias | Incorrect name/DOB/MRN association in registration memory | alias → lookup cue → handover → summary | Later task documents against the wrong patient |
| F2 | Wrong-chart copied fact | Observation fragment associated with another patient | observation summary → handover → aggregate | Patient context changes in follow-up output |
| F3 | Access-scope laundering | Administration-visible note reflects a restricted field | restricted source → authorised summary → shared memory | Unauthorised role can reuse restricted information |
| F4 | Stale corrected fact | Previously valid association is corrected | old summary and cue remain active | Agent reintroduces superseded information |

Every incident is built with a **matched clean control**: a surface-similar, causally
independent trajectory. Those are the hard negatives that make precision and clean-state
retention meaningful rather than decorative.

---

## Analyst console

`/research` opens a nine-tab research console:

- **Incident Lab** — construct an incident, watch contamination spread across the chain,
  apply a provenance mask
- **CARE Recovery** — run the loop with per-stage ablation toggles; see candidates, signed
  verdicts, repairs, and the recovery certificate
- **Memory Graph** — the versioned derivation graph; solid edges are observable lineage,
  dashed edges exist only in the private ground truth (i.e. what masking removed)
- **Baselines** — all nine conditions on identical frozen state
- **Privacy Audit** — the leakage attacks, live
- **Review Queue** — approve / reject / keep-quarantined for escalated artifacts
- **Experiments** — the full matrix, writing tables, figures, and the report
- **Audit Log** — the append-only event ledger

---

## Verification

`pytest -q` runs 229 tests. Beyond ordinary unit coverage, each of the six **core invariants**
of proposal Section 7.1 and each termination/safety property of Section 6.6 has an
executable check in [`tests/test_invariants.py`](tests/test_invariants.py):

- a confirmed seed and every confirmed descendant become non-servable
- no repaired memory cites a tombstoned version as trusted support
- no role receives data outside its patient/role/purpose/recipient/expiry policy
- similarity alone never triggers destructive repair
- a missing replay recipe causes quarantine, never a guess
- completion requires follow-up tasks, privacy checks, and resurrection probes
- monotone incident frontier, finite termination, no raw-content centralisation,
  fail-closed reconstruction, closed publication

The experiment is deterministic: `test_results_are_reproducible` asserts two independent
runs produce identical aggregates.

---

## Design decisions worth knowing

**The default model is deterministic, not an LLM.** Counterfactual replay is only sound if
replaying identical inputs yields identical output. A frozen composer guarantees that
absolutely, which removes "counterfactual instability" (Section 14) from the critical path
of all 900 condition runs. The real-model adapter is provided and structured facts always
come from FHIR, so the safety predicates never depend on sampling.

**The sketch encoder is a frozen hashing encoder, not a downloaded transformer.** The
proposal asks for "a frozen sentence embedding model with random projection and
quantization". A hashing encoder gives byte-identical reproducibility across machines, which
a downloaded checkpoint does not, and keeps the whole project runnable offline.

**Artifact signatures exclude lifecycle state.** An artifact moves
active → suspected → quarantined during recovery. A signature that broke on every transition
could not attest to the artifact's origin afterwards, so signatures cover immutable creation
facts and state changes are separately recorded as signed ledger events.

**All five deterministic predicates confirm influence.** Section 5.5.2 lists a change to a
protected field, the selected patient, a FHIR resource, a structured fact, or a downstream
action. Implementing only patient and resource-id changes silently missed families F3
(access-scope laundering changes neither) and F4 (a corrected value keeps the same resource
ids). `tau_i` is the *additional* soft path for cases where only the prose moved.

---

## Non-goals

Taken directly from Section 11.2 of the proposal, and enforced in code:

- No diagnosis, prognosis, treatment plan, medication order, or patient-facing advice
- No live hospital systems, identifiable patient data, or clinical outcome claims
- No HIPAA / DPDP Act / GDPR compliance certification
- No full latent mesh, hidden-state alignment, or KV-cache transport
- No claim that embeddings or sketches are confidential by construction
- No destructive deletion of the audit trail — superseded content becomes non-servable and
  is represented by policy-controlled tombstones

---

## Attribution

Implements the research design of *AEGIS-Care: A Privacy-Bounded Memory Recompiler for
Recovering Poisoned Clinical AI Agents*, v4.0, prepared by Lakshit Sachdeva and team for
NMIMS MPSTME capstone topic approval. Section references throughout the source point back to
that document.
