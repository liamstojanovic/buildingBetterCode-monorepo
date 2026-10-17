# Messaging component

The codec and local mailbox are implemented. All types below live in
`canopy.messaging`; no shared contract changes are required.

**Coordinator dependency follow-up:** declare `cbor2>=6.1.5,<7` directly in
`core/pyproject.toml`. Installed `cbor2==6.1.5` provides the strict decoder options
used here; `cbor-ld-ex==0.2.1` alone permits older cbor2 versions. No packages were
installed or shared metadata edited by this component.

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

class DeliveryReceipt:
    message_id: str
    inserted: bool  # False for an identical redelivery
    sequence: int

class Mailbox:
    def __init__(self, path: str | Path, *, tenant_id: str,
                 recipient_id: str, codec: MessageCodec | None = None): ...
    def deliver(self, message: MessageEnvelope | bytes) -> DeliveryReceipt: ...
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
