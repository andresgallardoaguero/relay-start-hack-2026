# Script: offline.py
# Purpose: Build the in-process copy of the platform from the public purchases, using the message builder of the offline replay
# Author: Jonas Lüthi
# Date: September 2026

import sys
from pathlib import Path

from app.api_client.fake_platform import FakePlatform









#### Step 1: Locate the replay script ####

# Locate the scripts folder from the location of this file
SCRIPTS_FOLDER = Path(__file__).resolve().parent.parent.parent.parent / "scripts"



# Import the replay module, which holds the loader of the public tables and the builder of live-shaped messages
def import_replay_module():
    if str(SCRIPTS_FOLDER) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_FOLDER))
    import replay
    return replay









#### Step 2: Build the platform ####

# Group the joined purchases by scenario in delivery order, each with its cart lines
def group_attempts_by_scenario(replay_tables):
    scenarios = {}
    for attempt in replay_tables.joined_attempts.to_dict("records"):
        scenario_attempts = scenarios.setdefault(attempt["scenario_id"], [])
        scenario_attempts.append({
            "attempt": attempt,
            "cart_lines": replay_tables.cart_lines_by_source_id[attempt["authorization_id"]],
        })
    for scenario_attempts in scenarios.values():
        scenario_attempts.sort(key = lambda entry: entry["attempt"]["replay_order_number"])
    return scenarios



# Read the name of every public scenario from the catalogue, so the scenario selector shows names and not only identifiers
def read_scenario_names(replay):
    catalogue = replay.read_case_table("scenario_catalogue.csv", replay.CASE_DATA_FOLDER)
    return {row["scenario_id"]: row["scenario_name"] for row in catalogue.to_dict("records")}



# Build the platform copy that serves the 45 public purchases, with the same message builder the replay uses
def build_offline_platform(team_api_key, settings = None):
    replay = import_replay_module()
    replay_tables = replay.load_replay_tables()
    return FakePlatform(
        scenarios = group_attempts_by_scenario(replay_tables),
        message_builder = replay.build_purchase_message,
        team_api_key = team_api_key,
        settings = settings,
        scenario_names = read_scenario_names(replay),
    )
