# AEGIS-Care experimental report

_Privacy-bounded memory recompilation for recovering poisoned clinical AI agents._

Wall time: 28.707s · 30 incidents · 270 condition runs


## Recovery conditions

| id | condition | purpose |
| --- | --- | --- |
| A | No recovery | Measures residual harm and ordinary task utility. |
| B | Delete confirmed seed only | Tests whether descendants survive local cleanup. |
| C | Full memory reset | Safety-heavy fallback and lower bound on retained utility. |
| D | Explicit-lineage quarantine | Strong privacy-respecting baseline with no missing-edge reconstruction. |
| E | Explicit-lineage clean replay | Separates recompilation value from latent candidate discovery. |
| F | Sketch-only quarantine | Measures false positives when semantic candidates are treated as causality. |
| G | Central raw-content oracle | Non-private upper comparator for discovery and reconstruction. |
| H | Complete private oracle graph | Unattainable provenance upper bound. |
| I | AEGIS-Care / full CARE | Lineage + sketches + local attribution + recompilation + enforcement. |


## Primary results by condition

RWH = residual wrong-patient/unauthorized harm (lower better); BSR = benign-state retention (higher better); RTS = repaired task success; UER = unauthorized exposure; DRR = deletion resurrection rate.

| condition | n | rwh | descendant_recall | descendant_precision | bsr | rts | false_repair_rate | uer | drr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 30 | 0.2667 | 0.0000 | 1.0000 | 1.0000 | 0.7500 | 0.0000 | 0.1108 | 1.0000 |
| B | 30 | 0.2667 | 0.0000 | 1.0000 | 1.0000 | 0.7500 | 0.0000 | 0.0819 | 1.0000 |
| C | 30 | 0.0000 | 1.0000 | 0.2711 | 0.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |
| D | 30 | 0.1444 | 0.4778 | 0.9833 | 0.9958 | 0.8667 | 0.0000 | 0.0372 | 0.0000 |
| E | 30 | 0.1444 | 0.4778 | 0.9833 | 1.0000 | 0.8667 | 0.0000 | 0.0347 | 0.0000 |
| F | 30 | 0.0000 | 1.0000 | 0.3359 | 0.2405 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |
| G | 30 | 0.0000 | 1.0000 | 0.7500 | 0.8675 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| H | 30 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |
| I | 30 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |


## RQ1 - sensitivity to provenance loss

_Hypothesis: targeted loss of cross-role and semantic-derivation edges harms provenance-only recovery more than random edge loss._

| condition | provenance | n | descendant_recall | descendant_precision | bsr | rwh |
| --- | --- | --- | --- | --- | --- | --- |
| A | complete | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| A | random40 | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| A | targeted | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| B | complete | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| B | random40 | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| B | targeted | 10 | 0.0000 | 1.0000 | 1.0000 | 0.2667 |
| C | complete | 10 | 1.0000 | 0.2711 | 0.0000 | 0.0000 |
| C | random40 | 10 | 1.0000 | 0.2711 | 0.0000 | 0.0000 |
| C | targeted | 10 | 1.0000 | 0.2711 | 0.0000 | 0.0000 |
| D | complete | 10 | 1.0000 | 0.9500 | 0.9875 | 0.0000 |
| D | random40 | 10 | 0.2500 | 1.0000 | 1.0000 | 0.2000 |
| D | targeted | 10 | 0.1833 | 1.0000 | 1.0000 | 0.2333 |
| E | complete | 10 | 1.0000 | 0.9500 | 1.0000 | 0.0000 |
| E | random40 | 10 | 0.2500 | 1.0000 | 1.0000 | 0.2000 |
| E | targeted | 10 | 0.1833 | 1.0000 | 1.0000 | 0.2333 |
| F | complete | 10 | 1.0000 | 0.3359 | 0.2405 | 0.0000 |
| F | random40 | 10 | 1.0000 | 0.3359 | 0.2405 | 0.0000 |
| F | targeted | 10 | 1.0000 | 0.3359 | 0.2405 | 0.0000 |
| G | complete | 10 | 1.0000 | 0.7500 | 0.8675 | 0.0000 |
| G | random40 | 10 | 1.0000 | 0.7500 | 0.8675 | 0.0000 |
| G | targeted | 10 | 1.0000 | 0.7500 | 0.8675 | 0.0000 |
| H | complete | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| H | random40 | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| H | targeted | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| I | complete | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| I | random40 | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| I | targeted | 10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |


### RQ1 at matched edge loss

_Comparing the `targeted` and `random*` labels directly is unfair: they remove different numbers of edges. This groups runs by the realised loss fraction and compares within each bucket. A positive `targeted_worse_by` supports the hypothesis._

| condition | loss_bucket | mean_loss_targeted | mean_loss_random | recall_targeted | recall_random | targeted_worse_by | n_targeted | n_random |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D | 25%-50% | 0.5000 | 0.3833 | 0.5000 | 0.2500 | -0.2500 | 2 | 10 |
| E | 25%-50% | 0.5000 | 0.3833 | 0.5000 | 0.2500 | -0.2500 | 2 | 10 |
| I | 25%-50% | 0.5000 | 0.3833 | 1.0000 | 1.0000 | 0.0000 | 2 | 10 |


## Macro averages by scenario family

| condition | family | n | descendant_recall | descendant_precision | bsr | rwh | uer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | F1 | 9 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| A | F2 | 6 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| A | F3 | 9 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.3694 |
| A | F4 | 6 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| B | F1 | 9 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| B | F2 | 6 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| B | F3 | 9 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.2730 |
| B | F4 | 6 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| C | F1 | 9 | 1.0000 | 0.4243 | 0.0000 | 0.0000 | 0.0000 |
| C | F2 | 6 | 1.0000 | 0.1825 | 0.0000 | 0.0000 | 0.0000 |
| C | F3 | 9 | 1.0000 | 0.2730 | 0.0000 | 0.0000 | 0.0000 |
| C | F4 | 6 | 1.0000 | 0.1270 | 0.0000 | 0.0000 | 0.0000 |
| D | F1 | 9 | 0.5556 | 1.0000 | 1.0000 | 0.1481 | 0.0000 |
| D | F2 | 6 | 0.3333 | 1.0000 | 1.0000 | 0.2222 | 0.0000 |
| D | F3 | 9 | 0.5926 | 1.0000 | 1.0000 | 0.1852 | 0.1241 |
| D | F4 | 6 | 0.3333 | 0.9167 | 0.9792 | 0.0000 | 0.0000 |
| E | F1 | 9 | 0.5556 | 1.0000 | 1.0000 | 0.1481 | 0.0000 |
| E | F2 | 6 | 0.3333 | 1.0000 | 1.0000 | 0.2222 | 0.0000 |
| E | F3 | 9 | 0.5926 | 1.0000 | 1.0000 | 0.1852 | 0.1157 |
| E | F4 | 6 | 0.3333 | 0.9167 | 1.0000 | 0.0000 | 0.0000 |
| F | F1 | 9 | 1.0000 | 0.4243 | 0.0000 | 0.0000 | 0.0000 |
| F | F2 | 6 | 1.0000 | 0.3667 | 0.6190 | 0.0000 | 0.0000 |
| F | F3 | 9 | 1.0000 | 0.2730 | 0.0000 | 0.0000 | 0.0000 |
| F | F4 | 6 | 1.0000 | 0.2667 | 0.5833 | 0.0000 | 0.0000 |
| G | F1 | 9 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |
| G | F2 | 6 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |
| G | F3 | 9 | 1.0000 | 0.6389 | 0.7944 | 0.0000 | 1.0000 |
| G | F4 | 6 | 1.0000 | 0.2917 | 0.6458 | 0.0000 | 1.0000 |
| H | F1 | 9 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| H | F2 | 6 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| H | F3 | 9 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| H | F4 | 6 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| I | F1 | 9 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| I | F2 | 6 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| I | F3 | 9 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| I | F4 | 6 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |


## Macro averages by propagation depth

| condition | depth | n | descendant_recall | bsr | rwh |
| --- | --- | --- | --- | --- | --- |
| A | 2 | 6 | 0.0000 | 1.0000 | 0.3333 |
| A | 3 | 12 | 0.0000 | 1.0000 | 0.2500 |
| A | 4 | 12 | 0.0000 | 1.0000 | 0.2500 |
| B | 2 | 6 | 0.0000 | 1.0000 | 0.3333 |
| B | 3 | 12 | 0.0000 | 1.0000 | 0.2500 |
| B | 4 | 12 | 0.0000 | 1.0000 | 0.2500 |
| C | 2 | 6 | 1.0000 | 0.0000 | 0.0000 |
| C | 3 | 12 | 1.0000 | 0.0000 | 0.0000 |
| C | 4 | 12 | 1.0000 | 0.0000 | 0.0000 |
| D | 2 | 6 | 0.6667 | 1.0000 | 0.1111 |
| D | 3 | 12 | 0.5000 | 1.0000 | 0.1389 |
| D | 4 | 12 | 0.3611 | 0.9896 | 0.1667 |
| E | 2 | 6 | 0.6667 | 1.0000 | 0.1111 |
| E | 3 | 12 | 0.5000 | 1.0000 | 0.1389 |
| E | 4 | 12 | 0.3611 | 1.0000 | 0.1667 |
| F | 2 | 6 | 1.0000 | 0.0000 | 0.0000 |
| F | 3 | 12 | 1.0000 | 0.3333 | 0.0000 |
| F | 4 | 12 | 1.0000 | 0.2679 | 0.0000 |
| G | 2 | 6 | 1.0000 | 0.8750 | 0.0000 |
| G | 3 | 12 | 1.0000 | 0.8667 | 0.0000 |
| G | 4 | 12 | 1.0000 | 0.8646 | 0.0000 |
| H | 2 | 6 | 1.0000 | 1.0000 | 0.0000 |
| H | 3 | 12 | 1.0000 | 1.0000 | 0.0000 |
| H | 4 | 12 | 1.0000 | 1.0000 | 0.0000 |
| I | 2 | 6 | 1.0000 | 1.0000 | 0.0000 |
| I | 3 | 12 | 1.0000 | 1.0000 | 0.0000 |
| I | 4 | 12 | 1.0000 | 1.0000 | 0.0000 |


## Paired comparisons (Section 10.1)

| comparison | metric | difference | ci_low | ci_high | n | significant | rationale |
| --- | --- | --- | --- | --- | --- | --- | --- |
| I vs D | rwh | -0.1444 | -0.2000 | -0.0889 | 30 | yes | value of missing-edge recovery plus recompilation |
| I vs D | descendant_recall | 0.5222 | 0.3500 | 0.6889 | 30 | yes |  |
| I vs D | bsr | 0.0042 | 0.0000 | 0.0125 | 30 | no |  |
| I vs D | uer | -0.0372 | -0.0717 | -0.0111 | 30 | yes |  |
| I vs E | rwh | -0.1444 | -0.2000 | -0.0889 | 30 | yes | value of latent candidate discovery under incomplete provenance |
| I vs E | descendant_recall | 0.5222 | 0.3500 | 0.6889 | 30 | yes |  |
| I vs E | bsr | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs E | uer | -0.0347 | -0.0673 | -0.0095 | 30 | yes |  |
| I vs F | rwh | 0.0000 | 0.0000 | 0.0000 | 30 | no | necessity of counterfactual confirmation to protect clean state |
| I vs F | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs F | bsr | 0.7595 | 0.6516 | 0.8627 | 30 | yes |  |
| I vs F | uer | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs C | rwh | 0.0000 | 0.0000 | 0.0000 | 30 | no | utility retained relative to the safest simple fallback |
| I vs C | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs C | bsr | 1.0000 | 1.0000 | 1.0000 | 30 | yes |  |
| I vs C | uer | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs G | rwh | 0.0000 | 0.0000 | 0.0000 | 30 | no | recovery loss and privacy gain vs centralized raw-content access |
| I vs G | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs G | bsr | 0.1325 | 0.0817 | 0.1842 | 30 | yes |  |
| I vs G | uer | -1.0000 | -1.0000 | -1.0000 | 30 | yes |  |
| I vs H | rwh | 0.0000 | 0.0000 | 0.0000 | 30 | no | oracle regret and irreducible cost of missing provenance |
| I vs H | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs H | bsr | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs H | uer | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs B | rwh | -0.2667 | -0.3111 | -0.2111 | 30 | yes | value of descendant repair over seed deletion |
| I vs B | descendant_recall | 1.0000 | 1.0000 | 1.0000 | 30 | yes |  |
| I vs B | bsr | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs B | uer | -0.0819 | -0.1298 | -0.0390 | 30 | yes |  |
| I vs A | rwh | -0.2667 | -0.3111 | -0.2111 | 30 | yes | total effect of recovery |
| I vs A | descendant_recall | 1.0000 | 1.0000 | 1.0000 | 30 | yes |  |
| I vs A | bsr | 0.0000 | 0.0000 | 0.0000 | 30 | no |  |
| I vs A | uer | -0.1108 | -0.1736 | -0.0517 | 30 | yes |  |


### McNemar exact tests on paired binary outcomes

| comparison | b (A better) | c (B better) | p_value | n |
| --- | --- | --- | --- | --- |
| I vs D | 13 | 0 | 0.0002 | 30 |
| I vs E | 13 | 0 | 0.0002 | 30 |
| I vs F | 0 | 0 | 1.0000 | 30 |
| I vs C | 0 | 0 | 1.0000 | 30 |
| I vs G | 0 | 0 | 1.0000 | 30 |
| I vs H | 0 | 0 | 1.0000 | 30 |
| I vs B | 24 | 0 | 0.0000 | 30 |
| I vs A | 24 | 0 | 0.0000 | 30 |


## Safety-utility-privacy frontier (Section 10.2)

| condition | safety | utility | privacy | recall | precision | pareto | n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 0.2667 | 1.0000 | 0.1108 | 0.0000 | 1.0000 | False | 30 |
| B | 0.2667 | 1.0000 | 0.0819 | 0.0000 | 1.0000 | False | 30 |
| C | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.2711 | False | 30 |
| D | 0.1444 | 0.9958 | 0.0372 | 0.4778 | 0.9833 | False | 30 |
| E | 0.1444 | 1.0000 | 0.0347 | 0.4778 | 0.9833 | False | 30 |
| F | 0.0000 | 0.2405 | 0.0000 | 1.0000 | 0.3359 | False | 30 |
| G | 0.0000 | 0.8675 | 1.0000 | 1.0000 | 0.7500 | False | 30 |
| H | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 30 |
| I | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 30 |


## Oracle regret vs condition H

| condition | regret |
| --- | --- |
| A | 1.0000 |
| B | 1.0000 |
| C | 1.0000 |
| D | 0.5264 |
| E | 0.5222 |
| F | 0.7595 |
| G | 0.1325 |
| I | 0.0000 |


## Empirical leakage (Section 7.2)

_The proposal makes no claim that sketches are private by construction; these are measured attacks._

| attack | n | accuracy | baseline | advantage |
| --- | --- | --- | --- | --- |
| attribute_inference[gender] | 100 | 0.2300 | 0.3400 | -0.1100 |
| attribute_inference[restricted_flag] | 100 | 0.5900 | 0.7500 | -0.1600 |
| membership_inference | 14 | 0.9286 | 0.6429 | 0.2857 |
| linkability[cross-recipient] | 40 | 0.0250 | 0.0250 | 0.0000 |


Raw content exported through the recovery interface: **False**. Fields released: `artifact_type_band, capsule_id, expires_at, incident_id, issued_at, issuer, nonce, patient_token, purpose, recipient, seed_commitment, sketch, time_band`.


Removing purpose/recipient scoping raises cross-recipient linkage accuracy to **1.0**, which is what the scoping ablation in Section 9.2 is meant to expose.


## Verification failures and negative results

| incident | condition | reason |
| --- | --- | --- |
| INC-F4-T-ID-04-d2-complete-s0 | - | seed did not propagate or could not change the target predicate |
| INC-F4-T-ID-04-d2-random40-s0 | - | seed did not propagate or could not change the target predicate |
| INC-F4-T-ID-04-d2-targeted-s0 | - | seed did not propagate or could not change the target predicate |
