# Script: test_item_scope_reading.py
# Purpose: Check that the kinds of goods are read from the published instructions, from unseen phrasings and from rules, and that they reach the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

import build_baselines
from app.models.events import MandateRule, read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.instruction_text import read_words, split_clauses, split_sentences
from app.policyc.item_scope import ItemScopeReading, build_categories_by_catalogue_word, read_item_scope
from app.state.item_catalogue import load_item_catalogue









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State the allowed categories each published instruction gives, keyed by the name of its row in the table
EXPECTED_PUBLIC_ALLOWED_CATEGORIES = {
    "Connection check": ("groceries",),
    "Household budget": ("groceries", "household"),
    "Requested item and order terms": ("sporting_goods",),
    "Session integrity": ("clothing",),
    "Manipulated agent": ("electronics",),
}



# Describe one phrasing with the allowed and the prohibited categories it must give
@dataclass(frozen = True)
class PhrasingCase:
    instruction: str
    expected_allowed: tuple
    expected_prohibited: tuple



# List phrasings that appear nowhere in the published data
PHRASING_CASES = [
    PhrasingCase("Renew my learning subscription each month for no more than CHF 19. No upgrades.", ("subscriptions",), ()),
    PhrasingCase("Book a hotel room for my trip for at most CHF 220 per night, cancellable.", ("hotel",), ()),
    PhrasingCase("Order my meal after a late shift for at most CHF 35 from a delivery service I use.", ("food_delivery",), ()),
    PhrasingCase("Buy the materials for one room for at most CHF 900 in total, for collection in store.", ("home_improvement",), ()),
    PhrasingCase("Restock our shared household essentials and cleaning supplies, at most CHF 80 per order.", ("household",), ()),
    PhrasingCase("Buy hiking boots from a specialist sports shop for at most CHF 260.", ("sporting_goods",), ()),
    PhrasingCase("Order dinner for the family for at most CHF 60, no automatic membership enrolment.", ("dining", "food_delivery"), ("membership",)),
    PhrasingCase("Get me a monthly transit pass for up to CHF 90.", ("transport",), ()),
    PhrasingCase("Buy me something nice for up to CHF 50.", (), ()),
    PhrasingCase("Buy a CHF 50 gift voucher for my nephew.", ("gift_card",), ()),
    PhrasingCase("Order our groceries for the week, but never cosmetics or gift cards.", ("groceries",), ("cosmetics", "gift_card")),
    PhrasingCase("Buy new work shoes for at most CHF 150.", ("clothing", "sporting_goods"), ()),
    PhrasingCase("Books for my course, at most CHF 120 in total.", ("books",), ()),
    PhrasingCase("Buy groceries, not cosmetics.", ("groceries",), ("cosmetics",)),
    PhrasingCase("Pay no more than CHF 30 for a paperback book.", ("books",), ()),
    PhrasingCase("Order a team meal at a restaurant for at most CHF 300.", ("dining",), ()),
    PhrasingCase("Charge the car for at most CHF 40.", (), ()),
    PhrasingCase("Don't go over CHF 50 for shoes.", ("clothing", "sporting_goods"), ()),
    PhrasingCase("Don't spend more than CHF 80 on groceries.", ("groceries",), ()),
    PhrasingCase("Order our groceries for the week, but never cosmetics and gift cards.", ("groceries",), ("cosmetics", "gift_card")),
    PhrasingCase("Never gift cards, cosmetics or perfume.", (), ("cosmetics", "gift_card")),
    PhrasingCase("No upgrades, and renew my learning subscription each month.", ("subscriptions",), ()),
    PhrasingCase("Do not buy cosmetics, but groceries are fine.", ("groceries",), ("cosmetics",)),
    PhrasingCase("Avoid cosmetics. Buy groceries.", ("groceries",), ("cosmetics",)),
    PhrasingCase("Never buy shoes over CHF 100.", ("clothing", "sporting_goods"), ()),
    PhrasingCase("Don't pay more than CHF 30 for a paperback book.", ("books",), ()),
]



# Describe one rule case, with the rules and the reading they must give, where expects_question says whether an open question must follow
@dataclass(frozen = True)
class RuleCase:
    name: str
    instruction: str
    rules: tuple
    expected_allowed: tuple
    expected_prohibited: tuple
    expects_question: bool



# State the two sentences the rule cases stand on
CLOTHING_INSTRUCTION = "Buy clothing for me."
INSTRUCTION_WITHOUT_CATEGORY_WORDS = "Buy me something nice."



# List the rules that count and the rules that are ignored, where an ignored rule leaves the clothing of the instruction in place
RULE_CASES = [
    RuleCase("not_in adds both categories to prohibited", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "not_in", value = ("cosmetics", "gift_card")),), ("clothing",), ("cosmetics", "gift_card"), False),
    RuleCase("in with clothing and books leaves clothing", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "in", value = ("clothing", "books")),), ("clothing",), (), False),
    RuleCase("in with books alone gives books and a question", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "in", value = ("books",)),), ("books",), (), True),
    RuleCase("equal to books gives books and a question", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "=", value = "books"),), ("books",), (), True),
    RuleCase("not equal to clothing removes clothing from allowed", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "!=", value = "clothing"),), (), ("clothing",), False),
    RuleCase("rule with the full field path counts", CLOTHING_INSTRUCTION, (MandateRule(field = "authorization.items.item_category", operator = "not_in", value = ("gift_card",)),), ("clothing",), ("gift_card",), False),
    RuleCase("rule with the items field path counts", CLOTHING_INSTRUCTION, (MandateRule(field = "items.item_category", operator = "not_in", value = ("gift_card",)),), ("clothing",), ("gift_card",), False),
    RuleCase("rule on another field is ignored", CLOTHING_INSTRUCTION, (MandateRule(field = "merchant_category", operator = "in", value = ("books",)),), ("clothing",), (), False),
    RuleCase("rule with a number as value is ignored", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "=", value = 5),), ("clothing",), (), False),
    RuleCase("rule on the amount is ignored", CLOTHING_INSTRUCTION, (MandateRule(field = "billing_amount_chf", operator = "<=", value = 250),), ("clothing",), (), False),
    RuleCase("rule alone gives its list", INSTRUCTION_WITHOUT_CATEGORY_WORDS, (MandateRule(field = "item_category", operator = "in", value = ("hotel", "books")),), ("books", "hotel"), (), False),
    RuleCase("two allowed rules count where they overlap", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "in", value = ("clothing", "books")), MandateRule(field = "item_category", operator = "in", value = ("clothing",))), ("clothing",), (), False),
    RuleCase("two allowed rules that share nothing give the last one and a question", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "in", value = ("clothing",)), MandateRule(field = "item_category", operator = "in", value = ("books",))), ("books",), (), True),
    RuleCase("two rules add up", CLOTHING_INSTRUCTION, (MandateRule(field = "item_category", operator = "not_in", value = ("cosmetics",)), MandateRule(field = "item_category", operator = "!=", value = "gift_card")), ("clothing",), ("cosmetics", "gift_card"), False),
]



# Build the item catalogue once into a temporary folder through the functions of the script, and load it from there
@pytest.fixture(scope = "module")
def item_catalogue(tmp_path_factory):
    output_folder = tmp_path_factory.mktemp("item_scope")
    build_baselines.build_and_write_baselines(output_folder = output_folder)
    return load_item_catalogue(output_folder)









#### Step 2: Check the text helpers ####

# Check that words come back in lower case, without hyphens and in their singular form
@pytest.mark.parametrize(
    "text, expected_words",
    [
        ("groceries", ("grocery",)),
        ("shoes", ("shoe",)),
        ("road-running", ("road", "running")),
        ("Gift Cards", ("gift", "card")),
        ("less pass bus", ("less", "pass", "bus")),
        ("27-inch monitor, CHF 45.50!", ("27", "inch", "monitor", "chf", "45", "50")),
        ("", ()),
    ],
)
def test_words_are_read_in_lower_case_and_singular_form(text, expected_words):
    assert read_words(text) == expected_words



# Check that a sentence ends at a point, an exclamation mark or a question mark before whitespace or the end, so an amount stays whole
def test_sentences_are_split_without_breaking_an_amount():
    assert split_sentences("Do not go over CHF 45.50 for the meal. Ask me when uncertain! Is that clear?") == (
        "Do not go over CHF 45.50 for the meal",
        "Ask me when uncertain",
        "Is that clear",
    )
    assert split_sentences("No sentence end here") == ("No sentence end here",)
    assert split_sentences("") == ()



# Check that clauses are split at a comma, at "and" and at "but", and never at "or"
def test_clauses_are_split_at_commas_and_at_and_and_but():
    assert split_clauses("Order our groceries for the week, but never cosmetics or gift cards") == (
        "Order our groceries for the week",
        "never cosmetics or gift cards",
    )
    assert split_clauses("household essentials and cleaning supplies") == ("household essentials", "cleaning supplies")
    assert split_clauses("CHF 20 or less") == ("CHF 20 or less",)









#### Step 3: Check the words of the catalogue ####

# Check that a word names the categories it occurs in, and that generic words, numbers and single letters name none
def test_catalogue_words_name_their_categories(item_catalogue):
    categories_by_catalogue_word = build_categories_by_catalogue_word(item_catalogue)
    assert categories_by_catalogue_word["monitor"] == ("electronics",)
    assert categories_by_catalogue_word["hiking"] == ("sporting_goods",)
    assert categories_by_catalogue_word["shoe"] == ("clothing", "sporting_goods")
    assert categories_by_catalogue_word["meal"] == ("dining", "food_delivery")
    assert categories_by_catalogue_word["storage"] == ("household", "subscriptions")
    words_that_must_be_missing = [word for word in ("order", "gift", "27", "s", "e", "children", "supply") if word in categories_by_catalogue_word]
    assert words_that_must_be_missing == []









#### Step 4: Check the published instructions ####

# Check that each of the five published instructions gives its allowed categories, nothing prohibited and no open question
def test_published_instructions_give_their_categories(item_catalogue):
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    readings_by_name = {
        scenario_name: read_item_scope(instruction, (), item_catalogue)
        for scenario_name, instruction in zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"])
    }
    assert {scenario_name: reading.allowed_item_categories for scenario_name, reading in readings_by_name.items()} == EXPECTED_PUBLIC_ALLOWED_CATEGORIES
    assert {reading.prohibited_item_categories for reading in readings_by_name.values()} == {()}
    assert {reading.open_questions for reading in readings_by_name.values()} == {()}









#### Step 5: Check the unseen phrasings ####

# Check the allowed and the prohibited categories of every phrasing, as sorted tuples, and that an instruction alone never raises a question
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_phrasing_gives_the_expected_categories(item_catalogue, case):
    reading = read_item_scope(case.instruction, (), item_catalogue)
    assert isinstance(reading, ItemScopeReading)
    assert reading.allowed_item_categories == case.expected_allowed
    assert reading.prohibited_item_categories == case.expected_prohibited
    assert reading.open_questions == ()
    assert set(reading.allowed_item_categories + reading.prohibited_item_categories) <= set(item_catalogue.item_categories)



# Check that upper and lower case do not matter
def test_matching_ignores_upper_and_lower_case(item_catalogue):
    reading = read_item_scope("BUY GROCERIES, NOT COSMETICS.", (), item_catalogue)
    assert reading.allowed_item_categories == ("groceries",)
    assert reading.prohibited_item_categories == ("cosmetics",)



# Check that a verb is dropped only as the exact first word of a sentence, so books in the middle of a sentence still count
def test_verb_is_dropped_only_at_the_start_of_a_sentence(item_catalogue):
    assert read_item_scope("Book a table.", (), item_catalogue).allowed_item_categories == ()
    assert read_item_scope("Please buy a book.", (), item_catalogue).allowed_item_categories == ("books",)
    assert read_item_scope("Ask me first. Order fuel for the van.", (), item_catalogue).allowed_item_categories == ("fuel",)



# Check that the result cannot be changed
def test_reading_is_frozen(item_catalogue):
    reading = read_item_scope(CLOTHING_INSTRUCTION, (), item_catalogue)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.allowed_item_categories = ("gift_card",)









#### Step 6: Check the rules ####

# Check which rules count and how they combine with the instruction
@pytest.mark.parametrize("case", RULE_CASES, ids = [case.name for case in RULE_CASES])
def test_rules_and_instruction_combine(item_catalogue, case):
    reading = read_item_scope(case.instruction, case.rules, item_catalogue)
    assert reading.allowed_item_categories == case.expected_allowed
    assert reading.prohibited_item_categories == case.expected_prohibited
    assert (len(reading.open_questions) == 1) == case.expects_question
    assert len(reading.open_questions) <= 1



# Check that the question about rules and instruction that share no category names both sides in plain words
def test_question_names_both_sides(item_catalogue):
    rules = (MandateRule(field = "item_category", operator = "in", value = ("gift_card",)),)
    reading = read_item_scope(CLOTHING_INSTRUCTION, rules, item_catalogue)
    assert reading.open_questions == (
        "The instruction names clothing, while the stored rules allow only gift card. The rules were followed. Which goods should this instruction cover?",
    )









# Check that the question about rules that contradict each other names the rule that was followed, and stands alone
def test_question_about_contradicting_rules_names_the_last_rule(item_catalogue):
    rules = (
        MandateRule(field = "item_category", operator = "in", value = ("clothing",)),
        MandateRule(field = "item_category", operator = "in", value = ("books",)),
    )
    reading = read_item_scope(CLOTHING_INSTRUCTION, rules, item_catalogue)
    assert reading.open_questions == (
        "The stored rules contradict each other, because no kind of goods is allowed by all of them. "
        "The last rule was followed, which allows only books. Which goods should this instruction cover?",
    )









#### Step 7: Check that the reading reaches the policy ####

# Read the example message with another instruction and other rules and return its mandate
def read_mandate(example_message, instruction, hard_rules = ()):
    example_message["mandate"]["instruction"] = instruction
    example_message["mandate"]["hard_rules"] = list(hard_rules)
    return read_purchase_message(example_message).mandate



# Check that the compiler fills both tuples
def test_compiler_fills_both_category_tuples(example_message, item_catalogue):
    mandate = read_mandate(example_message, "Order our groceries for the week for at most CHF 90, but never cosmetics or gift cards.")
    policy = build_policy_from_mandate(mandate, item_catalogue = item_catalogue)
    assert policy.expectations.allowed_item_categories == ("groceries",)
    assert policy.expectations.prohibited_item_categories == ("cosmetics", "gift_card")
    assert policy.expectations.per_order_limit_chf == 90
    assert policy.open_questions == ()



# Check that the questions of the order limit come before those of the item scope
def test_compiler_keeps_the_order_of_the_open_questions(example_message, item_catalogue):
    hard_rules = [{"field": "item_category", "operator": "in", "value": ["books"]}]
    mandate = read_mandate(example_message, "Buy clothing for at most EUR 150.", hard_rules)
    policy = build_policy_from_mandate(mandate, item_catalogue = item_catalogue)
    assert policy.expectations.allowed_item_categories == ("books",)
    assert len(policy.open_questions) == 2
    assert "EUR 150.00" in policy.open_questions[0]
    assert "stored rules allow only books" in policy.open_questions[1]
