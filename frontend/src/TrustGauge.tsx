import type { TrustBand, TrustScore } from "./types";

// The three views of the trust score: a tachometer for a card, a compact bar for a log row, and the calculation for an expanded row.
// The score itself comes from the backend; nothing here computes it, this only draws what the record says.

const BAND_COLOR: Record<TrustBand, string> = {
  trusted: "var(--green)",
  review: "#d67a17",
  blocked: "var(--red)",
  not_assessed: "#8a97ab",
};

const describeTrust = (trust: TrustScore) =>
  trust.score === null ? `Trust score not assessed, ${trust.summary}` : `Trust score ${trust.score} of 100, ${trust.label.toLowerCase()}`;

// Map a value on the scale to a point on the semicircle, drawn left to right over the top.
const CENTER_X = 50;
const CENTER_Y = 52;
const pointAt = (value: number, radius: number) => {
  const angle = Math.PI - (Math.PI * Math.min(100, Math.max(0, value))) / 100;
  return { x: CENTER_X + radius * Math.cos(angle), y: CENTER_Y - radius * Math.sin(angle) };
};

const arcPath = (from: number, to: number, radius: number) => {
  const start = pointAt(from, radius);
  const end = pointAt(to, radius);
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${radius} ${radius} 0 0 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
};

export function TrustGauge({ trust, size = 132 }: { trust: TrustScore; size?: number }) {
  const { review_from: reviewFrom, trusted_from: trustedFrom } = trust.scale;
  const assessed = trust.score !== null;
  const marker = assessed ? pointAt(trust.score as number, 40) : null;
  const zoneOpacity = assessed ? 1 : 0.35;
  return (
    <figure className={`trust-gauge band-${trust.band}`} style={{ width: size }}>
      <svg viewBox="0 0 100 58" role="img" aria-label={describeTrust(trust)} width={size} height={size * 0.58}>
        <g fill="none" strokeWidth="9" strokeLinecap="butt" opacity={zoneOpacity}>
          <path d={arcPath(0, reviewFrom, 40)} stroke="var(--red)" />
          <path d={arcPath(reviewFrom, trustedFrom, 40)} stroke="#d67a17" />
          <path d={arcPath(trustedFrom, 100, 40)} stroke="var(--green)" />
        </g>
        {marker && (
          <circle cx={marker.x.toFixed(2)} cy={marker.y.toFixed(2)} r="5.5" fill="#15223a" stroke="#ffffff" strokeWidth="2" />
        )}
        <text x={CENTER_X} y="47" textAnchor="middle" className="trust-gauge-value" fill={BAND_COLOR[trust.band]}>
          {assessed ? trust.score : "–"}
        </text>
      </svg>
      <figcaption style={{ color: BAND_COLOR[trust.band] }}>{trust.label}</figcaption>
    </figure>
  );
}

export function TrustBar({ trust }: { trust: TrustScore | null }) {
  if (!trust) return <span className="trust-bar trust-bar-empty" aria-label="Trust score not available">—</span>;
  const width = trust.score ?? 0;
  return (
    <span className="trust-bar" role="img" aria-label={describeTrust(trust)} title={trust.summary}>
      <i><b style={{ width: `${width}%`, background: BAND_COLOR[trust.band] }} /></i>
      <strong style={{ color: BAND_COLOR[trust.band] }}>{trust.score ?? "n/a"}</strong>
    </span>
  );
}

const kindLabel: Record<TrustScore["deductions"][number]["kind"], string> = {
  leading_finding: "Decided it",
  further_finding: "Further finding",
  doubt: "Doubt",
  signal: "Signal",
};

export function TrustBreakdown({ trust }: { trust: TrustScore }) {
  const { coverage, basis } = trust;
  return (
    <section className="trust-breakdown" aria-label="How the trust score was calculated">
      <TrustGauge trust={trust} size={150} />
      <div className="trust-calculation">
        <p className="trust-summary">{trust.summary}</p>
        {trust.score !== null && (
          <ol className="trust-steps">
            <li><span>Starts at</span><em>Every purchase begins with full trust</em><strong>{trust.scale.start}</strong></li>
            {trust.deductions.map((deduction, index) => (
              <li key={`${deduction.guard_id}-${deduction.kind}-${index}`} className={deduction.capped ? "capped" : ""}>
                <span>{kindLabel[deduction.kind]}</span>
                <em>{deduction.detail}{deduction.capped ? " · beyond the cap, no further points" : ""}</em>
                <strong>−{deduction.points}</strong>
              </li>
            ))}
            <li className="trust-total"><span>Trust score</span><em>{trust.label}</em><strong>{trust.score}</strong></li>
          </ol>
        )}
        <p className="trust-coverage">
          {coverage.checks_evaluated} of {coverage.checks_total - coverage.checks_not_applicable} applicable checks evaluated
          {coverage.checks_not_applicable > 0 ? ` · ${coverage.checks_not_applicable} did not apply` : ""}
          {coverage.checks_not_built > 0 ? ` · ${coverage.checks_not_built} not built yet` : ""}
          {coverage.checks_failed > 0 ? ` · ${coverage.checks_failed} failed` : ""}
          {` · in doubt the policy says "${basis.uncertainty_policy}"`}
          {` · trust score v${trust.version}`}
        </p>
        <p className="trust-caveat">The score summarizes the checks and explains the decision. It never replaces it: the decision comes from the checks themselves.</p>
      </div>
    </section>
  );
}
