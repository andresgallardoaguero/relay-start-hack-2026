import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  Inbox,
  LayoutList,
  LockKeyhole,
  Menu,
  RefreshCw,
  ShieldCheck,
  Store,
  TriangleAlert,
  UserRoundCheck,
  X,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, USE_MOCK_API } from "./api";
import { TrustBar, TrustBreakdown, TrustGauge } from "./TrustGauge";
import visecaWordmark from "../../data/raw/2026-09-18_viseca-2026/assets/logos/viseca-dark.png";
import type {
  Decision,
  DecisionTrace,
  EngineStatus,
  GuardFamily,
  GuardResult,
  GuardVerdict,
  PendingAuthorization,
  PolicyDraft,
  StreamEvent,
} from "./types";
import { useEventStream } from "./useEventStream";

type Screen = "policy" | "inbox" | "log";
type Notice = { tone: "success" | "error"; message: string } | null;

const FAMILY_ORDER: GuardFamily[] = [
  "Spending limits",
  "Item and terms",
  "Seller",
  "Session",
  "Repeats and manipulation",
];

const NAV_ITEMS: { id: Screen; label: string; icon: typeof FileCheck2 }[] = [
  { id: "policy", label: "Policy", icon: FileCheck2 },
  { id: "inbox", label: "Approval inbox", icon: Inbox },
  { id: "log", label: "Decision log", icon: LayoutList },
];

const severity: Record<GuardVerdict, number> = {
  SKIP: 0,
  PASS: 1,
  UNCERTAIN: 2,
  STEP_UP: 3,
  DECLINE: 4,
};

const friendlyGuardName = (guardId: string) =>
  guardId
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

const money = (amount: number) =>
  new Intl.NumberFormat("en-CH", { style: "currency", currency: "CHF" }).format(amount);

const time = (value: string) =>
  new Intl.DateTimeFormat("en-CH", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
    new Date(value),
  );

const familyVerdict = (guards: GuardResult[], family: GuardFamily): GuardVerdict =>
  guards
    .filter((guard) => guard.family === family)
    .reduce<GuardVerdict>(
      (strictest, guard) => (severity[guard.verdict] > severity[strictest] ? guard.verdict : strictest),
      "SKIP",
    );

const decisionLabel: Record<Decision, string> = {
  approve: "Approved",
  decline: "Declined",
  step_up: "Needs review",
};

function StatusPill({ verdict, label }: { verdict: GuardVerdict | Decision; label?: string }) {
  const normalized = verdict.toLowerCase().replace("_", "-");
  return <span className={`status-pill status-${normalized}`}>{label ?? verdict.replace("_", " ")}</span>;
}

function LoadingRows() {
  return (
    <div className="loading-stack" aria-label="Loading">
      <div className="skeleton skeleton-wide" />
      <div className="skeleton" />
      <div className="skeleton skeleton-short" />
    </div>
  );
}

function EmptyState({ icon: Icon, title, detail }: { icon: typeof Inbox; title: string; detail: string }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={22} /></span>
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}

function PolicyScreen({ status, restoredPolicy, onRunStarted }: { status: EngineStatus | null; restoredPolicy: PolicyDraft | null; onRunStarted: () => void }) {
  const [instruction, setInstruction] = useState(
    restoredPolicy?.instruction ?? "Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. Ask me when uncertain.",
  );
  const [policy, setPolicy] = useState<PolicyDraft | null>(restoredPolicy);
  const [scenarioId, setScenarioId] = useState("SCEN0000");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [showRevoke, setShowRevoke] = useState(false);
  const [liveRunConfirmed, setLiveRunConfirmed] = useState(false);

  useEffect(() => {
    if (restoredPolicy) {
      setInstruction(restoredPolicy.instruction);
      setPolicy(restoredPolicy);
    }
  }, [restoredPolicy]);

  async function runAction(key: string, action: () => Promise<void>) {
    setBusy(key);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "Something went wrong." });
    } finally {
      setBusy(null);
    }
  }

  const compile = () =>
    runAction("compile", async () => {
      const result = await api.compilePolicy(instruction.trim());
      setPolicy(result);
      setNotice({ tone: "success", message: "Policy translated into checks. Review it before confirming." });
    });

  const confirm = () =>
    policy &&
    runAction("confirm", async () => {
      const result = await api.confirmPolicy(policy);
      setPolicy(result);
      setNotice({ tone: "success", message: "Policy confirmed and ready to protect purchases." });
    });

  const tighten = () =>
    policy?.mandate_id &&
    runAction("tighten", async () => {
      const result = await api.tightenPolicy(policy);
      setPolicy(result);
      setNotice({ tone: "success", message: "Uncertain purchases will now be declined." });
    });

  const revoke = () =>
    policy?.mandate_id &&
    runAction("revoke", async () => {
      await api.revokePolicy();
      setPolicy({ ...policy, status: "revoked" });
      setShowRevoke(false);
      setNotice({ tone: "success", message: "Policy revoked. No new runs can use it." });
    });

  const startRun = () =>
    policy?.mandate_id &&
    runAction("run", async () => {
      const result = await api.startRun(scenarioId, policy.mandate_id!);
      setNotice({ tone: "success", message: `Run ${result.run_id} started. New decisions will appear in the log.` });
      onRunStarted();
    });

  return (
    <main className="screen-grid policy-grid">
      <section className="page-heading full-span">
        <div>
          <p className="eyebrow">Wallet policy</p>
          <h1>Set the boundaries. Relay handles the rest.</h1>
          <p>Describe what your shopping agent may do. You approve every check before it becomes active.</p>
        </div>
        {policy && <StatusPill verdict={policy.status === "active" ? "approve" : policy.status === "revoked" ? "decline" : "step_up"} label={policy.status} />}
      </section>

      <section className="panel compose-panel">
        <div className="panel-header">
          <div>
            <span className="step-number">1</span>
            <h2>Describe the purchase</h2>
          </div>
          <span className="privacy-label"><LockKeyhole size={14} /> Private</span>
        </div>
        <label className="sr-only" htmlFor="instruction">Purchase instruction</label>
        <textarea
          id="instruction"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          rows={7}
          placeholder="For example: Buy running shoes for up to CHF 200…"
        />
        <div className="compose-footer">
          <span>{instruction.length} characters</span>
          <button className="button button-primary" disabled={!instruction.trim() || busy !== null} onClick={compile}>
            {busy === "compile" ? <RefreshCw className="spin" size={17} /> : <ArrowRight size={17} />}
            Build policy
          </button>
        </div>
      </section>

      <section className="panel review-panel">
        <div className="panel-header">
          <div>
            <span className="step-number">2</span>
            <h2>Review the checks</h2>
          </div>
        </div>
        {!policy ? (
          <EmptyState icon={ShieldCheck} title="No checks yet" detail="Build the policy to see exactly what Relay will enforce." />
        ) : (
          <div className="policy-review">
            <div className="rule-group">
              <p className="group-label"><ShieldCheck size={16} /> Stored with platform</p>
              {policy.checks.filter((check) => check.stored_with_platform).map((check) => (
                <div className="rule-row" key={`platform-${check.label}`}>
                  <span className="rule-check"><Check size={15} /></span>
                  <div><strong>{check.label}</strong><p>{check.detail}</p></div>
                </div>
              ))}
            </div>
            <div className="rule-group">
              <p className="group-label"><ShieldCheck size={16} /> Additionally enforced by engine</p>
              {policy.checks.filter((check) => !check.stored_with_platform).map((check) => (
                <div className="rule-row" key={`engine-${check.label}`}>
                  <span className="rule-check"><Check size={15} /></span>
                  <div><strong>{check.label}</strong><p>{check.detail}</p></div>
                </div>
              ))}
            </div>
            <div className="rule-group questions">
              <p className="group-label"><UserRoundCheck size={16} /> When Relay will ask</p>
              {policy.open_questions.length ? policy.open_questions.map((question) => (
                <div className="question-row" key={question}><AlertTriangle size={17} /><span>{question}</span></div>
              )) : <div className="question-row calm"><CheckCircle2 size={17} /><span>No unresolved questions.</span></div>}
            </div>
            {(policy.status === "compiled" || policy.status === "draft") && (
              <button className="button button-primary button-full" disabled={busy !== null} onClick={confirm}>
                {busy === "confirm" ? <RefreshCw className="spin" size={17} /> : <ShieldCheck size={17} />}
                Confirm policy
              </button>
            )}
          </div>
        )}
      </section>

      {policy?.status === "active" && (
        <section className="panel control-panel full-span">
          <div className="run-control">
            <div><p className="eyebrow">Demo control</p><h2>Start a protected scenario</h2></div>
            <label>
              <span className="sr-only">Scenario</span>
              <select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>
                {status?.scenarios.map((scenario) => (
                  <option key={scenario.scenario_id} value={scenario.scenario_id}>
                    {scenario.scenario_id} · {scenario.name} ({scenario.purchase_count})
                  </option>
                ))}
              </select>
            </label>
            <button className="button button-primary" disabled={busy !== null || (status?.environment === "live" && !liveRunConfirmed)} onClick={startRun}>
              <Activity size={17} /> {busy === "run" ? "Starting…" : status?.environment === "live" ? "Start live run" : "Start offline run"}
            </button>
          </div>
          {status?.environment === "live" && (
            <label className="live-run-confirmation">
              <input type="checkbox" checked={liveRunConfirmed} onChange={(event) => setLiveRunConfirmed(event.target.checked)} />
              I am the designated operator and this run is coordinated with the team.
            </label>
          )}
          <div className="policy-actions">
            <button className="button button-secondary" disabled={busy !== null || policy.uncertainty_policy === "decline"} onClick={tighten}>
              <ShieldCheck size={17} /> {policy.uncertainty_policy === "decline" ? "Strict mode active" : "Tighten policy"}
            </button>
            <button className="button button-danger-ghost" disabled={busy !== null} onClick={() => setShowRevoke(true)}>
              Revoke policy
            </button>
          </div>
        </section>
      )}

      {notice && <div className={`notice notice-${notice.tone} full-span`} role="status">{notice.tone === "success" ? <CheckCircle2 size={17} /> : <XCircle size={17} />}{notice.message}</div>}

      {showRevoke && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowRevoke(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="revoke-title" onMouseDown={(event) => event.stopPropagation()}>
            <span className="modal-icon danger"><TriangleAlert size={23} /></span>
            <h2 id="revoke-title">Revoke this policy?</h2>
            <p>New purchases and runs will no longer be allowed under this mandate. Existing decisions remain in the audit log.</p>
            <div className="modal-actions">
              <button className="button button-secondary" onClick={() => setShowRevoke(false)}>Keep policy</button>
              <button className="button button-danger" onClick={revoke}>Revoke policy</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function Countdown({ expiresAt }: { expiresAt: string }) {
  const [seconds, setSeconds] = useState(() => Math.max(0, Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 1000)));

  useEffect(() => {
    const timer = window.setInterval(() => {
      setSeconds(Math.max(0, Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return <span className={`countdown ${seconds < 30 ? "urgent" : ""}`} aria-label={seconds ? `${seconds} seconds left to answer` : "Answer window expired"}><Clock3 size={18} /><small>{seconds ? "Answer within" : "Answer window"}</small><strong>{seconds ? `${minutes}:${remainder}` : "Expired"}</strong></span>;
}

function InboxScreen({ pending, onResolve }: { pending: PendingAuthorization[]; onResolve: (id: string, decision: "approve" | "decline") => Promise<string | null> }) {
  const [resolving, setResolving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);

  async function resolve(id: string, decision: "approve" | "decline") {
    setResolving(id);
    setError(null);
    setConfirmation(null);
    try {
      const warning = await onResolve(id, decision);
      setConfirmation(`Purchase ${decision === "approve" ? "approved once" : "declined"}. The audit log has been updated.${warning ? ` Note: ${warning}` : ""}`);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not send your answer."); }
    finally { setResolving(null); }
  }

  return (
    <main>
      <section className="page-heading heading-row">
        <div><p className="eyebrow">Approval inbox</p><h1>Purchases waiting for you</h1><p>Relay paused these purchases before any money moved.</p></div>
        <span className="inbox-count">{pending.length} open</span>
      </section>
      {error && <div className="notice notice-error"><XCircle size={17} />{error}</div>}
      {confirmation && <div className="notice notice-success" role="status"><CheckCircle2 size={17} />{confirmation}</div>}
      {!pending.length ? (
        <section className="panel"><EmptyState icon={Inbox} title="You're all caught up" detail="Any purchase that needs your decision will appear here instantly." /></section>
      ) : (
        <section className="pending-grid">
          {pending.map((item) => {
            const trace = item.trace;
            const expired = new Date(item.expires_at).getTime() <= Date.now();
            return (
              <article className="pending-card" key={item.authorization_id}>
                <div className="pending-accent" />
                <div className="pending-topline">
                  <span className="review-label"><AlertTriangle size={15} /> Review requested</span>
                  <Countdown expiresAt={item.expires_at} />
                </div>
                <div className="merchant-lockup">
                  <span className="merchant-mark"><Store size={23} /></span>
                  <div><h2>{trace.facts.merchant.merchant_name}</h2><p>{friendlyGuardName(trace.facts.merchant.merchant_category)}</p></div>
                  <strong>{money(trace.facts.amounts.billing_amount_chf)}</strong>
                </div>
                <div className="customer-question"><UserRoundCheck size={20} /><p>{trace.customer_message}</p></div>
                {trace.trust && (
                  <div className="trust-row">
                    <TrustGauge trust={trace.trust} size={118} />
                    <p>{trace.trust.summary}</p>
                  </div>
                )}
                <div className="reason-strip">
                  <span>Why Relay paused it</span>
                  <strong>{trace.reason_codes.map(friendlyGuardName).join(", ") || "Manual review"}</strong>
                </div>
                {item.approval_warning && (
                  <div className="approval-warning" role="status">
                    <TriangleAlert size={17} />
                    <p>{item.approval_warning} You can still approve it. The decision is yours.</p>
                  </div>
                )}
                <div className="pending-actions">
                  <button className="button button-decline" disabled={expired || resolving !== null} onClick={() => resolve(item.authorization_id, "decline")}><X size={17} /> {resolving === item.authorization_id ? "Sending…" : "Decline"}</button>
                  <button className="button button-approve" disabled={expired || resolving !== null} onClick={() => resolve(item.authorization_id, "approve")}><Check size={17} /> {resolving === item.authorization_id ? "Sending…" : "Approve once"}</button>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}

function Evidence({ guard }: { guard: GuardResult }) {
  if (!guard.evidence.length) return <span className="muted">No additional evidence</span>;
  return (
    <div className="evidence-list">
      {guard.evidence.map((item, index) => (
        <div key={`${item.fact}-${index}`}>
          <strong>{friendlyGuardName(item.fact)}</strong>
          <span>{String(item.value)} {item.comparator ?? ""} {item.threshold !== null ? String(item.threshold) : ""}</span>
        </div>
      ))}
    </div>
  );
}

function DecisionRow({ trace }: { trace: DecisionTrace }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={`decision-row ${open ? "is-open" : ""}`}>
      <button className="decision-summary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={`decision-icon decision-${trace.decision}`}>
          {trace.decision === "approve" ? <Check size={18} /> : trace.decision === "decline" ? <X size={18} /> : <AlertTriangle size={18} />}
        </span>
        <span className="decision-merchant"><strong>{trace.facts.merchant.merchant_name}</strong><small>{time(trace.decided_at)} · {trace.ids.source_authorization_id}</small></span>
        <strong className="decision-amount">{money(trace.facts.amounts.billing_amount_chf)}</strong>
        <TrustBar trust={trace.trust ?? null} />
        <StatusPill verdict={trace.decision} label={decisionLabel[trace.decision]} />
        <span className="latency">{trace.margin_ms >= 1000 ? `${(trace.margin_ms / 1000).toFixed(1)}s margin` : `${Math.round(trace.margin_ms)}ms margin`}</span>
        <ChevronDown className="chevron" size={18} />
      </button>
      <div className="family-strip" aria-label="Guard family results">
        {FAMILY_ORDER.map((family) => (
          <span className={`family-chip family-${familyVerdict(trace.guards, family).toLowerCase()}`} key={family}>
            <i />{family}
          </span>
        ))}
      </div>
      {open && (
        <div className="decision-detail">
          <div className="decision-message"><strong>Decision explanation</strong><p>{trace.customer_message}</p></div>
          {trace.trust && <TrustBreakdown trust={trace.trust} />}
          <div className="guard-table" role="table" aria-label={`Checks for ${trace.facts.merchant.merchant_name}`}>
            <div className="guard-head" role="row"><span>Check</span><span>Evidence</span><span>Result</span><span>Time</span></div>
            {trace.guards.map((guard) => (
              <div className="guard-row" role="row" key={`${guard.guard_number}-${guard.guard_id}`}>
                <div><strong>{friendlyGuardName(guard.guard_id)}</strong><small>{guard.family}</small></div>
                <Evidence guard={guard} />
                <StatusPill verdict={guard.verdict} />
                <span className="guard-time">{guard.elapsed_ms.toFixed(3)} ms</span>
              </div>
            ))}
          </div>
          <div className="trace-footer"><span>Engine {trace.engine_version}</span><span>Model {trace.llm.mode}</span><span>{trace.guards.length} checks recorded</span></div>
        </div>
      )}
    </article>
  );
}

function LogScreen({ decisions }: { decisions: DecisionTrace[] }) {
  const counts = useMemo(() => ({
    approve: decisions.filter((item) => item.decision === "approve").length,
    step_up: decisions.filter((item) => item.decision === "step_up").length,
    decline: decisions.filter((item) => item.decision === "decline").length,
  }), [decisions]);
  return (
    <main>
      <section className="page-heading heading-row">
        <div><p className="eyebrow">Decision log</p><h1>Every decision, fully explained</h1><p>A live audit trail from purchase facts to the final outcome.</p></div>
        <div className="summary-counts"><span><i className="dot approved" />{counts.approve} approved</span><span><i className="dot review" />{counts.step_up} review</span><span><i className="dot declined" />{counts.decline} declined</span></div>
      </section>
      {!decisions.length ? (
        <section className="panel"><EmptyState icon={LayoutList} title="No decisions yet" detail="Start a scenario to see each purchase evaluated here." /></section>
      ) : (
        <section className="decision-list">
          <div className="decision-columns"><span>Purchase</span><span>Amount</span><span>Trust</span><span>Decision</span><span>Deadline</span></div>
          {decisions.map((trace) => <DecisionRow key={trace.ids.authorization_id} trace={trace} />)}
        </section>
      )}
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>("inbox");
  const [mobileNav, setMobileNav] = useState(false);
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [decisions, setDecisions] = useState<DecisionTrace[]>([]);
  const [pending, setPending] = useState<PendingAuthorization[]>([]);
  const [currentPolicy, setCurrentPolicy] = useState<PolicyDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const handleStreamEvent = useCallback((event: StreamEvent) => {
    if (event.type === "decision") setDecisions((items) => [event.data, ...items.filter((item) => item.ids.authorization_id !== event.data.ids.authorization_id)]);
    if (event.type === "pending") setPending((items) => [event.data, ...items.filter((item) => item.authorization_id !== event.data.authorization_id)]);
    if (event.type === "resolution") {
      setPending((items) => items.filter((item) => item.authorization_id !== event.data.ids.authorization_id));
      setDecisions((items) => [event.data, ...items.filter((item) => item.ids.authorization_id !== event.data.ids.authorization_id)]);
      // An answer changes the budget room of the other open questions, so their warnings are read again from the backend.
      if (!USE_MOCK_API) void api.pending().then(setPending).catch(() => undefined);
    }
    if (event.type === "status") setStatus(event.data);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [nextStatus, nextDecisions, nextPending, nextPolicy] = await Promise.all([api.status(), api.decisions(), api.pending(), api.currentPolicy()]);
      setStatus(nextStatus);
      setDecisions(nextDecisions);
      setPending(nextPending);
      setCurrentPolicy(nextPolicy);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Relay could not load current data.");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshLiveData = useCallback(async () => {
    if (USE_MOCK_API) return;
    try {
      const [nextDecisions, nextPending] = await Promise.all([api.decisions(), api.pending()]);
      setDecisions(nextDecisions);
      setPending(nextPending);
    } catch {
      // EventSource retries automatically; keep the last known state visible meanwhile.
    }
  }, []);

  const streamConnected = useEventStream(handleStreamEvent, refreshLiveData);

  useEffect(() => { void load(); }, [load]);

  async function resolveAuthorization(id: string, decision: "approve" | "decline") {
    const warning = await api.resolve(id, decision);
    setPending((items) => items.filter((item) => item.authorization_id !== id));
    setDecisions((items) => items.map((trace) => trace.ids.authorization_id === id ? { ...trace, decision, resolution: { decision, resolved_at: new Date().toISOString() } } : trace));
    if (!USE_MOCK_API) void api.pending().then(setPending).catch(() => undefined);
    return warning;
  }

  const navigate = (destination: Screen) => { setScreen(destination); setMobileNav(false); };
  const environment = status?.environment ?? (USE_MOCK_API ? "mock" : null);
  const environmentCopy = environment === "live"
    ? { label: "LIVE TEAM ENVIRONMENT", detail: "Runs count toward the shared 300-run quota." }
    : environment === "offline"
      ? { label: "OFFLINE SIMULATOR", detail: "Safe local data - no team runs consumed." }
      : { label: "PROTOTYPE DATA", detail: "Static replay data - no backend calls." };

  return (
    <div className="app-shell">
      <header className="app-header">
        <button className="mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={21} /></button>
        <button className="brand" onClick={() => navigate("inbox")} aria-label="Viseca Relay home">
          <img className="viseca-wordmark" src={visecaWordmark} alt="Viseca" />
          <span className="brand-divider" aria-hidden="true" />
          <span className="product-lockup"><strong>Relay</strong><small>Agent controls</small></span>
        </button>
        <nav className="desktop-nav" aria-label="Main navigation">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button className={screen === id ? "active" : ""} key={id} onClick={() => navigate(id)}><Icon size={17} />{label}{id === "inbox" && pending.length > 0 && <span className="nav-badge">{pending.length}</span>}</button>
          ))}
        </nav>
        <div className="engine-status">
          <span className={`connection-dot ${(status?.connected && streamConnected) ? "online" : "offline"}`} />
          <div><strong>{environment === "live" ? "Live" : environment === "offline" ? "Offline" : "Prototype"} · {status?.llm_mode === "ready" ? status.model_name ?? "Model ready" : status?.llm_mode === "degraded" ? "Model degraded" : "Deterministic"}</strong><small>{status?.latest_margin_ms ? `${(status.latest_margin_ms / 1000).toFixed(1)}s margin` : "Waiting for engine"}</small></div>
        </div>
      </header>

      {mobileNav && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
      <aside className={`mobile-nav ${mobileNav ? "open" : ""}`}>
        <div className="mobile-nav-head">
          <span className="brand">
            <img className="viseca-wordmark" src={visecaWordmark} alt="Viseca" />
            <span className="brand-divider" aria-hidden="true" />
            <span className="product-lockup"><strong>Relay</strong><small>Agent controls</small></span>
          </span>
          <button aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={21} /></button>
        </div>
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => <button className={screen === id ? "active" : ""} key={id} onClick={() => navigate(id)}><Icon size={18} />{label}</button>)}
      </aside>

      <div className="workspace">
        <div className={`environment-banner environment-${environment ?? "loading"}`} role="status">
          <strong>{environmentCopy.label}</strong><span>{environmentCopy.detail}</span>
        </div>
        {!USE_MOCK_API && status && !streamConnected && <div className="stream-warning" role="status"><RefreshCw size={15} /> Live updates are reconnecting. Existing decisions remain visible.</div>}
        {loadError ? (
          <div className="load-error"><TriangleAlert size={24} /><h2>Relay is temporarily unavailable</h2><p>{loadError}</p><button className="button button-secondary" onClick={() => void load()}><RefreshCw size={17} />Try again</button></div>
        ) : loading ? <LoadingRows /> : (
          <>
            <div hidden={screen !== "policy"}><PolicyScreen status={status} restoredPolicy={currentPolicy} onRunStarted={() => navigate("inbox")} /></div>
            <div hidden={screen !== "inbox"}><InboxScreen pending={pending} onResolve={resolveAuthorization} /></div>
            <div hidden={screen !== "log"}><LogScreen decisions={decisions} /></div>
          </>
        )}
      </div>
    </div>
  );
}
