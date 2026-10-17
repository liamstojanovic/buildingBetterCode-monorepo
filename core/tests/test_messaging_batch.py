"""Batch transport keeps exact envelopes and requires no previous frames."""

import json
import math
import struct
from datetime import UTC, datetime

import cbor2
import pytest
from cbor_ld_ex.codec import ANNOTATION_TERM_ID, decode as dependency_decode

from canopy.messaging import (
    AuthorizationContext, DuplicateMessageConflict, Mailbox, MessageBatch, MessageBatchCodec,
    MessageCodec, MessageEnvelope, MessageError, ScopeMismatch, SequenceConflict,
)


def messages(count=8, **changes):
    result = []
    for sequence in range(count):
        data = dict(
            message_type="event", message_id=f"sample-message-{sequence}",
            tenant_id="sample-tenant", workflow_id="sample-workflow",
            stream_id="sample-stream", sequence=sequence,
            sender_id="sample-worker", recipient_id="sample-orchestrator",
            created_at=datetime(2026, 10, 17, 19, 0, sequence, tzinfo=UTC),
            authorization=AuthorizationContext(
                actor_id="sample-actor", policy_version="policy-007",
                permissions=("events:read", "events:read"),
            ),
            payload={"sample_data": True, "result": sequence, "text": "東京 🌳"},
        )
        data.update(changes)
        result.append(MessageEnvelope(**data))
    return result


def decode(codec, packet):
    return codec.decode(packet, tenant_id="sample-tenant", recipient_id="sample-orchestrator")


@pytest.mark.parametrize("encoding", ["auto", "cbor", "json"])
def test_batch_roundtrip_and_delivery_use_real_dependency(encoding, tmp_path):
    codec = MessageBatchCodec()
    original = messages()
    packet = codec.encode(original, encoding=encoding)
    restored = decode(codec, packet)
    assert list(restored) == original
    if "cbor" in packet.content_type:
        raw, annotation = dependency_decode(packet.data)
        assert raw[0] == "1"
        assert annotation.header.has_opinion is False
    with Mailbox(tmp_path / "sample.sqlite", tenant_id="sample-tenant",
                 recipient_id="sample-orchestrator") as mailbox:
        for message in reversed(restored):
            assert mailbox.deliver(message).inserted
        for message in original:
            assert mailbox.pending() == [message]
            assert mailbox.acknowledge(
                message.message_id, sender_id=message.sender_id,
                workflow_id=message.workflow_id, stream_id=message.stream_id,
            )
        assert mailbox.pending() == []
        assert not mailbox.deliver(original[0]).inserted


def test_shared_context_reduces_actual_bytes_against_unpadded_json_and_single_cbor():
    codec = MessageBatchCodec()
    original = messages()
    packet = codec.encode(original)
    baseline = codec.encode(original, encoding="json")
    assert baseline.data == json.dumps(json.loads(baseline.data), ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")
    assert json.loads(baseline.data) == [m.model_dump(mode="json") for m in original]
    assert len(packet.data) < sum(len(MessageCodec().encode(m)) for m in original)
    assert len(packet.data) < len(baseline.data)


def test_dense_float_payload_uses_actual_smaller_json_fallback():
    codec = MessageBatchCodec()
    original = messages(1, payload={"readings": [1.0] * 256})
    automatic = codec.encode(original)
    cbor = codec.encode(original, encoding="cbor")
    baseline = codec.encode(original, encoding="json")
    assert automatic == baseline
    assert len(baseline.data) < len(cbor.data)
    assert list(decode(codec, automatic)) == original


def test_context_refresh_and_missing_previous_frame_need_no_state():
    sender = MessageBatchCodec()
    first = messages(3)
    updated = messages(3, authorization=AuthorizationContext(
        actor_id="new actor", policy_version="000008", permissions=("read",),
    ))
    old_packet = sender.encode(first)
    new_packet = sender.encode(updated)
    assert list(decode(MessageBatchCodec(), new_packet)) == updated
    assert list(decode(MessageBatchCodec(), old_packet)) == first


@pytest.mark.parametrize("encoding", ["cbor", "json"])
def test_batch_preserves_numeric_types_signed_zero_and_unicode(encoding):
    original = messages(3)
    original[0].payload.update({"value": -0.0, "n": 1, "unicode": "é e\u0301 0007"})
    original[1].payload.update({"value": 0.0, "n": True, "unicode": "é e\u0301 0007"})
    original[2].payload.update({"value": math.nextafter(0.0, 1.0), "n": 1.0,
                                "unicode": "é e\u0301 0007"})
    codec = MessageBatchCodec()
    restored = decode(codec, codec.encode(original, encoding=encoding))
    for expected, actual in zip(original, restored, strict=True):
        assert type(actual.payload["n"]) is type(expected.payload["n"])
        assert struct.pack("!d", actual.payload["value"]) == struct.pack(
            "!d", expected.payload["value"]
        )
        assert actual.payload["unicode"] == expected.payload["unicode"]


@pytest.mark.parametrize("field", ["tenant_id", "recipient_id"])
def test_batch_rejects_mixed_scopes_and_wrong_receiver(field):
    codec = MessageBatchCodec()
    original = messages(2)
    original[1] = original[1].model_copy(update={field: "other"})
    with pytest.raises(ScopeMismatch):
        codec.encode(original)
    packet = codec.encode(messages(2))
    with pytest.raises(ScopeMismatch):
        codec.decode(packet, tenant_id="other" if field == "tenant_id" else "sample-tenant",
                     recipient_id="other" if field == "recipient_id" else "sample-orchestrator")


def test_counts_and_actual_byte_limits_are_enforced():
    original = messages(3)
    codec = MessageBatchCodec(max_messages=2)
    with pytest.raises(MessageError):
        codec.encode(original)
    with pytest.raises(MessageError):
        codec.encode([])
    packet = MessageBatchCodec().encode(original)
    with pytest.raises(MessageError):
        decode(codec, packet)
    exact = MessageBatchCodec(max_bytes=len(packet.data))
    assert exact.encode(original) == packet
    assert list(decode(exact, packet)) == original
    small = MessageBatchCodec(max_bytes=len(packet.data) - 1)
    with pytest.raises(MessageError):
        small.encode(original)
    with pytest.raises(MessageError):
        decode(small, packet)


@pytest.mark.parametrize("mutation", ["trailing", "annotation", "version", "fields",
                                       "overlap", "row", "semantic", "type_alias"])
def test_malformed_cbor_batch_is_rejected(mutation):
    codec = MessageBatchCodec()
    packet = codec.encode(messages(), encoding="cbor")
    if mutation == "trailing":
        changed = packet.data + b"x"
    else:
        raw = cbor2.loads(packet.data)
        if mutation == "annotation":
            raw[ANNOTATION_TERM_ID] = cbor2.CBORTag(ANNOTATION_TERM_ID, b"wrong")
        elif mutation == "version":
            raw[0] = "2"
        elif mutation == "fields":
            raw[2].append(raw[2][0])
        elif mutation == "overlap":
            raw[1][raw[2][0]] = "wrong"
        elif mutation == "row":
            raw[3][0].append("extra")
        elif mutation == "semantic":
            raw[1][0] = b"bytes"
        elif mutation == "type_alias":
            value = raw.pop(0)
            raw[False] = value
        changed = cbor2.dumps(raw)
    with pytest.raises(MessageError):
        decode(codec, MessageBatch(packet.content_type, changed))


@pytest.mark.parametrize("suffix", [b" ", b"{}"])
def test_noncanonical_json_and_trailing_json_are_rejected(suffix):
    codec = MessageBatchCodec()
    packet = codec.encode(messages(), encoding="json")
    with pytest.raises(MessageError):
        decode(codec, MessageBatch(packet.content_type, packet.data + suffix))


def test_duplicate_json_keys_and_invalid_nested_values_are_rejected():
    codec = MessageBatchCodec()
    packet = codec.encode(messages(), encoding="json")
    duplicate = packet.data.replace(b'"schema_version":"1"',
                                    b'"schema_version":"1","schema_version":"1"', 1)
    with pytest.raises(MessageError):
        decode(codec, MessageBatch(packet.content_type, duplicate))
    original = messages()
    original[0].payload["bad"] = float("nan")
    with pytest.raises(MessageError):
        codec.encode(original)


def test_batch_output_stable_across_nested_insertion_order():
    first = messages(3, payload={"b": {"z": 1, "a": 2}, "a": 1})
    second = messages(3, payload={"a": 1, "b": {"a": 2, "z": 1}})
    codec = MessageBatchCodec()
    assert codec.encode(first) == codec.encode(second)


@pytest.mark.parametrize("value", [0, -1, True, 1025])
def test_batch_rejects_invalid_message_count_configuration(value):
    with pytest.raises(ValueError):
        MessageBatchCodec(max_messages=value)


@pytest.mark.parametrize("as_packet", [False, True])
def test_atomic_mailbox_batch_persists_independent_messages_and_deduplicates(tmp_path, as_packet):
    original = messages(3)
    candidate = MessageBatchCodec().encode(original) if as_packet else original
    path = tmp_path / "mailbox.sqlite"
    with Mailbox(path, tenant_id="sample-tenant", recipient_id="sample-orchestrator") as mailbox:
        assert [r.inserted for r in mailbox.deliver_batch(candidate)] == [True] * 3
        assert [r.inserted for r in mailbox.deliver_batch(candidate)] == [False] * 3
        assert mailbox.pending() == [original[0]]
    with Mailbox(path, tenant_id="sample-tenant", recipient_id="sample-orchestrator") as mailbox:
        for message in original:
            assert mailbox.pending() == [message]
            mailbox.acknowledge(message.message_id, sender_id=message.sender_id,
                                workflow_id=message.workflow_id, stream_id=message.stream_id)
        assert mailbox.pending() == []
        assert [r.inserted for r in mailbox.deliver_batch(candidate)] == [False] * 3


@pytest.mark.parametrize("conflict", ["id", "sequence", "within_batch"])
def test_atomic_mailbox_conflicts_roll_back_new_messages(tmp_path, conflict):
    original = messages(3)
    with Mailbox(tmp_path / "mailbox.sqlite", tenant_id="sample-tenant",
                 recipient_id="sample-orchestrator") as mailbox:
        mailbox.deliver(original[0])
        changed = original[0].model_copy(update={"payload": {"changed": True}})
        error = DuplicateMessageConflict
        if conflict == "sequence":
            changed = original[0].model_copy(update={"message_id": "different-id"})
            error = SequenceConflict
        elif conflict == "within_batch":
            changed = original[1].model_copy(update={"payload": {"changed": True}})
        with pytest.raises(error):
            mailbox.deliver_batch([original[1], changed])
        assert mailbox.deliver(original[1]).inserted  # prior tentative insert rolled back
        assert not mailbox.deliver(original[0]).inserted


def test_atomic_mailbox_rejects_scope_before_any_insert(tmp_path):
    original = messages(3, tenant_id="other-tenant")
    with Mailbox(tmp_path / "mailbox.sqlite", tenant_id="sample-tenant",
                 recipient_id="sample-orchestrator") as mailbox:
        with pytest.raises(ScopeMismatch):
            mailbox.deliver_batch(original)
        assert mailbox.pending() == []


def test_shared_values_cannot_bypass_expanded_size_limit():
    original = messages(8, payload={"shared": "x" * 1000})
    permissive = MessageBatchCodec()
    packet = permissive.encode(original, encoding="cbor")
    compact = permissive.encode(original, encoding="json")
    assert len(packet.data) < len(compact.data)
    bounded = MessageBatchCodec(max_expanded_bytes=len(compact.data) - 1)
    with pytest.raises(MessageError, match="max_expanded_bytes"):
        bounded.encode(original)
    with pytest.raises(MessageError, match="max_expanded_bytes"):
        decode(bounded, packet)
    exact = MessageBatchCodec(max_expanded_bytes=len(compact.data))
    assert list(decode(exact, packet)) == original
