"""Container entry point for the local synthetic API.

The Compose file exposes nginx and this API only on host loopback. Binding this process to
0.0.0.0 allows nginx to reach it across the private container network. Anyone
who can reach the API acts as this fixed demo operator; it is not a production
authentication service. No hosted model credentials or calls are needed.
"""

import argparse
from contextlib import closing
from pathlib import Path
from typing import Any

import uvicorn
from canopy.api import ServiceContext, create_app
from canopy.contracts import BudgetLimits, Policy
from canopy.governance import ReviewerIdentity
from canopy.handlers import synthetic_registry
from canopy.runtime import create_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/api.sqlite3")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--require-approval", action="store_true")
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
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

    def authorize_reviewer(candidate: Any) -> ReviewerIdentity | None:
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
        reviewer_authorizer=authorize_reviewer,
    )) as runtime:
        uvicorn.run(create_app(runtime, context=context), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
