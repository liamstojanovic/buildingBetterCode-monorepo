/* Generated from core/schemas. Run bun run contracts; do not edit. */

export type SchemaVersion = '1';
export type TaskId = string;
export type TenantId = string;
export type WorkflowId = string;
export type StepId = string;
export type Kind = 'model' | 'tool';
export type Handler = string;
export type HandlerVersion = string;
export type DependsOn = string[];
export type EvidenceIds = string[];
export type SideEffect = boolean;
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
export type Steps = Step[];
export type EvidenceId = string;
export type TenantId1 = string;
export type Source = string;
export type Version = string;
export type ObservedAt = string;
export type ExpiresAt = string | null;
export type HalfLifeSeconds = number | null;
export type Quality = number;
export type Evidence = Evidence1[];

export interface Task {
  schema_version?: SchemaVersion;
  task_id: TaskId;
  tenant_id: TenantId;
  workflow_id: WorkflowId;
  steps: Steps;
  evidence?: Evidence;
  metadata?: Metadata;
}
export interface Step {
  step_id: StepId;
  kind: Kind;
  handler: Handler;
  handler_version?: HandlerVersion;
  inputs?: Inputs;
  depends_on?: DependsOn;
  evidence_ids?: EvidenceIds;
  side_effect?: SideEffect;
  estimated_usage?: Usage;
}
export interface Inputs {
  [k: string]: unknown;
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
export interface Evidence1 {
  evidence_id: EvidenceId;
  tenant_id: TenantId1;
  source: Source;
  version: Version;
  observed_at: ObservedAt;
  expires_at?: ExpiresAt;
  half_life_seconds?: HalfLifeSeconds;
  quality?: Quality;
  data?: Data;
}
export interface Data {
  [k: string]: unknown;
}
export interface Metadata {
  [k: string]: unknown;
}
