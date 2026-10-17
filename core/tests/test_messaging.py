"""Synthetic messaging fixtures; no external providers or side effects are used."""

import json
import math
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import cbor2
import pytest
from cbor_ld_ex.codec import ANNOTATION_TERM_ID
from cbor_ld_ex.codec import decode as dependency_decode
from pydantic import ValidationError

from canopy.contracts import Evidence
from canopy.messaging import (
    AuthorizationContext,
    DuplicateMessageConflict,
    EvidenceReference,
    Mailbox,
    MessageCodec,
    MessageEnvelope,
    MessageError,
    ProvenanceReference,
    ScopeMismatch,
    SequenceConflict,
)


def envelope(**changes):
    """A deterministic, explicitly synthetic message for transport tests."""
    data = {
        "message_type": "observation",
        "message_id": "synthetic-message-001",
        "tenant_id": "synthetic-tenant-A",
        "workflow_id": "synthetic-workflow-A",
        "stream_id": "synthetic-stream-A",
        "sequence": 0,
        "sender_id": "synthetic-agent-source",
        "recipient_id": "synthetic-agent-destination",
        "created_at": datetime(2026, 10, 17, 12, 0, 0, 123456, tzinfo=UTC),
        "authorization": AuthorizationContext(
            actor_id="synthetic-actor-001",
            policy_version="policy-007",
            permissions=("evidence:read", "tool:preview"),
            approval_id="synthetic-approval-001",
        ),
        "payload": {"synthetic": True, "text": "Canopy test evidence"},
    }
    data.update(changes)
    return MessageEnvelope(**data)


def decode(codec, data, message):
    return codec.decode(data, tenant_id=message.tenant_id, recipient_id=message.recipient_id)


def acknowledge(mailbox, message):
    return mailbox.acknowledge(
        message.message_id,
        sender_id=message.sender_id,
        workflow_id=message.workflow_id,
        stream_id=message.stream_id,
    )


def wire_key(codec, message, field, alternate):
    """Locate a context-compressed key without coupling tests to its integer ID."""
    original = cbor2.loads(codec.encode(message))
    replacement = message.model_dump()
    replacement[field] = alternate
    changed = cbor2.loads(codec.encode(MessageEnvelope(**replacement)))
    differing = [key for key in original if original[key] != changed[key]]
    assert len(differing) == 1
    return differing[0]


def definite_map_header(size):
    """Encode just a map header so malformed duplicate entries can be appended."""
    assert size < 24
    return bytes([0xA0 | size])


@pytest.mark.parametrize("message_type", ["observation", "proposal", "decision", "result", "event"])
def test_all_envelope_types_roundtrip_through_real_dependency(message_type):
    codec = MessageCodec()
    message = envelope(message_type=message_type)
    data = codec.encode(message)
    raw_document, annotation = dependency_decode(data)
    assert isinstance(raw_document, dict)
    assert annotation.header.has_opinion is False
    assert isinstance(cbor2.loads(data)[ANNOTATION_TERM_ID], cbor2.CBORTag)
    assert decode(codec, data, message) == message


def test_roundtrip_preserves_authorization_unicode_precision_and_references():
    codec = MessageCodec()
    tenant = "  Acme/東京/e\u0301  "
    authorization = AuthorizationContext(
        actor_id=" actor/é/e\u0301/000042 ",
        policy_version="0007.0000000000000001",
        permissions=("READ", "read", " permission:é ", "READ"),
        approval_id=" APPROVAL/0000001 ",
    )
    floats = [0.1, -0.0, math.pi, 2.0**-1074, 1.7976931348623157e308]
    message = envelope(
        tenant_id=tenant,
        sender_id=" agent/É ",
        recipient_id=" agent/e\u0301 ",
        authorization=authorization,
        created_at=datetime(2026, 10, 17, 8, 0, 0, 123456, tzinfo=timezone(timedelta(hours=-4))),
        payload={
            "Unicode": "東京 🌳 é e\u0301",
            "integer": 2**200 + 17,
            "negative_integer": -(2**100 + 3),
            "floats": floats,
            "nested": [None, True, False, {"@annotation": "ordinary payload text"}],
        },
        evidence_refs=(
            EvidenceReference(
                evidence_id="Evidence/0000123",
                tenant_id=tenant,
                source="erp/東京",
                version="0000000000000000001",
            ),
        ),
        provenance=(
            ProvenanceReference(
                tenant_id=tenant,
                run_id="run/000123",
                step_id="step/00001",
                message_id="preceding-message/0000001",
            ),
        ),
    )
    restored = decode(codec, codec.encode(message), message)
    assert restored == message
    assert restored.authorization == authorization
    assert restored.tenant_id == tenant
    assert restored.sender_id == " agent/É "
    assert restored.recipient_id == " agent/e\u0301 "
    assert restored.evidence_refs == message.evidence_refs
    assert restored.provenance == message.provenance
    assert type(restored.payload["integer"]) is int
    assert restored.payload["integer"] == 2**200 + 17
    assert restored.payload["negative_integer"] == -(2**100 + 3)
    for expected, actual in zip(floats, restored.payload["floats"], strict=True):
        assert type(actual) is float
        assert struct.pack("!d", actual) == struct.pack("!d", expected)


def test_evidence_reference_copies_exact_source_and_version():
    evidence = Evidence(
        evidence_id="synthetic-evidence-001",
        tenant_id="synthetic-tenant-A",
        source="warehouse/00007",
        version="000000000009",
        observed_at=datetime(2026, 10, 17, tzinfo=UTC),
        data={"synthetic": True, "private_body": "not transmitted by reference"},
    )
    reference = EvidenceReference.from_evidence(evidence)
    assert reference.model_dump() == {
        "evidence_id": evidence.evidence_id,
        "tenant_id": evidence.tenant_id,
        "source": evidence.source,
        "version": evidence.version,
    }


@pytest.mark.parametrize("offset", [timedelta(seconds=1), timedelta(microseconds=1),
                                    timedelta(seconds=-61, microseconds=42)])
def test_timestamp_with_subminute_offset_preserves_exact_instant(offset):
    codec = MessageCodec()
    message = envelope(created_at=datetime(
        2026, 10, 17, 12, 0, 0, 123456, tzinfo=timezone(offset),
    ))
    restored = decode(codec, codec.encode(message), message)
    assert restored.created_at == message.created_at
    assert restored.created_at.utcoffset() == offset
    assert restored.created_at.microsecond == 123456
    assert MessageEnvelope.model_validate(json.loads(codec.json_bytes(message))) == message


@pytest.mark.parametrize("field,value", [("schema_version", "2"), ("message_type", "command"),
                                         ("sequence", -1), ("sequence", True),
                                         ("sequence", "1"), ("sender_id", 123),
                                         ("tenant_id", ""), ("message_id", "")])
def test_invalid_typed_envelope_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        envelope(**{field: value})


@pytest.mark.parametrize("payload", [
    {"nested": {"value": float("nan")}},
    {"values": [float("inf")]},
    {"value": float("-inf")},
    {"value": b"bytes are not JSON"},
    {"value": {1, 2}},
    {"value": datetime(2026, 10, 17, tzinfo=UTC)},
    {"nested": {1: "integer key"}},
])
def test_non_json_or_nonfinite_payload_is_rejected(payload):
    with pytest.raises(ValidationError):
        envelope(payload=payload)


def test_naive_timestamp_and_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        envelope(created_at=datetime(2026, 10, 17))
    with pytest.raises(ValidationError):
        envelope(unexpected="do not silently discard")


@pytest.mark.parametrize("reference_field", ["evidence_refs", "provenance"])
def test_cross_tenant_references_are_rejected_at_construction(reference_field):
    reference = (
        EvidenceReference(evidence_id="e-1", tenant_id="other-tenant", source="erp", version="v1")
        if reference_field == "evidence_refs"
        else ProvenanceReference(tenant_id="other-tenant", run_id="run-1")
    )
    with pytest.raises(ValidationError):
        envelope(**{reference_field: (reference,)})


def test_decode_requires_exact_tenant_and_recipient_scope():
    codec = MessageCodec()
    message = envelope()
    data = codec.encode(message)
    with pytest.raises(ScopeMismatch):
        codec.decode(data, tenant_id=message.tenant_id.upper())
    with pytest.raises(ScopeMismatch):
        codec.decode(data, tenant_id=message.tenant_id, recipient_id="other-recipient")
    assert codec.decode(data, tenant_id=message.tenant_id) == message


@pytest.mark.parametrize("data", [b"", b"\xff", b"\xa1", b"not CBOR", b"\x01", b"\x80"])
def test_malformed_cbor_raises_transport_error(data):
    with pytest.raises(MessageError):
        MessageCodec().decode(data, tenant_id="synthetic-tenant-A")


@pytest.mark.parametrize("suffix", [b"\x00", b"\xa0", b"garbage"])
def test_trailing_cbor_data_is_not_silently_ignored(suffix):
    codec = MessageCodec()
    message = envelope()
    with pytest.raises(MessageError):
        decode(codec, codec.encode(message) + suffix, message)


def test_every_truncated_prefix_is_rejected():
    codec = MessageCodec()
    message = envelope()
    data = codec.encode(message)
    for end in range(len(data)):
        with pytest.raises(MessageError):
            decode(codec, data[:end], message)


@pytest.mark.parametrize("invalid_annotation", [None, b"\x80", cbor2.CBORTag(60001, b"\x80"),
                                               cbor2.CBORTag(60000, b""),
                                               cbor2.CBORTag(60000, "wrong-type")])
def test_missing_or_invalid_dependency_annotation_is_rejected(invalid_annotation):
    codec = MessageCodec()
    message = envelope()
    wire = cbor2.loads(codec.encode(message))
    if invalid_annotation is None:
        del wire[ANNOTATION_TERM_ID]
    else:
        wire[ANNOTATION_TERM_ID] = invalid_annotation
    with pytest.raises(MessageError):
        decode(codec, cbor2.dumps(wire), message)


def test_unknown_schema_type_and_field_are_rejected_on_wire():
    codec = MessageCodec()
    message = envelope()
    wire = cbor2.loads(codec.encode(message))
    schema_keys = [key for key, value in wire.items() if value == "1"]
    assert len(schema_keys) == 1
    type_key = wire_key(codec, message, "message_type", "decision")
    sequence_key = wire_key(codec, message, "sequence", 17)
    for field, value in [(schema_keys[0], "2"), (type_key, "unregistered-message-type"),
                         (sequence_key, True), ("unknown-envelope-field", "ignored?")]:
        malformed = dict(wire)
        malformed[field] = value
        with pytest.raises(MessageError):
            decode(codec, cbor2.dumps(malformed), message)


def test_nested_cross_tenant_reference_is_rejected_on_wire():
    codec = MessageCodec()
    message = envelope(evidence_refs=(EvidenceReference(
        evidence_id="e-1", tenant_id="synthetic-tenant-A", source="erp", version="v1",
    ),))
    wire = cbor2.loads(codec.encode(message))
    key = wire_key(codec, message, "evidence_refs", ())
    wire[key][0]["tenant_id"] = "other-tenant"
    with pytest.raises(MessageError):
        decode(codec, cbor2.dumps(wire), message)


def test_duplicate_top_level_cbor_keys_are_rejected_even_when_values_match():
    codec = MessageCodec()
    message = envelope()
    wire = cbor2.loads(codec.encode(message))
    first_key = next(iter(wire))
    data = definite_map_header(len(wire) + 1) + b"".join(
        cbor2.dumps(k) + cbor2.dumps(v) for k, v in wire.items()
    )
    data += cbor2.dumps(first_key) + cbor2.dumps(wire[first_key])
    with pytest.raises(MessageError):
        decode(codec, data, message)


def test_duplicate_nested_authorization_keys_are_rejected():
    codec = MessageCodec()
    message = envelope()
    wire = cbor2.loads(codec.encode(message))
    alternate = message.authorization.model_dump()
    alternate["actor_id"] = "different-actor"
    auth_key = wire_key(codec, message, "authorization", AuthorizationContext(**alternate))
    auth = wire[auth_key]
    duplicate_auth = definite_map_header(len(auth) + 1) + b"".join(
        cbor2.dumps(k) + cbor2.dumps(v) for k, v in auth.items()
    ) + cbor2.dumps("actor_id") + cbor2.dumps("attacker-actor")
    data = definite_map_header(len(wire)) + b"".join(
        cbor2.dumps(k) + (duplicate_auth if k == auth_key else cbor2.dumps(v))
        for k, v in wire.items()
    )
    with pytest.raises(MessageError):
        decode(codec, data, message)


def test_size_measurement_counts_actual_encoded_bytes():
    codec = MessageCodec()
    message = envelope(payload={"synthetic": True, "readings": [0.1, 42, "東京"] * 20})
    comparison = codec.measure(message)
    json_data = codec.json_bytes(message)
    assert isinstance(json_data, bytes)
    assert json.loads(json_data) == message.model_dump(mode="json")
    assert json_data.decode("utf-8").count("東京") == 20
    assert comparison.json_bytes == len(json_data)
    assert comparison.cbor_bytes == len(codec.encode(message))
    assert comparison.bytes_saved == comparison.json_bytes - comparison.cbor_bytes
    assert comparison.reduction_fraction == pytest.approx(
        comparison.bytes_saved / comparison.json_bytes
    )


def test_codec_enforces_actual_input_and_output_byte_limit():
    message = envelope()
    data = MessageCodec().encode(message)
    exact_codec = MessageCodec(max_bytes=len(data))
    assert exact_codec.encode(message) == data
    assert decode(exact_codec, data, message) == message
    smaller_codec = MessageCodec(max_bytes=len(data) - 1)
    with pytest.raises(MessageError):
        smaller_codec.encode(message)
    with pytest.raises(MessageError):
        decode(smaller_codec, data, message)


def test_encoder_revalidates_mutable_nested_payload():
    message = envelope()
    message.payload["tampered_after_validation"] = float("nan")
    with pytest.raises(MessageError):
        MessageCodec().encode(message)


@pytest.mark.parametrize("sign", [-1, 1])
def test_payload_integer_bound_retains_exact_json_measurable_values(sign):
    codec = MessageCodec()
    integer = sign * (2**4096 - 1)
    message = envelope(payload={"integer": integer})
    restored = decode(codec, codec.encode(message), message)
    assert restored.payload["integer"] == integer
    assert json.loads(codec.json_bytes(message))["payload"]["integer"] == integer
    with pytest.raises(ValidationError):
        envelope(payload={"integer": sign * 2**4096})


def test_cyclic_cbor_payload_and_excessive_depth_are_rejected():
    codec = MessageCodec()
    message = envelope()
    payload_key = wire_key(codec, message, "payload", {"alternate": True})
    wire = cbor2.loads(codec.encode(message))
    loop = []
    loop.append(loop)
    wire[payload_key] = {"loop": loop}
    shared_data = cbor2.dumps(wire, value_sharing=True)
    decoded_wire = cbor2.loads(shared_data)
    assert decoded_wire[payload_key]["loop"][0] is decoded_wire[payload_key]["loop"]
    with pytest.raises(MessageError):
        decode(codec, shared_data, message)

    nested = {"leaf": "synthetic"}
    for _ in range(40):
        nested = {"nested": nested}
    wire[payload_key] = nested
    with pytest.raises(MessageError):
        decode(codec, cbor2.dumps(wire), message)


@pytest.mark.parametrize("alias", [False, "schema_version"])
def test_compressed_field_keys_cannot_be_replaced_by_boolean_or_string_alias(alias):
    codec = MessageCodec()
    message = envelope()
    wire = cbor2.loads(codec.encode(message))
    schema_key = next(key for key, value in wire.items() if value == "1")
    assert schema_key == 0  # False aliases integer zero in ordinary Python dictionaries.
    data = definite_map_header(len(wire)) + b"".join(
        cbor2.dumps(alias if key == schema_key else key) + cbor2.dumps(value)
        for key, value in wire.items()
    )
    with pytest.raises(MessageError):
        decode(codec, data, message)


def test_wire_identity_is_stable_across_payload_dictionary_insertion_order():
    codec = MessageCodec()
    first = envelope(payload={"z": {"b": 2, "a": 1}, "a": [{"d": 4, "c": 3}]})
    second = envelope(payload={"a": [{"c": 3, "d": 4}], "z": {"a": 1, "b": 2}})
    assert codec.encode(first) == codec.encode(second)
    assert codec.json_bytes(first) == codec.json_bytes(second)


def test_mailbox_acknowledgement_and_deduplication_survive_reopening(tmp_path):
    path = tmp_path / "synthetic-mailbox.sqlite"
    message = envelope()
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
        receipt = mailbox.deliver(MessageCodec().encode(message))
        assert receipt.message_id == message.message_id
        assert receipt.inserted is True
        assert receipt.sequence == 0
        assert mailbox.pending() == [message]
        assert mailbox.pending() == [message]  # Reads do not claim work.
        assert mailbox.deliver(message).inserted is False
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
        assert mailbox.pending() == [message]
        assert acknowledge(mailbox, message) is True
        assert acknowledge(mailbox, message) is False
        assert mailbox.pending() == []
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
        assert mailbox.deliver(message).inserted is False
        assert acknowledge(mailbox, message) is False
        assert mailbox.pending() == []


def test_out_of_order_delivery_and_gaps_block_only_that_stream(tmp_path):
    first = envelope()
    second = envelope(sequence=1, message_id="synthetic-message-002")
    third = envelope(sequence=2, message_id="synthetic-message-003")
    other = envelope(sequence=0, stream_id="independent-stream")
    path = tmp_path / "synthetic-mailbox.sqlite"
    with Mailbox(path, tenant_id=first.tenant_id, recipient_id=first.recipient_id) as mailbox:
        mailbox.deliver(third)
        assert mailbox.pending() == []
        with pytest.raises(SequenceConflict):
            acknowledge(mailbox, third)
        mailbox.deliver(other)
        assert mailbox.pending() == [other]
        mailbox.deliver(first)
        assert {item.stream_id for item in mailbox.pending()} == {first.stream_id, other.stream_id}
        assert acknowledge(mailbox, first) is True
        assert mailbox.pending() == [other]
        assert acknowledge(mailbox, other) is True
        assert mailbox.pending() == []
    with Mailbox(path, tenant_id=first.tenant_id, recipient_id=first.recipient_id) as mailbox:
        assert mailbox.pending() == []
        mailbox.deliver(second)
        assert mailbox.pending() == [second]
        assert acknowledge(mailbox, second) is True
        assert mailbox.pending() == [third]
        assert acknowledge(mailbox, third) is True
        assert mailbox.pending() == []


def test_conflicting_duplicate_id_and_sequence_are_rejected(tmp_path):
    message = envelope()
    with Mailbox(tmp_path / "synthetic.sqlite", tenant_id=message.tenant_id,
                 recipient_id=message.recipient_id) as mailbox:
        mailbox.deliver(message)
        with pytest.raises(DuplicateMessageConflict):
            mailbox.deliver(envelope(payload={"synthetic": True, "changed": "body"}))
        with pytest.raises(DuplicateMessageConflict):
            mailbox.deliver(envelope(sequence=1))
        with pytest.raises(SequenceConflict):
            mailbox.deliver(envelope(message_id="different-id"))
        assert mailbox.pending() == [message]
        acknowledge(mailbox, message)
        with pytest.raises(DuplicateMessageConflict):
            mailbox.deliver(envelope(payload={"changed": "after acknowledgement"}))
        with pytest.raises(SequenceConflict):
            mailbox.deliver(envelope(message_id="another-id-after-ack"))


@pytest.mark.parametrize("scope_field", ["tenant_id", "recipient_id"])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_mailbox_rejects_cross_scope_delivery_without_persisting(tmp_path, scope_field, as_bytes):
    message = envelope()
    foreign = envelope(**{scope_field: "foreign-scope"})
    with Mailbox(tmp_path / "synthetic.sqlite", tenant_id=message.tenant_id,
                 recipient_id=message.recipient_id) as mailbox:
        with pytest.raises(ScopeMismatch):
            mailbox.deliver(MessageCodec().encode(foreign) if as_bytes else foreign)
        assert mailbox.pending() == []
        assert mailbox.deliver(message).inserted is True


@pytest.mark.parametrize("scope_field", [
    "tenant_id", "recipient_id", "sender_id", "workflow_id", "stream_id",
])
def test_identical_message_ids_are_isolated_by_full_stream_scope(tmp_path, scope_field):
    path = tmp_path / "synthetic.sqlite"
    original = envelope()
    distinct = envelope(**{scope_field: "separate-scope"})
    with Mailbox(path, tenant_id=original.tenant_id, recipient_id=original.recipient_id) as first:
        assert first.deliver(original).inserted is True
        with Mailbox(path, tenant_id=distinct.tenant_id, recipient_id=distinct.recipient_id) as second:
            assert second.deliver(distinct).inserted is True
            assert acknowledge(second, distinct) is True
            assert first.pending() == [original]
            assert acknowledge(first, original) is True
            assert second.pending() == []


def test_acknowledgement_requires_matching_full_stream_identity(tmp_path):
    message = envelope()
    with Mailbox(tmp_path / "synthetic.sqlite", tenant_id=message.tenant_id,
                 recipient_id=message.recipient_id) as mailbox:
        mailbox.deliver(message)
        assert mailbox.acknowledge(
            "unknown-id", sender_id=message.sender_id,
            workflow_id=message.workflow_id, stream_id=message.stream_id,
        ) is False
        for field in ("sender_id", "workflow_id", "stream_id"):
            scope = {"sender_id": message.sender_id, "workflow_id": message.workflow_id,
                     "stream_id": message.stream_id}
            scope[field] = "wrong-scope"
            assert mailbox.acknowledge(message.message_id, **scope) is False
        assert mailbox.pending() == [message]


def test_pending_limit_returns_at_most_one_per_stream(tmp_path):
    message = envelope()
    with Mailbox(tmp_path / "synthetic.sqlite", tenant_id=message.tenant_id,
                 recipient_id=message.recipient_id) as mailbox:
        for stream in range(4):
            for sequence in range(2):
                mailbox.deliver(envelope(
                    stream_id=f"stream-{stream}", sequence=sequence,
                    message_id=f"message-{stream}-{sequence}",
                ))
        pending = mailbox.pending(limit=2)
        assert len(pending) == 2
        assert len({item.stream_id for item in pending}) == 2
        assert all(item.sequence == 0 for item in pending)
        assert len(mailbox.pending(limit=100)) == 4


def test_concurrent_connections_insert_one_copy_of_duplicate(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    message = envelope()
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id):
        pass

    def deliver_once(_):
        with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
            return mailbox.deliver(message).inserted

    with ThreadPoolExecutor(max_workers=4) as workers:
        inserted = list(workers.map(deliver_once, range(12)))
    assert inserted.count(True) == 1
    assert inserted.count(False) == 11
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
        assert mailbox.pending() == [message]


def test_concurrent_sequence_conflict_has_one_winner(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    message = envelope()
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id):
        pass

    def deliver_competing_id(index):
        with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
            candidate = envelope(message_id=f"synthetic-contender-{index}")
            try:
                mailbox.deliver(candidate)
            except SequenceConflict:
                return None
            return candidate

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(deliver_competing_id, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    with Mailbox(path, tenant_id=message.tenant_id, recipient_id=message.recipient_id) as mailbox:
        assert mailbox.pending() == winners
