"""Generate the bundled advisor report with core code; write only under web/.

Use a Python environment containing the core's pinned dependencies. All run
databases and the core demo manifest are created in a temporary directory.
"""

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "core" / "src"))


def main():
    demo = runpy.run_path(str(ROOT / "core" / "examples" / "advisor_demo.py"))
    with TemporaryDirectory(prefix="canopy-web-advisor-") as temporary:
        manifest = demo["run_demo"](Path(temporary))
        contents = Path(manifest["advisor_report_path"]).read_bytes()
    report = json.loads(contents)
    output = ROOT / "web" / "public" / "recordings"
    output.mkdir(parents=True, exist_ok=True)
    (output / "advisor-demo.json").write_bytes(contents)
    provenance = {
        "source_script": "core/examples/advisor_demo.py",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "generated_at": report["generated_at"],
        "report_sha256": hashlib.sha256(contents).hexdigest(),
        "synthetic": True,
        "description": "Actual offline analysis of seven groups of synthetic Python handler recordings. "
        "No hosted model or OpenJEV inference, quality evaluation, or savings estimate.",
    }
    (output / "advisor-demo.provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Wrote {output / 'advisor-demo.json'} ({len(report['recommendations'])} groups)")


if __name__ == "__main__":
    main()
