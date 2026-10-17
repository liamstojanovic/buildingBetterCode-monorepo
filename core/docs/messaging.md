# Messaging component

The codec and local mailbox are implemented. All types below live in
`canopy.messaging`; no shared contract changes are required.

`core/pyproject.toml` declares `cbor2>=6.1.5,<7` directly for the strict decoder
options. The reproducible environment pins `cbor2==6.1.5`;
`cbor-ld-ex==0.2.1` alone permits older cbor2 versions.

```python
class EvidenceReference:
    evidence_id: str
    tenant_id: str
    source: str
    version: str
    @classmethod
    def from_evidence(cls, evidence: Evidence) -> EvidenceReference: ...

class ProvenanceReference:
    tenant_id: str
    run_id: str
    step_id: str | None = None
    message_id: str | None = None

class AuthorizationContext:
    actor_id: str
    policy_version: str
    permissions: tuple[str, ...] = ()
    approval_id: str | None = None

class MessageEnvelope:
    schema_version: Literal["1"] = "1"
    message_type: Literal["observation", "proposal", "decision", "result", "event"]
    message_id: str
    tenant_id: str
    workflow_id: str
    stream_id: str
    sequence: int  # 0..2**63-2, per tenant/recipient/sender/workflow/stream
    sender_id: str
    recipient_id: str
    created_at: datetime  # aware, microsecond precision; defaults to UTC now
    authorization: AuthorizationContext
    payload: dict[str, Any] = {}  # JSON values only; finite binary64 floats
    evidence_refs: tuple[EvidenceReference, ...] = ()
    provenance: tuple[ProvenanceReference, ...] = ()

class SizeMeasurement:
    cbor_bytes: int
    json_bytes: int
    bytes_saved: int
    reduction_fraction: float

class MessageCodec:
    def __init__(self, *, max_bytes: int = 1_048_576): ...
    def encode(self, envelope: MessageEnvelope) -> bytes: ...
    def decode(self, data: bytes, *, tenant_id: str,
               recipient_id: str | None = None) -> MessageEnvelope: ...
    def json_bytes(self, envelope: MessageEnvelope) -> bytes: ...
    def measure(self, envelope: MessageEnvelope) -> SizeMeasurement: ...

class MessageBatch:
    content_type: str
    data: bytes

class MessageBatchCodec:
    def __init__(self, *, max_bytes: int = 1_048_576,
                 max_messages: int = 128, max_expanded_bytes: int = 4_194_304): ...
    def encode(self, messages: Sequence[MessageEnvelope], *,
               encoding: Literal["auto", "cbor", "json"] = "auto") -> MessageBatch: ...
    def decode(self, packet: MessageBatch, *, tenant_id: str,
               recipient_id: str | None = None) -> tuple[MessageEnvelope, ...]: ...

class DeliveryReceipt:
    message_id: str
    inserted: bool  # False for an identical redelivery
    sequence: int

class Mailbox:
    def __init__(self, path: str | Path, *, tenant_id: str,
                 recipient_id: str, codec: MessageCodec | None = None): ...
    def deliver(self, message: MessageEnvelope | bytes) -> DeliveryReceipt: ...
    def deliver_batch(self, messages: MessageBatch | Sequence[MessageEnvelope], *,
                      batch_codec: MessageBatchCodec | None = None
                      ) -> tuple[DeliveryReceipt, ...]: ...
    def pending(self, *, limit: int = 100) -> list[MessageEnvelope]: ...
    def acknowledge(self, message_id: str, *, sender_id: str,
                    workflow_id: str, stream_id: str) -> bool: ...
    def close(self) -> None: ...
    # Context-manager support.
```

Each model above has normal Pydantic constructors and `model_dump()` support;
receipt/measurement are immutable dataclasses. Errors derive from `MessageError`
(`ValueError`): `ScopeMismatch`, `DuplicateMessageConflict`, `SequenceConflict`.
Invalid model construction raises Pydantic `ValidationError`. The codec wraps
malformed input in `MessageError`; expected tenant/recipient mismatches raise
`ScopeMismatch`. References crossing the envelope's tenant are always invalid.

## Wire format and precision

The implementation calls the installed dependency's
`cbor_ld_ex.codec.encode(doc, annotation, context_registry=registry)` and
`decode(data, context_registry=registry)`. It does not substitute a lookalike
encoder. The registry compresses these top-level names to integer keys 0 through
13, in order:

```text
schema_version, message_type, message_id, tenant_id, workflow_id, stream_id,
sequence, sender_id, recipient_id, created_at, authorization, payload,
evidence_refs, provenance
```

Nested dictionary keys remain strings. No value registry is used: the dependency
can otherwise reinterpret integer values as vocabulary strings. The required
annotation at key 60000 is `CBORTag(60000, b"\x80")`: Tier 1, INSUFFICIENT,
no delegation, no opinion, no extensions. Its precision selector is unused
because there is no opinion. No authorization, confidence, trust, or compliance
claim is encoded in that header.

There is **no quantization**. Finite Python binary64 floats, including negative
zero and subnormals, preserve their bits; integers preserve their exact values
within a 4096-bit magnitude limit. Strings preserve case, whitespace, leading
zeros, Unicode codepoints, permission order, and duplicate permissions. Opaque
source versions and identity/approval values are never hashed, truncated,
normalized, or converted to numbers. Timestamp instants and UTC offsets retain
microsecond precision, including fractional-second offsets; UTC serializes as
`Z`. Timestamp strings must have the explicit ISO date/time and offset form
produced by the codec, with at most six fractional digits.

Payloads accept only JSON objects/arrays, strings, integers, finite floats,
booleans, and null. Unsupported values are rejected rather than coerced.
Each field's JSON tree is limited to 24 nesting levels and 100,000 nodes; the
default wire limit is 1 MiB. Model validation is repeated at encoding, including
for nested payloads changed after construction. The integer bound keeps the
JSON comparison below Python's default decimal conversion safety limit.

The decoder requires this deterministic schema-1 profile, not arbitrary
CBOR-LD-ex. It rejects unknown fields/versions/types, raw string aliases for
compressed keys, duplicate keys (including nested objects), indefinite-length
items, invalid annotations, semantic values outside JSON, excessive nesting,
truncation, and trailing bytes. It then invokes the real dependency decoder and
checks that re-encoding produces exactly the input bytes. There is no remote
JSON-LD context loading. Registry/schema changes need an explicit protocol version.

Evidence references carry `evidence_id`, `tenant_id`, `source`, and exact
`version`; `from_evidence()` intentionally excludes the evidence body. Provenance
references carry tenant/run/optional step/message IDs. A receiver must resolve
references and separately check current source versions, freshness, policy, and
authorization. A structurally valid envelope is not proof that its claims are true.

## Local delivery semantics

Mailbox persistence is local SQLite. Streams start at sequence zero. Deliveries
may arrive out of order; `pending` exposes only the next unacknowledged sequence
per stream. Acknowledging advances that stream, so gaps block later messages.
Identical redelivery is a no-op even after acknowledgement; conflicting reuse of
an ID or sequence raises. ID deduplication is scoped to
tenant/recipient/sender/workflow/stream. Different streams have no causal order.
Reads do not claim messages, so consumers may see the same pending message until
acknowledged. This is local durable delivery, not distributed consensus, global
ordering, external-side-effect idempotency, authentication, or authorization.

Writes and acknowledgements use SQLite `BEGIN IMMEDIATE`, WAL, and FULL
synchronous mode, with unique constraints on scoped IDs and scoped sequences.
The tenant and recipient properties of a mailbox instance are read-only. Multiple
instances may share the same database; every read/write includes the bound scope.
Acknowledgement returns `True` once, `False` for an already acknowledged or
unknown scoped ID, and raises `SequenceConflict` for an unready sequence.
Duplicate-ID conflicts are checked before sequence conflicts. Retrying a delivery
requires the same full envelope, including its original timestamp.

Across ready streams, `pending()` sorts by local insertion order; this is only a
stable presentation order, not a cross-stream causal guarantee. Acknowledged
messages and deduplication records are retained indefinitely. There is no pruning,
lease, consumer group, gap skipping, network transport, replication, encryption,
signature verification, or distributed transaction. Use one coordinated consumer
per stream, and service-owned idempotency for external actions: a crash between an
external effect and local acknowledgement can cause redelivery. SQLite files must
be on a suitable local filesystem; `:memory:` is available but is not durable.

## Actual byte measurements

`measure()` counts `len(encode(message))` against `len(json_bytes(message))`.
JSON is compact UTF-8 with `ensure_ascii=False`, no padding, and identical
application fields. The fixed CBOR annotation counts as wire overhead; the JSON
baseline is not inflated with a verbose synthetic annotation. Negative savings
are reported as negative values. Bytes do not measure LLM tokens, latency, cost,
or energy.

Measured with cbor-ld-ex 0.2.1 and cbor2 6.1.5 on the explicitly synthetic
`envelope()` fixture in `core/tests/test_messaging.py`:

| Synthetic payload | CBOR bytes | JSON bytes | Bytes saved | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Default text envelope | 371 | 590 | 219 | 37.12% |
| `{"synthetic": True, "readings": [0.1, 42, "東京"] * 20}` | 716 | 893 | 177 | 19.82% |
| `{"synthetic": True, "readings": [1.0] * 256}` | 2661 | 1597 | -1064 | -66.62% |

The dense float case expands because preserving float64 uses nine CBOR bytes
per number, while the JSON text `1.0` is shorter. These examples do not predict
workload-wide savings. Reproduce from the repository root:

```python
import runpy
from canopy.messaging import MessageCodec

fixture = runpy.run_path("core/tests/test_messaging.py")["envelope"]
codec = MessageCodec()
for message in (
    fixture(),
    fixture(payload={"synthetic": True, "readings": [0.1, 42, "東京"] * 20}),
    fixture(payload={"synthetic": True, "readings": [1.0] * 256}),
):
    print(codec.measure(message))
```

## Minimal integration

```python
from canopy.messaging import AuthorizationContext, Mailbox, MessageCodec, MessageEnvelope

# Explicitly synthetic local example. Trusted application context supplies scope.
message = MessageEnvelope(
    message_type="observation", message_id="synthetic-message-1",
    tenant_id="synthetic-tenant", workflow_id="synthetic-workflow",
    stream_id="synthetic-stream", sequence=0,
    sender_id="synthetic-source", recipient_id="synthetic-consumer",
    authorization=AuthorizationContext(
        actor_id="synthetic-actor", policy_version="synthetic-policy-v1",
        permissions=("evidence:read",),
    ),
    payload={"synthetic": True, "answer": 42},
)
wire = MessageCodec().encode(message)
with Mailbox("mailbox.sqlite", tenant_id=message.tenant_id,
             recipient_id=message.recipient_id) as mailbox:
    receipt = mailbox.deliver(wire)
    for ready in mailbox.pending():
        # Resolve evidence and enforce policy before acting on the message.
        mailbox.acknowledge(ready.message_id, sender_id=ready.sender_id,
                            workflow_id=ready.workflow_id, stream_id=ready.stream_id)
```

Run focused checks from the root:

```powershell
.venv\Scripts\python.exe -m pytest core/tests/test_messaging.py -q
.venv\Scripts\python.exe -m ruff check core/src/canopy/messaging.py core/tests/test_messaging.py
```

## Shared context batching and atomic delivery

`MessageBatchCodec` is an opt-in transport layer around the unchanged schema-1
envelope. A batch has one tenant and recipient; it may contain several streams,
senders, policies or workflows. It preserves every application field. The batch
is self-contained: a receiver can decode the second frame without ever seeing
the first. Changed actor or policy values are carried in the new frame. There
is no remote context lookup, stateful delta chain or uncounted session setup.

The binary profile has four context-compressed header keys: `version` (0),
`shared` (1), `fields` (2), and `rows` (3), plus the standard non-authoritative
cbor-ld-ex annotation. `shared` maps envelope field indexes to values identical
across all messages. `fields` gives the varying indexes; `rows` carries their
per-message values in that order. Shared-value comparison uses CBOR bytes so
`True`, `1`, `1.0`, negative zero and positive zero remain distinct. Nested
keys and values are retained exactly; no numeric quantization is introduced.

`encode(..., encoding="auto")` actually constructs both candidate payloads and
selects the smaller. The fallback is a compact UTF-8 JSON array of complete
envelopes, with no whitespace padding or synthetic annotation. Dense float
payloads can be smaller in JSON. Both paths retain full validation and scope
checks. Carry `packet.content_type` together with `packet.data`:

- `application/vnd.canopy.message-batch+cbor;version=1`
- `application/vnd.canopy.message-batch+json;version=1`

The strict decoder rejects malformed dictionaries, duplicate or unknown keys,
alternate encodings, invalid annotations, incompatible scope and truncated
frames. Defaults bound the wire to 1 MiB, message count to 128 and reconstructed
compact-JSON size to 4 MiB. The expansion limit is checked before materializing
repeated shared values. Do not silently split an oversized frame; select an
application-appropriate bounded flush size and maximum wait. Waiting to fill a
batch adds latency and is excluded from the measured codec CPU times.

`Mailbox.deliver_batch()` fully validates the frame, prepares all independent
schema-1 stored messages, and inserts them under one SQLite transaction. A
conflicting message ID or sequence rolls back every new insert in that batch.
Identical redelivery still returns `inserted=False`, including after
acknowledgement. The existing ordering, scope checks and durable deduplication
continue to apply. This atomic local insertion does not make external effects
atomic. Acknowledgement remains per message. Existing single-message API and
database rows need no migration.

```python
from canopy.messaging import Mailbox, MessageBatchCodec

codec = MessageBatchCodec(max_messages=32)
packet = codec.encode(messages)  # list of validated MessageEnvelope objects
# Your transport carries packet.content_type and packet.data together.
with Mailbox("mailbox.sqlite", tenant_id="sample-tenant",
             recipient_id="sample-demo-orchestrator") as mailbox:
    receipts = mailbox.deliver_batch(packet, batch_codec=codec)
```

These codecs and local mailboxes are usable primitives. The runtime does not
automatically send its events through a distributed agent transport. Integration
must choose batch flush rules and an authenticated transport separately.

## Recorded-event efficiency demo

Open [the standalone offline demo](../examples/recordings/comms-efficiency-2026-10-17/index.html),
or inspect [the measured JSON report](../examples/recordings/comms-efficiency-2026-10-17/report.json).
No frontend build, model weights, API keys or network calls are required. The page
shows exact byte totals, a provider/category/run selector, a complete example
message, lossless/fallback checks, CPU medians and local mailbox timings.

The source is all **618 retained runtime events from 36 runs / nine provider
comparisons**, covering business decisions, tool selection and next steps. Their
original event bodies are wrapped into **new local inter-agent replay envelopes**;
these are not historical network captures. Hosted calls were real; the business
scenarios and new transport identities are marked sample data. Full event
payloads, references, authorization, timestamps and provenance survive every
roundtrip. SHA-256 source and output inventories accompany the report.

| Representation, same complete envelopes | Actual payload bytes |
| --- | ---: |
| Compact UTF-8 JSON arrays | 771,411 |
| Existing CBOR, one independent message at a time | 585,595 |
| Shared-context cbor-ld-ex batches | 356,491 |
| Adaptive selection | 356,491 |

The binary batches save **414,920 bytes (53.79%)** against compact JSON, and
**39.12%** against independent CBOR envelopes on this corpus. All 36 frames chose
CBOR. The separate dense-float counterexample needs **2,866 CBOR bytes vs 1,827
JSON bytes**, so automatic selection chooses JSON. A fresh receiver successfully
decodes changed policy/actor context without previous frames; wrong-tenant and
truncated frames are rejected. Every original event was delivered in reverse
order to the actual local SQLite mailbox, then acknowledged in stream order;
36 identical redeliveries were suppressed.

The raw binary count includes complete batch context and annotations but excludes
transport headers. `frames.json` carries base64 for portability: those base64
strings alone total **475,376 bytes**, before the surrounding JSON. Sending that
file does not get the raw binary byte count. Each frame declares its content type
and payload SHA-256; it can be decoded with the real batch codec.

This Windows/Python 3.12.2 sample used cbor-ld-ex 0.2.1 and cbor2 6.1.5. Median
whole-corpus codec times over five repetitions were:

| Profile | Encode ms | Decode ms |
| --- | ---: | ---: |
| Compact JSON | 24.29 | 40.74 |
| Independent CBOR | 29.75 | 65.00 |
| Shared-context CBOR | 32.87 | 54.86 |
| Adaptive selection | 30.91 | 53.84 |

JSON is cheaper in this local CPU measurement; batching primarily reduces wire
repetition. Timings include validation but exclude input loading, SQLite,
network and time waiting for a batch. The separate local mailbox sample took
**872.85 ms for 618 individual transactions** and **204.52 ms for 36 atomic batch
transactions**, with SQLite FULL synchronous/WAL in both. It includes validation
and commits, excluding frame construction, database creation and acknowledgements.
That is one sample per path on separate fresh databases, not a production
throughput result. Neither byte reduction nor local time establishes LLM token,
cost, energy or network-latency savings.

Reproduce with a new output directory; the runner never overwrites an existing
report and never calls a provider:

```powershell
.venv\Scripts\python.exe core/examples/messaging_demo.py --output-dir .canopy/comms-recheck --repeats 5
.venv\Scripts\python.exe -m pytest core/tests/test_messaging.py core/tests/test_messaging_batch.py core/tests/test_messaging_demo.py -q
```

To decode an exported frame offline:

```python
import base64
import json
from pathlib import Path
from canopy.messaging import MessageBatch, MessageBatchCodec

frame = json.loads(Path("core/examples/recordings/comms-efficiency-2026-10-17/frames.json")
                   .read_text(encoding="utf-8"))["frames"][0]
messages = MessageBatchCodec().decode(
    MessageBatch(frame["content_type"], base64.b64decode(frame["data_base64"])),
    tenant_id="sample-tenant", recipient_id="sample-demo-orchestrator",
)
```
