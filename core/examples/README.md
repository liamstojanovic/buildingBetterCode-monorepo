# Synthetic execution demo

From the repository root, using the installed editable core package:

```powershell
.venv\Scripts\python.exe core\examples\synthetic_demo.py
```

The script creates a temporary database and prints six full JSON run results:
baseline, governed, warm governed, source changed, budget refused, and replay.
Use `--db .canopy/demo.sqlite3` to retain records for inspection. A retained
database can already contain reusable results from earlier invocations.

This is a deterministic Python fixture. It reads an in-memory synthetic catalog,
formats a synthetic summary, and returns an independent greeting. It performs
no external actions and uses no credentials. Model dispatch counts include the
synthetic summary step, but its zero token count reflects no LLM call. Cost and
energy are unknown, not estimated savings.

`synthetic_task()` produces a catalog evidence snapshot that expires after one
hour. Passing the same task to consecutive governed runs allows warm reuse.
Changing the catalog source version and payload should rerun its consumer and
dependent summary while retaining the independent greeting. The refusal run
disables reuse and admits zero steps. Replay reads the stored governed run and
must spend no new model or tool calls.

Application code can create `HandlerRegistry`, register synchronous callables,
and pass it to `create_runtime(..., registry=registry)`. Registrations default
to `kind="tool"`; model providers must explicitly set `kind="model"`. The task
must match this trusted kind, so client requests cannot reclassify tools to
bypass tool policy. Registered handlers take
one `HandlerContext` and return `HandlerResult`. Registrations are immutable and
duplicate names are rejected; create a new registry to change a handler version.
Names are explicit local keys, not import paths or executable strings. A trusted
application chooses registrations, permissions, policy, and budgets.

Registrations default to `cacheable=False`. Setting `cacheable=True` is a trusted
application assertion that the handler's output depends only on explicit step
inputs, direct dependency outputs, declared transitive evidence, and configuration
bound in the cache key. Run/task IDs, execution mode, budgets, clocks, randomness,
and undeclared external state may be used for diagnostics but must not affect a
reused output. Python cannot infer hidden dependencies. A read-only handler that
reads current external state can remain non-cacheable without being marked as a
side effect. Side effects are never reusable. The three deterministic synthetic
handlers explicitly opt into caching and consume only their declared data.

## Local HTTP demo

```powershell
.venv\Scripts\python.exe core\examples\local_api.py
```

Open `http://127.0.0.1:8000/docs` for the request schema and interactive API.
Generate an example `POST /runs` body with:

```powershell
.venv\Scripts\python.exe core\examples\local_api.py --task-json
```

The local service supplies the tenant, operator identity, execution permissions,
policy, and budget. Request bodies cannot grant these powers. Every caller able
to reach this unauthenticated demo acts as the configured operator; this example
does not provide production authentication or authorize separate end users.
Its bind address is fixed to `127.0.0.1`.

Start with `--require-approval` to exercise approval handling. Submit the task,
then read `GET /runs/{run_id}/events` to find the `approval_required` event and
its `approval_id`. Approve it through
`POST /approvals/{approval_id}/decision` with `{"approved": true}`. Submit the
same original task again with `"approval_ids": {"read_catalog": "<approval_id>"}`
beside the `task` field. Preserve the complete original evidence snapshot:
regenerating the task changes its observation time and invalidates the approval.
Approval authorizes one dispatch with those exact inputs and policy. The sample
reviewer callback recognizes only the context supplied internally by the local
service. All registered handlers remain synthetic and have no external effects.
