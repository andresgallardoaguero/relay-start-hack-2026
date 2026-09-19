import { describe, expect, it } from "vitest";
import { normalizeCurrentPolicy, normalizeStatus, pendingFromRecord, traceFromRecord } from "./api";
import { mockPolicy } from "./mockData";
import type { DecisionRecord, DecisionTrace, TrustScore } from "./types";

const trust: TrustScore = {
  version: "1",
  score: 60,
  band: "review",
  label: "Needs your decision",
  summary: "Per order limit asked for your decision (small overshoot).",
  scale: { start: 100, trusted_from: 70, review_from: 35 },
  deductions: [
    { kind: "leading_finding", guard_id: "per_order_limit", family: "Spending limits", verdict: "STEP_UP", reason_code: "SMALL_OVERSHOOT", signal_name: null, points: 40, capped: false, detail: "Per order limit asked for your decision (small overshoot)" },
  ],
  families: [],
  coverage: { checks_total: 23, checks_evaluated: 5, checks_not_applicable: 0, checks_not_built: 18, checks_failed: 0, coverage_share: 0.217 },
  basis: { decision: "step_up", uncertainty_policy: "ask", engine_version: "relay-0.1.0", trace_version: "1", llm_mode: "off" },
};

const trace: DecisionTrace = {
  trace_version: "1",
  engine_version: "relay-0.1.0",
  ids: { authorization_id: "LA-1", source_authorization_id: "AU0004", request_id: "REQ-1", mandate_id: "TM-1", scenario_id: "SCEN0001" },
  received_at: "2026-09-19T07:00:00Z",
  decided_at: "2026-09-19T07:00:01Z",
  deadline_at: "2026-09-19T07:00:08Z",
  margin_ms: 7000,
  decision: "step_up",
  reason_codes: ["SMALL_OVERSHOOT"],
  customer_message: "This order is slightly above your limit. Approve it?",
  notes: [],
  facts: {
    amounts: { amount: 22, currency: "CHF", billing_amount_chf: 22 },
    merchant: { merchant_id: "ME-1", merchant_name: "Alpine Basket", merchant_category: "groceries", merchant_country: "CH" },
    familiarity: null,
    extracted_item_facts: null,
  },
  guards: [],
  aggregation: { initial: "approve", final: "step_up", raised_by: ["per_order_limit"], uncertainty_policy_applied: false },
  llm: { mode: "off", calls: [] },
  timings: { total_ms: 1.2 },
  resolution: null,
};

const record: DecisionRecord = {
  live_authorization_id: "LA-1",
  run_id: "RUN-1",
  source_authorization_id: "AU0004",
  scenario_id: "SCEN0001",
  replay_order: 4,
  mandate_id: "TM-1",
  sim_timestamp: "2026-08-09T10:00:00Z",
  amount_chf: "22.00",
  currency: "CHF",
  merchant_id: "ME-1",
  merchant_name: "Alpine Basket",
  decision: "step_up",
  status: "pending",
  reason_codes: ["SMALL_OVERSHOOT"],
  customer_message: trace.customer_message,
  notes: [],
  received_at: trace.received_at,
  decided_at: trace.decided_at,
  deadline_at: trace.deadline_at,
  margin_ms: 7000,
  total_ms: 1.2,
  human_deadline_at: "2026-09-19T07:02:00Z",
  trace,
  problems: [],
  resolution: null,
};

describe("Jonas web API adapters", () => {
  it("uses the human deadline for the visible 120 second countdown", () => {
    const pending = pendingFromRecord(record);
    expect(pending.authorization_id).toBe("LA-1");
    expect(pending.expires_at).toBe("2026-09-19T07:02:00Z");
    expect(pending.trace.customer_message).toMatch(/Approve/);
  });

  it("keeps the full trace and can safely display an invalid event without one", () => {
    expect(traceFromRecord(record).guards).toEqual([]);
    const invalid = traceFromRecord({ ...record, trace: null, reason_codes: ["INVALID_EVENT"], merchant_name: null, amount_chf: null });
    expect(invalid.ids.authorization_id).toBe("LA-1");
    expect(invalid.facts.merchant.merchant_name).toBe("Unknown merchant");
    expect(invalid.reason_codes).toEqual(["INVALID_EVENT"]);
  });

  it("carries the budget warning of an open question to its card", () => {
    const warning = "Since this question was asked you approved another order. This one would now bring your spending over 7 days to CHF 389.00, above your budget of CHF 300.00.";
    expect(pendingFromRecord({ ...record, approval_warning: warning }).approval_warning).toBe(warning);
    expect(pendingFromRecord(record).approval_warning).toBeNull();
  });

  it("shows the customer's answer as the outcome once a question is resolved", () => {
    const answered = traceFromRecord({ ...record, status: "approved", resolution: { decision: "approve", resolved_at: "2026-09-19T07:01:30Z" } });
    expect(answered.decision).toBe("approve");
    expect(answered.reason_codes).toEqual(["SMALL_OVERSHOOT"]);
    expect(answered.resolution).toEqual({ decision: "approve", resolved_at: "2026-09-19T07:01:30Z" });
    expect(traceFromRecord({ ...record, status: "declined" }).decision).toBe("decline");
    expect(traceFromRecord({ ...record, status: "expired" }).decision).toBe("step_up");
    expect(traceFromRecord({ ...record, trace: null, status: "approved" }).decision).toBe("approve");
  });

  it("carries the backend's trust score into the screen trace, also without a trace", () => {
    expect(traceFromRecord({ ...record, trust_score: trust }).trust).toEqual(trust);
    expect(traceFromRecord(record).trust).toBeNull();
    expect(traceFromRecord({ ...record, trace: null, trust_score: trust }).trust).toEqual(trust);
    expect(pendingFromRecord({ ...record, trust_score: trust }).trace.trust?.score).toBe(60);
  });

  it("reads scenarios and the deadline margin from the nested status response", () => {
    const status = normalizeStatus({
      leash_mode: "offline",
      engine_mode: "deterministic",
      llm_mode: "off",
      llm_model: null,
      bootstrap: { scenarios: [{ scenario_id: "SCEN0001", name: "Groceries", event_count: 10 }] },
      bootstrap_error: null,
      worker: { state: "polling", last_margin_ms: 7123, active_run_ids: ["RUN-1"] },
    });
    expect(status.connected).toBe(true);
    expect(status.environment).toBe("offline");
    expect(status.llm_mode).toBe("off");
    expect(status.latest_margin_ms).toBe(7123);
    expect(status.scenarios).toEqual([{ scenario_id: "SCEN0001", name: "Groceries", purchase_count: 10 }]);
  });

  it("keeps live and degraded model modes from backend decision traces", () => {
    expect(traceFromRecord({ ...record, trace: { ...trace, llm: { mode: "live", calls: [{ status: "success" }] } } }).llm.mode).toBe("live");
    expect(traceFromRecord({ ...record, trace: { ...trace, llm: { mode: "degraded", calls: [{ status: "timeout" }] } } }).llm.mode).toBe("degraded");
  });

  it("restores a confirmed mandate and its compiled checks", () => {
    const restored = normalizeCurrentPolicy({
      mandate: {
        mandate_id: "TM-1",
        draft_id: "TD-1",
        status: "active",
        instruction: mockPolicy.instruction,
        hard_rules: mockPolicy.hard_rules,
        uncertainty_policy: "ask",
      },
      policy: {
        instruction: mockPolicy.instruction,
        hard_rules: mockPolicy.hard_rules,
        uncertainty_policy: "ask",
        open_questions: mockPolicy.open_questions,
        checks: mockPolicy.checks,
      },
      policy_error: null,
    });
    expect(restored?.mandate_id).toBe("TM-1");
    expect(restored?.status).toBe("active");
    expect(restored?.checks).toEqual(mockPolicy.checks);
  });
});
