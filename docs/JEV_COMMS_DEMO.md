# JEV evaluation and agent communication demo

To rerun the complete demo in Docker on Windows/WSL2, use
[demo_handoff.md](../demo_handoff.md). It includes the GPU image, explicit model
download, complete-case capacity check and fresh results served beside this evidence.

Open [the demo start page](../core/examples/recordings/index.html) in a browser.
The pages contain their data and styling locally. Viewing them needs no model
credentials, GPU, database or network connection. The team's frontend stays in
`web/`; these standalone evidence pages also work before its new views are ready.

For a local URL, run from the repository root:

```powershell
.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory core
```

Then open `http://127.0.0.1:8765/examples/recordings/`. Stop the server with Ctrl+C
after presenting.

## Recorded JEV results

The [comparison page](../core/examples/recordings/jev-evaluation-2026-10-17/index.html)
contains 14 actual local NLI calls: two passes over seven distinct sample cases.
No new hosted calls were made. The first pass produced:

| Category | Distinct cases | Expected raw choice | Accepted expected selection |
| --- | ---: | ---: | ---: |
| Business priority | 1 | 1 | 0 |
| Tool selection | 3 | 3 | 3 |
| Next-step selection | 3 | 3 | 3 |
| Total | 7 | 7 | 6 |

The second pass returned the same choices and scores. The business case ranked
`high` at 0.9915 and `normal` at 0.8765: the 0.1150 margin failed the fixed 0.15
threshold. Keep this deferral visible. Thresholds and mapping wording were not
changed after observing these results. The required business explanation also
remains outside this classifier's output.

Across the 63 retained hosted calls, 62 normalized choices matched the authored
expectations; 29 outputs were bare JSON and 28 met both the requested output
contract and expected choice. JEV's first raw choices agree with 62 of those
63 outputs. These are comparisons against repetitions of seven cases, not a
63-case JEV benchmark. All three deterministic next-step rules also matched.

The first local call took **37.36 s**, including **36.47 s** of model loading.
The 13 subsequent calls had a **491.20 ms median**, ranging from **251.10 to
847.93 ms**, on an RTX 4090 Laptop GPU. Full phase timings, the exact runtime
versions and checkpoint hashes are in the
[artifact index](../core/examples/recordings/jev-evaluation-2026-10-17/README.md).
Cost and energy savings remain unknown. Seven sample cases do not qualify these
mappings for automatic replacement on other inputs.

## Present the decision workflow

1. Start with the retained hosted recordings: seven distinct sample cases and
   63 actual model calls across OpenAI, Anthropic and Gemini. The advisor found
   21 provider-specific groups across business priority, tool selection and
   next-step selection. Repeated calls are not extra independent cases.
2. Open the JEV comparison. Show the explicit premise, candidate hypotheses,
   original source digest and all NLI scores. These are actual local classifier
   outputs from the pinned OpenJEV checkpoint loaded through Pollard-Jev.
3. Show the raw choice and the separate acceptance result. The demo thresholds
   are entailment at least 0.8 and margin at least 0.15, fixed before this run and
   uncalibrated. A rejected choice remains visible and is never replaced with a
   correct label to make the model appear successful.
4. For next steps, compare the deterministic rule baseline. The supplied Boolean
   state and ordered policy already determine the answer; no model advantage is
   established for these cases. An ineligible model choice is deferred.
5. For business priority, show that the original reason still needs generation
   or another approved explanation path. A correct priority alone is a partial
   substitution, not completion of the original output contract.

The preserved hosted failure is OpenAI's first baseline `choose_retrieval`:
it chose `respond` while approved evidence was unavailable. Normalized selections
and bare JSON compliance remain separate checks. Historical hosted latency and
current local GPU latency are not a controlled speed comparison.

## Present the communication workflow

The [communication page](../core/examples/recordings/comms-efficiency-2026-10-17/index.html)
contains these measurements over all 618 events in 36 recorded runs:

| Encoding | Full body bytes |
| --- | ---: |
| Compact UTF-8 JSON | 771,411 |
| Existing individual CBOR-LD envelopes | 585,595 |
| Self-contained CBOR-LD batches | 356,491 |
| Adaptive smaller-format selection | 356,491 |

This is **53.79% below compact JSON**, or **39.12% below individual CBOR-LD**.
The portable base64 payload alone is 475,376 bytes before its JSON wrapper; it
must not be displayed as the measured binary wire size. The dense-float example
selects JSON: 1,827 bytes versus 2,866 bytes of CBOR.

An actual local SQLite FULL/WAL delivery sample took **872.85 ms** with 618
individual transactions and **204.52 ms** with 36 atomic batch transactions.
This includes validation and insertion, but not frame construction,
acknowledgement or network time. It is one sample per path, not a throughput
benchmark. Serialization has a cost: over five repeats, whole-corpus median
encode/decode time was **24.29 / 40.74 ms for JSON**, versus **32.87 / 54.86 ms
for shared CBOR**. The demo preserves those less favorable CPU results.

Open the communication page. It wraps the retained workflow events as local
inter-agent messages and measures full serialized bodies against compact JSON.
Show individual CBOR-LD envelopes, self-contained batches with shared context,
and the automatic choice of the smaller CBOR or JSON batch.

Every batch includes its own context, so a receiver does not need earlier frames
to decode it. Show the context-change check and the dense-float case where JSON
is smaller. All message values must survive the roundtrip exactly. Delivery and
duplicate handling use the actual SQLite mailbox; this is a local replay, not a
measurement of distributed agent traffic or network latency.

Byte reduction does not establish LLM-token, dollar, energy or overall latency
savings. Keep serialization CPU time and local mailbox timing beside wire size.
Applications must still choose a bounded batch size and flush interval suitable
for their latency requirements; a small urgent message need not wait for a batch.

To reproduce without any model calls, use a new output directory:

```powershell
.venv\Scripts\python.exe core/examples/messaging_demo.py --output-dir .canopy/comms-demo-new
```

The [messaging guide](../core/docs/messaging.md) documents `MessageBatchCodec`
and `Mailbox.deliver_batch`. Batch delivery validates the entire frame before
insertion and rolls back all inserts on a conflict. Deduplication and stream
ordering still apply; every batch is independently decodable after a context
change. These are opt-in Python components, not a deployed network service.

## Reproduce the JEV comparison

Use the isolated package setup and prepared checkpoint in the
[model guide](../core/docs/live_models.md), then run:

```powershell
.venv\Scripts\python.exe core/examples/jev_recorded_comparison.py --worker-python YOUR_JEV_PYTHON --model-cache-dir YOUR_CACHE --device cuda --repeats 2 --journal .canopy/jev-trials-new.jsonl --output-dir .canopy/jev-evaluation-new
```

The output directory and journal must be new. The worker never downloads weights
or calls a hosted provider. A CPU run can use `--device cpu`; its timings must
be reported as a separate measurement. The first call includes worker startup
and model loading; later calls reuse the loaded model, not a result cache.

The original evidence timestamps remain unchanged. New validity applies only to
an explicitly marked retrospective observation of historical sample data.
Neither those observations nor the classifier output authorize a tool or
transition. Production activation, representative held-out evaluation and
calibrated acceptance thresholds remain separate work.

The standalone JEV comparison and communication report use their own formats;
they are not `RunResult`, `AdvisorReport`, or `canopy-comparison/v1` bundles.
Use dedicated frontend adapters or link to these pages. Existing advisor
discovery reports remain immutable and unevaluated; link the new results beside
them rather than rewriting their provenance.
