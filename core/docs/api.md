# Runtime and local HTTP API

The synchronous Python adapter dispatches a dependency DAG through the actual
recording/cache, Pollard budget, Chronofy temporal, policy/approval and optional
Epicormic drift components. Names never trigger dynamic imports or shell calls.

```python
class HandlerContext:
    run_id: str
    task: Task
    step: Step
    config: RunConfig
    inputs: dict[str, Any]
    dependencies: dict[str, Any]

class HandlerRegistry:
    def register(self, name: str, handler: Callable[[HandlerContext], HandlerResult],
                 *, version: str = "1", synthetic: bool = False,
                 side_effect: bool = False, kind: Literal["model", "tool"] = "tool",
                 cacheable: bool = False,
                 preflight: Callable[[Task, Step, RunConfig], None] | None = None) -> None: ...
    def resolve(self, name: str, version: str = "1") -> HandlerRegistration: ...
    def registered_names(self) -> tuple[str, ...]: ...

class Runtime:
    def __init__(self, *, registry: HandlerRegistry, store: ExecutionStore,
                 cache: ResultCache | None = None,
                 approvals: ApprovalStore | None = None,
                 drift: DriftMonitor | None = None) -> None: ...
    def run_task(self, task: Task, config: RunConfig,
                 *, tenant_id: str | None = None,
                 approval_ids: Mapping[str, str] | None = None) -> RunResult: ...
    def budget_trace(self, tenant_id: str, run_id: str) -> list[pollard.Node]: ...
    def close(self) -> None: ...

def create_runtime(db_path: str = ".canopy/canopy.sqlite3",
                   *, registry: HandlerRegistry | None = None,
                   reviewer_authorizer: Callable[[Any], Any] | None = None) -> Runtime: ...
def run_task(task: Task, config: RunConfig, *, runtime: Runtime | None = None,
             approval_ids: Mapping[str, str] | None = None) -> RunResult: ...

def synthetic_registry() -> HandlerRegistry: ...
def synthetic_task(*, source_version: str = "1", source_amount_minor: int = 2400,
                   tenant_id: str = "demo-tenant", observed_at: datetime | None = None) -> Task: ...
```

Python callers supply trusted configuration; HTTP callers cannot supply actor,
tenant privileges, permissions, policy, or budget authority. The HTTP application
factory receives a trusted service context and overwrites those fields.

`HandlerRegistration` is immutable and exposes `name`, `handler`, `version`,
`synthetic`, `side_effect`, `kind`, `cacheable`, and `preflight`. Unknown names, duplicate
registrations, async/generator callables, version mismatches, and task attempts
to change trusted kind/side-effect declarations fail before dispatch. The runtime
passes detached context copies. Each handler returns `HandlerResult`. Outputs
are normalized through canonical JSON before reaching dependents, so live and
reused outputs have the same types (aware timestamps become strings).

Optional synchronous `preflight` callbacks validate detached task, step, and
configuration copies before run creation, cache lookup, or dispatch, including
warm cache hits. Historical replay remains an inspection of recorded results.

`run_task(task, config)` lazily creates a durable local runtime with synthetic
handlers. Custom applications should inject a runtime and close it after requests
finish. Runtime closes recording, cache and approval stores; an externally supplied
drift monitor remains owned by its caller. No packages or credentials are needed
beyond the core dependencies for synthetic handlers. The optional `models` extra
adds [live provider adapters](live_models.md) through `register_model_handler`.
The local HTTP example remains explicitly synthetic; applications can inject a
registry containing live handlers into `create_app`.

Every task is validated as a whole before dispatch. Independent ready steps run
sequentially in deterministic DAG order. Failed/blocked dependencies block their
descendants; unrelated work may continue. Baseline uses the same handlers without
reuse; authorization, explicit evidence expiry, approvals, and budgets still
apply. Governed mode additionally checks temporal freshness for transitive
evidence and can reuse registered cacheable computations.

The live order is evidence validation, cache inspection, current policy/drift and
approval evaluation, budget admission, atomic approval consumption, durable step
claim, dispatch, actual usage settlement, and recording. Hits still pass current
policy and freshness. Cache/replay do not charge historical work to a new budget.
Cache corruption fails closed. Failed handlers are recorded, attempted dispatches
are counted, and missing measurements remain unknown. Actual overrun or unknown
capped usage makes the outcome unsuccessful and contains later admissions.
Task-supplied estimates cannot guarantee consumption or cancel arbitrary Python
code; providers must enforce their own hard limits/timeouts.

Handlers may raise `HandlerExecutionError(message, usage=measured_usage,
output=safe_diagnostics)` to preserve usage from an incomplete or refused provider
response. The runtime settles those measurements and records a failed step;
diagnostics never become successful dependency inputs or reusable cache entries.

Handlers supply measured tokens, provider-cached input tokens, cost and energy.
Runtime measures dispatch duration, preserves reported nested call counts, and
counts at least one attempted dispatch of the declared kind. Run elapsed time
includes orchestration. `Usage.tokens_known` distinguishes absent measurement
from measured zero; unknown cost/energy stay null. Synthetic model dispatches are
labeled synthetic and produce zero LLM tokens. Zero new provider spend/calls does
not imply zero local energy consumption.

`cacheable=False` is the default. Opting in asserts that output depends only on
explicit inputs, direct dependency outputs, declared transitive evidence, and
cache-bound configuration (actor/permissions/policy/model/prompt/handler revisions).
Run/task IDs, execution mode, budgets, clocks, randomness and undeclared external
state may support diagnostics but must not affect reused output. Python cannot
infer hidden dependencies. Side effects are never reusable. The synthetic catalog
handler checks that its evidence was declared before reading it.

`invalidated_steps` explains evidence changes against the latest successful
governed run of this tenant/workflow found among the most recent 1,000 runs. It
counts current consumers and descendants, leaving unrelated work reusable. Cache
identity independently binds all dependency/evidence changes regardless of this
comparison window. Policy/model/prompt/argument misses do not increment this
evidence-specific counter.

Replay requires a terminal, integrity-verified source in the same tenant and an
identical task except `task_id`. It works with an empty registry and preserves
source outcomes/provenance with zero new usage. Runtime persists inspection as a
new run with `replay_loaded`; its payload includes historical usage and policy
version. The replay result and stored policy retain the source policy version;
`inspection_policy_version` records the requested inspection policy separately.
Replay does not establish conformance with that newer policy.

Events have contiguous per-run sequence numbers, including recording claim/result/
terminal events. Actual Pollard budget nodes persist alongside recording nodes in
`<db_path>.pollard.sqlite3`; retain it with the main database. `budget_trace`
resolves them after reopening and checks tenant access through the run. Crashes
can leave incomplete runs; strict replay refuses those. Unknown external effects
are never automatically retried. Services need idempotency across separate runs.

Approval IDs are supplied by step ID, outside shared task/config contracts. An
`approval_required` event exposes the ID. An approval is consumed atomically before
one dispatch. Reposting creates a new run, rather than resuming an old claim.
The policy proposal wraps original inputs as `arguments.inputs.handler_inputs`
and trusted revisions as `arguments.inputs.runtime_context`. The latter binds
model fingerprint, prompt version, permissions and execution mode. User keys cannot
overwrite these namespaces. PolicyGate also binds dependency outputs, relevant
evidence, actor, full policy and handler identity. An approval cannot carry over
to a different provider/prompt or altered action.

With a supplied `DriftMonitor`, every step reads current persisted monitoring
evidence. Missing observations and provider mismatch require review; known
violations remain denied. Runtime never makes unbudgeted canary calls. The host
owns probe scheduling/accounting. Without a monitor, deterministic policy applies
and no statistical stability claim is made.

## HTTP API

```python
from canopy.api import ServiceContext, create_app

ServiceContext(tenant_id: str, actor_id: str, permissions: frozenset[str] = frozenset(),
               policy: Policy = Policy(), budget: BudgetLimits = BudgetLimits(),
               model_fingerprint: str = "fixture-v1", prompt_version: str = "1")
create_app(runtime: Runtime, *, context: ServiceContext) -> FastAPI
```

The factory snapshots a fixed trusted service principal. Bodies cannot supply
actor, permissions, policy, budget, or model/prompt authority. Headers, roles and
metadata grant no privileges. Task/evidence tenants must match the service tenant.
The host owns runtime shutdown.

| Endpoint | Behavior |
| --- | --- |
| `POST /runs` | Synchronous execution; returns `RunResult`. Body exactly `{task, mode?, cache_enabled?, replay_run_id?, approval_ids?: {step_id: approval_id}}`. |
| `GET /runs?limit=100&offset=0` | Tenant-scoped `RunSummary[]`; unfinished records use status `running`. |
| `GET /runs/{id}` | Summary plus task, nullable result and recorded steps. |
| `GET /runs/{id}/events?after_sequence=-1&limit=1000` | Ordered `RunEvent[]`; exclusive sequence cursor. |
| `GET /approvals/{id}` | Exact binding snapshot/digest, status, reviewer and timestamps. |
| `POST /approvals/{id}/decision` | Body exactly `{approved: bool, reason?: str}`. |

Missing/foreign records return 404; tenant mismatch returns 403; malformed requests
return 422; incomplete/corrupt replay and conflicting approval decisions return
409. Refusals are HTTP 200 outcomes with `blocked` or `awaiting_approval`; clients
must inspect status. Decision access needs explicit `approvals:decide` and a
separately configured store reviewer authorizer that recognizes server-owned
context and returns `ReviewerIdentity`. The default authorizer denies. Never
authorize from body roles. Repost the original task with approved IDs to execute;
changed bindings and consumed approvals cannot authorize another dispatch.

This fixed context is **not HTTP authentication**. Everyone who can reach the
instance acts as that principal. The example binds only `127.0.0.1`; production
hosts must implement authenticated principal resolution and isolation. No CORS or
remote function-import mechanism is enabled.

## Examples and validation

```powershell
.venv\Scripts\python.exe core\examples\synthetic_demo.py
.venv\Scripts\python.exe core\examples\local_api.py --task-json
.venv\Scripts\python.exe core\examples\local_api.py --require-approval
.venv\Scripts\python.exe -m pytest core/tests/test_runtime.py core/tests/test_api.py
```

The six-scenario fixture uses actual local components: baseline and cold governed
each dispatch one synthetic model and two tools; warm reuses three steps; a source
change invalidates two and reuses one; a zero-step budget refuses dispatch; replay
makes zero calls. These are fixture behaviors, not savings/performance claims.

Verified: 50 runtime/API tests passed; Ruff passed for runtime, API, handlers,
their tests and examples. The installed FastAPI test client emits an httpx
deprecation warning, and the structured policy-canary fixture emits Pollard's
missing-token-usage warning; neither is interpreted as measured token usage.
No dependency additions or further shared-contract changes are required.
