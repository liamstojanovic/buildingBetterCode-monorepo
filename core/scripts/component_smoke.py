"""Exercise all core adapters with explicitly synthetic, credential-free data.

This is an integration demonstration, not a live model performance benchmark.
"""

import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from pollard import MemoryStore

from canopy.contracts import BudgetLimits, Policy, RunConfig
from canopy.drift import DEFAULT_POLICY_PROBES, DriftMonitor
from canopy.handlers import synthetic_task
from canopy.messaging import AuthorizationContext, Mailbox, MessageCodec, MessageEnvelope
from canopy.runtime import create_runtime


def run_smoke() -> dict:
    expected = {probe.probe_id: probe.expected_decision for probe in DEFAULT_POLICY_PROBES}
    probe_calls = 0

    def conforming(request):
        nonlocal probe_calls
        probe_calls += 1
        return {"decision": expected[request["probe_id"]], "usage": {
            "input_tokens": 0, "output_tokens": 0,
        }}

    def violating(request):
        nonlocal probe_calls
        probe_calls += 1
        return {"decision": "allow", "usage": {"input_tokens": 0, "output_tokens": 0}}

    with TemporaryDirectory(prefix="canopy-components-") as directory:
        with closing(create_runtime(str(Path(directory) / "runs.sqlite3"))) as runtime:
            monitor = DriftMonitor(
                MemoryStore(), tenant_id="demo-tenant", monitor_id="synthetic-monitor",
                policy_version="1",
            )
            baseline = monitor.observe(
                "baseline", conforming, provider_fingerprint="synthetic-v1",
                policy_version="1", establish_baseline=True,
            )
            stable = monitor.observe(
                "current", conforming, provider_fingerprint="synthetic-v1", policy_version="1",
            )
            runtime.drift = monitor
            config = RunConfig(
                permissions=["execute:*"],
                policy=Policy(allowed_tools=["synthetic.catalog", "synthetic.greeting"]),
                budget=BudgetLimits(max_steps=10), model_fingerprint="synthetic-v1",
            )
            task = synthetic_task()
            cold = runtime.run_task(task, config)
            warm = runtime.run_task(task, config)
            changed = runtime.run_task(
                synthetic_task(source_version="2", source_amount_minor=3100), config
            )
            failure = monitor.observe(
                "controlled-regression", violating,
                provider_fingerprint="synthetic-v1", policy_version="1",
            )
            contained = runtime.run_task(task, config)
            replay = runtime.run_task(task, config.model_copy(update={
                "mode": "replay", "replay_run_id": cold.run_id,
            }))
            envelope = MessageEnvelope(
                message_type="result", message_id="synthetic-message", tenant_id="demo-tenant",
                workflow_id=task.workflow_id, stream_id="result-stream", sequence=0,
                sender_id="analyst", recipient_id="reviewer",
                authorization=AuthorizationContext(actor_id="analyst", policy_version="1"),
                payload={"run_id": cold.run_id, "synthetic": True},
            )
            codec = MessageCodec()
            wire = codec.encode(envelope)
            with Mailbox(
                Path(directory) / "messages.sqlite3", tenant_id="demo-tenant",
                recipient_id="reviewer", codec=codec,
            ) as mailbox:
                first = mailbox.deliver(wire)
                duplicate = mailbox.deliver(wire)
                pending = mailbox.pending()
            assert cold.status == warm.status == changed.status == "completed"
            assert warm.reused_steps == 3
            assert changed.reused_steps == 1 and changed.invalidated_steps == 2
            assert contained.status == "blocked" and contained.usage.model_calls == 0
            assert replay.output == cold.output and replay.usage.model_calls == 0
            assert first.inserted and not duplicate.inserted and len(pending) == 1
            monitor.close()
            return {
                "synthetic": True,
                "live_provider_calls": 0,
                "synthetic_probe_calls": probe_calls,
                "monitor_states": [baseline.status, stable.status, failure.status],
                "message_bytes": asdict(codec.measure(envelope)),
                "duplicate_message_inserted": duplicate.inserted,
                "runs": {
                    label: {"status": result.status, "reused_steps": result.reused_steps,
                            "invalidated_steps": result.invalidated_steps,
                            "usage": result.usage.model_dump(mode="json")}
                    for label, result in {"cold": cold, "warm": warm, "changed": changed,
                                          "contained": contained, "replay": replay}.items()
                },
            }


if __name__ == "__main__":
    print(json.dumps(run_smoke(), indent=2))
