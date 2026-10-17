# Overview

This is a monorepository, containing Building Better Code's Enterprise AI solution for emergeAI's hackathon.

## Canopy agent execution core

Canopy connects recorded execution, validity-aware reuse, budgets, policy controls,
and drift monitoring. The Python core lives in `core/`; the team's frontend lives
in `web/`. The benchmark is independently owned by another teammate.

- [Team setup and integration handoff](docs/TEAM_HANDOFF.md)
- [Core component ownership and execution semantics](docs/CORE_BUILD_PLAN.md)
- [Shared Python contracts](core/src/canopy/contracts.py)

Use Python 3.11 or 3.12. From this directory:

```sh
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install -r core/requirements.lock
python -m pip install -e "core[dev]"
python -m pytest core/tests -q
```

Run the credential-free integration demo:

```sh
python core/scripts/component_smoke.py
python core/examples/synthetic_demo.py
```

The first command exercises all adapters, including fixed policy probes, controlled
containment, compact messages and duplicate delivery. The second prints the full
run records for baseline, cold/warm reuse, source change, budget refusal and replay.
Both use synthetic Python handlers and make zero live provider requests.

For real inference, install `python -m pip install -e "core[models]"`, set the
selected provider's key in the environment, then run:

```sh
python core/examples/live_comparison.py --provider openai --model YOUR_MODEL_ID --output-dir .canopy/comparisons
```

The same runner supports `anthropic`, `gemini`, `jev` (TypeSafe System One), and
`openai-compatible` endpoints. Supply `--task-file` for your own task DAG. See
[live model setup and benchmark integration](core/docs/live_models.md) for keys,
typed JEV questions, actual usage reporting, and cold/warm comparison semantics.

Start the local API with `python core/examples/local_api.py`, then open
`http://127.0.0.1:8000/docs`. It binds to loopback and uses a fixed demo principal;
production authentication is an application integration responsibility.

- [Runtime and HTTP API](core/docs/api.md)
- [Recording, replay and cache](core/docs/recording.md)
- [Budgets and metering](core/docs/budgets.md)
- [Temporal validity](core/docs/temporal.md)
- [Policy controls and drift](core/docs/governance.md)
- [Agent messages and local mailbox](core/docs/messaging.md)
- [Build provenance and existing dependencies](BUILT_TODAY.md)

Component documentation records implemented interfaces and limitations. Credentials
and local run databases are excluded from version control. The local adapters are
not a production identity service, distributed scheduler, or compliance certification.
External effects require service-owned idempotency; arbitrary Python computation
cannot be cancelled by an accounting limit. Benchmark savings must be measured
independently on successful equivalent tasks.
