import type {
  Decision,
  DecisionTrace,
  EngineStatus,
  GuardFamily,
  GuardResult,
  GuardVerdict,
  PendingAuthorization,
  PolicyDraft,
  TrustDeduction,
  TrustScore,
} from "./types";

const families: GuardFamily[] = [
  "Spending limits",
  "Item and terms",
  "Seller",
  "Session",
  "Repeats and manipulation",
];

const representativeGuards = (
  decisiveFamily: GuardFamily,
  verdict: GuardVerdict,
  reason: string | null,
  amount: number,
  threshold: number,
): GuardResult[] =>
  families.flatMap((family, familyIndex) => {
    const count = family === "Spending limits" ? 3 : 2;
    return Array.from({ length: count }, (_, index) => {
      const isDecisive = family === decisiveFamily && index === 0;
      return {
        guard_number: familyIndex * 4 + index + 1,
        guard_id: isDecisive
          ? reason?.toLowerCase() ?? "policy_check"
          : `${family.toLowerCase().replaceAll(" ", "_")}_${index + 1}`,
        family,
        verdict: isDecisive ? verdict : index === 0 ? "PASS" : "SKIP",
        reason_code: isDecisive ? reason : index === 0 ? null : "GUARD_NOT_BUILT",
        evidence: isDecisive
          ? [
              {
                fact: "billing_amount_chf",
                value: amount,
                comparator: amount > threshold ? ">" : "<=",
                threshold,
                source: "authorization against confirmed policy",
              },
            ]
          : [],
        signal: null,
        note: null,
        customer_message: null,
        elapsed_ms: Number((0.05 + familyIndex * 0.017 + index * 0.009).toFixed(3)),
      };
    });
  });

const verdictStrictness: Record<GuardVerdict, number> = { DECLINE: 4, STEP_UP: 3, UNCERTAIN: 2, PASS: 1, SKIP: 0 };

// A fixture of the backend's trust score, shaped like the record the web API returns. The backend computes real ones.
const mockTrust = (guards: GuardResult[], decision: Decision, summary: string, deductions: TrustDeduction[]): TrustScore => {
  const score = 100 - deductions.reduce((sum, deduction) => sum + deduction.points, 0);
  const band = decision === "approve" ? "trusted" : decision === "step_up" ? "review" : "blocked";
  return {
    version: "1",
    score,
    band,
    label: band === "trusted" ? "Trusted" : band === "review" ? "Needs your decision" : "Blocked",
    summary,
    scale: { start: 100, trusted_from: 70, review_from: 35 },
    deductions,
    families: families.map((family) => ({
      family,
      strictest_verdict: guards
        .filter((guard) => guard.family === family)
        .reduce<GuardVerdict>((strictest, guard) => (verdictStrictness[guard.verdict] > verdictStrictness[strictest] ? guard.verdict : strictest), "SKIP"),
      points_lost: deductions.filter((deduction) => deduction.family === family).reduce((sum, deduction) => sum + deduction.points, 0),
    })),
    coverage: {
      checks_total: guards.length,
      checks_evaluated: guards.filter((guard) => guard.verdict !== "SKIP").length,
      checks_not_applicable: 0,
      checks_not_built: guards.filter((guard) => guard.verdict === "SKIP").length,
      checks_failed: 0,
      coverage_share: Number((guards.filter((guard) => guard.verdict !== "SKIP").length / guards.length).toFixed(3)),
    },
    basis: { decision, uncertainty_policy: "ask", engine_version: "relay-0.1.0", trace_version: "1", llm_mode: "off" },
  };
};

function trace(
  authorizationId: string,
  sourceId: string,
  merchant: string,
  category: string,
  amount: number,
  decision: Decision,
  message: string,
  family: GuardFamily,
  guardVerdict: GuardVerdict,
  reason: string | null,
  threshold: number,
  minuteOffset: number,
  trustSummary: string,
  trustDeductions: TrustDeduction[],
): DecisionTrace {
  const decidedAt = new Date(Date.now() - minuteOffset * 60_000);
  const guards = representativeGuards(family, guardVerdict, reason, amount, threshold);
  const llmMode = decision === "decline" ? "live" : decision === "step_up" ? "degraded" : "off";
  return {
    trace_version: "1",
    engine_version: "relay-0.1.0",
    ids: {
      authorization_id: authorizationId,
      source_authorization_id: sourceId,
      request_id: `req_${authorizationId.toLowerCase()}`,
      mandate_id: "TM-DEMO-0001",
      scenario_id: "SCEN0001",
    },
    received_at: new Date(decidedAt.getTime() - 34).toISOString(),
    decided_at: decidedAt.toISOString(),
    deadline_at: new Date(decidedAt.getTime() + 7_824).toISOString(),
    margin_ms: 7824,
    decision,
    reason_codes: reason ? [reason] : [],
    customer_message: message,
    notes: [],
    facts: {
      amounts: { amount, currency: "CHF", billing_amount_chf: amount },
      merchant: {
        merchant_id: `ME-${authorizationId.slice(-4)}`,
        merchant_name: merchant,
        merchant_category: category,
        merchant_country: "CH",
      },
      familiarity: null,
      extracted_item_facts: null,
    },
    guards,
    aggregation: {
      initial: "approve",
      final: decision,
      raised_by: reason ? [reason.toLowerCase()] : [],
      uncertainty_policy_applied: guardVerdict === "UNCERTAIN",
    },
    llm: {
      mode: llmMode,
      calls: llmMode === "off" ? [] : [{
        purpose: "fact_extraction",
        status: llmMode === "live" ? "success" : "provider_error",
        provider: "swisscom-apertus",
        model: "Apertus",
        elapsed_ms: llmMode === "live" ? 418 : 2010,
      }],
    },
    timings: { total_ms: 1.176 },
    resolution: null,
    trust: mockTrust(guards, decision, trustSummary, trustDeductions),
  };
}

export const mockDecisions: DecisionTrace[] = [
  trace(
    "LA-DEMO-0041",
    "AU0041",
    "Giftly Market",
    "gift_card",
    400,
    "decline",
    "Declined. This purchase is above your CHF 250 limit.",
    "Spending limits",
    "DECLINE",
    "OVER_PER_ORDER_LIMIT",
    250,
    2,
    "Per order limit refused this purchase (over per order limit).",
    [
      {
        kind: "leading_finding",
        guard_id: "over_per_order_limit",
        family: "Spending limits",
        verdict: "DECLINE",
        reason_code: "OVER_PER_ORDER_LIMIT",
        signal_name: null,
        points: 70,
        capped: false,
        detail: "Per order limit refused this purchase (over per order limit)",
      },
    ],
  ),
  trace(
    "LA-DEMO-0034",
    "AU0034",
    "RainThread",
    "clothing",
    138,
    "step_up",
    "RainThread is new to you. Approve this seller?",
    "Seller",
    "UNCERTAIN",
    "UNFAMILIAR_MERCHANT",
    120,
    5,
    "Merchant familiarity could not be settled, and your policy asks in doubt (unfamiliar merchant).",
    [
      {
        kind: "leading_finding",
        guard_id: "unfamiliar_merchant",
        family: "Seller",
        verdict: "UNCERTAIN",
        reason_code: "UNFAMILIAR_MERCHANT",
        signal_name: null,
        points: 40,
        capped: false,
        detail: "Merchant familiarity could not be settled, and your policy asks in doubt (unfamiliar merchant)",
      },
    ],
  ),
  trace(
    "LA-DEMO-0001",
    "AU0001",
    "Alpine Basket",
    "groceries",
    20,
    "approve",
    "Approved. This purchase is within your instruction.",
    "Spending limits",
    "PASS",
    null,
    20,
    9,
    "All 5 evaluated checks passed.",
    [],
  ),
];

export const mockPending: PendingAuthorization[] = [
  {
    authorization_id: mockDecisions[1].ids.authorization_id,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 120_000).toISOString(),
    trace: mockDecisions[1],
  },
];

export const mockStatus: EngineStatus = {
  connected: true,
  environment: "mock",
  engine_mode: "deterministic",
  llm_mode: "ready",
  model_name: "Apertus",
  latest_margin_ms: 7824,
  active_run_id: "RUN-DEMO-0001",
  scenarios: [
    { scenario_id: "SCEN0000", name: "Connection check", purchase_count: 1 },
    { scenario_id: "SCEN0001", name: "Everyday groceries", purchase_count: 10 },
    { scenario_id: "SCEN0002", name: "Running shoes", purchase_count: 9 },
    { scenario_id: "SCEN0003", name: "Travel session", purchase_count: 12 },
    { scenario_id: "SCEN0004", name: "Monitor replacement", purchase_count: 13 },
  ],
};

export const mockPolicy: PolicyDraft = {
  draft_id: "TD-DEMO-0001",
  mandate_id: "TM-DEMO-0001",
  status: "active",
  instruction:
    "Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. Ask me when uncertain.",
  hard_rules: [
    {
      field: "authorization.billing_amount_chf",
      operator: "<=",
      value: 20,
      currency: "CHF",
      scope: "purchase",
    },
  ],
  uncertainty_policy: "ask",
  guidance: ["Use a shop the customer uses regularly."],
  open_questions: ["Ask the customer when seller familiarity cannot be established."],
  checks: [
    { label: "Limit per order", detail: "Each order at most CHF 20.00, delivery included.", stored_with_platform: true },
    { label: "Shop familiarity", detail: "Use a shop the customer uses regularly.", stored_with_platform: false },
  ],
};
