"""Generate fresh frontend tasks/recordings and publish them to the shared volume.

Run this one-shot service before web/API startup and again after an hour. The
existing exporter uses temporary SQLite stores and deterministic handlers; it
never calls a hosted model. Its image-local output is copied atomically so the
frontend can refresh fixtures without rebuilding its static assets.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_NAMES = ("core-synthetic.json", "core-task.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/demo-fixtures"))
    args = parser.parse_args()
    subprocess.run(
        [sys.executable, str(ROOT / "web/scripts/export-core-fixtures.py"), "--benchmark"],
        cwd=ROOT,
        check=True,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_NAMES:
        destination = args.output_dir / name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(ROOT / "web/public/recordings" / name, temporary)
        temporary.replace(destination)
    print(f"Published fresh frontend fixtures to {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
