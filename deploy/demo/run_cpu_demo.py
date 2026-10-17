"""Rerun the offline advisor, governance smoke checks and communication demo.

All inputs are committed sample recordings. No model or network call is made.
Each run requires a new directory so previous measurements remain reviewable.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDINGS = ROOT / "core/examples/recordings/advisor-live-2026-10-17"


def run_python(*arguments: str, output: Path | None = None) -> None:
    command = [sys.executable, *arguments]
    if output is None:
        subprocess.run(command, cwd=ROOT, check=True)
    else:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            subprocess.run(command, cwd=ROOT, stdout=stream, check=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    run_python("core/examples/synthetic_demo.py", output=directory / "synthetic.json")
    run_python("core/scripts/component_smoke.py", output=directory / "components.json")
    advisor = directory / "advisor"
    advisor.mkdir()
    rows = []
    totals = {"reports": 0, "recommendations": 0, "model_calls": 0}
    for provider in ("openai", "anthropic", "gemini"):
        for category in ("business-decision", "tool-selection", "next-step"):
            label = f"{provider}-{category}"
            path = advisor / f"{label}.json"
            run_python(
                "-m", "canopy.advisor_cli", "--report", str(RECORDINGS / label / "comparison.json"),
                "--tenant-id", "sample-tenant", "--output", str(path),
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            recommendations = report["recommendations"]
            totals["reports"] += 1
            totals["recommendations"] += len(recommendations)
            totals["model_calls"] += report["analysis"]["model_calls"]
            findings = "; ".join(
                f"{item['step_id']}: {item['finding']['status']} / {item['finding']['strategy']}"
                for item in recommendations
            )
            rows.append(
                f'<tr><td><a href="advisor/{label}.json">{label}</a></td>'
                f"<td>{len(recommendations)}</td><td>{escape(findings)}</td></tr>"
            )
    run_python("core/examples/messaging_demo.py", "--output-dir", str(directory / "comms"), "--repeats", "5")
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "advisor": totals,
        "source": "Committed actual hosted calls on invented sample data; offline reanalysis",
        "governance": "Fresh synthetic Python executions; no model calls",
        "communications": "Fresh codec/mailbox measurements of retained event payloads",
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (directory / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Canopy fresh CPU demo results</title><style>'
        'body{font:17px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;'
        'background:#101b20;color:#ecf4f3}a{color:#81dfbd}td,th{text-align:left;'
        'padding:12px;border-bottom:1px solid #38545a}table{border-collapse:collapse;width:100%}'
        '</style><main><p><a href="/evidence/">Retained evidence hub</a></p>'
        '<h1>Fresh CPU demo results</h1>'
        f'<p>Generated {escape(summary["generated_at"])}. Advisor: {totals["reports"]} reports, '
        f'{totals["recommendations"]} recommendation groups, {totals["model_calls"]} new model calls.</p>'
        '<p>Discovery findings remain unevaluated. The separately authored JEV mappings and '
        'GPU comparison establish retrospective sample results; this analyzer does not activate them.</p>'
        '<p><a href="comms/index.html">Open fresh communication measurements</a> · '
        '<a href="synthetic.json">Budget, reuse and replay</a> · '
        '<a href="components.json">Drift containment and message deduplication</a> · '
        '<a href="summary.json">Run summary</a></p>'
        '<h2>Advisor: business decisions, tool choices and next steps</h2>'
        '<table><thead><tr><th>Source/report</th><th>Groups</th><th>Findings</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table></main></html>',
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"Saved results to {directory}")


if __name__ == "__main__":
    main()
