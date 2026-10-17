"""Explicitly download, or offline-verify, the reviewed OpenJEV checkpoint.

No model is imported or executed. SHA256 reads are streamed, including the 9 GB
weights file. The inference containers mount this Hugging Face cache read-only.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from time import perf_counter

REPOSITORY = "AlexWortega/openjev"
REVISION = "552759daad712f1af6c4c13dabcb1e047886fc9c"
CHECKPOINT = "qwen3.5-4b-nli-v2"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MANIFEST = (REPO_ROOT / "core/examples/recordings/jev-evaluation-2026-10-17"
                  / "model-manifest.json")
EXPECTED_FILES = {"modeling_openjev.py", *(f"{CHECKPOINT}/{name}" for name in (
    "config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors",
))}


def file_sha256(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read_manifest(path: Path = MODEL_MANIFEST) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("repository") != REPOSITORY or value.get("revision") != REVISION
            or value.get("checkpoint") != CHECKPOINT or set(value.get("files", {})) != EXPECTED_FILES):
        raise ValueError("Manifest must describe the five reviewed files at the pinned revision")
    for name, entry in value["files"].items():
        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
            raise ValueError("Model manifest contains an invalid relative path")
        digest = entry.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or type(entry.get("bytes")) is not int or entry["bytes"] <= 0):
            raise ValueError("Model manifest must contain SHA256 and positive byte counts")
    return value


def snapshot_path(cache_dir: Path) -> Path:
    return cache_dir / "models--AlexWortega--openjev" / "snapshots" / REVISION


def verify_cache(cache_dir: Path, *, manifest_path: Path = MODEL_MANIFEST) -> dict:
    """Verify the exact cached executable helper and model files without network access."""
    manifest = read_manifest(manifest_path)
    snapshot = snapshot_path(cache_dir)
    files = {}
    for name, expected in manifest["files"].items():
        path = snapshot / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing cached model file: {name}; run model download first")
        if path.stat().st_size != expected["bytes"] or file_sha256(path) != expected["sha256"]:
            raise ValueError(f"Cached model file failed pinned size/SHA256 verification: {name}")
        files[name] = expected
    return {
        "repository": REPOSITORY, "revision": REVISION, "checkpoint": CHECKPOINT,
        "snapshot": str(snapshot), "manifest_sha256": file_sha256(manifest_path),
        "files": files, "verified_bytes": sum(item["bytes"] for item in files.values()),
    }


def prepare(cache_dir: Path, *, verify_only: bool = False) -> dict:
    started = perf_counter()
    manifest = read_manifest()
    if not verify_only:
        from huggingface_hub import snapshot_download

        cache_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=REPOSITORY, revision=REVISION, cache_dir=str(cache_dir),
            allow_patterns=list(manifest["files"]), token=False, max_workers=2,
        )
    result = verify_cache(cache_dir)
    return {
        "format": "canopy-openjev-model-preparation/v1", "ok": True,
        "generated_at": datetime.now(UTC).isoformat(), "download_requested": not verify_only,
        **result, "elapsed_ms": (perf_counter() - started) * 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("/models"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional new receipt file")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new receipt filename")
    result = prepare(args.cache_dir, verify_only=args.verify_only)
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
