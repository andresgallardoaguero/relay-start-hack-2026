export type Decision = "approve" | "decline" | "step_up";
export type LlmTraceMode = "off" | "live" | "degraded";
export type LlmServiceMode = "off" | "ready" | "degraded";
export type GuardVerdict = "PASS" | "STEP_UP" | "DECLINE" | "UNCERTAIN" | "SKIP";
export type GuardFamily =
  | "Spending limits"
  | "Item and terms"
  | "Seller"
  | "Session"
  | "Repeats and manipulation";

export interface EvidenceItem {
  fact: string;
  value: string | number | boolean | null;
  comparator: string | null;
  threshold: string | number | boolean | null;
  source: string;
}

export interface GuardResult {
  guard_number: number;
  guard_id: string;
  family: GuardFamily;
  verdict: GuardVerdict;
  reason_code: string | null;
  evidence: EvidenceItem[];
  signal: { name: string; strength: "normal" | "strong" } | null;
  note: string | null;
  customer_message: string | null;
  elapsed_ms: number;
}

// The trust score is derived by the backend from the guard verdicts of a trace. Every point taken is listed as a deduction.
export type TrustBand = "trusted" | "review" | "blocked" | "not_assessed";

export interface TrustDeduction {
  kind: "leading_finding" | "further_finding" | "doubt" | "signal";
  guard_id: string;
  family: GuardFamily | null;
  verdict: GuardVerdict | null;
  reason_code: string | null;
  signal_name: string | null;
  points: number;
  capped: boolean;
  detail: string;
}

export interface TrustFamilyScore {
  family: GuardFamily;
  strictest_verdict: GuardVerdict;
  points_lost: number;
}

export interface TrustScore {
  version: string;
  score: number | null;
  band: TrustBand;
  label: string;
  summary: string;
  scale: { start: number; trusted_from: number; review_from: number };
  deductions: TrustDeduction[];
  families: TrustFamilyScore[];
  coverage: { checks_total: number; checks_evaluated: number; checks_not_applicable: number; checks_not_built: number; checks_failed: number; coverage_share: number };
  basis: { decision: Decision; uncertainty_policy: string; engine_version: string; trace_version: string; llm_mode: string };
}

export interface DecisionTrace {
  trace_version: string;
  engine_version: string;
  ids: {
    authorization_id: string;
    source_authorization_id: string;
    request_id: string;
    mandate_id: string;
    scenario_id: string;
  };
  received_at: string;
  decided_at: string;
  deadline_at: string;
  margin_ms: number;
  decision: Decision;
  reason_codes: string[];
  customer_message: string;
  notes: string[];
  facts: {
    amounts: { amount: number; currency: string; billing_amount_chf: number };
    merchant: {
      merchant_id: string;
      merchant_name: string;
      merchant_category: string;
      merchant_country: string;
    };
    familiarity: Record<string, unknown> | null;
    extracted_item_facts: Record<string, unknown> | null;
  };
  guards: GuardResult[];
  aggregation: {
    initial: Decision;
    final: Decision;
    raised_by: string[];
    uncertainty_policy_applied: boolean;
  };
  llm: { mode: LlmTraceMode; calls: Record<string, unknown>[] };
  timings: { total_ms: number };
  resolution: Record<string, unknown> | null;
  // Filled by the adapter from the record's trust_score; absent on a trace that predates the score.
  trust?: TrustScore | null;
}

export interface PendingAuthorization {
  authorization_id: string;
  created_at: string;
  expires_at: string;
  trace: DecisionTrace;
  // Set by the backend when an approval given since the question was asked would now break the budget. The customer still decides.
  approval_warning?: string | null;
}

export interface HardRule {
  field: string;
  operator: string;
  value: string | number | boolean;
  currency?: string;
  scope?: string;
}

export interface PolicyDraft {
  draft_id?: string;
  mandate_id?: string;
  status: "compiled" | "draft" | "active" | "revoked";
  instruction: string;
  hard_rules: HardRule[];
  uncertainty_policy: "ask" | "decline" | "approve";
  guidance: string[];
  open_questions: string[];
  checks: PolicyCheck[];
}

export interface PolicyCheck {
  label: string;
  detail: string;
  stored_with_platform: boolean;
}

export interface Scenario {
  scenario_id: string;
  name: string;
  purchase_count: number;
}

export interface EngineStatus {
  connected: boolean;
  environment: "mock" | "offline" | "live";
  engine_mode: "deterministic" | "model" | "degraded";
  llm_mode: LlmServiceMode;
  model_name: string | null;
  latest_margin_ms: number | null;
  active_run_id: string | null;
  scenarios: Scenario[];
}

export type StreamEvent =
  | { type: "decision"; data: DecisionTrace }
  | { type: "pending"; data: PendingAuthorization }
  | { type: "resolution"; data: DecisionTrace }
  | { type: "status"; data: EngineStatus };

export interface DecisionRecord {
  live_authorization_id: string;
  run_id: string;
  source_authorization_id: string | null;
  scenario_id: string | null;
  replay_order: number | null;
  mandate_id: string | null;
  sim_timestamp: string | null;
  amount_chf: string | null;
  currency: string | null;
  merchant_id: string | null;
  merchant_name: string | null;
  decision: Decision;
  status: "approved" | "declined" | "pending" | "expired" | "cancelled";
  reason_codes: string[];
  customer_message: string;
  notes: string[];
  received_at: string;
  decided_at: string;
  deadline_at: string;
  margin_ms: number;
  total_ms: number;
  human_deadline_at: string | null;
  trace: DecisionTrace | null;
  problems: string[];
  resolution: Record<string, unknown> | null;
  trust_score?: TrustScore | null;
  approval_warning?: string | null;
}
