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

Core components are being integrated in parallel. Component documentation under
`core/docs/` records implemented interfaces and limitations. Credentials and local
run databases are excluded from version control. Synthetic examples and measured
results must remain clearly distinguished.
