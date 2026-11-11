Generated 2026-11-11 by `eval/run_eval.py`. Note and check: `claude-opus-5-5` (effort `medium` / `medium`); coverage judge: `claude-opus-5-5`. Raw outputs are in `eval/results/`.

| Transcript | Sample | Facts | Coverage | Present / partial / absent | Statements | Unsupported | Partly supported | Rejected |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Scripted | ankle-sprain | 23 | 100.0% | 23 / 0 / 0 | 24 | 0 (0.0%) | 1 | 0 |
| Scripted | diabetes-follow-up | 24 | 93.8% | 21 / 3 / 0 | 28 | 0 (0.0%) | 1 | 0 |
| Scripted | sore-throat | 19 | 100.0% | 19 / 0 / 0 | 21 | 0 (0.0%) | 1 | 0 |
| **Scripted** | **all** | **66** | **97.7%** | 63 / 3 / 0 | 73 | 0 (0.0%) | 3 | 0 |
| Gemini (gemini-3.8-flash) | ankle-sprain | 23 | 100.0% | 23 / 0 / 0 | 24 | 0 (0.0%) | 1 | 0 |
| Gemini (gemini-3.8-flash) | diabetes-follow-up | 24 | 95.8% | 22 / 2 / 0 | 28 | 0 (0.0%) | 1 | 0 |
| Gemini (gemini-3.8-flash) | sore-throat | 19 | 100.0% | 19 / 0 / 0 | 21 | 0 (0.0%) | 1 | 0 |
| **Gemini (gemini-3.8-flash)** | **all** | **66** | **98.5%** | 64 / 2 / 0 | 73 | 0 (0.0%) | 3 | 0 |

Coverage = (present + 0.5 x partial) / facts. Unsupported rate = statements the claim check labelled unsupported / all statements.

Planted errors (claim check run on the scripted-transcript notes after editing them; see `eval/planted_errors.json`):

| Sample | Planted | Flagged unsupported | Flagged partly supported | Missed | Untouched statements flagged |
| --- | --- | --- | --- | --- | --- |
| ankle-sprain | 4 | 4 | 0 | 0 | 0 of 21 |
| diabetes-follow-up | 4 | 4 | 0 | 0 | 0 of 25 |
| sore-throat | 4 | 4 | 0 | 0 | 0 of 18 |
| **all** | **12** | **12** | **0** | **0** | **0 of 64** |

Known PII values found in the redacted Gemini transcripts sent to the note model: none.

Model calls behind these results: 15, $0.526252 at list prices.
