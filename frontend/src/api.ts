import { mockDecisions, mockPending, mockPolicy, mockStatus } from "./mockData";
import type {
  Decision,
  DecisionTrace,
  DecisionRecord,
  EngineStatus,
  LlmServiceMode,
  PendingAuthorization,
  PolicyDraft,
  StreamEvent,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
export const USE_MOCK_API = import.meta.env.VITE_USE_MOCK_API !== "false";

const wait = (milliseconds = 240) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new Error(body?.error?.message || `Request failed with status ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

interface BackendStatus {
  leash_mode: "offline" | "live";
  engine_mode: "deterministic" | "model" | "degraded";
  llm_mode: LlmServiceMode;
  llm_model: string | null;
  bootstrap: { scenarios?: Array<Record<string, unknown>> } | null;
  bootstrap_error: string | null;
  worker: { state: string; last_margin_ms: number | null; active_run_ids: string[] };
}

interface CurrentPolicyResponse {
  mandate: {
    mandate_id: string;
    draft_id?: string;
    status: "active" | "revoked";
    instruction: string;
    hard_rules: PolicyDraft["hard_rules"];
    uncertainty_policy: PolicyDraft["uncertainty_policy"];
  };
  policy: Omit<PolicyDraft, "draft_id" | "mandate_id" | "status" | "guidance"> | null;
  policy_error: string | null;
}

export function normalizeCurrentPolicy(response: CurrentPolicyResponse): PolicyDraft | null {
  if (response.policy === null) return null;
  return {
    ...response.policy,
    draft_id: response.mandate.draft_id,
    mandate_id: response.mandate.mandate_id,
    status: response.mandate.status,
    instruction: response.mandate.instruction,
    hard_rules: response.mandate.hard_rules,
    uncertainty_policy: response.mandate.uncertainty_policy,
    guidance: [],
  };
}

function normalizeScenario(raw: Record<string, unknown>) {
  return {
    scenario_id: String(raw.scenario_id ?? raw.id ?? ""),
    name: String(raw.name ?? raw.title ?? raw.scenario_id ?? "Scenario"),
    purchase_count: Number(raw.purchase_count ?? raw.event_count ?? raw.authorization_count ?? 0),
  };
}

export function normalizeStatus(raw: BackendStatus): EngineStatus {
  return {
    connected: raw.bootstrap_error === null,
    environment: raw.leash_mode,
    engine_mode: raw.engine_mode,
    llm_mode: raw.llm_mode,
    model_name: raw.llm_mode === "off" ? null : raw.llm_model,
    latest_margin_ms: raw.worker.last_margin_ms,
    active_run_id: raw.worker.active_run_ids[0] ?? null,
    scenarios: (raw.bootstrap?.scenarios ?? []).map(normalizeScenario).filter((scenario) => scenario.scenario_id !== ""),
  };
}

function fallbackTrace(record: DecisionRecord): DecisionTrace {
  const amount = Number(record.amount_chf ?? 0);
  return {
    trace_version: "1",
    engine_version: "relay",
    ids: {
      authorization_id: record.live_authorization_id,
      source_authorization_id: record.source_authorization_id ?? "unknown",
      request_id: "unknown",
      mandate_id: record.mandate_id ?? "unknown",
      scenario_id: record.scenario_id ?? "unknown",
    },
    received_at: record.received_at,
    decided_at: record.decided_at,
    deadline_at: record.deadline_at,
    margin_ms: record.margin_ms,
    decision: record.decision,
    reason_codes: record.reason_codes,
    customer_message: record.customer_message,
    notes: record.notes,
    facts: {
      amounts: { amount, currency: record.currency ?? "CHF", billing_amount_chf: amount },
      merchant: {
        merchant_id: record.merchant_id ?? "unknown",
        merchant_name: record.merchant_name ?? "Unknown merchant",
        merchant_category: "unknown",
        merchant_country: "unknown",
      },
      familiarity: null,
      extracted_item_facts: null,
    },
    guards: [],
    aggregation: { initial: "approve", final: record.decision, raised_by: [], uncertainty_policy_applied: false },
    llm: { mode: "off", calls: [] },
    timings: { total_ms: record.total_ms },
    resolution: record.resolution,
    trust: record.trust_score ?? null,
  };
}

// The store status of a record is its outcome: a question the customer answered shows the answer, not the engine's step_up.
const outcomeOf = (record: DecisionRecord): Decision =>
  record.status === "approved" ? "approve" : record.status === "declined" ? "decline" : record.decision;

export function traceFromRecord(record: DecisionRecord) {
  const decision = outcomeOf(record);
  return record.trace
    ? { ...record.trace, decision, resolution: record.resolution, trust: record.trust_score ?? null }
    : fallbackTrace({ ...record, decision });
}

export function pendingFromRecord(record: DecisionRecord): PendingAuthorization {
  return {
    authorization_id: record.live_authorization_id,
    created_at: record.received_at,
    expires_at: record.human_deadline_at ?? record.deadline_at,
    trace: traceFromRecord(record),
    approval_warning: record.approval_warning ?? null,
  };
}

export const api = {
  async currentPolicy(): Promise<PolicyDraft | null> {
    if (USE_MOCK_API) return null;
    const response = await fetch(`${API_BASE_URL}/api/policy`, { headers: { "Content-Type": "application/json" } });
    if (response.status === 404) return null;
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
      throw new Error(body?.error?.message || `Request failed with status ${response.status}`);
    }
    return normalizeCurrentPolicy(await response.json() as CurrentPolicyResponse);
  },

  async status(): Promise<EngineStatus> {
    if (USE_MOCK_API) {
      await wait();
      return structuredClone(mockStatus);
    }
    return normalizeStatus(await request<BackendStatus>("/api/status"));
  },

  async decisions(): Promise<DecisionTrace[]> {
    if (USE_MOCK_API) {
      await wait();
      return structuredClone(mockDecisions);
    }
    const response = await request<{ decisions: DecisionRecord[] }>("/api/decisions");
    return response.decisions.map(traceFromRecord).reverse();
  },

  async pending(): Promise<PendingAuthorization[]> {
    if (USE_MOCK_API) {
      await wait();
      return structuredClone(mockPending);
    }
    const response = await request<{ pending: DecisionRecord[] }>("/api/pending");
    return response.pending.map(pendingFromRecord);
  },

  async compilePolicy(instruction: string): Promise<PolicyDraft> {
    if (USE_MOCK_API) {
      await wait(500);
      const amountMatch = instruction.match(/(?:CHF\s*)?(\d+(?:\.\d{1,2})?)/i);
      const limit = amountMatch ? Number(amountMatch[1]) : null;
      return {
        ...structuredClone(mockPolicy),
        draft_id: undefined,
        mandate_id: undefined,
        status: "compiled",
        instruction,
        hard_rules: limit
          ? [{ field: "authorization.billing_amount_chf", operator: "<=", value: limit, currency: "CHF", scope: "purchase" }]
          : [],
        open_questions: limit ? [] : ["What is the maximum amount for one purchase?"],
      };
    }
    const compiled = await request<Omit<PolicyDraft, "status" | "guidance">>("/api/policy/compile", {
      method: "POST",
      body: JSON.stringify({ instruction, uncertainty_policy: "ask", hard_rules: [] }),
    });
    return { ...compiled, status: "compiled", guidance: [] };
  },

  async confirmPolicy(policy: PolicyDraft): Promise<PolicyDraft> {
    if (USE_MOCK_API) {
      await wait();
      return { ...structuredClone(policy), draft_id: `TD-MOCK-${Date.now()}`, mandate_id: "TM-DEMO-0001", status: "active" };
    }
    const draft = await request<{ draft_id: string }>("/api/policy/draft", {
      method: "POST",
      body: JSON.stringify({
        instruction: policy.instruction,
        hard_rules: policy.hard_rules,
        uncertainty_policy: policy.uncertainty_policy,
        guidance: policy.guidance,
        open_questions: policy.open_questions,
      }),
    });
    const mandate = await request<Omit<PolicyDraft, "draft_id" | "checks" | "guidance" | "open_questions">>("/api/policy/confirm", {
      method: "POST",
      body: JSON.stringify({ draft_id: draft.draft_id }),
    });
    return { ...policy, ...mandate, draft_id: draft.draft_id, status: "active" };
  },

  async tightenPolicy(policy: PolicyDraft): Promise<PolicyDraft> {
    if (USE_MOCK_API) {
      await wait();
      return { ...structuredClone(policy), uncertainty_policy: "decline" };
    }
    const mandate = await request<Partial<PolicyDraft>>("/api/policy", {
      method: "PATCH",
      body: JSON.stringify({ add_rules: [], uncertainty_policy: "decline" }),
    });
    return { ...policy, ...mandate, status: "active", uncertainty_policy: "decline" };
  },

  async revokePolicy(): Promise<void> {
    if (USE_MOCK_API) return wait();
    return request("/api/policy", { method: "DELETE" });
  },

  async startRun(scenarioId: string, mandateId: string): Promise<{ run_id: string }> {
    if (USE_MOCK_API) {
      await wait();
      return { run_id: `RUN-${scenarioId}-MOCK` };
    }
    return request("/api/run/start", {
      method: "POST",
      body: JSON.stringify({ scenario_id: scenarioId, mandate_id: mandateId }),
    });
  },

  // The answer always goes through. The backend hands back the record, whose resolution may carry a budget warning to show the customer.
  async resolve(authorizationId: string, decision: "approve" | "decline"): Promise<string | null> {
    if (USE_MOCK_API) { await wait(); return null; }
    const record = await request<DecisionRecord>(`/api/resolve/${authorizationId}`, {
      method: "POST",
      body: JSON.stringify({ decision, customer_message: null }),
    });
    const warning = record.resolution?.budget_warning;
    return typeof warning === "string" && warning !== "" ? warning : null;
  },
};

export function connectEventStream(onEvent: (event: StreamEvent) => void, onState: (connected: boolean) => void, onReconnect: () => void) {
  if (USE_MOCK_API) {
    onState(true);
    return () => undefined;
  }

  const eventSource = new EventSource(`${API_BASE_URL}/api/stream`);
  const parseRecord = (message: MessageEvent<string>) => JSON.parse(message.data) as DecisionRecord;
  const handleStatus = (message: MessageEvent<string>) => {
    try {
      onEvent({ type: "status", data: normalizeStatus(JSON.parse(message.data) as BackendStatus) });
    } catch {
      // A malformed event is ignored; reconnect reloads authoritative state.
    }
  };
  const handleDecision = (message: MessageEvent<string>) => {
    try {
      const record = parseRecord(message);
      onEvent({ type: "decision", data: traceFromRecord(record) });
      if (record.status === "pending") onEvent({ type: "pending", data: pendingFromRecord(record) });
    } catch { /* reconnect reloads authoritative state */ }
  };
  const handleResolution = (message: MessageEvent<string>) => {
    try { onEvent({ type: "resolution", data: traceFromRecord(parseRecord(message)) }); }
    catch { /* reconnect reloads authoritative state */ }
  };
  eventSource.addEventListener("status", handleStatus as EventListener);
  eventSource.addEventListener("decision", handleDecision as EventListener);
  eventSource.addEventListener("resolution", handleResolution as EventListener);
  eventSource.onopen = () => { onState(true); onReconnect(); };
  eventSource.onerror = () => onState(false);
  return () => eventSource.close();
}
