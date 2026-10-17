"""Offline integrity and failure-reporting checks; no model downloads or CUDA required."""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "deploy/demo"


@pytest.fixture
def helpers(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    modules = []
    for name in ("prepare_openjev", "gpu_preflight"):
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, name, module)
        modules.append(module)
    return modules


@pytest.fixture
def sample_cache(tmp_path, helpers):
    prepare, _ = helpers
    cache = tmp_path / "cache"
    snapshot = prepare.snapshot_path(cache)
    manifest = {"repository": prepare.REPOSITORY, "revision": prepare.REVISION,
                "checkpoint": prepare.CHECKPOINT, "files": {}}
    for name in prepare.EXPECTED_FILES:
        payload = f"unit-test-only:{name}".encode()
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        manifest["files"][name] = {"sha256": sha256(payload).hexdigest(), "bytes": len(payload)}
    manifest_path = tmp_path / "model-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return cache, snapshot, manifest_path


def test_offline_verification_checks_every_file(sample_cache, helpers):
    prepare, _ = helpers
    cache, _, manifest_path = sample_cache
    result = prepare.verify_cache(cache, manifest_path=manifest_path)
    assert set(result["files"]) == prepare.EXPECTED_FILES
    assert result["verified_bytes"] == sum(item["bytes"] for item in result["files"].values())
    assert result["manifest_sha256"] == prepare.file_sha256(manifest_path)


@pytest.mark.parametrize("name", ["modeling_openjev.py", "qwen3.5-4b-nli-v2/model.safetensors"])
def test_tampered_helper_or_weights_fail_before_loading(sample_cache, helpers, name):
    prepare, _ = helpers
    cache, snapshot, manifest_path = sample_cache
    path = snapshot / name
    # Preserve byte count so this exercises the digest, not only the size check.
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(ValueError, match="failed pinned size/SHA256 verification"):
        prepare.verify_cache(cache, manifest_path=manifest_path)


def test_partial_cache_fails_with_download_instruction(sample_cache, helpers):
    prepare, _ = helpers
    cache, snapshot, manifest_path = sample_cache
    (snapshot / "qwen3.5-4b-nli-v2/model.safetensors").unlink()
    with pytest.raises(FileNotFoundError, match="run model download first"):
        prepare.verify_cache(cache, manifest_path=manifest_path)


@pytest.mark.parametrize("field,value", [("repository", "other/model"), ("revision", "main")])
def test_manifest_cannot_switch_to_unreviewed_model(sample_cache, helpers, field, value):
    prepare, _ = helpers
    _, _, manifest_path = sample_cache
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="five reviewed files"):
        prepare.read_manifest(manifest_path)


def test_capacity_inputs_keep_all_candidates_without_expectations(helpers):
    _, gpu = helpers
    cases = gpu.retained_inputs()
    assert len(cases) == 7
    assert sum(len(case["choices"]) for case in cases) == 26
    assert all(set(case) == {"case_id", "category", "premise", "choices"} for case in cases)
    assert all("expected_choice" not in case for case in cases)


def test_capacity_inputs_reject_modified_retained_report(tmp_path, helpers):
    _, gpu = helpers
    (tmp_path / "manifest.json").write_bytes((gpu.RETAINED / "manifest.json").read_bytes())
    (tmp_path / "comparison.json").write_bytes((gpu.RETAINED / "comparison.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifest SHA256"):
        gpu.retained_inputs(tmp_path)


def test_runtime_failure_persists_diagnostics_and_exits_nonzero(tmp_path, helpers, monkeypatch):
    _, gpu = helpers
    output = tmp_path / "failed-preflight.json"

    def failure(args, report):
        report["stage"] = "load_checkpoint"
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(gpu, "check", failure)
    monkeypatch.setattr(sys, "argv", ["gpu_preflight.py", "--output", str(output)])
    with pytest.raises(SystemExit) as exc:
        gpu.main()
    assert exc.value.code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["stage"] == "load_checkpoint"
    assert report["error"]["message"] == "CUDA out of memory"


def test_existing_receipt_is_never_overwritten(tmp_path, helpers, monkeypatch):
    _, gpu = helpers
    output = tmp_path / "preflight.json"
    output.write_text("previous run", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["gpu_preflight.py", "--output", str(output)])
    with pytest.raises(SystemExit) as exc:
        gpu.main()
    assert exc.value.code == 2
    assert output.read_text(encoding="utf-8") == "previous run"
