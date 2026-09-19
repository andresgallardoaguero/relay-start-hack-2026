# Script: test_period_budget_reading.py
# Purpose: Check that a budget over a period is read from the published instructions, from phrasings outside the published data and from machine-readable rules
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pandas as pd
import pytest

from app.models.events import MandateRule
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.period_budget import PeriodBudgetReading, read_period_budget









#### Step 1: Define the cases ####

# Locate the published scenario catalogue and the source file of the reader
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
SCENARIO_CATALOGUE_PATH = REPOSITORY_FOLDER / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
READER_SOURCE_PATH = REPOSITORY_FOLDER / "backend" / "app" / "policyc" / "period_budget.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id")



# Describe one phrasing with the budget it must give, where expects_question None means the questions are not checked
@dataclass(frozen = True)
class PhrasingCase:
    instruction: str
    expected_limit: Optional[Decimal]
    expected_days: Optional[int]
    expected_inclusive: bool
    expected_reading: str
    expects_question: Optional[bool]



# List phrasings that are not in the published data
PHRASING_CASES = [
    PhrasingCase("Buy groceries, no more than CHF 250 per week.", Decimal("250"), 7, True, "read", False),
    PhrasingCase("Order what we need, up to CHF 500 a month.", Decimal("500"), 30, True, "read", False),
    PhrasingCase("Buy office supplies, CHF 400 over 14 days.", Decimal("400"), 14, True, "read", True),
    PhrasingCase("Buy snacks for the team, no more than CHF 150 in any two weeks.", Decimal("150"), 14, True, "read", False),
    PhrasingCase("Coffee for the office, CHF 100 weekly.", Decimal("100"), 7, True, "read", True),
    PhrasingCase("Buy the garden materials with a budget of CHF 900 for the next three months.", Decimal("900"), 90, True, "read", False),
    PhrasingCase("Buy the renovation materials, under CHF 1'200 in total for the whole project.", Decimal("1200"), None, False, "read", False),
    PhrasingCase("Buy what the trip needs but stay below CHF 200 within 10 days.", Decimal("200"), 10, False, "read", False),
    PhrasingCase("Book the hotel, at most CHF 220 per night, CHF 900 in total.", Decimal("900"), None, True, "read", True),
    PhrasingCase("Order lunch, at most CHF 300 per week including delivery.", Decimal("300"), 7, True, "read", False),
    PhrasingCase("Buy groceries, at most CHF 20 per order and at most CHF 50 across any seven days. Ask me when uncertain.", Decimal("50"), 7, True, "read", False),
    PhrasingCase("Top up the transit pass, not more than CHF 60 each month.", Decimal("60"), 30, True, "read", False),
    PhrasingCase("Buy stationery, at most CHF 1,000 per year.", Decimal("1000"), 365, True, "read", False),
    PhrasingCase("Buy groceries, at most CHF 250 each week.", Decimal("250"), 7, True, "read", False),
    PhrasingCase("Buy groceries, no more than CHF 250 in any week.", Decimal("250"), 7, True, "read", False),
    PhrasingCase("Buy what the workshop needs, up to CHF 2'000 a year.", Decimal("2000"), 365, True, "read", False),
    PhrasingCase("Order lunch, at most CHF 50 with two days delivery.", None, None, True, "not_stated", False),
    PhrasingCase("Buy books, at most EUR 300 per month.", None, None, True, "unclear", True),
    PhrasingCase("Spend at least CHF 50 per week so delivery stays free.", None, None, True, "not_stated", False),
    PhrasingCase("Pay no more than CHF 200 if it can be returned within 14 days.", None, None, True, "not_stated", False),
    PhrasingCase("Buy snacks for CHF 40 per order. Deliver within 3 days.", None, None, True, "not_stated", False),
    PhrasingCase("Buy one grocery item for CHF 20 or less.", None, None, True, "not_stated", False),
    PhrasingCase("Buy whatever seems reasonable. Ask me when uncertain.", None, None, True, "not_stated", False),
]



# Build one rule on the franc amount of the purchase, with the given fields on top
def build_rule(rule_fields):
    return MandateRule(**{"field": "billing_amount_chf", **rule_fields})



# Build the three fields of a mandate that the compiler reads
def build_mandate(instruction):
    return SimpleNamespace(instruction = instruction, hard_rules = (), uncertainty_policy = "ask")









#### Step 2: Check the published instructions ####

# Check that the household instruction gives CHF 300 over seven days, inclusive and without a question, and the other four give no budget.
# The word "total" next to "any seven days" does not make the budget run over the whole mandate.
def test_published_instructions_give_one_budget():
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    readings_by_name = {
        scenario_name: read_period_budget(instruction, ())
        for scenario_name, instruction in zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"])
    }
    assert len(readings_by_name) == 5
    assert readings_by_name["Household budget"] == PeriodBudgetReading(
        limit_chf = Decimal("300"),
        period_days = 7,
        inclusive = True,
        reading = "read",
        open_questions = (),
    )
    other_readings = [reading for scenario_name, reading in readings_by_name.items() if scenario_name != "Household budget"]
    assert len(other_readings) == 4
    assert all(reading.limit_chf is None and reading.period_days is None for reading in other_readings)
    assert {reading.reading for reading in other_readings} == {"not_stated"}
    assert all(reading.open_questions == () for reading in other_readings)









#### Step 3: Check the unseen phrasings ####

# Check the amount, the days, whether the budget is inclusive, the reading and the open questions of every phrasing
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_phrasing_gives_the_expected_budget(case):
    reading = read_period_budget(case.instruction, ())
    assert isinstance(reading, PeriodBudgetReading)
    assert reading.limit_chf == case.expected_limit
    assert reading.period_days == case.expected_days
    assert reading.inclusive == case.expected_inclusive
    assert reading.reading == case.expected_reading
    assert isinstance(reading.open_questions, tuple)
    assert all(isinstance(open_question, str) and open_question != "" for open_question in reading.open_questions)
    if case.expects_question is not None:
        assert (len(reading.open_questions) > 0) == case.expects_question



# Check that an amount without a bound word states its reading in the question
def test_amount_without_a_bound_word_states_its_reading():
    reading = read_period_budget("Buy office supplies, CHF 400 over 14 days.", ())
    assert len(reading.open_questions) == 1
    assert "CHF 400.00" in reading.open_questions[0]
    assert "over 14 days" in reading.open_questions[0]
    assert reading.open_questions[0].endswith("Is that right?")



# Check that a word about a term of the order after a count makes the count an order term,
# while the same word after a single period such as "per week" leaves the budget in place
def test_order_term_after_a_count_gives_no_budget():
    counted_reading = read_period_budget("At most CHF 50 with two days delivery.", ())
    single_reading = read_period_budget("At most CHF 300 per week including delivery.", ())
    assert (counted_reading.limit_chf, counted_reading.reading, counted_reading.open_questions) == (None, "not_stated", ())
    assert (single_reading.limit_chf, single_reading.period_days, single_reading.inclusive) == (Decimal("300"), 7, True)



# Check that an amount in another currency is asked about and never becomes a budget
def test_foreign_currency_is_unclear_and_never_a_budget():
    reading = read_period_budget("Buy books, at most EUR 300 per month.", ())
    assert reading.limit_chf is None
    assert reading.reading == "unclear"
    assert len(reading.open_questions) == 1
    assert "EUR 300.00" in reading.open_questions[0]



# Check that a period that cannot be read is asked about and never guessed
def test_period_that_cannot_be_read_is_unclear():
    reading = read_period_budget("Buy snacks, at most CHF 80 over the coming days.", ())
    assert reading.limit_chf is None
    assert reading.reading == "unclear"
    assert len(reading.open_questions) == 1



# Check that two budgets over the same period give the smaller one, and on equal amounts the exclusive one
def test_same_period_takes_the_smallest_amount_and_then_the_exclusive_one():
    smaller_reading = read_period_budget("Spend at most CHF 300 per week, and no more than CHF 250 per week.", ())
    exclusive_reading = read_period_budget("Spend at most CHF 300 per week, but stay under CHF 300 per week.", ())
    assert (smaller_reading.limit_chf, smaller_reading.period_days, smaller_reading.inclusive) == (Decimal("250"), 7, True)
    assert (exclusive_reading.limit_chf, exclusive_reading.period_days, exclusive_reading.inclusive) == (Decimal("300"), 7, False)
    assert smaller_reading.open_questions == ()



# Check that two budgets over different periods give the smaller amount and a question that names both
def test_different_periods_take_the_smallest_amount_and_ask():
    reading = read_period_budget("Spend at most CHF 300 per week, and at most CHF 1,000 per month.", ())
    assert (reading.limit_chf, reading.period_days) == (Decimal("300"), 7)
    assert len(reading.open_questions) == 1
    assert "CHF 300.00 over 7 days" in reading.open_questions[0]
    assert "CHF 1000.00 over 30 days" in reading.open_questions[0]









#### Step 4: Check the rules ####

# Check that a rule with the scope period and seven days is read as a budget
def test_rule_with_scope_period_is_read():
    reading = read_period_budget("Buy groceries.", (build_rule({"operator": "<=", "value": 250, "scope": "period", "period_days": 7, "currency": "CHF"}),))
    assert reading == PeriodBudgetReading(limit_chf = Decimal("250"), period_days = 7, inclusive = True, reading = "read", open_questions = ())



# Check that the stricter of rule and instruction wins, in both directions, and that the operator < makes a budget exclusive
def test_stricter_of_rule_and_instruction_wins():
    instruction = "Order our groceries, and keep the total across any seven days at or below CHF 300."
    stricter_rule = build_rule({"operator": "<=", "value": 250, "scope": "period", "period_days": 7})
    looser_rule = build_rule({"operator": "<=", "value": 400, "scope": "period", "period_days": 7})
    exclusive_rule = build_rule({"field": "authorization.billing_amount_chf", "operator": "<", "value": 300, "period_days": 7})
    assert read_period_budget(instruction, (stricter_rule,)).limit_chf == Decimal("250")
    assert read_period_budget(instruction, (looser_rule,)).limit_chf == Decimal("300")
    exclusive_reading = read_period_budget(instruction, (exclusive_rule,))
    assert (exclusive_reading.limit_chf, exclusive_reading.inclusive) == (Decimal("300"), False)



# Check that a rule with the scope period and no number of days runs over the whole mandate
def test_rule_without_days_runs_over_the_whole_mandate():
    reading = read_period_budget("Buy the materials.", (build_rule({"operator": "<=", "value": 900, "scope": "period"}),))
    assert (reading.limit_chf, reading.period_days, reading.reading) == (Decimal("900"), None, "read")



# Check the rules that set no budget, which are a limit on one purchase, another field, a text value, another currency and a lower bound
@pytest.mark.parametrize(
    "rule_fields",
    [
        {"operator": "<=", "value": 120},
        {"operator": "<=", "value": 120, "scope": "purchase"},
        {"field": "authorization.delivery_fee", "operator": "<=", "value": 5, "scope": "period", "period_days": 7},
        {"operator": "<=", "value": "250", "scope": "period", "period_days": 7},
        {"operator": "<=", "value": 250, "scope": "period", "period_days": 7, "currency": "EUR"},
        {"operator": ">=", "value": 250, "scope": "period", "period_days": 7},
    ],
    ids = ["no scope", "scope purchase", "another field", "text value", "another currency", "lower bound"],
)
def test_rule_that_sets_no_budget_is_ignored(rule_fields):
    reading = read_period_budget("Buy groceries.", (build_rule(rule_fields),))
    assert reading.limit_chf is None
    assert reading.reading == "not_stated"









#### Step 5: Check the compiler and the source ####

# Check that the compiler fills the four budget fields and the split order minutes, and keeps the limit per order apart from the budget
def test_compiler_fills_the_budget_fields():
    mandate = build_mandate(
        "Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery, and keep the total across any seven days at or below CHF 300. Ask me when uncertain."
    )
    expectations = build_policy_from_mandate(mandate).expectations
    assert expectations.per_order_limit_chf == Decimal("120")
    assert expectations.period_limit_chf == Decimal("300")
    assert expectations.period_days == 7
    assert expectations.period_limit_inclusive is True
    assert expectations.period_limit_reading == "read"
    assert expectations.split_order_window_minutes == 120



# Check that the questions of the budget follow the questions of the limit per order
def test_compiler_appends_the_budget_questions_after_the_order_limit_questions():
    mandate = build_mandate("Book the hotel, at most CHF 220 per night, CHF 900 in total.")
    policy = build_policy_from_mandate(mandate)
    budget_questions = read_period_budget(mandate.instruction, ()).open_questions
    assert len(budget_questions) == 1
    assert budget_questions[0] in policy.open_questions
    assert policy.open_questions.index(budget_questions[0]) > 0



# Check the source text of the reader for the words that would tie a reading to a test case
def test_source_names_no_identifier():
    source_text = READER_SOURCE_PATH.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
