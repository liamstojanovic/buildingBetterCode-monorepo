# Actual OpenJEV evaluation on retained support cases

Open [the offline comparison](index.html), or start at the
[combined demo](../index.html). The [presentation and reproduction guide](../../../../docs/JEV_COMMS_DEMO.md)
explains how to show these results alongside advisor discovery and message batching.

This run performed **14 actual local OpenJEV inferences**, two passes over seven
invented historical cases. It made no new hosted requests and executed no selected
tool or transition. All source LLM responses are retained in
[comparison.json](comparison.json), including the wrong OpenAI next-step choice.

| Result | Measured value |
| --- | --- |
| Raw expected choices, first pass | 7 / 7 |
| Gate-accepted expected selections, first pass | 6 / 7 |
| Second pass | Same choices, scores and dispositions |
| Business priority | `high` ranked first; deferred because margin 0.1150 was below 0.15 |
| Tool selection / next steps | 3 / 3 accepted in each category |
| Recorded hosted normalized expected choices | 62 / 63 repeated calls |
| Recorded hosted bare JSON / strict correct outputs | 29 / 63 and 28 / 63 |
| First local end-to-end call | 37,362.06 ms |
| Model loading within first call | 36,469.99 ms |
| Subsequent local call median | 491.20 ms over 13 calls |

The independent per-hypothesis entailment scores are not a probability distribution
over actions. The fixed 0.8 support / 0.15 margin checks are uncalibrated demo
heuristics. A deferred choice is not a successful automatic replacement. The
business explanation remains separate work; the Boolean next-step policy also has
a correct deterministic baseline. This small smoke evaluation is not held-out
business-quality validation, a fair provider ranking, or proof of cost savings.

Artifacts:

- [Full comparison](comparison.json): exact mapped requests, source digests,
  original evidence timestamps, raw NLI rows, output projection, gates, timings,
  all 63 hosted-call outputs and their format checks.
- [Runtime environment](environment.json): actual dependency versions and GPU.
- [Model manifest](model-manifest.json): SHA-256 hashes computed from the reused
  pinned checkpoint, tokenizer, configuration and inspected helper.
- [File inventory](manifest.json): byte hashes for the portable artifacts.

The existing CUDA libraries and cached checkpoint were reused without downloads
or changes to their original environment. The isolated worker retains
`pollard-jev==0.13` and `pollard==1.6.0`; core uses its own Pollard version.
Model identity is `AlexWortega/openjev/qwen3.5-4b-nli-v2`, revision
`552759daad712f1af6c4c13dabcb1e047886fc9c`, BF16 on CUDA.

Original recordings remain unchanged. Fresh validity here refers only to the
explicit historical re-evaluation observation, not renewed operational evidence.
The mapping is versioned and the worker performs no implicit network lookup.
Use the reproduction command in the guide with a new output directory and trial
journal. The optional journal preserves completed trials even if a later call fails.
