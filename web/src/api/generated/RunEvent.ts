/* Generated from core/schemas. Run bun run contracts; do not edit. */

export type SchemaVersion = '1';
export type RunId = string;
export type Sequence = number;
export type Timestamp = string;
export type EventType = string;
export type StepId = string | null;

export interface RunEvent {
  schema_version?: SchemaVersion;
  run_id: RunId;
  sequence: Sequence;
  timestamp?: Timestamp;
  event_type: EventType;
  step_id?: StepId;
  payload?: Payload;
}
export interface Payload {
  [k: string]: unknown;
}
