"""Local synthetic HTTP demo with an explicit server-owned principal.

Run from the repository root:
    .venv/Scripts/python.exe core/examples/local_api.py

The service always binds to 127.0.0.1. It is an unauthenticated local demo:
anyone able to connect acts as its operator, including approval decisions.
"""

import argparse
from contextlib import closing
import json
from typing import Any

import uvicorn

from canopy.api import ServiceContext, create_app
from canopy.contracts import BudgetLimits, Policy
from canopy.governance import ReviewerIdentity
from canopy.handlers import synthetic_registry, synthetic_task
from canopy.runtime import create_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".canopy/local-demo.sqlite3")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--require-approval", action="store_true",
        help="Require one exact approved request before synthetic catalog dispatch",
    )
    parser.add_argument(
        "--task-json", action="store_true",
        help="Print a synthetic POST /runs request body and exit",
    )
    args = parser.parse_args()
    if args.task_json:
        print(json.dumps({"task": synthetic_task().model_dump(mode="json")}, indent=2))
        return

    context = ServiceContext(
        tenant_id="demo-tenant",
        actor_id="local-demo-operator",
        permissions=frozenset({"execute:*", "approvals:decide"}),
        policy=Policy(
            allowed_tools=["synthetic.catalog", "synthetic.greeting"],
            approval_required_tools=["synthetic.catalog"] if args.require_approval else [],
        ),
        budget=BudgetLimits(max_steps=10),
        model_fingerprint="synthetic-python-fixture-v1",
    )

    def authorize_local_reviewer(candidate: Any) -> ReviewerIdentity | None:
        # create_app supplies its private copy of this server-owned context.
        # This callback does not authenticate HTTP clients or trust request roles.
        if not isinstance(candidate, ServiceContext) or candidate != context:
            return None
        return ReviewerIdentity(
            tenant_id=context.tenant_id,
            reviewer_id=context.actor_id,
            permissions=context.permissions,
        )

    with closing(create_runtime(
        args.db,
        registry=synthetic_registry(),
        reviewer_authorizer=authorize_local_reviewer,
    )) as runtime:
        uvicorn.run(create_app(runtime, context=context), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
