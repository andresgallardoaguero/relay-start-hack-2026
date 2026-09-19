# Script: test_merchant_type_reading.py
# Purpose: Check that the kind of shop is read from the published instructions, from unseen phrasings and from rules, and that it reaches the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from app.models.events import MandateRule, read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.merchant_type import MerchantTypeReading, read_merchant_type









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State what each published instruction gives, keyed by the name of its row in the table, as the categories first and strict second
EXPECTED_PUBLIC_MERCHANT_TYPES = {
    "Connection check": ((), False),
    "Household budget": ((), False),
    "Requested item and order terms": (("sporting_goods",), True),
    "Session integrity": ((), False),
    "Manipulated agent": ((), False),
}



# Describe one phrasing with the categories it must give and whether it must be strict
@dataclass(frozen = True)
class PhrasingCase:
    instruction: str
    expected_categories: tuple
    expected_strict: bool



# List phrasings that appear nowhere in the published data
PHRASING_CASES = [
    PhrasingCase("Buy hiking boots only from a specialist sports shop for at most CHF 260.", ("sporting_goods",), True),
    PhrasingCase("Order my meal after a late shift from a delivery service I use.", ("food_delivery",), False),
    PhrasingCase("Order our groceries for the week from the supermarket.", ("groceries",), False),
    PhrasingCase("Buy a phone charger from an electronics store.", ("electronics",), False),
    PhrasingCase("Book a hotel room for my trip.", (), False),
    PhrasingCase("Buy sports socks.", (), False),
    PhrasingCase("Buy from a shop I use regularly.", (), False),
    PhrasingCase("Buy the monitor from a seller I have bought from before.", (), False),
    PhrasingCase("Order the textbook, and it must come from a bookshop.", ("books",), True),
    PhrasingCase("Buy the drill exclusively from DIY stores.", ("home_improvement",), True),
    PhrasingCase("Get the dog food from pet shops or from pharmacies.", ("health", "pet_care"), False),
    PhrasingCase("Buy only organic apples, from a grocery store.", ("groceries",), False),
    PhrasingCase("Fill the tank at a petrol station for at most CHF 90.", ("fuel",), False),
    PhrasingCase("Buy a jacket from a sporting-goods retailer or a fashion retailer.", ("clothing", "sporting_goods"), False),
    PhrasingCase("ORDER LUNCH FROM A RESTAURANT.", ("dining",), False),
]



# Describe one rule case, with the rules and the reading they must give, where expects_question says whether an open question must follow
@dataclass(frozen = True)
class RuleCase:
    name: str
    instruction: str
    rules: tuple
    expected_categories: tuple
    expected_strict: bool
    expects_question: bool



# State the two sentences the rule cases stand on
SPORTS_INSTRUCTION = "Buy only from a specialist sports retailer."
INSTRUCTION_WITHOUT_SHOP_WORDS = "Buy me something nice."



# List the rules that count and the rules that are ignored, where an ignored rule leaves the reading of the instruction in place
RULE_CASES = [
    RuleCase("in with sporting goods and clothing leaves sporting goods", SPORTS_INSTRUCTION, (MandateRule(field = "merchant_category", operator = "in", value = ("sporting_goods", "clothing")),), ("sporting_goods",), True, False),
    RuleCase("in with clothing alone gives clothing and a question", SPORTS_INSTRUCTION, (MandateRule(field = "merchant_category", operator = "in", value = ("clothing",)),), ("clothing",), True, True),
    RuleCase("rule alone gives its list", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "merchant_category", operator = "in", value = ("hotel", "books")),), ("books", "hotel"), True, False),
    RuleCase("equal to a text gives that category", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "merchant_category", operator = "=", value = "hotel"),), ("hotel",), True, False),
    RuleCase("rule with the full field path counts", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "authorization.merchant.merchant_category", operator = "in", value = ("hotel",)),), ("hotel",), True, False),
    RuleCase("rule with the merchant field path counts", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "merchant.merchant_category", operator = "in", value = ("hotel",)),), ("hotel",), True, False),
    RuleCase("two rules count where they overlap", "Buy from a sports shop.", (MandateRule(field = "merchant_category", operator = "in", value = ("sporting_goods", "clothing")), MandateRule(field = "merchant_category", operator = "in", value = ("sporting_goods",))), ("sporting_goods",), True, False),
    RuleCase("two rules that share nothing give the last one and a question", "Buy from a sports shop.", (MandateRule(field = "merchant_category", operator = "in", value = ("clothing",)), MandateRule(field = "merchant_category", operator = "in", value = ("books",))), ("books",), True, True),
    RuleCase("rule makes a loose instruction strict", "Buy a phone charger from an electronics store.", (MandateRule(field = "merchant_category", operator = "in", value = ("electronics",)),), ("electronics",), True, False),
    RuleCase("rule on another field is ignored", SPORTS_INSTRUCTION, (MandateRule(field = "item_category", operator = "in", value = ("clothing",)),), ("sporting_goods",), True, False),
    RuleCase("rule on another field alone gives nothing", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "item_category", operator = "in", value = ("clothing",)),), (), False, False),
    RuleCase("rule with a number as value is ignored", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "merchant_category", operator = "=", value = 5),), (), False, False),
    RuleCase("rule with another operator is ignored", INSTRUCTION_WITHOUT_SHOP_WORDS, (MandateRule(field = "merchant_category", operator = "not_in", value = ("hotel",)),), (), False, False),
]









#### Step 2: Check the published instructions ####

# Check that of the five published instructions only one names a kind of shop, and that none raises a question
def test_published_instructions_give_their_merchant_types():
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    readings_by_name = {
        scenario_name: read_merchant_type(instruction, ())
        for scenario_name, instruction in zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"])
    }
    observed_merchant_types = {scenario_name: (reading.required_merchant_categories, reading.is_strict) for scenario_name, reading in readings_by_name.items()}
    assert observed_merchant_types == EXPECTED_PUBLIC_MERCHANT_TYPES
    assert {reading.open_questions for reading in readings_by_name.values()} == {()}









#### Step 3: Check the unseen phrasings ####

# Check the categories of every phrasing, as a sorted tuple, whether it is strict, and that an instruction alone never raises a question
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_phrasing_gives_the_expected_merchant_type(case):
    reading = read_merchant_type(case.instruction, ())
    assert isinstance(reading, MerchantTypeReading)
    assert reading.required_merchant_categories == case.expected_categories
    assert reading.is_strict is case.expected_strict
    assert reading.open_questions == ()



# Check that missing rules may arrive as None
def test_missing_rules_may_be_none():
    reading = read_merchant_type(SPORTS_INSTRUCTION, None)
    assert reading.required_merchant_categories == ("sporting_goods",)
    assert reading.is_strict is True



# Check that the result cannot be changed
def test_reading_is_frozen():
    reading = read_merchant_type(SPORTS_INSTRUCTION, ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.is_strict = False









#### Step 4: Check the rules ####

# Check which rules count and how they combine with the instruction
@pytest.mark.parametrize("case", RULE_CASES, ids = [case.name for case in RULE_CASES])
def test_rules_and_instruction_combine(case):
    reading = read_merchant_type(case.instruction, case.rules)
    assert reading.required_merchant_categories == case.expected_categories
    assert reading.is_strict is case.expected_strict
    assert (len(reading.open_questions) == 1) == case.expects_question
    assert len(reading.open_questions) <= 1



# Check that the question about rules and instruction that share no category names both sides in plain words
def test_question_names_both_sides():
    rules = (MandateRule(field = "merchant_category", operator = "in", value = ("clothing",)),)
    reading = read_merchant_type(SPORTS_INSTRUCTION, rules)
    assert reading.open_questions == (
        "The instruction asks for a shop for sporting goods, while the stored rules allow only shops for clothing. "
        "The rules were followed. Which kind of shop should this instruction ask for?",
    )









# Check that the question about rules that contradict each other names the rule that was followed, and stands alone
def test_question_about_contradicting_rules_names_the_last_rule():
    rules = (
        MandateRule(field = "merchant_category", operator = "in", value = ("clothing",)),
        MandateRule(field = "merchant_category", operator = "in", value = ("books",)),
    )
    reading = read_merchant_type("Buy from a sports shop.", rules)
    assert reading.open_questions == (
        "The stored rules contradict each other, because no kind of shop is allowed by all of them. "
        "The last rule was followed, which allows only shops for books. Which kind of shop should this instruction ask for?",
    )









#### Step 5: Check that the reading reaches the policy ####

# Read the example message with another instruction and other rules and return its mandate
def read_mandate(example_message, instruction, hard_rules = ()):
    example_message["mandate"]["instruction"] = instruction
    example_message["mandate"]["hard_rules"] = list(hard_rules)
    return read_purchase_message(example_message).mandate



# Check that the compiler fills the two fields about the kind of shop
def test_compiler_fills_the_merchant_type_fields(example_message):
    mandate = read_mandate(example_message, "Buy hiking boots only from a specialist sports shop for at most CHF 260.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.required_merchant_categories == ("sporting_goods",)
    assert policy.expectations.merchant_category_is_strict is True
    assert policy.open_questions == ()



# Check that an instruction without a kind of shop leaves both fields at their defaults
def test_compiler_leaves_the_defaults_without_a_kind_of_shop(example_message):
    mandate = read_mandate(example_message, "Buy one grocery item for CHF 20 or less from a shop I use regularly.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.required_merchant_categories == ()
    assert policy.expectations.merchant_category_is_strict is False



# Check that the question of the kind of shop comes last, after the questions of the order limit and of the item scope
def test_compiler_puts_the_merchant_type_question_last(example_message):
    hard_rules = [
        {"field": "item_category", "operator": "in", "value": ["books"]},
        {"field": "merchant_category", "operator": "in", "value": ["books"]},
    ]
    mandate = read_mandate(example_message, "Buy clothing from a fashion retailer for at most EUR 150.", hard_rules)
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.required_merchant_categories == ("books",)
    assert len(policy.open_questions) == 3
    assert "EUR 150.00" in policy.open_questions[0]
    assert "stored rules allow only books" in policy.open_questions[1]
    assert "stored rules allow only shops for books" in policy.open_questions[2]
