# Script: test_session_reading.py
# Purpose: Check that the reader finds in an instruction whether the customer asks to watch who is driving the session
# Author: Andrés Gallardo
# Date: September 2026

from pathlib import Path

import pandas as pd
import pytest

from app.config import get_settings
from app.models.events import read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.session import SessionReading, read_session_sensitivity









#### Step 1: Define the instructions and what each must give ####

# Locate the published instructions, which the tests only ever read
CASE_DATA_FOLDER = Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data"
INSTRUCTION_TABLE_PATH = CASE_DATA_FOLDER / "scenario_catalogue.csv"



# State what each of the five published instructions must give, in the order of the file
EXPECTED_PUBLIC_READINGS = ["normal", "normal", "normal", "high", "normal"]



# State phrasings outside the published data that ask to watch who is driving the session
WATCHING_PHRASINGS = [
    "Buy socks for CHF 30 or less. Stop if it does not look like me.",
    "Ask me first if my account looks hijacked.",
    "Decline if someone else seems to be using my card.",
    "Hold anything that looks like it is not me.",
    "Pause the order when it doesn't seem to be me.",
    "Pause the order when it doesn’t seem to be me.",
    "Check with me if somebody other than me is controlling the agent.",
    "Block everything if my card was stolen or my phone has been taken over.",
    "Ask me if it isn't me who is ordering.",
    "Stop when the session looks unlike me.",
    "Buy the book, but pause if a stranger is behind the order.",
    "PAUSE ANYTHING THAT LOOKS LIKE SOMEONE OTHER THAN ME IS DRIVING THE SESSION.",
]



# State phrasings that use the same words and say nothing about who is driving the session
HARMLESS_PHRASINGS = [
    "Buy from someone else if my shop is closed.",
    "Book a session of yoga classes for CHF 80 or less.",
    "Buy a gift for someone other than me, up to CHF 50.",
    "Buy the jacket that looks like the one I had.",
    "Do not buy stolen goods.",
    "Order a card game for my nephew.",
    "Buy a phone case for my new device.",
    "Buy me something nice. Ask me when uncertain.",
]



# State phrasings that ask to watch for something vague, which stay at normal and become an open question
VAGUE_PHRASINGS = [
    "Buy socks and stop anything suspicious.",
    "Watch out for fraud.",
    "Tell me about any unusual activity.",
]



# Read the example message with another instruction and return its mandate
def read_mandate(example_message, instruction):
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message).mandate









#### Step 2: Check the published instructions ####

# Check that the five published instructions give normal, normal, normal, high and normal, without an open question
def test_five_public_instructions():
    instruction_table = pd.read_csv(INSTRUCTION_TABLE_PATH, dtype = str, keep_default_na = False)
    instructions = instruction_table.sort_values("scenario_id")["cardholder_instruction"].tolist()
    assert len(instructions) == 5
    readings = [read_session_sensitivity(instruction) for instruction in instructions]
    assert [reading.session_sensitivity for reading in readings] == EXPECTED_PUBLIC_READINGS
    assert [reading.open_questions for reading in readings] == [()] * 5









#### Step 3: Check phrasings outside the published data ####

# Check that a phrasing that asks to watch the session gives high without an open question
@pytest.mark.parametrize("instruction", WATCHING_PHRASINGS)
def test_phrasing_that_asks_to_watch_the_session_gives_high(instruction):
    assert read_session_sensitivity(instruction) == SessionReading(session_sensitivity = "high", open_questions = ())



# Check that a harmless phrasing with the same words gives normal without an open question
@pytest.mark.parametrize("instruction", HARMLESS_PHRASINGS)
def test_harmless_phrasing_gives_normal(instruction):
    assert read_session_sensitivity(instruction) == SessionReading(session_sensitivity = "normal", open_questions = ())



# Check that a vague wish to watch stays at normal and becomes one open question
@pytest.mark.parametrize("instruction", VAGUE_PHRASINGS)
def test_vague_phrasing_gives_normal_and_an_open_question(instruction):
    reading = read_session_sensitivity(instruction)
    assert reading.session_sensitivity == "normal"
    assert len(reading.open_questions) == 1
    assert reading.open_questions[0].endswith("?")



# Check that a clear wish wins over a vague one in the same instruction, so no question is left open
def test_clear_wish_wins_over_a_vague_one():
    reading = read_session_sensitivity("Stop anything suspicious. Pause if it does not look like me.")
    assert reading == SessionReading(session_sensitivity = "high", open_questions = ())



# Check that an empty instruction gives normal
def test_empty_instruction_gives_normal():
    assert read_session_sensitivity("") == SessionReading(session_sensitivity = "normal", open_questions = ())









#### Step 4: Check the compiler ####

# Check that the compiler fills the sensitivity and copies the four numbers of the settings into the policy
def test_compiler_fills_the_sensitivity_and_the_four_numbers(example_message):
    policy = build_policy_from_mandate(read_mandate(example_message, "Buy clothing up to CHF 250 per order. Pause anything that looks like someone other than me is driving the session."))
    assert policy.expectations.session_sensitivity == "high"
    assert policy.expectations.session_ask_signal_count == get_settings().session_ask_signal_count == 2
    assert policy.expectations.session_decline_signal_count == get_settings().session_decline_signal_count == 3
    assert policy.expectations.velocity_min_recent_attempts == get_settings().velocity_min_recent_attempts == 2
    assert policy.expectations.hour_min_history_purchases == get_settings().hour_min_history_purchases == 20



# Check that an instruction without a word about the session leaves the sensitivity at normal
def test_compiler_leaves_the_sensitivity_at_normal(example_message):
    policy = build_policy_from_mandate(read_mandate(example_message, "Buy one grocery item for CHF 20 or less."))
    assert policy.expectations.session_sensitivity == "normal"



# Check that the open question about the session comes last, after the open question about a shop the customer has never used
def test_open_question_about_the_session_comes_last(example_message):
    policy = build_policy_from_mandate(read_mandate(example_message, "Buy one grocery item from a shop I have never used. Stop anything suspicious."))
    assert len(policy.open_questions) >= 2
    assert policy.open_questions[-1] == read_session_sensitivity("Stop anything suspicious.").open_questions[0]
