"""Lossless Canopy envelopes over cbor-ld-ex and a local, durable SQLite mailbox.

Envelope identity and authorization are assertions supplied by the caller. Neither
encoding nor tenant matching authenticates a sender or grants a permission.
"""

from __future__ import annotations

import io
import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Self
from collections.abc import Iterator, Sequence

import cbor2
from cbor_ld_ex import codec as cbor_ld_codec
from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.headers import ComplianceStatus, PrecisionMode, Tier1Header
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from canopy.contracts import Evidence, utcnow


class MessageError(ValueError):
    """The envelope or wire message is not valid for this protocol."""


class ScopeMismatch(MessageError):
    """An envelope or reference crosses its expected tenant/recipient scope."""


class DuplicateMessageConflict(MessageError):
    """A message ID was already delivered with different contents in its scope."""


class SequenceConflict(MessageError):
    """A stream sequence was reused or acknowledged before its predecessor."""


class _MessageModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, validate_default=True
    )

    @field_validator("*", mode="after")
    @classmethod
    def _nonempty_strings(cls, value: Any) -> Any:
        # Preserve whitespace, case, Unicode normalization, and leading zeros.
        if isinstance(value, str) and not value:
            raise ValueError("message string fields must not be empty")
        return value


class EvidenceReference(_MessageModel):
    evidence_id: StrictStr
    tenant_id: StrictStr
    source: StrictStr
    version: StrictStr

    @classmethod
    def from_evidence(cls, evidence: Evidence) -> Self:
        return cls(
            evidence_id=evidence.evidence_id,
            tenant_id=evidence.tenant_id,
            source=evidence.source,
            version=evidence.version,
        )


class ProvenanceReference(_MessageModel):
    tenant_id: StrictStr
    run_id: StrictStr
    step_id: StrictStr | None = None
    message_id: StrictStr | None = None


class AuthorizationContext(_MessageModel):
    actor_id: StrictStr
    policy_version: StrictStr
    permissions: tuple[StrictStr, ...] = ()
    approval_id: StrictStr | None = None


def _json_tree(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Validate and detach a bounded JSON tree, without numeric/string coercion."""
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 24:
        raise ValueError("message JSON tree exceeds depth (24) or node (100000) limit")
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        if value.bit_length() > 4096:
            raise ValueError("message integers must fit within 4096 bits")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("message numbers must be finite")
        return value
    if type(value) is list:
        return [_json_tree(item, depth=depth + 1, budget=budget) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("message JSON object keys must be strings")
        return {
            key: _json_tree(value[key], depth=depth + 1, budget=budget) for key in sorted(value)
        }
    raise ValueError(f"unsupported message value type: {type(value).__name__}")


class MessageEnvelope(_MessageModel):
    schema_version: Literal["1"] = "1"
    message_type: Literal["observation", "proposal", "decision", "result", "event"]
    message_id: StrictStr
    tenant_id: StrictStr
    workflow_id: StrictStr
    stream_id: StrictStr
    sequence: StrictInt = Field(ge=0, le=2**63 - 2)
    sender_id: StrictStr
    recipient_id: StrictStr
    created_at: AwareDatetime = Field(default_factory=utcnow)
    authorization: AuthorizationContext
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: tuple[EvidenceReference, ...] = ()
    provenance: tuple[ProvenanceReference, ...] = ()

    @field_validator("payload", mode="before")
    @classmethod
    def _payload_json(cls, value: Any) -> dict[str, Any]:
        if type(value) is not dict:
            raise ValueError("payload must be a JSON object")
        return _json_tree(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _timestamp(cls, value: Any) -> datetime:
        if type(value) is str:
            match = re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
                r"(?:Z|(?P<sign>[+-])(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)"
                r"(?::(?P<second>[0-5]\d)(?:\.(?P<fraction>\d{1,6}))?)?)",
                value,
            )
            if match is None:
                raise ValueError(
                    "created_at must be an aware ISO timestamp with microsecond precision"
                )
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if match["sign"]:
                # fromisoformat treats offsets below one second as UTC; rebuild
                # the explicit offset to preserve the exact represented instant.
                offset = timedelta(
                    hours=int(match["hour"]),
                    minutes=int(match["minute"]),
                    seconds=int(match["second"] or "0"),
                    microseconds=int((match["fraction"] or "").ljust(6, "0")),
                )
                parsed = parsed.replace(
                    tzinfo=timezone(offset if match["sign"] == "+" else -offset)
                )
            return parsed
        if isinstance(value, datetime):
            return value
        raise ValueError("created_at must be an aware datetime or ISO 8601 string")

    @model_validator(mode="after")
    def _reference_tenants(self) -> Self:
        if any(ref.tenant_id != self.tenant_id for ref in (*self.evidence_refs, *self.provenance)):
            raise ValueError("evidence and provenance must belong to the envelope tenant")
        return self


@dataclass(frozen=True)
class SizeMeasurement:
    cbor_bytes: int
    json_bytes: int
    bytes_saved: int
    reduction_fraction: float


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    inserted: bool
    sequence: int


# Explicit, versioned, key-only dictionary. Never value-map authorization strings
# or integers: ContextRegistry.decompress treats integer values as vocabulary IDs.
_KEYS = (
    "schema_version",
    "message_type",
    "message_id",
    "tenant_id",
    "workflow_id",
    "stream_id",
    "sequence",
    "sender_id",
    "recipient_id",
    "created_at",
    "authorization",
    "payload",
    "evidence_refs",
    "provenance",
)
_REGISTRY = cbor_ld_codec.ContextRegistry({name: index for index, name in enumerate(_KEYS)})
_ANNOTATION_ID = cbor_ld_codec.ANNOTATION_TERM_ID


def _annotation() -> Annotation:
    # No claim about policy compliance, delegation, trust, or subjective opinion.
    return Annotation(
        Tier1Header(
            compliance_status=ComplianceStatus.INSUFFICIENT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
    )


_ANNOTATION_BYTES = encode_annotation(_annotation())


class MessageCodec:
    """Strict schema-1 wire profile, backed by real cbor-ld-ex encode/decode.

    Only this deterministic profile is accepted; arbitrary CBOR-LD-ex documents
    and alternate CBOR encodings are rejected. No payload quantization is used.
    """

    def __init__(self, *, max_bytes: int = 1_048_576):
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes

    def _document(self, envelope: MessageEnvelope) -> dict[str, Any]:
        if not isinstance(envelope, MessageEnvelope):
            raise MessageError("encode requires a MessageEnvelope")
        try:
            # Revalidate even model_construct/model_copy or mutated nested payloads.
            validated = MessageEnvelope.model_validate(
                envelope.model_dump(mode="python", warnings=False)
            )
            doc = validated.model_dump(mode="json")
            # Pydantic's datetime JSON formatter rounds sub-minute UTC offsets;
            # Python's ISO formatter preserves the complete represented instant.
            timestamp = validated.created_at.isoformat()
            doc["created_at"] = timestamp[:-6] + "Z" if timestamp.endswith("+00:00") else timestamp
            return {key: _json_tree(doc[key]) for key in _KEYS}
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise MessageError("invalid message envelope") from exc

    def encode(self, envelope: MessageEnvelope) -> bytes:
        doc = self._document(envelope)
        try:
            encoded = cbor_ld_codec.encode(doc, _annotation(), context_registry=_REGISTRY)
        except (cbor2.CBOREncodeError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise MessageError("message cannot be encoded") from exc
        if len(encoded) > self.max_bytes:
            raise MessageError("encoded message exceeds max_bytes")
        return encoded

    def decode(
        self, data: bytes, *, tenant_id: str, recipient_id: str | None = None
    ) -> MessageEnvelope:
        _scope_string(tenant_id, "tenant_id")
        if recipient_id is not None:
            _scope_string(recipient_id, "recipient_id")
        if type(data) is not bytes or not data or len(data) > self.max_bytes:
            raise MessageError("wire message must be nonempty bytes within max_bytes")
        try:
            stream = io.BytesIO(data)
            raw = cbor2.CBORDecoder(
                stream, max_depth=32, allow_indefinite=False, allow_duplicate_keys=False
            ).decode()
            if stream.read(1):
                raise MessageError("trailing data after wire message")
            if type(raw) is not dict or any(type(key) is not int for key in raw):
                raise MessageError("wire message requires integer field keys")
            if set(raw) != {*range(len(_KEYS)), _ANNOTATION_ID}:
                raise MessageError("wire field dictionary does not match schema 1")
            ann = raw[_ANNOTATION_ID]
            if (
                not isinstance(ann, cbor2.CBORTag)
                or ann.tag != _ANNOTATION_ID
                or type(ann.value) is not bytes
                or ann.value != _ANNOTATION_BYTES
            ):
                raise MessageError("unsupported annotation in schema 1 message")
            # Reject CBOR semantic types and cycles before the dependency decode.
            for key in range(len(_KEYS)):
                _json_tree(raw[key])
            doc, annotation = cbor_ld_codec.decode(data, context_registry=_REGISTRY)
            if annotation != _annotation():
                raise MessageError("unsupported message annotation")
            envelope = MessageEnvelope.model_validate(doc)
            if self.encode(envelope) != data:
                raise MessageError("wire message is not the deterministic schema 1 encoding")
        except MessageError:
            raise
        except (
            cbor2.CBORDecodeError,
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
            EOFError,
        ) as exc:
            raise MessageError("malformed message") from exc
        if envelope.tenant_id != tenant_id:
            raise ScopeMismatch("message tenant differs from expected tenant")
        if recipient_id is not None and envelope.recipient_id != recipient_id:
            raise ScopeMismatch("message recipient differs from expected recipient")
        return envelope

    def json_bytes(self, envelope: MessageEnvelope) -> bytes:
        """Compact UTF-8 JSON of the same application envelope, with no padding.

        The fixed, non-authoritative CBOR annotation is counted as wire overhead
        in measure(), without adding a verbose synthetic annotation to JSON.
        """
        doc = self._document(envelope)
        try:
            return json.dumps(
                doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
            raise MessageError("message cannot be represented as JSON") from exc

    def measure(self, envelope: MessageEnvelope) -> SizeMeasurement:
        cbor_size = len(self.encode(envelope))
        json_size = len(self.json_bytes(envelope))
        saved = json_size - cbor_size
        return SizeMeasurement(cbor_size, json_size, saved, saved / json_size)


_BATCH_CBOR = "application/vnd.canopy.message-batch+cbor;version=1"
_BATCH_JSON = "application/vnd.canopy.message-batch+json;version=1"
_BATCH_REGISTRY = cbor_ld_codec.ContextRegistry(
    {"version": 0, "shared": 1, "fields": 2, "rows": 3}
)


@dataclass(frozen=True)
class MessageBatch:
    """A self-contained transport frame; content_type must travel with data."""

    content_type: str
    data: bytes


class MessageBatchCodec:
    """Lossless, bounded batches with shared context and actual-size fallback.

    Every frame carries its own context. Receivers need no prior frame, session,
    remote registry, or cache. Changed policies/identities are encoded in full.
    The codec does not itself deliver messages or provide a network transport.
    """

    def __init__(self, *, max_bytes: int = 1_048_576, max_messages: int = 128,
                 max_expanded_bytes: int = 4_194_304):
        self._codec = MessageCodec(max_bytes=max_bytes)
        if type(max_messages) is not int or not 1 <= max_messages <= 1024:
            raise ValueError("max_messages must be an integer from 1 to 1024")
        if type(max_expanded_bytes) is not int or max_expanded_bytes < 1:
            raise ValueError("max_expanded_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self.max_messages = max_messages
        self.max_expanded_bytes = max_expanded_bytes

    @staticmethod
    def _json(documents: list[dict[str, Any]]) -> bytes:
        return json.dumps(
            documents, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _cbor(documents: list[dict[str, Any]]) -> bytes:
        # Compare encoded values: Python equality conflates bool/int and signed
        # zero. The key-only registry never value-maps authorization or numbers.
        shared = {}
        fields = []
        for index, key in enumerate(_KEYS):
            first = cbor2.dumps(documents[0][key])
            if all(cbor2.dumps(doc[key]) == first for doc in documents[1:]):
                shared[index] = documents[0][key]
            else:
                fields.append(index)
        rows = [[doc[_KEYS[index]] for index in fields] for doc in documents]
        return cbor_ld_codec.encode(
            {"version": "1", "shared": shared, "fields": fields, "rows": rows},
            _annotation(), context_registry=_BATCH_REGISTRY,
        )

    def encode(
        self, messages: Sequence[MessageEnvelope], *,
        encoding: Literal["auto", "cbor", "json"] = "auto",
    ) -> MessageBatch:
        if encoding not in ("auto", "cbor", "json"):
            raise ValueError("encoding must be auto, cbor or json")
        if not isinstance(messages, Sequence) or not 1 <= len(messages) <= self.max_messages:
            raise MessageError("batch message count is outside configured limits")
        try:
            documents = [self._codec._document(message) for message in messages]
            scope = (documents[0]["tenant_id"], documents[0]["recipient_id"])
            if any((doc["tenant_id"], doc["recipient_id"]) != scope for doc in documents):
                raise ScopeMismatch("a batch must have one tenant and recipient")
            compact_json = self._json(documents)
            if len(compact_json) > self.max_expanded_bytes:
                raise MessageError("expanded batch exceeds max_expanded_bytes")
            cbor = self._cbor(documents) if encoding != "json" else None
            if encoding == "json" or (encoding == "auto" and len(compact_json) < len(cbor)):
                packet = MessageBatch(_BATCH_JSON, compact_json)
            else:
                packet = MessageBatch(_BATCH_CBOR, cbor)
            if len(packet.data) > self.max_bytes:
                raise MessageError("encoded batch exceeds max_bytes")
            return packet
        except MessageError:
            raise
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
            raise MessageError("batch cannot be encoded") from exc

    def _cbor_documents(self, data: bytes) -> list[dict[str, Any]]:
        stream = io.BytesIO(data)
        raw = cbor2.CBORDecoder(
            stream, max_depth=36, allow_indefinite=False, allow_duplicate_keys=False
        ).decode()
        if stream.read(1) or type(raw) is not dict or any(type(key) is not int for key in raw):
            raise MessageError("invalid batch frame")
        if set(raw) != {0, 1, 2, 3, _ANNOTATION_ID} or raw[0] != "1":
            raise MessageError("unsupported batch profile")
        ann = raw[_ANNOTATION_ID]
        if (not isinstance(ann, cbor2.CBORTag) or ann.tag != _ANNOTATION_ID
                or type(ann.value) is not bytes or ann.value != _ANNOTATION_BYTES):
            raise MessageError("unsupported batch annotation")
        shared, fields, rows = raw[1], raw[2], raw[3]
        if type(shared) is not dict or type(fields) is not list or type(rows) is not list:
            raise MessageError("invalid batch context or rows")
        if (any(type(key) is not int for key in shared)
                or any(type(key) is not int for key in fields)
                or len(set(fields)) != len(fields) or set(shared) & set(fields)
                or set(shared) | set(fields) != set(range(len(_KEYS)))):
            raise MessageError("batch fields must partition the envelope dictionary")
        if not 1 <= len(rows) <= self.max_messages:
            raise MessageError("batch message count is outside configured limits")
        for value in shared.values():
            _json_tree(value)
        for row in rows:
            if type(row) is not list or len(row) != len(fields):
                raise MessageError("batch row does not match field dictionary")
            for value in row:
                _json_tree(value)
        # Account for repeated shared values BEFORE constructing full envelopes.
        # A small shared body repeated many times must not evade expansion bounds.
        def value_bytes(value):
            return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).encode("utf-8"))

        field_overhead = sum(value_bytes(key) + 1 for key in _KEYS) + len(_KEYS) + 1
        shared_bytes = sum(value_bytes(value) for value in shared.values())
        expanded = (1 + len(rows) + len(rows) * (field_overhead + shared_bytes)
                    + sum(value_bytes(value) for row in rows for value in row))
        if expanded > self.max_expanded_bytes:
            raise MessageError("expanded batch exceeds max_expanded_bytes")
        # The real dependency decoder handles the versioned header dictionary
        # and annotation after structural validation of the untrusted frame.
        doc, annotation = cbor_ld_codec.decode(data, context_registry=_BATCH_REGISTRY)
        if annotation != _annotation():
            raise MessageError("unsupported batch annotation")
        return [
            {_KEYS[index]: value for index, value in
             sorted({**doc["shared"], **dict(zip(doc["fields"], row, strict=True))}.items())}
            for row in doc["rows"]
        ]

    def decode(
        self, packet: MessageBatch, *, tenant_id: str, recipient_id: str | None = None,
    ) -> tuple[MessageEnvelope, ...]:
        _scope_string(tenant_id, "tenant_id")
        if recipient_id is not None:
            _scope_string(recipient_id, "recipient_id")
        if (not isinstance(packet, MessageBatch) or type(packet.data) is not bytes
                or not packet.data or len(packet.data) > self.max_bytes):
            raise MessageError("batch must contain nonempty bytes within max_bytes")
        try:
            if packet.content_type == _BATCH_CBOR:
                documents = self._cbor_documents(packet.data)
            elif packet.content_type == _BATCH_JSON:
                if len(packet.data) > self.max_expanded_bytes:
                    raise MessageError("expanded batch exceeds max_expanded_bytes")
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise MessageError("duplicate JSON object key")
                        result[key] = value
                    return result

                documents = json.loads(packet.data, object_pairs_hook=unique_object)
                if type(documents) is not list or not 1 <= len(documents) <= self.max_messages:
                    raise MessageError("batch message count is outside configured limits")
            else:
                raise MessageError("unsupported batch content type")
            messages = tuple(MessageEnvelope.model_validate(doc) for doc in documents)
            canonical = [self._codec._document(message) for message in messages]
            restored = self._cbor(canonical) if packet.content_type == _BATCH_CBOR else self._json(
                canonical
            )
            if restored != packet.data:
                raise MessageError("batch is not the deterministic profile encoding")
            recipients = {message.recipient_id for message in messages}
            if len(recipients) != 1 or any(message.tenant_id != tenant_id for message in messages):
                raise ScopeMismatch("batch tenant or recipient scope differs")
            if recipient_id is not None and recipients != {recipient_id}:
                raise ScopeMismatch("batch recipient differs from expected recipient")
            return messages
        except MessageError:
            raise
        except (cbor2.CBORDecodeError, ValueError, TypeError, OverflowError, RecursionError,
                EOFError, UnicodeError) as exc:
            raise MessageError("malformed message batch") from exc


def _scope_string(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


class Mailbox:
    """Local SQLite mailbox with per-stream delivery and retained deduplication.

    The instance binds trusted tenant and recipient context, but does not provide
    caller authentication. A pending read is not a claim or lease. Separate
    consumers can see the same message until it is acknowledged.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        tenant_id: str,
        recipient_id: str,
        codec: MessageCodec | None = None,
    ):
        self._tenant_id = _scope_string(tenant_id, "tenant_id")
        self._recipient_id = _scope_string(recipient_id, "recipient_id")
        self.codec = codec if codec is not None else MessageCodec()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(path), timeout=10, isolation_level=None, check_same_thread=False
        )
        try:
            self._db.execute("PRAGMA busy_timeout = 10000")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS canopy_mailbox_streams (
                    tenant_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL, next_sequence INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, recipient_id, sender_id, workflow_id, stream_id)
                );
                CREATE TABLE IF NOT EXISTS canopy_mailbox_messages (
                    delivery_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, wire BLOB NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (tenant_id, recipient_id, sender_id, workflow_id, stream_id, message_id),
                    UNIQUE (tenant_id, recipient_id, sender_id, workflow_id, stream_id, sequence)
                );
            """)
        except BaseException:
            self._db.close()
            raise

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def recipient_id(self) -> str:
        return self._recipient_id

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def deliver(self, message: MessageEnvelope | bytes) -> DeliveryReceipt:
        wire = message if type(message) is bytes else self.codec.encode(message)
        envelope = self.codec.decode(wire, tenant_id=self.tenant_id, recipient_id=self.recipient_id)
        with self._transaction() as db:
            return self._insert_message(db, envelope, wire)

    def deliver_batch(
        self, messages: MessageBatch | Sequence[MessageEnvelope], *,
        batch_codec: MessageBatchCodec | None = None,
    ) -> tuple[DeliveryReceipt, ...]:
        """Validate a bounded frame, then atomically insert it in one transaction.

        A duplicate/sequence conflict rolls back all new inserts in this batch.
        Existing records and stream acknowledgement positions stay unchanged.
        Stored messages retain the existing independent schema-1 wire format.
        """
        codec = batch_codec if batch_codec is not None else MessageBatchCodec()
        packet = messages if isinstance(messages, MessageBatch) else codec.encode(messages)
        envelopes = codec.decode(packet, tenant_id=self.tenant_id, recipient_id=self.recipient_id)
        # Prepare and validate every persistent message before beginning writes.
        prepared = [(envelope, self.codec.encode(envelope)) for envelope in envelopes]
        with self._transaction() as db:
            return tuple(self._insert_message(db, envelope, wire) for envelope, wire in prepared)

    def _insert_message(
        self, db: sqlite3.Connection, envelope: MessageEnvelope, wire: bytes,
    ) -> DeliveryReceipt:
        scope = (
            self.tenant_id,
            self.recipient_id,
            envelope.sender_id,
            envelope.workflow_id,
            envelope.stream_id,
        )
        existing = db.execute(
            """
            SELECT wire FROM canopy_mailbox_messages
            WHERE tenant_id=? AND recipient_id=? AND sender_id=?
              AND workflow_id=? AND stream_id=? AND message_id=?
            """,
            (*scope, envelope.message_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != wire:
                raise DuplicateMessageConflict("message ID already has different contents")
            return DeliveryReceipt(envelope.message_id, False, envelope.sequence)
        occupied = db.execute(
            """
            SELECT 1 FROM canopy_mailbox_messages
            WHERE tenant_id=? AND recipient_id=? AND sender_id=?
              AND workflow_id=? AND stream_id=? AND sequence=?
            """,
            (*scope, envelope.sequence),
        ).fetchone()
        if occupied is not None:
            raise SequenceConflict("stream sequence already belongs to another message")
        db.execute(
            """
            INSERT OR IGNORE INTO canopy_mailbox_streams
            (tenant_id, recipient_id, sender_id, workflow_id, stream_id) VALUES (?, ?, ?, ?, ?)
            """,
            scope,
        )
        db.execute(
            """
            INSERT INTO canopy_mailbox_messages
            (tenant_id, recipient_id, sender_id, workflow_id, stream_id,
             message_id, sequence, wire) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*scope, envelope.message_id, envelope.sequence, wire),
        )
        return DeliveryReceipt(envelope.message_id, True, envelope.sequence)

    def pending(self, *, limit: int = 100) -> list[MessageEnvelope]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            rows = self._db.execute(
                """
                SELECT m.wire FROM canopy_mailbox_messages AS m
                JOIN canopy_mailbox_streams AS s
                  USING (tenant_id, recipient_id, sender_id, workflow_id, stream_id)
                WHERE m.tenant_id=? AND m.recipient_id=? AND m.acknowledged=0
                  AND m.sequence=s.next_sequence
                ORDER BY m.delivery_order LIMIT ?
            """,
                (self.tenant_id, self.recipient_id, limit),
            ).fetchall()
        return [
            self.codec.decode(row[0], tenant_id=self.tenant_id, recipient_id=self.recipient_id)
            for row in rows
        ]

    def acknowledge(
        self, message_id: str, *, sender_id: str, workflow_id: str, stream_id: str
    ) -> bool:
        _scope_string(message_id, "message_id")
        scope = (
            self.tenant_id,
            self.recipient_id,
            _scope_string(sender_id, "sender_id"),
            _scope_string(workflow_id, "workflow_id"),
            _scope_string(stream_id, "stream_id"),
        )
        with self._transaction() as db:
            row = db.execute(
                """
                SELECT m.sequence, m.acknowledged, s.next_sequence
                FROM canopy_mailbox_messages AS m JOIN canopy_mailbox_streams AS s
                  USING (tenant_id, recipient_id, sender_id, workflow_id, stream_id)
                WHERE m.tenant_id=? AND m.recipient_id=? AND m.sender_id=?
                  AND m.workflow_id=? AND m.stream_id=? AND m.message_id=?
            """,
                (*scope, message_id),
            ).fetchone()
            if row is None or row[1]:
                return False
            if row[0] != row[2]:
                raise SequenceConflict("acknowledgement must follow stream sequence")
            db.execute(
                """
                UPDATE canopy_mailbox_messages SET acknowledged=1
                WHERE tenant_id=? AND recipient_id=? AND sender_id=?
                  AND workflow_id=? AND stream_id=? AND message_id=?
            """,
                (*scope, message_id),
            )
            db.execute(
                """
                UPDATE canopy_mailbox_streams SET next_sequence=next_sequence+1
                WHERE tenant_id=? AND recipient_id=? AND sender_id=?
                  AND workflow_id=? AND stream_id=?
            """,
                scope,
            )
        return True

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
