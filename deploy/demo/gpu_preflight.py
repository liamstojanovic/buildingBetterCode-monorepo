"""Check WSL2 CUDA access, then fit all seven complete OpenJEV demo inputs.

Run with the isolated JEV Python. This is an offline capacity check of historical
text, not a new quality benchmark or authorization to execute any selected action.
The separate comparison command applies the current mapping and decision gates.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from time import perf_counter

from prepare_openjev import REPO_ROOT, file_sha256, verify_cache

MIB = 1024 * 1024
RETAINED = REPO_ROOT / "core/examples/recordings/jev-evaluation-2026-10-17"
VERSIONS = {
    "pollard-jev": "0.13", "pollard": "1.6.0", "pydantic": "2.14.1",
    "torch": "2.11.0+cu128", "transformers": "5.15.0", "huggingface-hub": "1.33.0",
    "tokenizers": "0.22.2", "numpy": "2.5.3", "safetensors": "0.8.0",
}


def retained_inputs(directory: Path = RETAINED) -> list[dict]:
    """Read only the previously published mapped inputs, not expected labels."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    path = directory / "comparison.json"
    if (manifest.get("format") != "canopy-jev-demo/v1"
            or manifest.get("files", {}).get("comparison.json") != file_sha256(path)):
        raise ValueError("Retained JEV comparison failed its manifest SHA256 check")
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = report["cases"]
    categories = Counter(case["category"] for case in cases)
    if (categories != {"business_decision": 1, "tool_selection": 3, "next_step": 3}
            or len({case["case_id"] for case in cases}) != 7):
        raise ValueError("Capacity check requires all seven retained cases across three categories")
    return [{"case_id": case["case_id"], "category": case["category"],
             "premise": case["text_request"]["premise"],
             "choices": case["text_request"]["choices"]} for case in cases]


def cuda_memory(torch) -> dict:
    free, total = torch.cuda.mem_get_info()
    return {
        "free_mib": round(free / MIB, 2), "total_mib": round(total / MIB, 2),
        "allocated_mib": round(torch.cuda.memory_allocated() / MIB, 2),
        "reserved_mib": round(torch.cuda.memory_reserved() / MIB, 2),
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / MIB, 2),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / MIB, 2),
    }


def check(args: argparse.Namespace, report: dict) -> None:
    import torch

    report["stage"] = "cuda_access"
    report["packages"] = {name: metadata.version(name) for name in VERSIONS}
    if report["packages"] != VERSIONS:
        raise RuntimeError("Use the pinned JEV container; runtime package versions differ")
    report["cuda_runtime"] = torch.version.cuda
    report["cuda_available"] = torch.cuda.is_available()
    if not report["cuda_available"]:
        raise RuntimeError("CUDA is unavailable; check Docker Desktop WSL2 integration and GPU access")
    torch.cuda.set_device(0)
    report["gpu_name"] = torch.cuda.get_device_name(0)
    report["compute_capability"] = list(torch.cuda.get_device_capability(0))
    report["bf16_supported"] = torch.cuda.is_bf16_supported()
    if not report["bf16_supported"]:
        raise RuntimeError("This reviewed checkpoint requires a BF16-capable CUDA GPU")
    report["memory_before_model"] = cuda_memory(torch)
    if args.device_only:
        report.update(ok=True, stage="device_check_complete", actual_model_calls=0)
        return

    report["stage"] = "verify_model_cache"
    report["model"] = verify_cache(args.cache_dir)
    cases = retained_inputs()
    report["max_context_tokens"] = args.max_context_tokens
    # This bounds PyTorch's caching allocator, not total driver/process memory.
    # On the teammate's 12,282 MiB GPU the default leaves about 1 GiB outside it.
    total_bytes = torch.cuda.get_device_properties(0).total_memory
    allocator_bytes = min(args.max_allocator_mib * MIB, int(total_bytes * 0.95))
    torch.cuda.set_per_process_memory_fraction(allocator_bytes / total_bytes)
    torch.cuda.reset_peak_memory_stats()
    report["allocator_limit_mib"] = round(allocator_bytes / MIB, 2)
    report["memory_measurement_note"] = (
        "The limit and peaks cover the PyTorch caching allocator. CUDA context, driver and "
        "other applications also use memory. Passing proves these seven complete inputs "
        "fit this run; it does not guarantee capacity for larger workloads."
    )
    report["stage"] = "load_checkpoint"
    from pollard_jev.providers.context import ContextCheckedEncoder
    from pollard_jev.providers.openjev import OpenJevProvider, _validated_rows

    started = perf_counter()
    provider = OpenJevProvider.from_local_cache(
        cache_dir=args.cache_dir, device="cuda", max_length=args.max_context_tokens,
        representation="json-v1",
    )
    torch.cuda.synchronize()
    report["model_load_ms"] = (perf_counter() - started) * 1000
    report["model_identity"] = provider.identity.model_dump(mode="json")
    guard = ContextCheckedEncoder(provider._encoder)
    report["stage"] = "run_complete_demo_inputs"
    report["cases"] = []
    for case in cases:
        started = perf_counter()
        rows = _validated_rows(guard.predict_hypotheses(
            case["premise"], [choice["hypothesis"] for choice in case["choices"]],
        ), len(case["choices"]))
        torch.cuda.synchronize()
        scores = {choice["name"]: row.entailment
                  for choice, row in zip(case["choices"], rows, strict=True)}
        report["cases"].append({
            "case_id": case["case_id"], "category": case["category"],
            "hypothesis_count": len(rows), "raw_choice": max(scores, key=scores.get),
            "entailment_scores": scores, "measurements": guard.measurements[-1],
            "inference_ms": (perf_counter() - started) * 1000,
        })
        print(f"Capacity check {len(report['cases'])}/7: {case['case_id']}", file=sys.stderr, flush=True)
    report["memory_after_model_and_cases"] = cuda_memory(torch)
    report["actual_model_calls"] = guard.forward_calls
    report["hypothesis_pairs"] = sum(item["hypothesis_count"] for item in report["cases"])
    report["maximum_observed_pair_tokens"] = max(
        max(item["measurements"]["tokenized_pair_lengths"]) for item in report["cases"]
    )
    report.update(ok=True, stage="all_seven_cases_fit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("/models"))
    parser.add_argument("--device-only", action="store_true", help="Check CUDA before downloading weights")
    parser.add_argument("--max-context-tokens", type=int, default=1024)
    parser.add_argument("--max-allocator-mib", type=int, default=11264)
    parser.add_argument("--output", type=Path, help="New JSON file; failures are recorded too")
    args = parser.parse_args()
    if args.max_context_tokens < 1 or args.max_allocator_mib < 1:
        parser.error("context and allocator limits must be positive")
    if args.output and args.output.exists():
        parser.error("output already exists; use a fresh run directory")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                 "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_PROGRESS_BARS"):
        # Before importing Transformers or Hugging Face. Compose additionally
        # disables networking, so a model dependency cannot silently download.
        os.environ[name] = "1"
    report = {
        "format": "canopy-openjev-gpu-preflight/v1", "ok": False,
        "generated_at": datetime.now(UTC).isoformat(), "python": platform.python_version(),
        "platform": platform.platform(), "stage": "import_runtime",
        "mode": "device_only" if args.device_only else "capacity_check_historical_text_only",
        "quality_benchmark": False, "actions_executed": False,
    }
    started = perf_counter()
    try:
        check(args, report)
    except Exception as exc:  # noqa: BLE001 -- Persist runtime/OOM diagnostics, then exit nonzero.
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    report["elapsed_ms"] = (perf_counter() - started) * 1000
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    print(payload, end="")
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
