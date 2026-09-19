# Script: trust_report.py
# Purpose: Score every purchase of a replay or audit file with the trust score, and write a table and a visualization that show how each score came about
# Author: Jonas Lüthi
# Date: September 2026

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path



# Make the backend importable, so the report uses the same calculation as the worker
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
BACKEND_FOLDER = REPOSITORY_FOLDER / "backend"
if str(BACKEND_FOLDER) not in sys.path:
    sys.path.insert(0, str(BACKEND_FOLDER))

from app.trust.score import (  # noqa: E402
    DOUBT_POINTS,
    FURTHER_DEDUCTIONS_CAP,
    FURTHER_FINDING_POINTS,
    LABEL_BY_BAND,
    LEADING_FINDING_POINTS,
    REVIEW_FROM,
    SIGNAL_POINTS,
    STARTING_POINTS,
    TRUST_SCORE_VERSION,
    TRUSTED_FROM,
    Decision,
    TrustScore,
    build_invalid_event_score,
    score_trace,
)









#### Step 1: Fix the paths and the words ####

# Locate the replay files and the folder the report goes to
REPLAY_FOLDER = REPOSITORY_FOLDER / "outputs" / "replay"
DEFAULT_OUTPUT_FOLDER = REPOSITORY_FOLDER / "outputs" / "trust"



# Order the bands as the report lists them, with the glyph that accompanies each color so the band never rests on color alone
BAND_ORDER = ("trusted", "review", "blocked", "not_assessed")
BAND_GLYPHS = {"trusted": "✓", "review": "?", "blocked": "✕", "not_assessed": "–"}
BAND_RANGES = {
    "trusted": f"{STARTING_POINTS - FURTHER_DEDUCTIONS_CAP} to {STARTING_POINTS}",
    "review": f"{STARTING_POINTS - LEADING_FINDING_POINTS[Decision.STEP_UP] - FURTHER_DEDUCTIONS_CAP} to {STARTING_POINTS - LEADING_FINDING_POINTS[Decision.STEP_UP]}",
    "blocked": f"{STARTING_POINTS - LEADING_FINDING_POINTS[Decision.DECLINE] - FURTHER_DEDUCTIONS_CAP} to {STARTING_POINTS - LEADING_FINDING_POINTS[Decision.DECLINE]}",
    "not_assessed": "none",
}



# Write the table of points once, from the constants of the calculation, so the report can never drift from the code
POINT_ROWS = (
    ("Starts at", str(STARTING_POINTS), "Every purchase begins with full trust"),
    ("Refusal that decided it", "−" + str(LEADING_FINDING_POINTS[Decision.DECLINE]), "The first guard at the severity of a decline"),
    ("Question that decided it", "−" + str(LEADING_FINDING_POINTS[Decision.STEP_UP]), "The first guard at the severity of a step-up"),
    ("Further finding", "−" + str(FURTHER_FINDING_POINTS), "Every other guard above approve"),
    ("Doubt", "−" + str(DOUBT_POINTS), "An unsettled check the customer's policy approved"),
    ("Signal, normal", "−" + str(SIGNAL_POINTS["normal"]), "A signal a guard noticed without raising the decision"),
    ("Signal, strong", "−" + str(SIGNAL_POINTS["strong"]), "The same, called strong by the guard"),
    ("Cap", "at most −" + str(FURTHER_DEDUCTIONS_CAP), "Further findings, doubts and signals together, so the score stays in the band of its decision"),
)









#### Step 2: Read the purchases ####

# Take the newest engine replay when no file is named
def find_default_input():
    candidates = sorted(REPLAY_FOLDER.glob("all_engine_*.jsonl"))
    if not candidates:
        raise SystemExit("No engine replay found under " + str(REPLAY_FOLDER) + ". Run scripts/replay.py --all first, or name a file with --input.")
    return candidates[-1]



# Turn one line of a replay file into a row of the report
def read_replay_line(line):
    record = line["record"]
    uncertainty_policy = line.get("message", {}).get("mandate", {}).get("uncertainty_policy", "ask")
    trace = record.get("trace")
    trust = score_trace(trace, uncertainty_policy) if trace is not None else build_invalid_event_score(uncertainty_policy)
    return {
        "scenario_id": record.get("scenario_id"),
        "replay_order": record.get("replay_order"),
        "purchase_id": record.get("source_authorization_id"),
        "merchant_name": record.get("merchant_name"),
        "amount_chf": record.get("billing_amount_chf"),
        "decision": record.get("decision"),
        "outcome": record.get("final_status"),
        "uncertainty_policy": uncertainty_policy,
        "policy_assumed": "message" not in line,
        "trust": trust,
    }



# Turn one decision line of an audit file into a row of the report, taking the stored score when the record carries one
def read_audit_line(line):
    if line.get("trust_score") is not None:
        trust = TrustScore.model_validate(line["trust_score"])
        uncertainty_policy = trust.basis.uncertainty_policy
        policy_assumed = False
    elif line.get("trace") is not None:
        uncertainty_policy = "ask"
        policy_assumed = True
        trust = score_trace(line["trace"], uncertainty_policy)
    else:
        uncertainty_policy = "unknown"
        policy_assumed = False
        trust = build_invalid_event_score(uncertainty_policy)
    return {
        "scenario_id": line.get("scenario_id"),
        "replay_order": line.get("replay_order"),
        "purchase_id": line.get("source_authorization_id"),
        "merchant_name": line.get("merchant_name"),
        "amount_chf": float(line["amount_chf"]) if line.get("amount_chf") is not None else None,
        "decision": line.get("decision"),
        "outcome": line.get("status"),
        "uncertainty_policy": uncertainty_policy,
        "policy_assumed": policy_assumed,
        "trust": trust,
    }



# Read every purchase of a replay or an audit file, told apart by the shape of the lines
def read_rows(input_path):
    rows = []
    for raw_line in input_path.read_text(encoding = "utf-8").splitlines():
        if raw_line.strip() == "":
            continue
        line = json.loads(raw_line)
        if "record" in line:
            rows.append(read_replay_line(line))
        elif line.get("kind") == "decision":
            rows.append(read_audit_line(line))
    rows.sort(key = lambda row: (str(row["scenario_id"]), row["replay_order"] if row["replay_order"] is not None else 0))
    return rows









#### Step 3: Write the calculation in words ####

# Write the deductions of one score as one line, as in "100 − 40 per order limit (small overshoot) − 5 addon signal = 55"
def write_calculation(trust):
    if trust.score is None:
        return "not assessed: " + trust.summary
    parts = [str(trust.scale.start)]
    for deduction in trust.deductions:
        name = deduction.guard_id.replace("_", " ")
        if deduction.kind == "signal":
            name = name + " signal"
        elif deduction.reason_code is not None:
            name = name + " (" + deduction.reason_code.value.lower().replace("_", " ") + ")"
        if deduction.capped:
            name = name + ", capped"
        parts.append("− " + str(deduction.points) + " " + name)
    return " ".join(parts) + " = " + str(trust.score)



# Write an amount in francs, or a dash when the message could not be read
def write_amount(amount_chf):
    if amount_chf is None:
        return "–"
    return f"CHF {amount_chf:,.2f}"



# Count the rows per band and the mean score per band
def summarize(rows):
    counts = Counter(row["trust"].band for row in rows)
    scores_by_band = defaultdict(list)
    for row in rows:
        if row["trust"].score is not None:
            scores_by_band[row["trust"].band].append(row["trust"].score)
    means = {band: sum(scores) / len(scores) for band, scores in scores_by_band.items()}
    return counts, means









#### Step 4: Write the markdown table ####

# Write the report as markdown, with the points table, the summary and one table per scenario
def write_markdown(rows, input_path, written_at):
    counts, means = summarize(rows)
    lines = [
        "# Trust score report",
        "",
        f"Written on {written_at:%d %B %Y at %H.%M} from `{input_path.relative_to(REPOSITORY_FOLDER) if input_path.is_relative_to(REPOSITORY_FOLDER) else input_path}`, "
        f"{len(rows)} purchases, trust score version {TRUST_SCORE_VERSION}. The calculation is `backend/app/trust/score.py`, the method is `docs/trust-score.md`.",
        "",
        "## How the score is calculated",
        "",
        "| Step | Points | Meaning |",
        "|---|---|---|",
    ]
    lines.extend("| " + step + " | " + points + " | " + meaning + " |" for step, points, meaning in POINT_ROWS)
    lines.extend([
        "",
        f"Trusted from {TRUSTED_FROM}, needs a decision from {REVIEW_FROM}, blocked below. A record without an evaluated check has no score.",
        "",
        "## Summary",
        "",
        "| Band | Purchases | Mean score |",
        "|---|---|---|",
    ])
    for band in BAND_ORDER:
        mean_text = f"{means[band]:.1f}" if band in means else "–"
        lines.append("| " + LABEL_BY_BAND[band] + " | " + str(counts.get(band, 0)) + " | " + mean_text + " |")
    if any(row["policy_assumed"] for row in rows):
        lines.extend(["", "The uncertainty policy was not in the file for some purchases and was taken as `ask`."])



    # One table per scenario, in replay order
    rows_by_scenario = defaultdict(list)
    for row in rows:
        rows_by_scenario[row["scenario_id"]].append(row)
    for scenario_id, scenario_rows in rows_by_scenario.items():
        lines.extend([
            "",
            "## " + str(scenario_id),
            "",
            "| Order | Purchase | Shop | Amount | Decision | Outcome | Trust | Band | Calculation |",
            "|---|---|---|---|---|---|---|---|---|",
        ])
        for row in scenario_rows:
            trust = row["trust"]
            lines.append("| " + " | ".join([
                str(row["replay_order"] if row["replay_order"] is not None else ""),
                str(row["purchase_id"]),
                str(row["merchant_name"]),
                write_amount(row["amount_chf"]),
                str(row["decision"]),
                str(row["outcome"]),
                str(trust.score) if trust.score is not None else "–",
                LABEL_BY_BAND[trust.band],
                write_calculation(trust).replace("|", "/"),
            ]) + " |")
    return "\n".join(lines) + "\n"









#### Step 5: Write the visualization ####

# Style the page, where the fill of a bar carries the band and a glyph with a label repeats it, so nothing rests on color alone
PAGE_STYLE = """
:root { color-scheme: light; --surface: #ffffff; --plane: #f4f7fb; --ink: #15223a; --ink-2: #52607a; --muted: #7c889a; --line: #e1e6ed; --grid: #dce3ed;
  --trusted: #167454; --review: #d67a17; --blocked: #b72c2c; --none: #8a97ab; --track: #e9eef5; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --surface: #1a1a19; --plane: #0d0d0d; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --line: #2c2c2a; --grid: #383835;
  --trusted: #2ea27a; --review: #e08b27; --blocked: #e05a5a; --none: #8a97ab; --track: #2c2c2a; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 24px 64px; background: var(--plane); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 1.6rem; letter-spacing: -.02em; margin: 0 0 6px; }
h2 { font-size: 1rem; margin: 28px 0 10px; }
p { margin: 0; color: var(--ink-2); }
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 22px 0; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; display: grid; gap: 4px; }
.tile small { color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }
.tile strong { font-size: 1.7rem; font-weight: 650; }
.tile span { color: var(--ink-2); font-size: .8rem; }
.tile i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.method { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
.method table { border-collapse: collapse; width: 100%; font-size: .82rem; }
.method th, .method td { text-align: left; padding: 6px 8px; border-top: 1px solid var(--line); }
.method th { color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; border-top: 0; }
.method td:nth-child(2) { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 650; }
.scenario { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.head, .row { display: grid; grid-template-columns: 62px minmax(120px, 1fr) 96px 78px 250px 158px minmax(220px, 1.6fr); gap: 14px; align-items: center; padding: 9px 16px; }
.head { color: var(--muted); font-size: .66rem; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; border-bottom: 1px solid var(--line); }
.row { border-top: 1px solid var(--line); font-size: .82rem; }
.row:first-of-type { border-top: 0; }
.row:hover { background: var(--plane); }
.id { color: var(--muted); font-variant-numeric: tabular-nums; }
.shop { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.amount { font-variant-numeric: tabular-nums; text-align: right; }
.decision { color: var(--ink-2); font-size: .74rem; }
.meter { display: grid; grid-template-columns: 1fr 30px; align-items: center; gap: 8px; }
.track { position: relative; display: block; height: 10px; border-radius: 0 4px 4px 0; background: var(--track); }
.fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 0 4px 4px 0; }
.tick { position: absolute; top: -3px; bottom: -3px; width: 1px; background: var(--grid); }
.value { font-variant-numeric: tabular-nums; text-align: right; font-weight: 650; }
.band { display: flex; align-items: center; gap: 7px; font-size: .76rem; color: var(--ink-2); }
.glyph { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; color: #fff; font-size: .68rem; font-weight: 800; }
.band-trusted { background: var(--trusted); } .band-review { background: var(--review); } .band-blocked { background: var(--blocked); } .band-not_assessed { background: var(--none); }
.calc { color: var(--ink-2); font-size: .76rem; font-variant-numeric: tabular-nums; }
.scale { display: flex; justify-content: space-between; color: var(--muted); font-size: .66rem; padding: 4px 0 0; }
.note { margin-top: 18px; color: var(--muted); font-size: .76rem; }
@media (max-width: 900px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } .head { display: none; }
  .row { grid-template-columns: 62px 1fr 96px; grid-auto-rows: auto; row-gap: 6px; } .meter, .band, .calc, .decision { grid-column: 1 / -1; } }
"""



# Write one purchase as a row with its bar on the scale
def write_html_row(row):
    trust = row["trust"]
    score_text = str(trust.score) if trust.score is not None else "–"
    fill_width = trust.score if trust.score is not None else 0
    fill = f'<b class="fill band-{trust.band}" style="width:{fill_width}%"></b>' if trust.score is not None else ""
    ticks = f'<u class="tick" style="left:{REVIEW_FROM}%"></u><u class="tick" style="left:{TRUSTED_FROM}%"></u>'
    aria = f"Trust score {score_text} of {STARTING_POINTS}, {LABEL_BY_BAND[trust.band].lower()}"
    return (
        f'<div class="row" title="{html.escape(trust.summary)}">'
        f'<span class="id">{html.escape(str(row["purchase_id"]))}</span>'
        f'<span class="shop">{html.escape(str(row["merchant_name"]))}</span>'
        f'<span class="amount">{html.escape(write_amount(row["amount_chf"]))}</span>'
        f'<span class="decision">{html.escape(str(row["decision"]))}</span>'
        f'<span class="meter" role="img" aria-label="{html.escape(aria)}"><i class="track">{fill}{ticks}</i><strong class="value">{score_text}</strong></span>'
        f'<span class="band"><span class="glyph band-{trust.band}">{BAND_GLYPHS[trust.band]}</span>{html.escape(LABEL_BY_BAND[trust.band])}</span>'
        f'<span class="calc">{html.escape(write_calculation(trust))}</span>'
        "</div>"
    )



# Write the whole page, which stands on its own and needs nothing from the network
def write_html(rows, input_path, written_at):
    counts, means = summarize(rows)
    source_text = str(input_path.relative_to(REPOSITORY_FOLDER) if input_path.is_relative_to(REPOSITORY_FOLDER) else input_path)
    tiles = "".join(
        f'<div class="tile"><small>{html.escape(LABEL_BY_BAND[band])}</small><strong>{counts.get(band, 0)}</strong>'
        f'<span><i class="band-{band}"></i>{html.escape(BAND_GLYPHS[band])} · scores {html.escape(BAND_RANGES[band])}'
        + (f' · mean {means[band]:.1f}' if band in means else "") + "</span></div>"
        for band in BAND_ORDER
    )
    method_rows = "".join(
        f"<tr><td>{html.escape(step)}</td><td>{html.escape(points)}</td><td>{html.escape(meaning)}</td></tr>"
        for step, points, meaning in POINT_ROWS
    )
    rows_by_scenario = defaultdict(list)
    for row in rows:
        rows_by_scenario[row["scenario_id"]].append(row)
    scenarios = "".join(
        f"<h2>{html.escape(str(scenario_id))} · {len(scenario_rows)} {'purchase' if len(scenario_rows) == 1 else 'purchases'}</h2>"
        '<section class="scenario"><div class="head"><span>Purchase</span><span>Shop</span><span>Amount</span><span>Decision</span>'
        f'<span>Trust score, 0 to {STARTING_POINTS}</span><span>Band</span><span>Calculation</span></div>'
        + "".join(write_html_row(row) for row in scenario_rows)
        + "</section>"
        for scenario_id, scenario_rows in rows_by_scenario.items()
    )
    assumed_note = "<p class=\"note\">The uncertainty policy was not in the file for some purchases and was taken as ask.</p>" if any(row["policy_assumed"] for row in rows) else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Trust score report</title><style>{PAGE_STYLE}</style></head><body><main>"
        f"<h1>Trust score of {len(rows)} purchases</h1>"
        f"<p>From {html.escape(source_text)}, written on {written_at:%d %B %Y at %H.%M}. Trust score version {TRUST_SCORE_VERSION}, "
        "computed by backend/app/trust/score.py from the verdicts and signals of the guards. "
        f"The two marks on every bar are the band lines at {REVIEW_FROM} and {TRUSTED_FROM}.</p>"
        f'<div class="tiles">{tiles}</div>'
        f'<section class="method"><table><thead><tr><th>Step</th><th>Points</th><th>Meaning</th></tr></thead><tbody>{method_rows}</tbody></table></section>'
        f"{scenarios}{assumed_note}"
        "</main></body></html>\n"
    )









#### Step 6: Run ####

# Read the arguments, score the purchases and write both files
def main(argv = None):
    parser = argparse.ArgumentParser(description = "Score every purchase of a replay or audit file with the trust score and write a table and a visualization")
    parser.add_argument("--input", type = Path, default = None, help = "A replay .jsonl from outputs/replay/ or an audit .jsonl from outputs/audit/. Default: the newest engine replay")
    parser.add_argument("--output-folder", type = Path, default = DEFAULT_OUTPUT_FOLDER, help = "Where the .md and .html files go. Default: outputs/trust/")
    arguments = parser.parse_args(argv)
    input_path = arguments.input if arguments.input is not None else find_default_input()
    if not input_path.is_file():
        raise SystemExit("No such file: " + str(input_path))
    rows = read_rows(input_path)
    if not rows:
        raise SystemExit("No decision found in " + str(input_path))



    # Write both files under one stamp
    written_at = datetime.now()
    stamp = written_at.strftime("%Y%m%d-%H%M%S")
    arguments.output_folder.mkdir(parents = True, exist_ok = True)
    markdown_path = arguments.output_folder / ("trust_report_" + stamp + ".md")
    html_path = arguments.output_folder / ("trust_report_" + stamp + ".html")
    markdown_path.write_text(write_markdown(rows, input_path, written_at), encoding = "utf-8")
    html_path.write_text(write_html(rows, input_path, written_at), encoding = "utf-8")



    # Report the summary on the console
    counts, means = summarize(rows)
    print("Scored " + str(len(rows)) + " purchases from " + str(input_path))
    for band in BAND_ORDER:
        mean_text = f", mean {means[band]:.1f}" if band in means else ""
        print("  " + LABEL_BY_BAND[band].ljust(20) + str(counts.get(band, 0)).rjust(3) + mean_text)
    for row in rows:
        trust = row["trust"]
        if trust.score is None or trust.deductions:
            print("  " + str(row["purchase_id"]) + " " + str(row["decision"]).ljust(8) + " " + write_calculation(trust))
    print("Wrote " + str(markdown_path))
    print("Wrote " + str(html_path))
    return 0



if __name__ == "__main__":
    sys.exit(main())
