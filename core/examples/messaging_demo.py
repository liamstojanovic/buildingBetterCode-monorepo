"""Measure and replay real recorded events through a lossless local message transport.

No provider or network calls. Events came from hosted-model calls on invented
sample tasks; these message envelopes are newly constructed replay fixtures.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import platform
import statistics
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canopy.messaging import (
    AuthorizationContext, EvidenceReference, Mailbox, MessageBatch, MessageBatchCodec,
    MessageCodec, MessageEnvelope, MessageError, ProvenanceReference, ScopeMismatch,
)


DEFAULT_RECORDINGS = Path(__file__).parent / "recordings" / "advisor-live-2026-10-17"


@dataclass(frozen=True)
class RecordedBatch:
    source: str
    alias: str
    source_sha256: str
    messages: tuple[MessageEnvelope, ...]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8")


def load_recorded_batches(directory: Path) -> list[RecordedBatch]:
    batches = []
    for source in sorted(directory.glob("*/frontend-comparison.json")):
        raw = source.read_bytes()
        bundle = json.loads(raw)
        if bundle.get("format") != "canopy-comparison/v1":
            raise ValueError("expected a canopy-comparison/v1 recording")
        for alias, recording in bundle["recordings"].items():
            task = recording["task"]
            result = recording["result"]
            if task["metadata"].get("sample_data") is not True or result.get("synthetic") is not False:
                raise ValueError("demo requires actual hosted runs on marked sample data")
            events = recording["events"]
            if not events or len(events) > 128:
                raise ValueError("each recorded run must have 1..128 events")
            tenant = task["tenant_id"]
            refs = tuple(EvidenceReference(
                evidence_id=item["evidence_id"], tenant_id=item["tenant_id"],
                source=item["source"], version=item["version"],
            ) for item in task.get("evidence", []))
            messages = []
            for sequence, event in enumerate(events):
                if event["run_id"] != result["run_id"] or event["sequence"] != sequence:
                    raise ValueError("recording event identity or ordering mismatch")
                messages.append(MessageEnvelope(
                    message_type="event", message_id=f"{event['run_id']}:{sequence}",
                    tenant_id=tenant, workflow_id=task["workflow_id"],
                    stream_id=event["run_id"], sequence=sequence,
                    sender_id="sample-runtime-worker", recipient_id="sample-demo-orchestrator",
                    created_at=event["timestamp"],
                    authorization=AuthorizationContext(
                        actor_id="sample-event-exporter", policy_version="recorded-event-read-v1",
                        permissions=("recorded-events:read",),
                    ),
                    payload={"source_recording": source.parent.name, "event": event},
                    evidence_refs=refs,
                    provenance=(ProvenanceReference(
                        tenant_id=tenant, run_id=event["run_id"], step_id=event["step_id"],
                    ),),
                ))
            batches.append(RecordedBatch(source.parent.name, alias,
                                         hashlib.sha256(raw).hexdigest(), tuple(messages)))
    if not batches:
        raise ValueError("no retained frontend recordings found")
    if len({m.tenant_id for batch in batches for m in batch.messages}) != 1:
        raise ValueError("demo requires one sample tenant")
    return batches


def _timings(batches: list[RecordedBatch], repeats: int) -> dict[str, Any]:
    batch_codec = MessageBatchCodec()
    single_codec = MessageCodec()
    results = {}
    count = sum(len(batch.messages) for batch in batches)
    for encoding in ("json", "single_cbor", "cbor", "auto"):
        encodes, decodes = [], []
        for _ in range(repeats):
            started = time.perf_counter_ns()
            packets = [
                [single_codec.encode(m) for m in batch.messages] if encoding == "single_cbor"
                else batch_codec.encode(batch.messages, encoding=encoding)
                for batch in batches
            ]
            encoded = time.perf_counter_ns()
            for batch, packet in zip(batches, packets, strict=True):
                scope = dict(tenant_id=batch.messages[0].tenant_id,
                             recipient_id=batch.messages[0].recipient_id)
                if encoding == "single_cbor":
                    restored = tuple(single_codec.decode(wire, **scope) for wire in packet)
                else:
                    restored = batch_codec.decode(packet, **scope)
                if restored != batch.messages:
                    raise AssertionError("timed roundtrip changed messages")
            finished = time.perf_counter_ns()
            encodes.append((encoded - started) / 1_000_000)
            decodes.append((finished - encoded) / 1_000_000)
        results[encoding] = {
            "encode_total_ms_samples": encodes, "decode_total_ms_samples": decodes,
            "encode_median_ms": statistics.median(encodes),
            "decode_median_ms": statistics.median(decodes),
            "encode_median_us_per_message": statistics.median(encodes) * 1000 / count,
            "decode_median_us_per_message": statistics.median(decodes) * 1000 / count,
        }
    return results


def _mailbox_check(batches: list[RecordedBatch], packets: list[MessageBatch]) -> dict[str, Any]:
    codec = MessageBatchCodec()
    first = batches[0].messages[0]
    decoded = [message for packet in packets for message in codec.decode(
        packet, tenant_id=first.tenant_id, recipient_id=first.recipient_id
    )]
    reversed_packets = [codec.encode(tuple(reversed(batch.messages))) for batch in reversed(batches)]
    with tempfile.TemporaryDirectory(prefix="canopy-comms-demo-") as temporary:
        with Mailbox(Path(temporary) / "individual.sqlite", tenant_id=first.tenant_id,
                     recipient_id=first.recipient_id) as individual:
            started = time.perf_counter_ns()
            for message in reversed(decoded):
                if not individual.deliver(message).inserted:
                    raise AssertionError("unexpected duplicate source message")
            individual_ms = (time.perf_counter_ns() - started) / 1_000_000
        with Mailbox(Path(temporary) / "mailbox.sqlite", tenant_id=first.tenant_id,
                     recipient_id=first.recipient_id) as mailbox:
            # Reverse each stream deliberately: the mailbox must expose only
            # the next sequence, then advance on acknowledgement.
            started = time.perf_counter_ns()
            for packet in reversed_packets:
                if not all(receipt.inserted for receipt in mailbox.deliver_batch(packet)):
                    raise AssertionError("unexpected duplicate source message")
            batch_ms = (time.perf_counter_ns() - started) / 1_000_000
            acknowledged = 0
            while ready := mailbox.pending():
                for message in ready:
                    if not mailbox.acknowledge(
                        message.message_id, sender_id=message.sender_id,
                        workflow_id=message.workflow_id, stream_id=message.stream_id,
                    ):
                        raise AssertionError("mailbox did not acknowledge the next message")
                    acknowledged += 1
            if acknowledged != len(decoded):
                raise AssertionError("mailbox left an ordering gap")
            duplicates = sum(not mailbox.deliver(batch.messages[0]).inserted for batch in batches)
    return {"delivered": len(decoded), "acknowledged": acknowledged,
            "duplicate_redeliveries_suppressed": duplicates, "reverse_delivery_verified": True,
            "individual_delivery_ms": individual_ms, "batch_delivery_ms": batch_ms,
            "individual_transactions": len(decoded), "batch_transactions": len(batches),
            "timing_scope": "One local sample per path on separate fresh SQLite databases; "
                            "FULL synchronous WAL, validation and commits included. "
                            "Frame construction, database creation and acknowledgements excluded. "
                            "Not a production throughput or network-latency benchmark."}


def _scenarios(batch: RecordedBatch) -> list[dict[str, Any]]:
    codec = MessageBatchCodec()
    first = batch.messages[0]
    dense = first.model_copy(update={"payload": {"sample_data": True, "readings": [1.0] * 256}})
    cbor = codec.encode([dense], encoding="cbor")
    compact = codec.encode([dense], encoding="json")
    selected = codec.encode([dense])
    if selected != compact or len(compact.data) >= len(cbor.data):
        raise AssertionError("dense floats must demonstrate actual-size fallback")
    refreshed = tuple(message.model_copy(update={
        "authorization": AuthorizationContext(
            actor_id="sample-new-actor", policy_version="recorded-event-read-v2",
            permissions=("recorded-events:read",),
        )
    }) for message in batch.messages)
    new_packet = codec.encode(refreshed)
    if MessageBatchCodec().decode(new_packet, tenant_id=first.tenant_id,
                                  recipient_id=first.recipient_id) != refreshed:
        raise AssertionError("new receiver could not decode refreshed context")
    try:
        codec.decode(new_packet, tenant_id="different-tenant")
    except ScopeMismatch:
        wrong_scope_rejected = True
    else:
        raise AssertionError("wrong tenant accepted")
    try:
        codec.decode(MessageBatch(new_packet.content_type, new_packet.data[:-1]),
                     tenant_id=first.tenant_id)
    except MessageError:
        truncated_rejected = True
    else:
        raise AssertionError("truncated frame accepted")
    return [
        {"name": "Dense float fallback", "sample_fixture": True,
         "json_bytes": len(compact.data), "cbor_bytes": len(cbor.data),
         "selected_bytes": len(selected.data), "selected_encoding": "json",
         "detail": "Float64 is preserved exactly; compact JSON is smaller for this payload."},
        {"name": "Policy context refresh", "sample_fixture": True,
         "verified": True, "frame_bytes": len(new_packet.data),
         "detail": "A fresh receiver decodes policy v2 and new actor without receiving v1."},
        {"name": "Wrong tenant / truncated frame", "sample_fixture": True,
         "verified": wrong_scope_rejected and truncated_rejected,
         "detail": "Both malformed or wrong-scope deliveries are rejected before mailbox insertion."},
    ]


def run_demo(recordings: Path = DEFAULT_RECORDINGS, *, repeats: int = 5) -> tuple[dict, dict]:
    if type(repeats) is not int or not 1 <= repeats <= 100:
        raise ValueError("repeats must be an integer from 1 to 100")
    batches = load_recorded_batches(recordings)
    codec = MessageBatchCodec()
    single = MessageCodec()
    frames, wires, packets = [], [], []
    for batch in batches:
        selected = codec.encode(batch.messages)
        compact = codec.encode(batch.messages, encoding="json")
        cbor = codec.encode(batch.messages, encoding="cbor")
        packets.append(selected)
        first = batch.messages[0]
        restored = codec.decode(selected, tenant_id=first.tenant_id, recipient_id=first.recipient_id)
        if any(single.json_bytes(expected) != single.json_bytes(actual)
               for expected, actual in zip(batch.messages, restored, strict=True)):
            raise AssertionError("lossless envelope roundtrip failed")
        frame_id = f"{batch.source}/{batch.alias}"
        event_hash = hashlib.sha256(_json_bytes([
            message.payload["event"] for message in restored
        ])).hexdigest()
        frames.append({
            "id": frame_id, "source": batch.source, "alias": batch.alias,
            "messages": len(batch.messages), "json_bytes": len(compact.data),
            "single_cbor_bytes": sum(len(single.encode(m)) for m in batch.messages),
            "batch_cbor_bytes": len(cbor.data), "selected_bytes": len(selected.data),
            "selected_encoding": "cbor" if "cbor" in selected.content_type else "json",
            "base64_bytes": len(base64.b64encode(selected.data)),
            "bytes_saved": len(compact.data) - len(selected.data),
            "reduction_fraction": 1 - len(selected.data) / len(compact.data),
            "lossless_verified": True, "event_payload_sha256": event_hash,
            "first_message": json.loads(single.json_bytes(first)),
        })
        wires.append({"id": frame_id, "content_type": selected.content_type,
                      "sha256": hashlib.sha256(selected.data).hexdigest(),
                      "data_base64": base64.b64encode(selected.data).decode("ascii")})
    totals = {key: sum(frame[key] for frame in frames) for key in (
        "messages", "json_bytes", "single_cbor_bytes", "batch_cbor_bytes", "selected_bytes",
        "base64_bytes", "bytes_saved",
    )}
    totals.update({"batches": len(frames), "source_bundles": len({b.source for b in batches}),
                   "reduction_fraction": totals["bytes_saved"] / totals["json_bytes"],
                   "roundtrip_verified": True, "network_calls": 0})
    report = {
        "format": "canopy-messaging-demo/v1", "created_at": datetime.now(UTC).isoformat(),
        "description": "Retained actual hosted-model runtime events on sample tasks, wrapped in "
                       "new local inter-agent replay envelopes. Not production network captures.",
        "measurement": {
            "cbor_ld_ex": importlib.metadata.version("cbor-ld-ex"),
            "cbor2": importlib.metadata.version("cbor2"), "python": platform.python_version(),
            "platform": platform.system(), "timing_repetitions": repeats,
            "baseline": "Compact UTF-8 JSON array of exactly the same full envelopes.",
            "timing_scope": "Local validated encode/decode; excludes source I/O, SQLite, network "
                            "and batch accumulation time. Median of repeats; machine-dependent.",
            "wire_scope": "Payload bytes including shared context and annotations; excludes "
                          "transport headers. Content type is required for either format.",
            "limitations": [
                "Binary wire savings are not LLM token, cost, latency or energy savings.",
                "Batching can delay first-event delivery; choose flush thresholds for the workload.",
                "Base64 adds bytes: the export is inspectable JSON, not the measured binary wire.",
                "Each batch is independently decodable; no hidden context setup is excluded.",
                "Batch codec and mailbox are opt-in primitives, not automatic runtime networking.",
            ],
        },
        "totals": totals, "frames": frames,
        "sources": [{"path": f"{name}/frontend-comparison.json", "sha256": digest}
                    for name, digest in sorted({(b.source, b.source_sha256) for b in batches})],
        "scenarios": _scenarios(batches[0]), "timings": _timings(batches, repeats),
        "mailbox": _mailbox_check(batches, packets),
    }
    return report, {"format": "canopy-message-frames/v1", "frames": wires}


def render_html(report: dict) -> str:
    serialized = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    return _HTML.replace("__REPORT__", serialized)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit("output directory must be new")
    report, wires = run_demo(args.recordings, repeats=args.repeats)
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)
                                               + "\n", encoding="utf-8", newline="\n")
    (args.output_dir / "frames.json").write_text(json.dumps(wires, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
    (args.output_dir / "index.html").write_text(render_html(report), encoding="utf-8", newline="\n")
    inventory = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "bytes": path.stat().st_size} for path in sorted(args.output_dir.iterdir())]
    (args.output_dir / "manifest.json").write_text(json.dumps({
        "format": "canopy-messaging-demo-manifest/v1", "files": inventory,
        "source_recordings": report["sources"],
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output_dir), **report["totals"]}, indent=2))


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canopy · Efficient agent messages</title>
<style>
:root{color-scheme:dark;--bg:#091a1a;--card:#132a29;--line:#2d4541;--ink:#ecf5ee;--muted:#afc7bf;--green:#a0efb7;--gold:#ead08b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:42px 28px 70px}.eyebrow{letter-spacing:.16em;text-transform:uppercase;font-size:12px;color:var(--green)}h1{font-size:clamp(32px,5vw,52px);line-height:1.1;max-width:850px;margin:15px 0}h2{font-size:23px;margin:0 0 15px}h3{font-size:16px;margin:0 0 6px}p{color:var(--muted);max-width:950px}a{color:var(--green)}.intro{font-size:18px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0}.card,section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px}.metric{font-size:36px;font-weight:700;line-height:1.1;color:var(--green)}.label{font-size:13px;color:var(--muted);margin-top:10px}.small{font-size:13px;color:var(--muted)}section{margin-top:18px}.barrow{display:grid;grid-template-columns:180px 1fr 100px;gap:14px;align-items:center;margin:15px 0}.track{height:18px;background:#223c37;border-radius:4px}.bar{height:100%;border-radius:4px;background:var(--green)}.barrow:first-child .bar{background:#81928b}.barrow:nth-child(2) .bar{background:var(--gold)}.number{text-align:right;font-variant-numeric:tabular-nums}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:12px 10px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:500}.scroll{overflow:auto}select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--ink);font:inherit;border-radius:8px;padding:12px}pre{max-height:330px;overflow:auto;font:12px/1.6 ui-monospace,monospace;background:var(--bg);padding:18px;border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;color:var(--green);margin:12px 0}.case{border-left:3px solid var(--green);padding:2px 0 2px 15px;margin:18px 0}.case p{margin:4px 0}.badge{display:inline-block;padding:3px 8px;border:1px solid var(--line);border-radius:30px;color:var(--green);font-size:12px}footer{margin-top:28px;color:var(--muted);font-size:13px}@media(max-width:700px){main{padding:28px 16px}.grid{grid-template-columns:1fr}.barrow{grid-template-columns:115px 1fr 75px;font-size:12px}section{padding:16px}.metric{font-size:30px}}
</style></head><body><main>
<div class="eyebrow">Canopy / cbor-ld-ex / offline transport demo</div>
<p class="small"><a href="../index.html">← Back to demo hub</a></p>
<h1>Less repetition.<br>Every message preserved.</h1>
<p class="intro">Shared context removes repeated envelope fields from each batch. The actual cbor-ld-ex codec encodes the remainder; compact JSON wins automatically when it is smaller.</p>
<span class="badge">No model or network calls during playback</span>
<div class="grid"><div class="card"><div class="metric" id="saving"></div><div class="label">fewer payload bytes than compact JSON</div></div><div class="card"><div class="metric" id="events"></div><div class="label">recorded events, losslessly roundtripped</div></div><div class="card"><div class="metric" id="batches"></div><div class="label">self-contained frames; no prior context required</div></div></div>
<section><h2>Same information, measured bytes</h2><div id="bars"></div><p class="small">Includes each frame’s complete shared context and cbor-ld-ex annotation. Raw payload bytes exclude transport headers. These are local replay envelopes built from retained hosted-model events on sample tasks, not production network captures.</p><p class="small" id="base64"></p></section>
<section><h2>Inspect a recorded run</h2><label for="frame" class="small">Choose provider, category and execution</label><select id="frame"></select><div id="frame-summary"></div><details><summary>Inspect the first complete message</summary><pre id="message"></pre></details><p class="small">The full event payload, evidence references, identity, authorization, provenance, sequence and timestamp survive decoding. <a href="frames.json">Download actual wire frames (base64)</a>.</p></section>
<section><h2>Efficiency without silent loss</h2><div id="cases"></div><p class="small" id="mailbox"></p><p class="small" id="delivery-time"></p></section>
<section><h2>Local CPU cost is measured separately</h2><div class="scroll"><table><thead><tr><th>Profile</th><th>Encode median</th><th>Decode median</th><th>Encode / message</th></tr></thead><tbody id="timings"></tbody></table></div><p class="small" id="timing-note"></p></section>
<footer><p>Batching trades first-event immediacy for smaller frames. Use a bounded size or time flush in a real transport. These opt-in codec and local mailbox primitives do not automatically change runtime networking. Bytes do not establish LLM token, cost, energy or network-latency savings.</p><a href="report.json">Full measured report</a> · <a href="../../../docs/messaging.md">Protocol and integration guide</a></footer>
</main><script type="application/json" id="report">__REPORT__</script><script>
const r=JSON.parse(document.getElementById('report').textContent),t=r.totals;const fmt=n=>n.toLocaleString('en-US'),put=(id,s)=>document.getElementById(id).textContent=s;
put('saving',(t.reduction_fraction*100).toFixed(1)+'%');put('events',fmt(t.messages));put('batches',fmt(t.batches));
const bars=document.getElementById('bars');for(const [label,key]of[['Compact JSON','json_bytes'],['One CBOR / message','single_cbor_bytes'],['Shared-context CBOR','batch_cbor_bytes'],['Adaptive selected','selected_bytes']]){const row=document.createElement('div');row.className='barrow';const title=document.createElement('span');title.textContent=label;const track=document.createElement('div');track.className='track';const bar=document.createElement('div');bar.className='bar';bar.style.width=(t[key]/t.json_bytes*100)+'%';track.append(bar);const n=document.createElement('span');n.className='number';n.textContent=fmt(t[key])+' B';row.append(title,track,n);bars.append(row)}
put('base64','The portable base64 payloads total '+fmt(t.base64_bytes)+' bytes before JSON wrapper overhead; '+fmt(t.selected_bytes)+' is the binary wire total.');
const selector=document.getElementById('frame');r.frames.forEach((f,i)=>{const o=document.createElement('option');o.value=i;o.textContent=f.id;selector.append(o)});function show(){const f=r.frames[Number(selector.value)];put('frame-summary',f.messages+' messages · '+fmt(f.json_bytes)+' B JSON → '+fmt(f.selected_bytes)+' B '+f.selected_encoding.toUpperCase()+' · '+(f.reduction_fraction*100).toFixed(1)+'% smaller · exact roundtrip verified');put('message',JSON.stringify(f.first_message,null,2))}selector.addEventListener('change',show);show();
for(const c of r.scenarios){const el=document.createElement('div');el.className='case';const h=document.createElement('h3');h.textContent=c.name+(c.verified?' · verified':'');const p=document.createElement('p');p.textContent=c.detail;el.append(h,p);if(c.json_bytes){const n=document.createElement('p');n.className='small';n.textContent=fmt(c.cbor_bytes)+' B CBOR vs '+fmt(c.json_bytes)+' B JSON → '+c.selected_encoding.toUpperCase()+' selected';el.append(n)}document.getElementById('cases').append(el)}
put('mailbox',fmt(r.mailbox.delivered)+' messages delivered in reverse order to the real SQLite mailbox, '+fmt(r.mailbox.acknowledged)+' acknowledged in stream order, and '+r.mailbox.duplicate_redeliveries_suppressed+' duplicate redeliveries suppressed.');
put('delivery-time','Local delivery sample: '+r.mailbox.individual_transactions+' individual transactions took '+r.mailbox.individual_delivery_ms.toFixed(1)+' ms; '+r.mailbox.batch_transactions+' atomic batch transactions took '+r.mailbox.batch_delivery_ms.toFixed(1)+' ms. Includes validation and SQLite FULL/WAL commits; excludes frame construction and acknowledgements. One sample per path, not a production benchmark.');
for(const [key,label]of[['json','Compact JSON'],['single_cbor','One CBOR / message'],['cbor','Shared-context CBOR'],['auto','Adaptive selection']]){const v=r.timings[key],tr=document.createElement('tr');for(const value of [label,v.encode_median_ms.toFixed(2)+' ms',v.decode_median_ms.toFixed(2)+' ms',v.encode_median_us_per_message.toFixed(1)+' µs']){const td=document.createElement('td');td.textContent=value;tr.append(td)}document.getElementById('timings').append(tr)}put('timing-note','Whole-corpus medians over '+r.measurement.timing_repetitions+' repetitions on '+r.measurement.platform+', Python '+r.measurement.python+', cbor-ld-ex '+r.measurement.cbor_ld_ex+'. Includes validation; excludes source I/O, mailbox writes, network and time waiting for a batch to fill.');
</script></body></html>'''


if __name__ == "__main__":
    main()
