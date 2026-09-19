# Script: pitch_figures.py
# Purpose: Replay the 45 public purchases under no control, a plain spending limit and the engine, and write the tables and charts for the pitch
# Author: Andrés Gallardo
# Date: September 2026

import html
import os
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd









#### Step 1: Name the folders, the three setups and the look of the charts ####

# Name the folders and make the replay script importable
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
SCRIPTS_FOLDER = REPOSITORY_FOLDER / "scripts"
OUTPUT_FOLDER = REPOSITORY_FOLDER / "outputs" / "pitch"
VERDICT_TABLE_PATH = REPOSITORY_FOLDER / "data" / "processed" / "purchase_verdicts.csv"
if str(SCRIPTS_FOLDER) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_FOLDER))

import replay
from app.config import get_settings



# Name the three setups that are compared, with the baseline of the replay that stands for each, where None is the engine
SETUP_NO_CONTROL = "No control"
SETUP_PLAIN_LIMIT = "Plain spending limit"
SETUP_RELAY = "Relay"
BASELINE_BY_SETUP = {
    SETUP_NO_CONTROL: "none",
    SETUP_PLAIN_LIMIT: "limit_only",
    SETUP_RELAY: None,
}



# Group the kinds of problems of the reviewed purchases into six families a slide can show
FAMILY_BY_PROBLEM_KIND = {
    "small_overshoot": "Over the limit or the budget",
    "over_order_limit": "Over the limit or the budget",
    "split_order": "Over the limit or the budget",
    "off_purpose_item": "Wrong item or unrequested extras",
    "wrong_attribute": "Wrong item or unrequested extras",
    "wrong_item": "Wrong item or unrequested extras",
    "unrequested_addon": "Wrong item or unrequested extras",
    "order_terms": "Return terms not met or not stated",
    "missing_fact": "Return terms not met or not stated",
    "wrong_merchant_type": "Wrong, unknown or imitated seller",
    "lookalike_merchant": "Wrong, unknown or imitated seller",
    "unfamiliar_merchant": "Wrong, unknown or imitated seller",
    "familiarity_card_or_person": "Wrong, unknown or imitated seller",
    "new_device": "Someone else driving the session",
    "session_anomaly": "Someone else driving the session",
    "duplicate": "Repeated order or text aimed at the agent",
    "injection": "Repeated order or text aimed at the agent",
}
FAMILY_ORDER = [
    "Over the limit or the budget",
    "Wrong item or unrequested extras",
    "Someone else driving the session",
    "Wrong, unknown or imitated seller",
    "Return terms not met or not stated",
    "Repeated order or text aimed at the agent",
]



# State the deadline of one decision on the platform, in milliseconds
DECISION_DEADLINE_MS = 8000



# Choose the colors. Each setup and each decision keeps its color in every chart, and all text stays in ink colors.
COLOR_BY_SETUP = {
    SETUP_NO_CONTROL: "#898781",
    SETUP_PLAIN_LIMIT: "#eb6834",
    SETUP_RELAY: "#2a78d6",
}
COLOR_BY_DECISION = {
    "approve": "#1baf7a",
    "step_up": "#eda100",
    "decline": "#e34948",
}
LABEL_BY_DECISION = {
    "approve": "Approved without friction",
    "step_up": "Put to the customer",
    "decline": "Declined",
}
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID_COLOR = "#e4e3df"
PAGE_SURFACE = "#fcfcfb"
FONT_FAMILY = "Segoe UI, Helvetica, Arial, sans-serif"
CHART_WIDTH = 1200









#### Step 2: Replay the purchases under every setup ####

# Replay all scenarios with one decision function and return one row per purchase, with the amount as an exact decimal
def replay_one_setup(replay_tables, baseline_name):
    purchase_results = replay.replay_scenarios(
        replay_tables = replay_tables,
        scenario_ids = replay.list_scenario_ids(replay_tables),
        decide_purchase = replay.choose_decision_function(baseline_name),
    )
    return (
        replay.build_result_table(purchase_results)
        .assign(amount_chf = lambda table: table["billing_amount_chf"].map(lambda amount: Decimal(str(amount))))
        .loc[:, ["scenario_id", "replay_order", "source_authorization_id", "merchant_name", "amount_chf", "decision", "elapsed_ms"]]
    )



# Replay the engine under one answer to a second order of the one requested thing, and check that it matches all reference decisions of that reading
def replay_engine_under_action(replay_tables, goal_fulfilled_action):
    os.environ["GOAL_FULFILLED_ACTION"] = goal_fulfilled_action
    get_settings.cache_clear()
    purchase_results = replay.replay_scenarios(
        replay_tables = replay_tables,
        scenario_ids = replay.list_scenario_ids(replay_tables),
        decide_purchase = replay.choose_decision_function(None),
    )
    result_table = replay.build_result_table(purchase_results)
    compared_table = replay.compare_with_reference_decisions(result_table)
    match_count = int((compared_table["decision_match"] == "match").sum())
    assert match_count == len(compared_table), "The engine differs from the reference decisions under " + goal_fulfilled_action
    return (
        result_table
        .assign(amount_chf = lambda table: table["billing_amount_chf"].map(lambda amount: Decimal(str(amount))))
        .loc[:, ["scenario_id", "replay_order", "source_authorization_id", "merchant_name", "amount_chf", "decision", "elapsed_ms"]]
    )









#### Step 3: Join the decisions of the three setups with the reviewed verdicts ####

# Read the reviewed verdicts as text. A purchase is risky when its review says it needs a refusal or a question on its own facts.
def read_reviewed_purchases():
    return (
        pd.read_csv(VERDICT_TABLE_PATH, dtype = str, keep_default_na = False)
        .loc[:, ["authorization_id", "verdict", "problem_kind", "decision_on_own_facts"]]
        .rename(columns = {"authorization_id": "source_authorization_id"})
        .assign(
            is_risky = lambda table: table["decision_on_own_facts"] != "approve",
            problem_family = lambda table: table["problem_kind"].map(FAMILY_BY_PROBLEM_KIND),
        )
    )



# Put the decision of every setup next to the review of each purchase, one row per purchase and setup
def build_long_table(result_table_by_setup, reviewed_purchases):
    long_table = pd.concat(
        [result_table.assign(setup = setup_name) for setup_name, result_table in result_table_by_setup.items()],
        ignore_index = True,
    ).merge(reviewed_purchases, on = "source_authorization_id", how = "left", validate = "many_to_one", indicator = "review_join")
    assert (long_table["review_join"] == "both").all(), "A purchase has no reviewed verdict"
    risky_without_family = long_table.query("is_risky and problem_family.isna()")
    assert len(risky_without_family) == 0, "A kind of problem has no family"
    return long_table.assign(was_stopped_or_asked = lambda table: table["decision"] != "approve")









#### Step 4: Build the tables ####

# Sum a column of exact decimals, where an empty selection sums to zero
def sum_decimals(amounts):
    return sum(amounts, Decimal("0"))



# Count per setup how many risky purchases were stopped or put to the customer, how much money that is,
# and how many ordinary purchases went through untouched
def build_headline_table(long_table):
    rows = [
        {
            "setup": setup_name,
            "risky_purchases": int(setup_table["is_risky"].sum()),
            "risky_stopped_or_asked": int((setup_table["is_risky"] & setup_table["was_stopped_or_asked"]).sum()),
            "risky_amount_chf": sum_decimals(setup_table.loc[setup_table["is_risky"], "amount_chf"]),
            "risky_amount_stopped_or_asked_chf": sum_decimals(setup_table.loc[setup_table["is_risky"] & setup_table["was_stopped_or_asked"], "amount_chf"]),
            "risky_amount_paid_unchecked_chf": sum_decimals(setup_table.loc[setup_table["is_risky"] & ~setup_table["was_stopped_or_asked"], "amount_chf"]),
            "ordinary_purchases": int((~setup_table["is_risky"]).sum()),
            "ordinary_approved_untouched": int((~setup_table["is_risky"] & ~setup_table["was_stopped_or_asked"]).sum()),
            "ordinary_amount_approved_chf": sum_decimals(setup_table.loc[~setup_table["is_risky"] & ~setup_table["was_stopped_or_asked"], "amount_chf"]),
        }
        for setup_name, setup_table in long_table.groupby("setup", sort = False)
    ]
    return pd.DataFrame(rows)



# Count per family of problems how many risky purchases the plain limit catches as well, and how many only the engine catches
def build_family_table(long_table):
    risky_wide = (
        long_table
        .query("is_risky")
        .pivot(index = ["source_authorization_id", "problem_family"], columns = "setup", values = "was_stopped_or_asked")
        .reset_index()
    )
    return (
        risky_wide
        .assign(
            caught_by_plain_limit = lambda table: table[SETUP_PLAIN_LIMIT].astype(bool),
            caught_by_relay_only = lambda table: table[SETUP_RELAY].astype(bool) & ~table[SETUP_PLAIN_LIMIT].astype(bool),
            caught_by_relay = lambda table: table[SETUP_RELAY].astype(bool),
        )
        .groupby("problem_family")
        .agg(
            risky_purchases = ("source_authorization_id", "count"),
            caught_by_plain_limit = ("caught_by_plain_limit", "sum"),
            caught_by_relay_only = ("caught_by_relay_only", "sum"),
            caught_by_relay = ("caught_by_relay", "sum"),
        )
        .reindex(FAMILY_ORDER)
        .reset_index()
        .astype({"risky_purchases": int, "caught_by_plain_limit": int, "caught_by_relay_only": int, "caught_by_relay": int})
    )



# Count the three decisions of one engine run, with the amount behind each
def build_decision_mix_table(engine_table, reading_name):
    return (
        engine_table
        .groupby("decision")
        .agg(purchases = ("source_authorization_id", "count"), amount_chf = ("amount_chf", sum_decimals))
        .reindex(["approve", "step_up", "decline"])
        .reset_index()
        .assign(reading = reading_name)
    )



# Measure how long the engine needs for one decision, against the deadline of the platform
def build_speed_table(engine_table):
    elapsed_ms = engine_table["elapsed_ms"].astype(float)
    return pd.DataFrame([{
        "decisions": len(elapsed_ms),
        "median_ms": round(float(elapsed_ms.median()), 1),
        "p95_ms": round(float(elapsed_ms.quantile(0.95)), 1),
        "slowest_ms": round(float(elapsed_ms.max()), 1),
        "deadline_ms": DECISION_DEADLINE_MS,
        "deadline_over_slowest": int(DECISION_DEADLINE_MS / float(elapsed_ms.max())),
    }])









#### Step 5: Draw the charts as SVG ####

# Write an amount in Swiss francs with thousands separators and without cents
def describe_francs(amount_chf):
    return "CHF " + f"{int(amount_chf.quantize(Decimal('1'))):,}"



# Escape a text for SVG
def escape_text(text):
    return html.escape(str(text), quote = True)



# Write one line of text
def draw_text(x, y, text, size, color, weight = "400", anchor = "start"):
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT_FAMILY}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}" text-anchor="{anchor}">{escape_text(text)}</text>'
    )



# Draw one horizontal bar with a rounded data end, anchored to the baseline at its left, and a tooltip
def draw_bar(x, y, width, height, color, tooltip):
    if width <= 0:
        return ""
    radius = min(4, width / 2)
    path = (
        f"M{x},{y} h{width - radius} a{radius},{radius} 0 0 1 {radius},{radius} "
        f"v{height - 2 * radius} a{radius},{radius} 0 0 1 -{radius},{radius} h-{width - radius} z"
    )
    return f'<path d="{path}" fill="{color}"><title>{escape_text(tooltip)}</title></path>'



# Wrap the parts of a chart into one SVG document with a title and a subtitle, without a background, so it sits on any slide
def wrap_chart(title, subtitle, body_parts, height):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} {height}" width="{CHART_WIDTH}" height="{height}" role="img" aria-label="{escape_text(title)}">'
        + draw_text(40, 52, title, 30, INK_PRIMARY, weight = "600")
        + draw_text(40, 86, subtitle, 18, INK_SECONDARY)
        + "".join(body_parts)
        + "</svg>"
    )



# Draw one bar per setup, with the setup named at the left and the value written at the end of the bar
def draw_setup_bar_chart(title, subtitle, bar_rows, axis_maximum):
    label_width = 260
    plot_left = 40 + label_width
    plot_width = CHART_WIDTH - plot_left - 300
    bar_height = 44
    row_gap = 34
    top = 130
    body_parts = [
        draw_text(40, top + row_index * (bar_height + row_gap) + 30, bar_row["setup"], 22, INK_PRIMARY, weight = "600")
        + f'<line x1="{plot_left}" y1="{top + row_index * (bar_height + row_gap) - 8}" x2="{plot_left}" y2="{top + row_index * (bar_height + row_gap) + bar_height + 8}" stroke="{GRID_COLOR}" stroke-width="2"/>'
        + draw_bar(plot_left, top + row_index * (bar_height + row_gap), plot_width * bar_row["value"] / axis_maximum, bar_height, COLOR_BY_SETUP[bar_row["setup"]], bar_row["setup"] + " - " + bar_row["value_label"])
        + draw_text(plot_left + plot_width * bar_row["value"] / axis_maximum + 14, top + row_index * (bar_height + row_gap) + 30, bar_row["value_label"], 22, INK_PRIMARY, weight = "600")
        for row_index, bar_row in enumerate(bar_rows)
    ]
    return wrap_chart(title, subtitle, body_parts, top + len(bar_rows) * (bar_height + row_gap) + 20)



# Draw one stacked bar per family of problems, the part a plain limit catches as well and the part only the engine catches
def draw_family_chart(title, subtitle, family_table):
    label_width = 430
    plot_left = 40 + label_width
    plot_width = CHART_WIDTH - plot_left - 300
    bar_height = 34
    row_gap = 22
    top = 170
    axis_maximum = int(family_table["risky_purchases"].max())
    unit_width = plot_width / axis_maximum
    segment_gap = 2
    legend_parts = [
        f'<rect x="40" y="112" width="18" height="18" rx="3" fill="{COLOR_BY_SETUP[SETUP_PLAIN_LIMIT]}"/>'
        + draw_text(66, 127, "A plain spending limit catches it too", 17, INK_SECONDARY)
        + f'<rect x="400" y="112" width="18" height="18" rx="3" fill="{COLOR_BY_SETUP[SETUP_RELAY]}"/>'
        + draw_text(426, 127, "Only Relay catches it", 17, INK_SECONDARY)
    ]
    row_parts = [
        draw_text(40, top + row_index * (bar_height + row_gap) + 24, family_row["problem_family"], 19, INK_PRIMARY)
        + (
            f'<rect x="{plot_left}" y="{top + row_index * (bar_height + row_gap)}" width="{max(unit_width * family_row["caught_by_plain_limit"] - segment_gap, 0)}" height="{bar_height}" fill="{COLOR_BY_SETUP[SETUP_PLAIN_LIMIT]}">'
            f'<title>{escape_text(family_row["problem_family"])} - a plain limit catches {family_row["caught_by_plain_limit"]}</title></rect>'
            if family_row["caught_by_plain_limit"] > 0 else ""
        )
        + draw_bar(
            plot_left + unit_width * family_row["caught_by_plain_limit"],
            top + row_index * (bar_height + row_gap),
            unit_width * family_row["caught_by_relay_only"],
            bar_height,
            COLOR_BY_SETUP[SETUP_RELAY],
            family_row["problem_family"] + " - only Relay catches " + str(family_row["caught_by_relay_only"]),
        )
        + draw_text(
            plot_left + unit_width * family_row["caught_by_relay"] + 12,
            top + row_index * (bar_height + row_gap) + 24,
            "Relay " + str(family_row["caught_by_relay"]) + " of " + str(family_row["risky_purchases"]) + ", a limit " + str(family_row["caught_by_plain_limit"]),
            17,
            INK_SECONDARY,
        )
        for row_index, family_row in enumerate(family_table.to_dict("records"))
    ]
    return wrap_chart(title, subtitle, legend_parts + row_parts, top + len(family_table) * (bar_height + row_gap) + 20)



# Draw the three decisions of the engine as one stacked bar over all purchases, each part labeled below the bar
def draw_decision_mix_chart(title, subtitle, decision_mix_table):
    plot_left = 40
    plot_width = CHART_WIDTH - 80
    bar_top = 130
    bar_height = 64
    segment_gap = 2
    mix_rows = decision_mix_table.to_dict("records")
    total_purchases = sum(mix_row["purchases"] for mix_row in mix_rows)
    purchases_before = [sum(earlier_row["purchases"] for earlier_row in mix_rows[:row_index]) for row_index in range(len(mix_rows))]
    body_parts = [
        f'<rect x="{plot_left + plot_width * before / total_purchases}" y="{bar_top}" width="{plot_width * mix_row["purchases"] / total_purchases - segment_gap}" height="{bar_height}" rx="4" fill="{COLOR_BY_DECISION[mix_row["decision"]]}">'
        f'<title>{escape_text(LABEL_BY_DECISION[mix_row["decision"]])} - {mix_row["purchases"]} purchases, {describe_francs(mix_row["amount_chf"])}</title></rect>'
        + draw_text(plot_left + plot_width * before / total_purchases, bar_top + bar_height + 44, str(mix_row["purchases"]), 40, INK_PRIMARY, weight = "600")
        + draw_text(plot_left + plot_width * before / total_purchases, bar_top + bar_height + 74, LABEL_BY_DECISION[mix_row["decision"]], 19, INK_PRIMARY)
        + draw_text(plot_left + plot_width * before / total_purchases, bar_top + bar_height + 100, describe_francs(mix_row["amount_chf"]), 17, INK_SECONDARY)
        for mix_row, before in zip(mix_rows, purchases_before)
    ]
    return wrap_chart(title, subtitle, body_parts, bar_top + bar_height + 130)



# Draw the headline numbers as four tiles, each a large figure with one line of explanation and one line of comparison
def draw_headline_tiles(tiles):
    tile_width = (CHART_WIDTH - 80 - 3 * 24) / 4
    body_parts = [
        f'<rect x="{40 + tile_index * (tile_width + 24)}" y="20" width="{tile_width}" height="220" rx="12" fill="none" stroke="{GRID_COLOR}" stroke-width="2"/>'
        + draw_text(40 + tile_index * (tile_width + 24) + 22, 96, tile["figure"], 44, INK_PRIMARY, weight = "700")
        + draw_text(40 + tile_index * (tile_width + 24) + 22, 136, tile["first_line"], 18, INK_PRIMARY)
        + draw_text(40 + tile_index * (tile_width + 24) + 22, 162, tile["second_line"], 18, INK_PRIMARY)
        + draw_text(40 + tile_index * (tile_width + 24) + 22, 206, tile["comparison"], 16, INK_MUTED)
        for tile_index, tile in enumerate(tiles)
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} 260" width="{CHART_WIDTH}" height="260" role="img" aria-label="Headline numbers">'
        + "".join(body_parts)
        + "</svg>"
    )









#### Step 6: Write the files ####

# Write a table as a markdown table, with every value as text
def write_markdown_table(table):
    header_line = "| " + " | ".join(table.columns) + " |"
    fence_line = "|" + "|".join(["---"] * len(table.columns)) + "|"
    body_lines = ["| " + " | ".join(str(value) for value in row_values) + " |" for row_values in table.itertuples(index = False)]
    return "\n".join([header_line, fence_line] + body_lines)



# Put all charts on one page, so they can be looked at together and captured as images
def build_overview_page(chart_by_name):
    chart_blocks = "".join('<section>' + chart_svg + '</section>' for chart_svg in chart_by_name.values())
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Relay - figures for the pitch</title>'
        f'<style>body{{margin:0;background:{PAGE_SURFACE};font-family:{FONT_FAMILY}}}section{{max-width:{CHART_WIDTH}px;margin:24px auto}}svg{{width:100%;height:auto}}</style>'
        '</head><body>' + chart_blocks + '</body></html>'
    )



def main():

    # Replay the three setups on the reading of each purchase on its own facts, where a clean second order is approved with a note,
    # and replay the engine once more as it ships, where such an order asks the customer
    replay_tables = replay.load_replay_tables()
    reviewed_purchases = read_reviewed_purchases()
    engine_table_own_facts = replay_engine_under_action(replay_tables, "note")
    engine_table_as_shipped = replay_engine_under_action(replay_tables, "step_up")
    result_table_by_setup = {
        SETUP_NO_CONTROL: replay_one_setup(replay_tables, BASELINE_BY_SETUP[SETUP_NO_CONTROL]),
        SETUP_PLAIN_LIMIT: replay_one_setup(replay_tables, BASELINE_BY_SETUP[SETUP_PLAIN_LIMIT]),
        SETUP_RELAY: engine_table_own_facts,
    }
    long_table = build_long_table(result_table_by_setup, reviewed_purchases)



    # Build the tables and check the figures that the pitch rests on
    headline_table = build_headline_table(long_table)
    family_table = build_family_table(long_table)
    decision_mix_own_facts = build_decision_mix_table(engine_table_own_facts, "each purchase on its own facts")
    decision_mix_as_shipped = build_decision_mix_table(engine_table_as_shipped, "as shipped, a second order of the one requested thing asks")
    speed_table = build_speed_table(engine_table_as_shipped)
    headline_by_setup = headline_table.set_index("setup").to_dict("index")
    relay_headline = headline_by_setup[SETUP_RELAY]
    limit_headline = headline_by_setup[SETUP_PLAIN_LIMIT]
    assert relay_headline["risky_stopped_or_asked"] == relay_headline["risky_purchases"], "The engine lets a risky purchase through"
    assert relay_headline["ordinary_approved_untouched"] == relay_headline["ordinary_purchases"], "The engine touches an ordinary purchase"
    assert int(family_table["risky_purchases"].sum()) == relay_headline["risky_purchases"], "The families do not add up to the risky purchases"



    # Draw the charts
    risky_count = relay_headline["risky_purchases"]
    speed_row = speed_table.to_dict("records")[0]
    chart_by_name = {
        "01_headline_numbers": draw_headline_tiles([
            {
                "figure": str(relay_headline["risky_stopped_or_asked"]) + " of " + str(risky_count),
                "first_line": "risky purchases stopped",
                "second_line": "or put to the customer",
                "comparison": "A plain limit - " + str(limit_headline["risky_stopped_or_asked"]) + " of " + str(risky_count),
            },
            {
                "figure": describe_francs(relay_headline["risky_amount_stopped_or_asked_chf"]),
                "first_line": "of risky spend kept from",
                "second_line": "going through unchecked",
                "comparison": "A plain limit - " + describe_francs(limit_headline["risky_amount_stopped_or_asked_chf"]),
            },
            {
                "figure": str(relay_headline["ordinary_approved_untouched"]) + " of " + str(relay_headline["ordinary_purchases"]),
                "first_line": "ordinary purchases approved",
                "second_line": "without any friction",
                "comparison": "No good purchase blocked",
            },
            {
                "figure": str(speed_row["median_ms"]) + " ms",
                "first_line": "for a typical decision,",
                "second_line": "23 checks each",
                "comparison": "Deadline - " + f"{DECISION_DEADLINE_MS:,}" + " ms",
            },
        ]),
        "02_risky_purchases_caught": draw_setup_bar_chart(
            "Relay catches every risky purchase. A spending limit catches " + str(limit_headline["risky_stopped_or_asked"]) + ".",
            "Risky purchases stopped or put to the customer, of " + str(risky_count) + " in Viseca's five public scenarios",
            [
                {"setup": setup_name, "value": headline_by_setup[setup_name]["risky_stopped_or_asked"], "value_label": str(headline_by_setup[setup_name]["risky_stopped_or_asked"]) + " of " + str(risky_count)}
                for setup_name in BASELINE_BY_SETUP
            ],
            risky_count,
        ),
        "03_risky_spend_kept_back": draw_setup_bar_chart(
            "A spending limit lets " + describe_francs(limit_headline["risky_amount_paid_unchecked_chf"]) + " of risky spend through. Relay lets none through.",
            "Risky spend stopped or put to the customer, of " + describe_francs(relay_headline["risky_amount_chf"]) + " in total",
            [
                {"setup": setup_name, "value": float(headline_by_setup[setup_name]["risky_amount_stopped_or_asked_chf"]), "value_label": describe_francs(headline_by_setup[setup_name]["risky_amount_stopped_or_asked_chf"])}
                for setup_name in BASELINE_BY_SETUP
            ],
            float(relay_headline["risky_amount_chf"]),
        ),
        "04_what_a_limit_cannot_see": draw_family_chart(
            "A spending limit only sees the amount. Most problems are somewhere else.",
            "The " + str(risky_count) + " risky purchases by kind of problem",
            family_table,
        ),
        "05a_how_relay_answers_as_shipped": draw_decision_mix_chart(
            "Relay declines what is clearly wrong and asks the customer when it is their call",
            "All 45 purchases of the five public scenarios, where a second order of the one requested thing asks the customer",
            decision_mix_as_shipped,
        ),
        "05b_how_relay_answers_on_own_facts": draw_decision_mix_chart(
            "Relay declines what is clearly wrong and asks the customer when it is their call",
            "All 45 purchases of the five public scenarios, each judged on its own facts",
            decision_mix_own_facts,
        ),
    }



    # Write the charts, the overview page, the tables and the summary
    OUTPUT_FOLDER.mkdir(parents = True, exist_ok = True)
    written_paths = [OUTPUT_FOLDER / (chart_name + ".svg") for chart_name in chart_by_name]
    for written_path, chart_svg in zip(written_paths, chart_by_name.values()):
        written_path.write_text(chart_svg, encoding = "utf-8")
    (OUTPUT_FOLDER / "all_figures.html").write_text(build_overview_page(chart_by_name), encoding = "utf-8")
    headline_table.to_csv(OUTPUT_FOLDER / "table_headline_by_setup.csv", index = False)
    family_table.to_csv(OUTPUT_FOLDER / "table_problem_families.csv", index = False)
    decision_mix_table = pd.concat([decision_mix_own_facts, decision_mix_as_shipped], ignore_index = True)
    decision_mix_table.to_csv(OUTPUT_FOLDER / "table_decision_mix.csv", index = False)
    speed_table.to_csv(OUTPUT_FOLDER / "table_decision_speed.csv", index = False)
    summary_text = "\n\n".join([
        "# Figures for the pitch",
        "Every figure comes from a replay of the 45 purchases of Viseca's five public scenarios. A purchase counts as risky when its review says it needs a refusal or a question on its own facts, which holds for "
        + str(risky_count) + " of the 45. The three setups are compared on that reading, where the engine approves a clean second order of the one requested thing with a note. As it ships, the engine asks the customer about such an order, which turns five approvals into questions and changes nothing for the "
        + str(risky_count) + " risky purchases.",
        "## Headline by setup",
        write_markdown_table(headline_table),
        "## The risky purchases by kind of problem",
        write_markdown_table(family_table),
        "## The decisions of the engine under both readings",
        write_markdown_table(decision_mix_table),
        "## The time of one decision",
        write_markdown_table(speed_table),
    ]) + "\n"
    (OUTPUT_FOLDER / "pitch_figures.md").write_text(summary_text, encoding = "utf-8")



    # Print the tables
    print("--- Headline by setup ---")
    print(headline_table.to_string(index = False))
    print()
    print("--- The risky purchases by kind of problem ---")
    print(family_table.to_string(index = False))
    print()
    print("--- The decisions of the engine under both readings ---")
    print(decision_mix_table.to_string(index = False))
    print()
    print("--- The time of one decision ---")
    print(speed_table.to_string(index = False))
    print()
    print("--- Files written ---")
    print(OUTPUT_FOLDER)


if __name__ == "__main__":
    main()
