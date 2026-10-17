"""Demo integration uses retained records only; it cannot contact model providers."""

import base64
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from canopy.messaging import MessageBatch, MessageBatchCodec


@pytest.fixture
def demo():
    path = Path(__file__).parents[1] / "examples" / "messaging_demo.py"
    spec = importlib.util.spec_from_file_location("canopy_messaging_demo_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recordings(demo, tmp_path):
    source = demo.DEFAULT_RECORDINGS / "openai-next-step" / "frontend-comparison.json"
    directory = tmp_path / "recordings" / "openai-next-step"
    directory.mkdir(parents=True)
    shutil.copyfile(source, directory / source.name)
    return directory.parent


def test_demo_preserves_actual_events_and_exports_decodable_frames(demo, recordings):
    report, wire_export = demo.run_demo(recordings, repeats=1)
    original = demo.load_recorded_batches(recordings)
    assert report["totals"]["batches"] == 4
    assert report["totals"]["messages"] == sum(len(b.messages) for b in original)
    assert report["totals"]["selected_bytes"] < report["totals"]["json_bytes"]
    assert report["mailbox"]["individual_transactions"] == report["totals"]["messages"]
    assert report["mailbox"]["batch_transactions"] == 4
    assert report["mailbox"]["acknowledged"] == report["totals"]["messages"]
    assert report["mailbox"]["duplicate_redeliveries_suppressed"] == 4
    assert report["scenarios"][0]["selected_encoding"] == "json"
    for source, frame in zip(original, wire_export["frames"], strict=True):
        decoded = MessageBatchCodec().decode(
            MessageBatch(frame["content_type"], base64.b64decode(frame["data_base64"])),
            tenant_id="sample-tenant", recipient_id="sample-demo-orchestrator",
        )
        assert decoded == source.messages
    assert report["totals"]["network_calls"] == 0


def test_demo_rejects_missing_recordings_and_synthetic_results(demo, recordings, tmp_path):
    with pytest.raises(ValueError, match="no retained"):
        demo.load_recorded_batches(tmp_path / "missing")
    file = recordings / "openai-next-step" / "frontend-comparison.json"
    data = json.loads(file.read_text(encoding="utf-8"))
    data["recordings"]["baseline_cold"]["result"]["synthetic"] = True
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="actual hosted"):
        demo.load_recorded_batches(recordings)


def test_demo_rejects_misbound_or_out_of_order_events(demo, recordings):
    file = recordings / "openai-next-step" / "frontend-comparison.json"
    data = json.loads(file.read_text(encoding="utf-8"))
    data["recordings"]["baseline_cold"]["events"][0]["run_id"] = "different-run"
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="identity or ordering"):
        demo.load_recorded_batches(recordings)


def test_html_embeds_report_without_allowing_markup_execution(demo):
    html = demo.render_html({"value": "</script><script>alert('wrong')</script>"})
    assert "</script><script>alert" not in html
    assert "\\u003c/script>" in html
    assert "https://" not in html
    assert "fetch(" not in html
