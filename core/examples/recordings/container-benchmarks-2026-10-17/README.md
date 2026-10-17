# Current container benchmark evidence — 17 October 2026

Open the [results overview](index.html), [JEV comparison](jev/index.html), or
[CPU results](cpu/index.html). This publication closes the gap between the local
Docker rehearsal and the evidence available on GitHub. The CPU suite was rerun
at **20:09 UTC** on source commit `28a276b`; the latest completed GPU comparison
from **19:56 UTC** is preserved exactly. Its runtime and evaluator code match
that commit. [Provenance](provenance.json) records full revisions, tree hashes,
image identities, execution scope and the limited publication transformations.

**Incremental provider/API spending: $0 of the authorized $5 maximum.** No new
hosted request was needed. [The spending record](spend.json) covers this refresh;
earlier hosted costs and local electricity costs remain unmeasured.

## Results and their scope

| Check | Current evidence | Interpretation |
| --- | --- | --- |
| Hosted inputs | 9 comparisons, 36 verified original runs, 63 actual calls, 21 warm reuses | Historical OpenAI, Anthropic and Gemini calls; not rerun for this publication |
| Offline advisor | 9 fresh reports, 21 recommendation groups, 0 new model calls | Discovery remains `needs_mapping` / `not_evaluated`; separate JEV evidence does not activate routing |
| Actual local JEV | 14/14 raw expected choices, 12/14 accepted selections | Two passes over **7 distinct cases**, not 14 independent cases |
| Business priority | Raw choice correct in both passes; deferred in both | Margin 0.1150 is below the unchanged 0.15 gate; explanation work remains |
| Tool / next-step selection | All 3 tool and all 3 next-step cases accepted in both passes | Next-step rules also solve these explicit Boolean states without a model |
| GPU capacity | 7 complete calls, 26 hypothesis pairs, at most 217 tokens, no truncation | Peak allocated 9,143.99 MiB; reserved 9,186 MiB on a **16 GB 4090 Laptop** |
| Message bodies | 771,411 JSON bytes → 356,491 batched CBOR bytes | **53.79% smaller**, exact roundtrips for all 618 messages in 36 batches |
| CPU governance checks | Warm reuse, targeted invalidation, containment, replay and deduplication passed | Deterministic synthetic handlers; no hosted quality or token-saving claim |

JEV's first call took **6,469.67 ms**, including **5,643.78 ms** of model loading.
The other 13 calls had a **301.87 ms median**. Keep the cold cost visible. The
[original native run](../jev-evaluation-2026-10-17/README.md) remains unchanged;
different native, Docker and historical hosted timings are not a controlled
speed comparison. Seven invented cases and uncalibrated gates do not qualify
automatic replacement on other workloads. Cost and energy savings remain unknown.

The fresh whole-corpus encode/decode medians were **32.05 / 52.38 ms for JSON**
and **38.24 / 72.39 ms for batched CBOR**, over five repetitions. CBOR was smaller
but slower to serialize here. Local SQLite delivery took **1,219.06 ms** for 618
individual transactions and **219.77 ms** for 36 atomic batch transactions, one
sample per path. Those times exclude frame construction, acknowledgement and
network time; they do not establish distributed throughput. The dense-float
fixture correctly selected JSON (1,827 bytes versus 2,866 CBOR bytes). All 36
duplicate-message redeliveries were suppressed.

## GPU compatibility and preserved attempts

This host used Docker Desktop **4.28**, whose default GPU library injection
failed CUDA initialization. A temporary read-only bind of an installed Windows
driver library enabled the completed tests; no host software was changed.
The [validation note](gpu-validation.json) describes the workaround, and the
[device failure](gpu/device-validation.json),
[unsuccessful bind attempt](gpu/device-validation-driver-bind.json),
[successful device check](gpu/device-validation-driver-path.json),
[interrupted Windows-cache attempt](gpu/gpu-preflight-windows-bind-interrupted.json)
and [complete named-volume preflight](gpu/gpu-preflight-validation-named-volume.json)
remain visible. The interrupted attempt is not counted as a successful inference
or a capacity failure. The [model verification receipt](gpu/model-verification-validation.json)
retains the five pinned file hashes.

**The teammate's physical 12 GB 4080 has not been tested.** Follow the
[Windows/WSL2 runbook](../../../../demo_handoff.md), including current Docker
Desktop and the complete capacity preflight. The shipped standard GPU setup was
not validated on current Docker Desktop in this rehearsal. Linux named-volume
storage was used for the successful model load; no weights were downloaded again.

## Inspect or present

- [JEV comparison JSON](jev/comparison.json) retains all mapped inputs, raw scores,
  gate decisions, phase timings and hosted responses, including the historical
  wrong next-step choice. [The trial journal](jev/trials.jsonl) contains all 14
  completed trials and no failures.
- [CPU overview](cpu/index.html) links all nine current advisor JSON exports,
  [communication measurements](cpu/comms/index.html),
  [synthetic workflow results](cpu/synthetic.json), and
  [component checks](cpu/components.json).
- In the current frontend, **Optimization → Import advisor report** accepts the
  per-provider/category files under `cpu/advisor/`. The old immutable hosted
  bundle's README predates this UI. Execution playback still uses its separate
  `frontend-comparison.json` files. JEV and comms pages are separate report formats.
- [Audit record](audit.json) and [file inventory](manifest.json) document integrity
  checks. Original source timestamps and the earlier evidence bundles were not
  rewritten or relabeled as fresh operational evidence.

The CPU image ran with `network_mode: none`; the GPU runs also used offline
inference. Reproduce using the commands in the runbook with new output and journal
paths. This bundle adds reproducible sample evidence, not a held-out independent
business-quality benchmark or automatic production activation.
