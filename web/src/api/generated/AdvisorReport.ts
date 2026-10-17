/* Generated from core/schemas. Run bun run contracts; do not edit. */

export type SchemaVersion = '1';
export type ReportType = 'canopy.offline_advisor';
export type AnalyzerVersion = 'offline-advisor-v1';
export type GeneratedAt = string;
export type TenantId = string;
export type Source = 'recording_store' | 'comparison_report' | 'python_records';
export type SourceIntegrity = 'verified_recordings' | 'validated_import' | 'caller_supplied';
export type MaxRuns = number;
export type MaxGroups = number;
export type MaxSamplesPerGroup = number;
export type MaxStepsPerRun = number;
export type MaxStepChars = number;
export type MaxSeconds = number;
export type RankBy = 'model_calls' | 'elapsed_ms' | 'cost_usd';
export type ScannedRuns = number;
export type ScannedModelSteps = number;
export type SkippedReplayRuns = number;
export type SkippedIncompleteRuns = number;
export type SkippedDuplicateRuns = number;
export type SkippedOversizedSteps = number;
export type SkippedGroupObservations = number;
export type LimitReasons = string[];
export type ElapsedMs = number;
export type ModelCalls = 0;
export type ToolCalls = 0;
export type InputTokens = 0;
export type OutputTokens = 0;
export type CostUsd = null;
export type EnergyJ = null;
export type RecommendationId = string;
export type GroupFingerprint = string;
export type WorkflowId = string;
export type StepId = string;
export type Handler = string;
export type HandlerVersion = string;
export type ModelFingerprint = string;
export type PromptVersion = string;
export type GroupingBasis = 'declared_template' | 'exact_inputs';
export type Category = 'business_decision' | 'tool_selection' | 'next_step' | 'unknown';
export type Status = 'candidate' | 'needs_mapping' | 'unsupported';
export type Strategy = 'evaluate_openjev' | 'deterministic_function' | 'retain_llm' | 'retain_backend';
export type Signals = string[];
export type Reasons = string[];
export type Blockers = string[];
export type RequiredChanges = string[];
export type Choices = string[];
export type PartialSubstitution = boolean;
export type NativeCompatibility =
  'native_request_shape' | 'requires_text_adapter' | 'requires_mapping' | 'not_applicable';
export type Observations = number;
export type Completed = number;
export type Failed = number;
export type Blocked = number;
export type Reused = number;
export type AwaitingApproval = number;
export type ModelCalls1 = number;
export type InputTokens1 = number | null;
export type OutputTokens1 = number | null;
export type CostUsd1 = number | null;
export type ElapsedMs1 = number | null;
export type UnknownTokenObservations = number;
export type UnknownCostObservations = number;
export type UnknownElapsedObservations = number;
export type SyntheticObservations = number;
export type RunId = string;
export type StepId1 = string;
export type Status1 = string;
export type Mode = 'baseline' | 'governed' | 'replay';
export type Synthetic = boolean;
export type SelectedChoice = string | null;
export type Samples = AdvisorSample[];
export type SampleLimitReached = boolean;
export type EvaluationStatus = 'not_evaluated';
export type EstimatedSavingsUsd = null;
export type MeasuredQuality = null;
export type Recommendations = AdvisorRecommendation[];
export type Limitations = string[];

export interface AdvisorReport {
  schema_version?: SchemaVersion;
  report_type?: ReportType;
  analyzer_version?: AnalyzerVersion;
  generated_at?: GeneratedAt;
  tenant_id: TenantId;
  source: Source;
  source_integrity: SourceIntegrity;
  limits: AdvisorLimits;
  analysis: AdvisorAnalysis;
  recommendations: Recommendations;
  limitations?: Limitations;
}
export interface AdvisorLimits {
  max_runs?: MaxRuns;
  max_groups?: MaxGroups;
  max_samples_per_group?: MaxSamplesPerGroup;
  max_steps_per_run?: MaxStepsPerRun;
  max_step_chars?: MaxStepChars;
  max_seconds?: MaxSeconds;
  rank_by?: RankBy;
}
export interface AdvisorAnalysis {
  scanned_runs?: ScannedRuns;
  scanned_model_steps?: ScannedModelSteps;
  skipped_replay_runs?: SkippedReplayRuns;
  skipped_incomplete_runs?: SkippedIncompleteRuns;
  skipped_duplicate_runs?: SkippedDuplicateRuns;
  skipped_oversized_steps?: SkippedOversizedSteps;
  skipped_group_observations?: SkippedGroupObservations;
  limit_reasons?: LimitReasons;
  elapsed_ms?: ElapsedMs;
  model_calls?: ModelCalls;
  tool_calls?: ToolCalls;
  input_tokens?: InputTokens;
  output_tokens?: OutputTokens;
  cost_usd?: CostUsd;
  energy_j?: EnergyJ;
}
export interface AdvisorRecommendation {
  recommendation_id: RecommendationId;
  group_fingerprint: GroupFingerprint;
  workflow_id: WorkflowId;
  step_id: StepId;
  handler: Handler;
  handler_version: HandlerVersion;
  model_fingerprint: ModelFingerprint;
  prompt_version: PromptVersion;
  grouping_basis: GroupingBasis;
  finding: AdvisorFinding;
  observed: AdvisorMetrics;
  samples: Samples;
  sample_limit_reached?: SampleLimitReached;
  evaluation_status?: EvaluationStatus;
  estimated_savings_usd?: EstimatedSavingsUsd;
  measured_quality?: MeasuredQuality;
}
export interface AdvisorFinding {
  category?: Category;
  status: Status;
  strategy: Strategy;
  signals?: Signals;
  reasons?: Reasons;
  blockers?: Blockers;
  required_changes?: RequiredChanges;
  choices?: Choices;
  partial_substitution?: PartialSubstitution;
  native_compatibility?: NativeCompatibility;
}
export interface AdvisorMetrics {
  observations?: Observations;
  completed?: Completed;
  failed?: Failed;
  blocked?: Blocked;
  reused?: Reused;
  awaiting_approval?: AwaitingApproval;
  model_calls?: ModelCalls1;
  input_tokens?: InputTokens1;
  output_tokens?: OutputTokens1;
  cost_usd?: CostUsd1;
  elapsed_ms?: ElapsedMs1;
  unknown_token_observations?: UnknownTokenObservations;
  unknown_cost_observations?: UnknownCostObservations;
  unknown_elapsed_observations?: UnknownElapsedObservations;
  synthetic_observations?: SyntheticObservations;
}
export interface AdvisorSample {
  run_id: RunId;
  step_id: StepId1;
  status: Status1;
  mode: Mode;
  synthetic: Synthetic;
  selected_choice?: SelectedChoice;
}
