# Script: test_order_limit_reading.py
# Purpose: Check that the limit per order is read from the published instructions, from unseen phrasings and from rules, and that it reaches the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest

from app.models.events import MandateRule, read_purchase_message
from app.models.policy import RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.order_limit import OrderLimitReading, read_order_limit









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State the limit each published instruction gives, keyed by the name of its row in the table
EXPECTED_PUBLIC_LIMITS = {
    "Connection check": Decimal("20"),
    "Household budget": Decimal("120"),
    "Requested item and order terms": Decimal("200"),
    "Session integrity": Decimal("250"),
    "Manipulated agent": Decimal("400"),
}



# Describe one phrasing with the reading it must give, where expects_question is None when the case does not care
@dataclass(frozen = True)
class PhrasingCase:
    instruction: str
    expected_limit: Optional[Decimal]
    expected_inclusive: bool
    expected_reading: str
    expects_question: Optional[bool] = None



# List phrasings that appear nowhere in the published data
PHRASING_CASES = [
    PhrasingCase("Spend at most 80 francs on each order.", Decimal("80"), True, "read", False),
    PhrasingCase("My limit is CHF 1'500 per purchase.", Decimal("1500"), True, "read", False),
    PhrasingCase("My limit is CHF 1’500 per purchase.", Decimal("1500"), True, "read", False),
    PhrasingCase("My limit is CHF 1,200 per purchase.", Decimal("1200"), True, "read", False),
    PhrasingCase("Do not go over CHF 45.50 for the meal.", Decimal("45.50"), True, "read", False),
    PhrasingCase("Keep it under CHF 60.", Decimal("60"), False, "read", False),
    PhrasingCase("Anything below 35 CHF is fine.", Decimal("35"), False, "read", False),
    PhrasingCase("Renew my learning subscription each month for no more than CHF 19. No upgrades.", Decimal("19"), True, "read", True),
    PhrasingCase("Buy the materials for one room for at most CHF 900 in total, for collection in store.", Decimal("900"), True, "read", True),
    PhrasingCase("Keep each order at or below CHF 90 and the total per week at or below CHF 250.", Decimal("90"), True, "read", False),
    PhrasingCase("Spend at least CHF 30 so delivery is free, and at most CHF 70.", Decimal("70"), True, "read", True),
    PhrasingCase("I saw it for CHF 289.", Decimal("289"), True, "read", True),
    PhrasingCase("Book a hotel room for my trip for at most CHF 220 per night, cancellable.", None, True, "unclear", True),
    PhrasingCase("Book a room for at most EUR 150.", None, True, "unclear", True),
    PhrasingCase("Buy me running shoes, no more than two hundred francs.", None, True, "unclear", True),
    PhrasingCase("Buy one requested item within my stated limit. Ask me when uncertain.", None, True, "unclear", True),
    PhrasingCase("Buy the materials for one room over the next three weeks for CHF 900 in total.", Decimal("900"), True, "read", True),
    PhrasingCase("Order snacks over the weekend for CHF 80.", Decimal("80"), True, "read", True),
    PhrasingCase("Buy a gift for over CHF 50.", None, True, "unclear", True),
    PhrasingCase("Above all, keep it at most CHF 40.", Decimal("40"), True, "read"),
    PhrasingCase("Order my meal after a late shift from a delivery service I use.", None, True, "not_stated", False),
    PhrasingCase("I understand the unlimited plan is fine.", None, True, "not_stated", False),
    PhrasingCase("Don't go over CHF 50 for shoes.", Decimal("50"), True, "read", False),
    PhrasingCase("Don't spend more than CHF 80.", Decimal("80"), True, "read", False),
    PhrasingCase("Never pay more than CHF 25 for lunch.", Decimal("25"), True, "read", False),
    PhrasingCase("Spend no less than CHF 30.", None, True, "unclear", True),
    PhrasingCase("Never buy shoes over CHF 100.", Decimal("100"), True, "read", False),
]



# Describe one rule case on the grocery sentence or on a sentence without an amount
@dataclass(frozen = True)
class RuleCase:
    name: str
    instruction: str
    rule_fields: dict
    expected_limit: Decimal
    expected_inclusive: bool
    expected_source: str



# State the two sentences the rule cases stand on
GROCERY_INSTRUCTION = "Buy one grocery item for CHF 20 or less."
INSTRUCTION_WITHOUT_AMOUNT = "Buy one grocery item from a shop I use."



# List the rules that count and the rules that are ignored, where an ignored rule leaves the 20 of the instruction in place
RULE_CASES = [
    RuleCase("stricter rule wins", GROCERY_INSTRUCTION, {"operator": "<=", "value": 15}, Decimal("15"), True, "hard_rule"),
    RuleCase("looser rule loses", GROCERY_INSTRUCTION, {"operator": "<=", "value": 25}, Decimal("20"), True, "instruction"),
    RuleCase("exclusive rule wins on equal amounts", GROCERY_INSTRUCTION, {"operator": "<", "value": 20}, Decimal("20"), False, "hard_rule"),
    RuleCase("rule alone gives the limit", INSTRUCTION_WITHOUT_AMOUNT, {"operator": "<=", "value": 20}, Decimal("20"), True, "hard_rule"),
    RuleCase("rule with the full field path counts", GROCERY_INSTRUCTION, {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 12.5}, Decimal("12.5"), True, "hard_rule"),
    RuleCase("rule with scope purchase and currency CHF counts", GROCERY_INSTRUCTION, {"operator": "<=", "value": 15, "scope": "purchase", "currency": "CHF"}, Decimal("15"), True, "hard_rule"),
    RuleCase("rule with scope period is ignored", GROCERY_INSTRUCTION, {"operator": "<=", "value": 15, "scope": "period", "period_days": 7}, Decimal("20"), True, "instruction"),
    RuleCase("rule with period_days is ignored", GROCERY_INSTRUCTION, {"operator": "<=", "value": 15, "period_days": 7}, Decimal("20"), True, "instruction"),
    RuleCase("rule on another field is ignored", GROCERY_INSTRUCTION, {"field": "authorization.delivery_fee", "operator": "<=", "value": 5}, Decimal("20"), True, "instruction"),
    RuleCase("rule with a text value is ignored", GROCERY_INSTRUCTION, {"operator": "<=", "value": "15"}, Decimal("20"), True, "instruction"),
    RuleCase("rule in EUR is ignored", GROCERY_INSTRUCTION, {"operator": "<=", "value": 15, "currency": "EUR"}, Decimal("20"), True, "instruction"),
    RuleCase("rule with a lower bound operator is ignored", GROCERY_INSTRUCTION, {"operator": ">=", "value": 5}, Decimal("20"), True, "instruction"),
]



# Build one rule on the franc amount of the purchase, with the given fields on top
def build_rule(rule_fields):
    return MandateRule(**{"field": "billing_amount_chf", **rule_fields})









#### Step 2: Check the published instructions ####

# Check that each of the five published instructions gives its limit, inclusive, with the reading read.
# The household instruction names 120 per order and 300 across seven days, and the limit per order is the 120.
def test_published_instructions_give_their_limits():
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    readings_by_name = {
        scenario_name: read_order_limit(instruction, ())
        for scenario_name, instruction in zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"])
    }
    assert {scenario_name: reading.limit_chf for scenario_name, reading in readings_by_name.items()} == EXPECTED_PUBLIC_LIMITS
    assert all(reading.inclusive for reading in readings_by_name.values())
    assert {reading.reading for reading in readings_by_name.values()} == {"read"}
    assert {reading.source for reading in readings_by_name.values()} == {"instruction"}
    assert readings_by_name["Household budget"].limit_chf != Decimal("300")
    assert "CHF 120" in readings_by_name["Household budget"].phrase









#### Step 3: Check the unseen phrasings ####

# Check the limit, whether it is inclusive, the reading and the open questions of every phrasing
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_phrasing_gives_the_expected_reading(case):
    reading = read_order_limit(case.instruction, ())
    assert isinstance(reading, OrderLimitReading)
    assert reading.limit_chf == case.expected_limit
    assert reading.inclusive == case.expected_inclusive
    assert reading.reading == case.expected_reading
    assert isinstance(reading.open_questions, tuple)
    assert all(isinstance(open_question, str) and open_question != "" for open_question in reading.open_questions)
    if case.expects_question is not None:
        assert (len(reading.open_questions) > 0) == case.expects_question



# Check that a read limit names its source and its words, and that a missing limit names neither
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_source_and_phrase_follow_the_limit(case):
    reading = read_order_limit(case.instruction, ())
    if case.expected_limit is None:
        assert reading.source is None
        assert reading.phrase is None
    else:
        assert reading.source == "instruction"
        assert reading.phrase in case.instruction



# Check that the question about a minimum names the minimum and not the limit
def test_minimum_is_named_in_its_question():
    reading = read_order_limit("Spend at least CHF 30 so delivery is free, and at most CHF 70.", ())
    assert len(reading.open_questions) == 1
    assert "CHF 30.00" in reading.open_questions[0]
    assert "70" not in reading.open_questions[0]



# Check that the questions about a price per night and about a foreign amount name the amount
def test_set_aside_amounts_are_named_in_their_questions():
    night_reading = read_order_limit("Book a hotel room for my trip for at most CHF 220 per night, cancellable.", ())
    euro_reading = read_order_limit("Book a room for at most EUR 150.", ())
    assert "CHF 220.00" in night_reading.open_questions[0]
    assert "EUR 150.00" in euro_reading.open_questions[0]



# Check that "over" directly before an amount makes it a minimum, and that the question names the amount
def test_over_before_the_amount_is_named_in_its_question():
    reading = read_order_limit("Buy a gift for over CHF 50.", ())
    assert len(reading.open_questions) == 1
    assert "CHF 50.00" in reading.open_questions[0]



# Check that a negated "less than" reads as a minimum, and that the question names the amount
def test_negated_less_than_is_named_in_its_question():
    reading = read_order_limit("Spend no less than CHF 30.", ())
    assert len(reading.open_questions) == 1
    assert "CHF 30.00" in reading.open_questions[0]



# Check that upper and lower case do not matter
def test_matching_ignores_upper_and_lower_case():
    reading = read_order_limit("KEEP IT UNDER chf 60 PER ORDER.", ())
    assert reading.limit_chf == Decimal("60")
    assert reading.inclusive is False
    assert reading.open_questions == ()



# Check that the currency signs and words are read on either side of the number, and that a foreign amount never becomes the limit
@pytest.mark.parametrize(
    "instruction",
    ["Spend at most €150.", "Spend at most 150 euros.", "Spend at most $150.", "Spend at most 150 dollars.", "Spend at most £150.", "Spend at most 150 pounds.", "Spend at most USD 150.", "Spend at most 150 GBP."],
)
def test_foreign_amount_never_becomes_the_limit(instruction):
    reading = read_order_limit(instruction, ())
    assert reading.limit_chf is None
    assert reading.reading == "unclear"
    assert len(reading.open_questions) == 1



# Check that the result cannot be changed
def test_reading_is_frozen():
    reading = read_order_limit(GROCERY_INSTRUCTION, ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.limit_chf = Decimal("1000")









#### Step 4: Check the rules ####

# Check which rules count and that the strictest limit wins
@pytest.mark.parametrize("case", RULE_CASES, ids = [case.name for case in RULE_CASES])
def test_rules_and_instruction_combine_to_the_strictest_limit(case):
    reading = read_order_limit(case.instruction, (build_rule(case.rule_fields),))
    assert reading.limit_chf == case.expected_limit
    assert reading.inclusive == case.expected_inclusive
    assert reading.source == case.expected_source
    assert reading.reading == "read"



# Check that an ignored rule on a sentence without an amount leaves the limit not stated
def test_ignored_rule_alone_states_no_limit():
    reading = read_order_limit(INSTRUCTION_WITHOUT_AMOUNT, (build_rule({"operator": "<=", "value": 15, "currency": "EUR"}),))
    assert reading.limit_chf is None
    assert reading.reading == "not_stated"
    assert reading.open_questions == ()









#### Step 5: Check that the reading reaches the policy ####

# Read the example message with another instruction and return its mandate
def read_mandate_with_instruction(example_message, instruction):
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message).mandate



# Check the five fields the compiler fills, with the share from the settings when none is handed in
def test_compiler_fills_the_order_limit_fields(example_message):
    mandate = read_mandate_with_instruction(example_message, "Keep it under CHF 60.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.per_order_limit_chf == Decimal("60")
    assert policy.expectations.per_order_limit_inclusive is False
    assert policy.expectations.per_order_limit_reading == "read"
    assert policy.expectations.overshoot_tolerance_share == Decimal("0.10")
    assert policy.open_questions == ()
    assert policy.instruction == "Keep it under CHF 60."
    assert policy.uncertainty_policy == "ask"



# Check that the open questions of the reader reach the policy
def test_compiler_hands_on_the_open_questions(example_message):
    mandate = read_mandate_with_instruction(example_message, "Book a room for at most EUR 150.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.per_order_limit_chf is None
    assert policy.expectations.per_order_limit_reading == "unclear"
    assert len(policy.open_questions) == 1



# Check that a handed-in share of zero reaches the policy
def test_handed_in_share_of_zero_reaches_the_policy(example_message):
    mandate = read_mandate_with_instruction(example_message, GROCERY_INSTRUCTION)
    policy = build_policy_from_mandate(mandate, overshoot_tolerance_share = Decimal("0"))
    assert policy.expectations.overshoot_tolerance_share == Decimal("0")
    assert policy.expectations.per_order_limit_chf == Decimal("20")



# Check that every expectation no reader fills stays at its default.
# The sentence asks for one grocery item, so the requested item holds one unit, and it names no kind of shop and forbids no extras.
def test_other_expectations_stay_at_their_defaults(example_message):
    mandate = read_mandate_with_instruction(example_message, GROCERY_INSTRUCTION)
    expectations = build_policy_from_mandate(mandate).expectations
    assert expectations.period_limit_chf is None
    assert expectations.allowed_item_categories == ("groceries",)
    assert expectations.prohibited_item_categories == ()
    assert expectations.requested_item == RequestedItem(kind_keywords = (), max_quantity = 1, goal_quantity = 1)
    assert expectations.no_addons is False
    assert expectations.required_merchant_categories == ()
    assert expectations.merchant_category_is_strict is False
    assert expectations.merchant_familiarity == "any"
    assert expectations.session_sensitivity == "normal"
