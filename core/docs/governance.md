# Governance public integration surface

Owner: policy and drift. No shared contract changes or additional dependencies
are required. Implemented against installed Pollard 1.6.2 and Epicormic 0.1.0.

```python
@dataclass(frozen=True)
class ExecutionContext:
    tenant_id: str
    actor_id: str
    permissions: frozenset[str]

@dataclass(frozen=True)
class ReviewerIdentity:
    tenant_id: str
    reviewer_id: str
    permissions: frozenset[str]

ApprovalStore(path: str | Path, *, reviewer_authorizer:
    Callable[[Any], ReviewerIdentity | None] | None = None)
ApprovalStore.request(binding: ApprovalBinding, *, expires_at: datetime | None = None,
    now: datetime | None = None) -> ApprovalRecord
ApprovalStore.get(approval_id: str, *, tenant_id: str) -> ApprovalRecord | None
ApprovalStore.decide(approval_id: str, *, reviewer_context: Any, approved: bool,
    reason: str | None = None, now: datetime | None = None) -> ApprovalRecord
ApprovalStore.consume(approval_id: str, binding: ApprovalBinding, *,
    now: datetime | None = None) -> ApprovalRecord
ApprovalStore.close() -> None

PolicyGate(approval_store: ApprovalStore | None = None)
PolicyGate.evaluate(step: Step, *, context: ExecutionContext, policy: Policy,
    evidence: Sequence[Evidence] = (), dependency_outputs: Mapping[str, Any] | None = None,
    approval_id: str | None = None, drift: Any = None,
    now: datetime | None = None) -> PolicyDecision
```

`PolicyDecision.status` is `allow`, `deny`, or `approval_required`; `.allowed` is a
boolean convenience. `.pollard_decision` returns the real Pollard `Decision` enum
(`ALLOW`, `DENY`, or `CONFIRM`). The decision exposes `.reason`, `.binding` (exact
immutable `ApprovalBinding`, absent for invalid/prohibited actions), and
`.approval_id`. Request approval only for `approval_required` decisions with
`store.request(decision.binding)`. Requests default to a one-hour lifetime; an
existing live request for the same binding is reused without extending its expiry.
`ApprovalRecord` has `approval_id`, `binding`, `status` (`pending`, `approved`,
`denied`), `requested_at`, `expires_at`, `reviewer_id`, `decided_at`, `reason`, and
`consumed_at`. `ApprovalBinding` exposes `digest`, `tenant_id`, `actor_id`,
`policy_version`, `canonical_json`, and a detached `to_dict()` review snapshot.

Re-evaluate current policy, permissions, evidence and drift after review. After
checking an approved decision, atomically claim the approval with
`store.consume(approval_id, decision.binding)` immediately before dispatch. The
claim must use the freshly checked binding. `consume` verifies the exact approval
and expiry; it cannot re-query application authorization or source freshness.
Handlers must dispatch exactly the captured arguments. Any action parameter
derived from task metadata, model/prompt configuration, other evidence, or another
context field must first be resolved into the bound inputs/dependency outputs.
Giving a handler broader task/config access does not automatically bind those
fields; trusted handlers must not silently source action arguments from them.
One approval authorizes one dispatch. External side effects still
require service-owned idempotency keys; a claim does not prove an action succeeded.

The application must construct `ExecutionContext` from authenticated identity and
server-side authorization. `RunConfig.permissions`, user payload role labels, and
agent labels are not authentication. The gate accepts `execute:<handler>`,
`execute:*`, or `*`; all tool handlers must additionally occur in
`Policy.allowed_tools`. Model handlers require execution permission but are not
governed by the tool allowlist. **Required runtime integration check:** verify
`Step.kind` matches the registered handler's trusted `kind` before evaluating or
dispatching. Otherwise a client could mislabel a tool as a model to bypass its
allowlist. The registry must also validate handler version and side-effect flags;
the gate cannot infer those properties from an untrusted task. The approval store
denies decisions unless its
configured `reviewer_authorizer(reviewer_context)` returns a `ReviewerIdentity`
with the matching tenant and `approvals:decide` permission. That callback must
validate the application's session/token context, never merely read payload roles.

Approval bindings include tenant, actor, step ID/kind, handler/version, side-effect
flag, canonical JSON inputs and dependency outputs, full policy content/version,
and full supplied relevant evidence content plus the exact drift report, if given.
Dependency/evidence references are included. Referenced evidence must be present;
cross-tenant or duplicate evidence is rejected. Arguments support JSON data only,
with string object keys and finite numbers. Key ordering is normalized, list order
is retained. Changing a policy without bumping its version still invalidates an
approval through its content digest. The amount cap conservatively checks every
`amount_minor` field in inputs and dependency outputs; monetary values must be
nonnegative integers. Freshness and transitive source authority belong to the
temporal validator and must be checked separately before dispatch. Evidence JSON
is validated before Pydantic serialization to prevent lossy coercion of NaN,
non-string object keys, sets or tuples into an approved payload. Approval storage
contains reviewable plaintext action/evidence snapshots; the host owns database
access control. Local SQLite ownership is trusted, not a signed audit ledger.

## Fixed probes and drift monitoring

```python
@dataclass(frozen=True)
class PolicyProbe:
    probe_id: str
    prompt: str
    expected_decision: Literal["allow", "deny", "review"]

DriftMonitor(store: pollard.store.Store | str | Path, *, tenant_id: str,
    monitor_id: str, policy_version: str,
    probe_version: str = "enterprise-policy-v1",
    probes: tuple[PolicyProbe, ...] = DEFAULT_POLICY_PROBES,
    min_samples: int = 5)
DriftMonitor.observe(window_id: str,
    provider: Callable[[dict[str, Any]], dict[str, Any]], *,
    provider_fingerprint: str, policy_version: str, samples: int = 5,
    establish_baseline: bool = False) -> DriftReport
DriftMonitor.latest() -> DriftReport | None
DriftMonitor.close() -> None
```

The synchronous provider receives `{"probe_id": ..., "prompt": ...}` and returns
`{"decision": "allow" | "deny" | "review", "explanation": ...}`; explanation is
optional. The same structure inside `structured_output` is supported. Requests
ask only for classification and never tool execution. `samples` and `min_samples`
are **per probe**, with `min_samples >= 2`. The three fixed default scenarios cover
cross-tenant access, trusted approval, and an authorized catalog read. They are
synthetic test scenarios, not live enterprise records or general compliance tests.

`DriftReport` is a frozen dataclass with `status`, `containment`,
`known_violations`, `reason`, `tenant_id`, `monitor_id`, `policy_version`,
`probe_version`, `window_id`, `provider_fingerprint`, `provider_changed`,
`baseline_window_id`, `samples_observed` (total across probes), `panel_digest`,
`epicormic_state`, and `verdict_node_id`. Pass the report directly as `drift=` to
`PolicyGate.evaluate`.

| State | Meaning | Forward containment |
| --- | --- | --- |
| `unknown` | Missing baseline, insufficient/unscorable samples, interruption, or provider failure | Review |
| `stable` | Real Epicormic comparison found no change on the pinned probe surface | None; deterministic gate still applies |
| `drifted` | Epicormic detected changed behavior, including changed conforming explanations | Review |
| `policy_changed` | Supplied policy version differs from the monitor configuration | Review; zero provider calls |
| `nonconformant` | A pinned expected decision was contradicted or a tool call was proposed | Deny; stop remaining probes |

The adapter calls real Epicormic `observe` and `analyze`, registers the custom
absolute `canopy_policy_conformance_v1` scorer, and compares behavior using the
built-in `normalized_match`. Baselines are independently scored against the fixed
expected decisions; repeating a violation can never make it an acceptable
baseline. Every probe must have enough scorable conforming observations. Baseline
establishment stays `unknown` until a separate current window is compared.
Missing/unrecognized structured decisions remain unknown, not inferred compliant.

The default probe version is pinned in code. Custom probe versions bind the exact
panel digest (including expected decisions) within the shared store; changed
prompts/expectations need a new version. Monitor configuration and baselines are
immutable. Policy version changes are reported separately and make no calls with
the old probe panel. A provider fingerprint change is recorded separately and
does not replace the baseline. The host must bump policy versions for policy
revisions and provision reviewed new probes/monitor configuration deliberately.

Known violations deny immediately regardless of sample size, statistical state,
`Policy.drift_action`, or an existing approval. A deny latch survives process
restart and provider renaming; subsequent observations make zero provider calls.
`latest()` also checks interrupted persisted windows for recorded violations and
may persist recovered deny containment; it is not guaranteed to be read-only.
Recovery requires a trusted, explicit new monitor identity and independently
checked baseline. This contains future dispatch; it cannot undo prior actions.
Other unresolved reports require review; `Policy.drift_action="deny"` can tighten
them to denial. The gate rejects cross-tenant reports. Omitting `drift` means
monitoring has not been requested, not that a provider was certified.

The host owns provider authentication, probe budgets, scheduling, and choice of
current report. There is one writer per drift monitor. A passed Pollard store
remains caller-owned; a path creates an owned SQLiteStore closed by `close()`.
No robotics policies or Pollard-Jev dependency pins are imported. Pollard's
installed typed decision interface informs the adapter; its declared actor labels
are not used as authenticated reviewer identity.

## Verification

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest core/tests/test_governance.py core/tests/test_drift.py -q
.venv/Scripts/python.exe -m ruff check core/src/canopy/governance.py core/src/canopy/drift.py core/tests/test_governance.py core/tests/test_drift.py
```

Tests use explicitly synthetic actions/providers, real SQLite, real Pollard nodes,
and real Epicormic windows/verdicts. They cover exact binding mutations, malformed
JSON/evidence, reviewer authority, expiry, restart, concurrent claims, deterministic
denial, fixed probe versions, independent baselines, unknown evidence, provider vs.
policy changes, behavior drift, and persistent containment.

Verified result: **72 tests passed; Ruff clean**. Pollard emitted 22 expected
warnings because these synthetic classification fixtures provide no measured
usage payload; the tests do not invent token/cost measurements.
