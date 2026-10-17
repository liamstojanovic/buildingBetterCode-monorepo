/* Generated from core/schemas. Run bun run contracts; do not edit. */

export type SchemaVersion = '1';
export type RunId = string;
export type TaskId = string;
export type TenantId = string;
export type Mode = 'baseline' | 'governed' | 'replay';
export type Status = 'completed' | 'blocked' | 'awaiting_approval' | 'failed';
export type StartedAt = string;
export type FinishedAt = string;
export type StepId = string;
export type Status1 = 'completed' | 'reused' | 'blocked' | 'awaiting_approval' | 'failed';
export type Output1 = unknown;
export type Reason = string | null;
export type CacheKey = string | null;
export type SourceRunId = string | null;
export type InputTokens = number;
export type OutputTokens = number;
export type ProviderCachedInputTokens = number;
export type ModelCalls = number;
export type ToolCalls = number;
export type CostUsd = number | null;
export type EnergyJ = number | null;
export type ElapsedMs = number;
export type TokensKnown = boolean;
export type ElapsedKnown = boolean;
export type Steps = StepResult[];
export type PolicyVersion = string;
export type ReusedSteps = number;
export type InvalidatedSteps = number;
export type ReplaySourceRunId = string | null;
export type Synthetic = boolean;
export type Reason1 = string | null;

export interface RunResult {
  schema_version?: SchemaVersion;
  run_id: RunId;
  task_id: TaskId;
  tenant_id: TenantId;
  mode: Mode;
  status: Status;
  started_at: StartedAt;
  finished_at: FinishedAt;
  output?: Output;
  steps?: Steps;
  usage?: Usage;
  policy_version: PolicyVersion;
  evidence_versions?: EvidenceVersions;
  reused_steps?: ReusedSteps;
  invalidated_steps?: InvalidatedSteps;
  replay_source_run_id?: ReplaySourceRunId;
  synthetic?: Synthetic;
  reason?: Reason1;
}
export interface Output {
  [k: string]: unknown;
}
export interface StepResult {
  step_id: StepId;
  status: Status1;
  output?: Output1;
  reason?: Reason;
  cache_key?: CacheKey;
  source_run_id?: SourceRunId;
  usage?: Usage;
}
export interface Usage {
  input_tokens?: InputTokens;
  output_tokens?: OutputTokens;
  provider_cached_input_tokens?: ProviderCachedInputTokens;
  model_calls?: ModelCalls;
  tool_calls?: ToolCalls;
  cost_usd?: CostUsd;
  energy_j?: EnergyJ;
  elapsed_ms?: ElapsedMs;
  tokens_known?: TokensKnown;
  elapsed_known?: ElapsedKnown;
}
export interface EvidenceVersions {
  [k: string]: string;
}
