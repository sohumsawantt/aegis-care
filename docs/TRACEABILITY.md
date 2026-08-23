# Proposal → implementation traceability

Maps every numbered commitment in *AEGIS-Care v4.0* to the code that implements it and the
test that verifies it. Intended for mentor review and for the reproducibility appendix of
the final paper.

## 5. Proposed system

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 5.2 | Three role-separated agents with distinct authorized functions | `policy/rbac.py::ROLE_FIELD_MATRIX`, `agents/runtime.py` | `test_foundations.py::TestPolicy` |
| 5.3 | Versioned memory object: id/version/owner, content_ref, parent commitments, patient/role/purpose, sketch, replay recipe, status, signature | `memory/models.py::MemoryArtifact` | `test_foundations.py::TestMemoryLedger` |
| 5.4 | Scoped recovery capsule with exactly the declared fields | `care/capsule.py::RecoveryCapsule`, `ALLOWED_CAPSULE_FIELDS` | `test_care.py::TestCapsules` |
| 5.4 | Receiver-specific keyed patient token limiting linkability | `util/crypto.py::receiver_scoped_token` | `test_foundations.py::test_patient_token_is_receiver_scoped`, `test_evaluation.py::test_scoping_prevents_linkability` |
| 5.5.1 | **C** — exact parents first, then local sketch search with temporal/type/patient/workflow filters; ranked candidates only | `care/candidate.py::CandidateDiscoverer` | `test_care.py::test_recovery_under_targeted_masking` |
| 5.5.2 | **A** — local counterfactual replay; coordinator receives band + disposition, not text | `care/attribution.py::AttributionEngine` | `test_care.py::test_coordinator_never_sees_clinical_text` |
| 5.5.3 | **R** — rebuild from trusted FHIR; quarantine when support is unavailable | `care/recompile.py::Recompiler` | `test_invariants.py::test_missing_recipe_causes_quarantine_not_guessing` |
| 5.5.4 | **E** — signed tombstones, revocation checks, resurrection firewall | `care/enforcement.py::ResurrectionFirewall` | `test_care.py::test_resurrection_probes_blocked` |

## 6. Formal model

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 6.1 | `q(v)` over six states; observed lineage `E_obs ⊆ E` | `memory/models.py::MemoryState`, `incident/masks.py` | `test_foundations.py::test_transition_matrix` |
| 6.2 | `score(s,v) = a·explicit + b·sim + c·compat`, threshold `tau_c` | `care/candidate.py::discover` | `test_care.py::TestCARELoop` |
| 6.3 | `I(s→v) = w1·semantic + w2·patient + w3·resource`; exact predicate overrides | `care/attribution.py::assess` | `test_invariants.py::test_similarity_alone_cannot_trigger_destructive_repair` |
| 6.4 | `J = λs·RWH + λu·(1−BSR) + λp·UER + λr·DRR + λc·Cost` | `eval/stats.py::objective_j` | `test_evaluation.py::test_objective_j_prefers_care` |
| 6.5 | The nine-step algorithm | `care/coordinator.py::recover` | `test_care.py::TestCARELoop` |
| 6.6 | Monotone frontier, finite termination, no raw-content centralisation, fail-closed reconstruction, closed publication | `memory/models.py::state_transition_allowed`, `care/coordinator.py` | `test_invariants.py::TestSection66Properties` |

## 7. Threat, privacy, invariants

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 7.1 | Six core invariants | across `care/` | `test_invariants.py::TestSection71Invariants` (one test per bullet) |
| 7.2 | Leakage measured empirically: attribute prediction, membership inference, linkability, exact field logging | `eval/privacy.py::PrivacyAuditor` | `test_evaluation.py::TestPrivacyAttacks` |

## 8. Benchmark and data

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 8.1 | FHIR R4 environment; gate G0 allows a synthetic fixture with the same APIs | `fhir/generator.py`, `fhir/store.py` | `test_foundations.py::TestFHIRStore` |
| 8.2 | Families F1–F4 | `incident/scenarios.py::FAMILY_INFO` | `test_care.py::test_every_family_builds_and_contaminates` |
| 8.3 | 24 base tasks (8/8/8); 3 roles; matched controls; depths 1–4; complete / random 20-40-60% / targeted masking | `incident/tasks.py`, `incident/masks.py` | `test_care.py::TestProvenanceMasks` |
| 8.4 | Private ground-truth instrumentation used only for scoring | `environment.py::GroundTruthGraph` | `test_care.py::test_masking_does_not_touch_ground_truth` |

## 9. Experimental design

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 9 | Conditions A–I | `eval/baselines.py::CONDITION_INFO` | `test_evaluation.py::TestBaselines` |
| 9.1 | Every condition on the same frozen snapshot, seed, mask, and follow-ups | `eval/runner.py::_run_cell` + `environment.snapshot/restore` | `test_evaluation.py::test_conditions_are_paired_on_identical_state` |
| 9.1 step 2 | Verify the seed propagated before running conditions | `eval/runner.py::_verify_incident` | reported in `verification_failures` |
| 9.2 | Seven critical ablations | `care/coordinator.py::CAREOptions` | `test_api.py::test_ablation_changes_outcome` |
| 9.3 | Temperature zero; frozen thresholds | `agents/model.py`, `config.py` | `test_evaluation.py::test_results_are_reproducible` |

## 10. Metrics and statistics

| Proposal | Commitment | Implementation | Verified by |
| --- | --- | --- | --- |
| 10 | RWH, recall, precision, BSR, RTS, false repair, UER, sketch leakage, DRR, regret, overhead | `eval/metrics.py::MetricSet` | `test_evaluation.py::TestMetrics` |
| 10.1 | Primary comparisons I vs D/E/F/C/G/H | `eval/report.py::PRIMARY_COMPARISONS` | `test_evaluation.py::test_full_care_dominates_non_oracle_baselines` |
| 10.2 | Paired bootstrap, McNemar exact, Brier/ECE, macro averages, frontier plots, negative results | `eval/stats.py`, `eval/report.py` | `test_evaluation.py::TestStatistics` |

## 11. Functional requirements F1–F10

| ID | Function | Implementation | Verified by |
| --- | --- | --- | --- |
| F1 | Persist versioned memories with parent/replay metadata | `memory/store.py::MemoryVault.put` | `test_foundations.py::test_put_signs_and_indexes` |
| F2 | Initiate recovery; seed non-servable before reconstruction | `care/coordinator.py::recover` step 1 | `test_invariants.py::test_seed_and_confirmed_descendants_are_non_servable` |
| F3 | Signed, receiver-scoped capsules; reject tampered/expired/wrong-purpose/wrong-recipient | `care/capsule.py::CapsuleMinter.verify` | `test_care.py::TestCapsules` (6 rejection tests) |
| F4 | Discover exact and latent candidates locally | `care/candidate.py` | `test_care.py::test_recovery_under_targeted_masking` |
| F5 | Local counterfactual confirmation; raw text absent from coordinator log | `care/attribution.py` | `test_care.py::test_coordinator_never_sees_clinical_text` |
| F6 | Recompile from trusted FHIR or quarantine | `care/recompile.py` | `test_care.py::test_repairs_restore_intended_patient` |
| F7 | Propagate repair downstream; closure test | `care/coordinator.py` frontier loop | `test_care.py::test_closure_is_reached` |
| F8 | Prevent resurrection | `care/enforcement.py::probe` | `test_care.py::test_resurrection_probes_blocked` |
| F9 | Machine- and human-readable recovery certificate | `care/certificate.py` | `test_care.py::test_certificate_is_signed_and_complete` |
| F10 | Human override and safe resume | `api/app.py::/api/review` | `test_api.py::test_review_decision_applies` |

## 12. Technology stack

| Proposal layer | Proposed | Used here | Note |
| --- | --- | --- | --- |
| Agent runtime | Python 3.11, FastAPI, lightweight orchestrator | ✅ as proposed | custom orchestrator, no LangGraph dependency |
| Clinical sandbox | MedAgentBench Docker / HAPI FHIR | synthetic FHIR R4 fixture with same APIs | permitted by gate G0; no Docker needed |
| Memory store | SQLite, append-only version/event tables | ✅ as proposed | `memory/store.py` |
| Primary model | Qwen3-8B or comparable, temperature 0 | deterministic composer by default; OpenAI-compatible adapter provided | guarantees replay determinism |
| Sketch encoder | frozen embedding + random projection + quantisation | frozen hashing encoder + keyed projection + int8 | byte-reproducible across machines |
| Lineage/calibration | scikit-learn logistic regression, calibration | ✅ used in the privacy attacks and calibration report | `eval/privacy.py`, `eval/stats.py` |
| Integrity | SHA-256 commitments, Ed25519 signatures | ✅ via `cryptography` | `util/crypto.py` |
| Policy | deterministic RBAC/ABAC from EICU-AC | ✅ as proposed | `policy/rbac.py` |
| Evaluation | PyTest, frozen JSON traces, paired bootstrap, Pandas/Matplotlib | ✅ as proposed | `eval/`, `tests/` |
| Interface | Streamlit or React dashboard (stretch) | vanilla-JS dashboard served by FastAPI | no build step, runs offline |

## Deviations from the proposal

Each is a deliberate, documented choice rather than an omission.

1. **Synthetic FHIR fixture instead of the MedAgentBench Docker image.** Feasibility gate G0
   explicitly permits this. The search API surface, resource shapes, and task families
   match; the record content is synthetic. Swapping in the real sandbox means replacing
   `fhir/store.py` behind the same interface.
2. **Deterministic composer as the default model.** Section 9.3 asks for temperature zero;
   this goes further and removes sampling entirely, which makes all 900 condition runs
   exactly reproducible. The real-model path is implemented and selectable.
3. **Hashing sketch encoder instead of a downloaded sentence transformer.** Preserves the
   "frozen encoder + random projection + quantisation" structure while keeping runs
   byte-identical across machines and removing a network dependency.
4. **Signatures exclude mutable lifecycle state.** Necessary so an origin attestation
   survives the state transitions recovery performs; state changes are separately signed in
   the ledger.
5. **All five deterministic predicates from Section 5.5.2 confirm influence**, not just
   patient and resource-id changes. Restricting to the latter silently missed families F3
   and F4 — see the design note in the README.
