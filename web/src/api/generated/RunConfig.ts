/* Generated from core/schemas. Run bun run contracts; do not edit. */

export type Mode = 'baseline' | 'governed' | 'replay';
export type ActorId = string;
export type Permissions = string[];
export type Version = string;
export type AllowedTools = string[];
export type ApprovalRequiredTools = string[];
export type MaxAmountMinor = number | null;
export type FreshnessThreshold = number;
export type DriftAction = 'allow' | 'review' | 'deny';
export type MaxTokens = number | null;
export type MaxSteps = number | null;
export type MaxCostUsd = number | null;
export type MaxElapsedMs = number | null;
export type MaxEnergyJ = number | null;
export type ModelFingerprint = string;
export type PromptVersion = string;
export type CacheEnabled = boolean;
export type ReplayRunId = string | null;

export interface RunConfig {
  mode?: Mode;
  actor_id?: ActorId;
  permissions?: Permissions;
  policy?: Policy;
  budget?: BudgetLimits;
  model_fingerprint?: ModelFingerprint;
  prompt_version?: PromptVersion;
  cache_enabled?: CacheEnabled;
  replay_run_id?: ReplayRunId;
}
export interface Policy {
  version?: Version;
  allowed_tools?: AllowedTools;
  approval_required_tools?: ApprovalRequiredTools;
  max_amount_minor?: MaxAmountMinor;
  freshness_threshold?: FreshnessThreshold;
  drift_action?: DriftAction;
}
export interface BudgetLimits {
  max_tokens?: MaxTokens;
  max_steps?: MaxSteps;
  max_cost_usd?: MaxCostUsd;
  max_elapsed_ms?: MaxElapsedMs;
  max_energy_j?: MaxEnergyJ;
}
