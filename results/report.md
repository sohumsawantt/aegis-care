# AEGIS-Care experimental report

_Privacy-bounded memory recompilation for recovering poisoned clinical AI agents._

Wall time: 1.226s · 1 incidents · 9 condition runs


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
| A | 1 | 0.3333 | 0.0000 | 1.0000 | 1.0000 | 0.5000 | 0.0000 | 0.0000 | 1.0000 |
| B | 1 | 0.3333 | 0.0000 | 1.0000 | 1.0000 | 0.5000 | 0.0000 | 0.0000 | 1.0000 |
| C | 1 | 0.0000 | 1.0000 | 0.4444 | 0.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |
| D | 1 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |
| E | 1 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |
| F | 1 | 0.0000 | 1.0000 | 0.4444 | 0.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |
| G | 1 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| H | 1 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |
| I | 1 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |


## RQ1 - sensitivity to provenance loss

_Hypothesis: targeted loss of cross-role and semantic-derivation edges harms provenance-only recovery more than random edge loss._

| condition | provenance | n | descendant_recall | descendant_precision | bsr | rwh |
| --- | --- | --- | --- | --- | --- | --- |
| A | complete | 1 | 0.0000 | 1.0000 | 1.0000 | 0.3333 |
| B | complete | 1 | 0.0000 | 1.0000 | 1.0000 | 0.3333 |
| C | complete | 1 | 1.0000 | 0.4444 | 0.0000 | 0.0000 |
| D | complete | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| E | complete | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| F | complete | 1 | 1.0000 | 0.4444 | 0.0000 | 0.0000 |
| G | complete | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| H | complete | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| I | complete | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |


### RQ1 at matched edge loss

_Comparing the `targeted` and `random*` labels directly is unfair: they remove different numbers of edges. This groups runs by the realised loss fraction and compares within each bucket. A positive `targeted_worse_by` supports the hypothesis._

_no data_


## Macro averages by scenario family

| condition | family | n | descendant_recall | descendant_precision | bsr | rwh | uer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | F1 | 1 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| B | F1 | 1 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0000 |
| C | F1 | 1 | 1.0000 | 0.4444 | 0.0000 | 0.0000 | 0.0000 |
| D | F1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| E | F1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| F | F1 | 1 | 1.0000 | 0.4444 | 0.0000 | 0.0000 | 0.0000 |
| G | F1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |
| H | F1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| I | F1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |


## Macro averages by propagation depth

| condition | depth | n | descendant_recall | bsr | rwh |
| --- | --- | --- | --- | --- | --- |
| A | 4 | 1 | 0.0000 | 1.0000 | 0.3333 |
| B | 4 | 1 | 0.0000 | 1.0000 | 0.3333 |
| C | 4 | 1 | 1.0000 | 0.0000 | 0.0000 |
| D | 4 | 1 | 1.0000 | 1.0000 | 0.0000 |
| E | 4 | 1 | 1.0000 | 1.0000 | 0.0000 |
| F | 4 | 1 | 1.0000 | 0.0000 | 0.0000 |
| G | 4 | 1 | 1.0000 | 1.0000 | 0.0000 |
| H | 4 | 1 | 1.0000 | 1.0000 | 0.0000 |
| I | 4 | 1 | 1.0000 | 1.0000 | 0.0000 |


## Paired comparisons (Section 10.1)

| comparison | metric | difference | ci_low | ci_high | n | significant | rationale |
| --- | --- | --- | --- | --- | --- | --- | --- |
| I vs D | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | value of missing-edge recovery plus recompilation |
| I vs D | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs D | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs D | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs E | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | value of latent candidate discovery under incomplete provenance |
| I vs E | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs E | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs E | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs F | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | necessity of counterfactual confirmation to protect clean state |
| I vs F | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs F | bsr | 1.0000 | 1.0000 | 1.0000 | 1 | yes |  |
| I vs F | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs C | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | utility retained relative to the safest simple fallback |
| I vs C | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs C | bsr | 1.0000 | 1.0000 | 1.0000 | 1 | yes |  |
| I vs C | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs G | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | recovery loss and privacy gain vs centralized raw-content access |
| I vs G | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs G | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs G | uer | -1.0000 | -1.0000 | -1.0000 | 1 | yes |  |
| I vs H | rwh | 0.0000 | 0.0000 | 0.0000 | 1 | no | oracle regret and irreducible cost of missing provenance |
| I vs H | descendant_recall | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs H | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs H | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs B | rwh | -0.3333 | -0.3333 | -0.3333 | 1 | yes | value of descendant repair over seed deletion |
| I vs B | descendant_recall | 1.0000 | 1.0000 | 1.0000 | 1 | yes |  |
| I vs B | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs B | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs A | rwh | -0.3333 | -0.3333 | -0.3333 | 1 | yes | total effect of recovery |
| I vs A | descendant_recall | 1.0000 | 1.0000 | 1.0000 | 1 | yes |  |
| I vs A | bsr | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |
| I vs A | uer | 0.0000 | 0.0000 | 0.0000 | 1 | no |  |


### McNemar exact tests on paired binary outcomes

| comparison | b (A better) | c (B better) | p_value | n |
| --- | --- | --- | --- | --- |
| I vs D | 0 | 0 | 1.0000 | 1 |
| I vs E | 0 | 0 | 1.0000 | 1 |
| I vs F | 0 | 0 | 1.0000 | 1 |
| I vs C | 0 | 0 | 1.0000 | 1 |
| I vs G | 0 | 0 | 1.0000 | 1 |
| I vs H | 0 | 0 | 1.0000 | 1 |
| I vs B | 1 | 0 | 1.0000 | 1 |
| I vs A | 1 | 0 | 1.0000 | 1 |


## Safety-utility-privacy frontier (Section 10.2)

| condition | safety | utility | privacy | recall | precision | pareto | n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 0.3333 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | False | 1 |
| B | 0.3333 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | False | 1 |
| C | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.4444 | False | 1 |
| D | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 1 |
| E | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 1 |
| F | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.4444 | False | 1 |
| G | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | False | 1 |
| H | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 1 |
| I | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | True | 1 |


## Oracle regret vs condition H

| condition | regret |
| --- | --- |
| A | 1.0000 |
| B | 1.0000 |
| C | 1.0000 |
| D | 0.0000 |
| E | 0.0000 |
| F | 1.0000 |
| G | 0.0000 |
| I | 0.0000 |


## Verification failures and negative results

_No incident failed pre-recovery verification._
