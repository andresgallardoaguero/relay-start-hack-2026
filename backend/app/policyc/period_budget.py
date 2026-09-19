# Script: period_budget.py
# Purpose: Read a spending budget over a period, such as CHF 300 across any seven days, from the words of a customer instruction and from its machine-readable rules
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.policyc.instruction_text import WORD_PATTERN, build_alternation_pattern, build_alternation_text, spans_overlap
from app.policyc.order_limit import AMOUNT_PATTERN, find_instruction_amounts, read_bound, read_number_text









#### Step 1: Define the words the reader knows ####

# State how many days each unit of time counts, where a month is thirty days and a year 365
DAYS_IN_A_DAY = 1
DAYS_IN_A_WEEK = 7
DAYS_IN_A_MONTH = 30
DAYS_IN_A_YEAR = 365



# Map every unit word, singular and plural, and every period adverb to its number of days
DAYS_BY_UNIT_WORD = {
    "day": DAYS_IN_A_DAY,
    "days": DAYS_IN_A_DAY,
    "week": DAYS_IN_A_WEEK,
    "weeks": DAYS_IN_A_WEEK,
    "month": DAYS_IN_A_MONTH,
    "months": DAYS_IN_A_MONTH,
    "year": DAYS_IN_A_YEAR,
    "years": DAYS_IN_A_YEAR,
}
DAYS_BY_PERIOD_ADVERB = {
    "daily": DAYS_IN_A_DAY,
    "weekly": DAYS_IN_A_WEEK,
    "monthly": DAYS_IN_A_MONTH,
    "yearly": DAYS_IN_A_YEAR,
    "annually": DAYS_IN_A_YEAR,
}



# Map the number words the reader knows to their count, which are one to twelve, fourteen and thirty
COUNT_BY_NUMBER_WORD = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fourteen": 14,
    "thirty": 30,
}



# List the words that may open a period phrase, as in "within 10 days" and "across any seven days".
# They belong to the period and say nothing about the amount, so they are removed with it before the bound is read.
PERIOD_OPENING_WORDS = ("within", "in", "over", "across", "during", "for", "per", "every", "each")
PERIOD_FILLER_WORDS = ("any", "the", "a", "next", "last", "past", "coming", "following", "rolling")



# List the words that make a single unit a period, as in "per week", "a month" and "any week"
SINGLE_PERIOD_WORDS = ("per", "a", "each", "every", "any")



# List the words that tie an amount to everything bought under the instruction, without any period
TOTAL_PHRASES = ("in total", "total", "altogether", "overall", "whole project", "for the project")



# Describe the start of a word about a term of the order. A count of days next to such a word, as in "returned within 14 days"
# and "two days delivery", states a term of the order and no period of a budget.
ORDER_TERM_WORD_PATTERN = re.compile("^(?:return|refund|deliver|ship|arriv|warrant)", re.IGNORECASE)



# State how many words before a count and how many words after its unit are searched for a word about a term of the order
ORDER_TERM_REACH_BEFORE_IN_WORDS = 4
ORDER_TERM_REACH_AFTER_IN_WORDS = 2



# Name the two fields of a rule that carry the amount of a purchase in Swiss francs
AMOUNT_RULE_FIELDS = ("authorization.billing_amount_chf", "billing_amount_chf")









#### Step 2: Build the patterns ####

# Describe the optional opening of a period phrase, which is one opening word and up to two filler words
PERIOD_OPENING_TEXT = (
    "(?:" + build_alternation_text(PERIOD_OPENING_WORDS) + "\\s+)?"
    + "(?:" + build_alternation_text(PERIOD_FILLER_WORDS) + "\\s+){0,2}"
)
UNIT_TEXT = build_alternation_text(DAYS_BY_UNIT_WORD)



# Find a counted period, which is a digit or a number word before a unit, as in "any seven days", "14 days" and "two weeks"
COUNTED_PERIOD_PATTERN = re.compile(
    PERIOD_OPENING_TEXT
    + "(?P<count>(?<![\\w.,'’])\\d{1,3}(?!\\d|[.,'’]\\d)|" + build_alternation_text(COUNT_BY_NUMBER_WORD) + ")"
    + "[\\s-]*(?P<unit>" + UNIT_TEXT + ")",
    re.IGNORECASE,
)



# Find a single period, which is a word such as "per" before a singular unit, or a period adverb, as in "a month" and "weekly"
SINGLE_PERIOD_PATTERN = re.compile(
    "(?:" + build_alternation_text(("within", "in", "over", "across", "during", "for")) + "\\s+)?"
    + "(?:" + build_alternation_text(SINGLE_PERIOD_WORDS) + "\\s+(?P<unit>" + build_alternation_text(("day", "week", "month", "year")) + ")"
    + "|(?P<adverb>" + build_alternation_text(DAYS_BY_PERIOD_ADVERB) + "))",
    re.IGNORECASE,
)
TOTAL_PATTERN = build_alternation_pattern(TOTAL_PHRASES)









#### Step 3: Define what the reader returns ####

# Describe what a clause says about the period of its amount.
# kind is "period" with a number of days, "total" for the whole mandate, "order_term" when the days belong to a term of the order,
# and "unknown" when the clause names a period that could not be read. phrase_span locates the words that named the period.
@dataclass(frozen = True)
class ClausePeriod:
    kind: str
    period_days: Optional[int]
    phrase_span: Optional[tuple]



# Describe one possible budget, from the instruction or from a rule, where reading_question states a reading the words leave open
@dataclass(frozen = True)
class BudgetCandidate:
    limit_chf: Decimal
    period_days: Optional[int]
    inclusive: bool
    reading_question: Optional[str]



# Describe the result, where period_days None means the budget runs over the whole mandate,
# and open_questions holds plain sentences for the customer
@dataclass(frozen = True)
class PeriodBudgetReading:
    limit_chf: Optional[Decimal]
    period_days: Optional[int]
    inclusive: bool
    reading: str
    open_questions: tuple









#### Step 4: Read the period of one clause ####

# Report whether a word about a term of the order stands within four words before a counted period or within two words after its unit.
# The rule applies to counted periods only, so "per week including delivery" stays a period.
def count_belongs_to_an_order_term(clause, counted_match):
    words_before = WORD_PATTERN.findall(clause[:counted_match.start("count")])[-ORDER_TERM_REACH_BEFORE_IN_WORDS:]
    words_after = WORD_PATTERN.findall(clause[counted_match.end("unit"):])[:ORDER_TERM_REACH_AFTER_IN_WORDS]
    return any(ORDER_TERM_WORD_PATTERN.search(word) is not None for word in words_before + words_after)



# Read the count of a counted period, from digits or from a number word
def read_period_count(count_text):
    if count_text.isdigit():
        return int(count_text)
    return COUNT_BY_NUMBER_WORD[count_text.lower()]



# Read what a clause says about the period of its amount. A counted period comes first, then a single period, then a total word.
# A count that is part of a money amount is no count, and a count next to a word about a term of the order is no period.
def read_clause_period(clause):
    amount_spans = tuple(amount_match.span() for amount_match in AMOUNT_PATTERN.finditer(clause))
    counted_matches = tuple(
        counted_match
        for counted_match in COUNTED_PERIOD_PATTERN.finditer(clause)
        if not any(spans_overlap(counted_match.span("count"), amount_span) for amount_span in amount_spans)
    )
    period_matches = tuple(
        counted_match
        for counted_match in counted_matches
        if not count_belongs_to_an_order_term(clause, counted_match)
    )
    if period_matches:
        first_match = period_matches[0]
        counted_days = read_period_count(first_match.group("count")) * DAYS_BY_UNIT_WORD[first_match.group("unit").lower()]
        if counted_days == 0:
            return ClausePeriod(kind = "unknown", period_days = None, phrase_span = first_match.span())
        return ClausePeriod(kind = "period", period_days = counted_days, phrase_span = first_match.span())



    # Look for a single period, as in "per week" and "monthly"
    single_match = SINGLE_PERIOD_PATTERN.search(clause)
    if single_match is not None:
        if single_match.group("adverb") is not None:
            single_days = DAYS_BY_PERIOD_ADVERB[single_match.group("adverb").lower()]
        else:
            single_days = DAYS_BY_UNIT_WORD[single_match.group("unit").lower()]
        return ClausePeriod(kind = "period", period_days = single_days, phrase_span = single_match.span())



    # Look for a total word without any period, which makes the budget run over the whole mandate.
    # The word "total" next to a period never arrives here, so it does not change that period.
    total_match = TOTAL_PATTERN.search(clause)
    if total_match is not None:
        return ClausePeriod(kind = "total", period_days = None, phrase_span = total_match.span())



    # Set the clause aside when its only days belong to a term of the order, and call the period unknown otherwise
    if counted_matches:
        return ClausePeriod(kind = "order_term", period_days = None, phrase_span = None)
    return ClausePeriod(kind = "unknown", period_days = None, phrase_span = None)









#### Step 5: Read the budgets of the instruction ####

# Write an amount for the customer, with its currency code and two decimals
def describe_amount(instruction_amount):
    return f"{instruction_amount.currency_code} {instruction_amount.amount:.2f}"



# Write the period of a budget for the customer, where no number of days means the whole mandate
def describe_period(period_days):
    if period_days is None:
        return "in total under this instruction"
    if period_days == 1:
        return "over 1 day"
    return "over " + str(period_days) + " days"



# Write a budget for the customer, with its amount and its period
def describe_budget(budget_candidate):
    return f"CHF {budget_candidate.limit_chf:.2f} " + describe_period(budget_candidate.period_days)



# Read the bound of an amount from its clause without the words that named the period,
# so "within 10 days" does not turn "stay below CHF 200 within 10 days" into an inclusive limit
def read_bound_without_period_phrase(instruction_amount, clause_period):
    clause = instruction_amount.clause
    if clause_period.phrase_span is not None:
        phrase_start, phrase_end = clause_period.phrase_span
        clause = clause[:phrase_start] + " " + clause[phrase_end:]
    amount_match = next(
        (
            found_match
            for found_match in AMOUNT_PATTERN.finditer(clause)
            if read_number_text(found_match.group("number_after") or found_match.group("number_before")) == instruction_amount.amount
        ),
        None,
    )
    text_before_amount = "" if amount_match is None else clause[:amount_match.start()]
    return read_bound(clause, text_before_amount)



# Describe what one amount of the instruction gives, which is a budget, a question about something that could not be read, or nothing
@dataclass(frozen = True)
class AmountReading:
    budget_candidate: Optional[BudgetCandidate]
    unclear_question: Optional[str]



# State the reading of an amount that gives nothing
NO_BUDGET = AmountReading(budget_candidate = None, unclear_question = None)



# Read one amount that the instruction ties to a period or a total
def read_instruction_budget(instruction_amount):
    clause_period = read_clause_period(instruction_amount.clause)
    if clause_period.kind == "order_term":
        return NO_BUDGET
    bound = read_bound_without_period_phrase(instruction_amount, clause_period)
    if bound == "lower":
        return NO_BUDGET



    # Ask about a budget that is mentioned and cannot be enforced, which is one in another currency or over a period that could not be read
    if instruction_amount.currency_code != "CHF":
        return AmountReading(
            budget_candidate = None,
            unclear_question = describe_amount(instruction_amount) + " is not in Swiss francs, so it cannot serve as a budget. What is the most you want to spend in CHF, and over how many days?",
        )
    if clause_period.kind == "unknown":
        return AmountReading(
            budget_candidate = None,
            unclear_question = describe_amount(instruction_amount) + " seems to be a budget, but its period could not be read from the words \"" + instruction_amount.clause + "\". Over how many days does it run?",
        )



    # State the reading as a question when no bound word says what the amount means
    reading_question = None
    if bound is None:
        reading_question = (
            describe_amount(instruction_amount) + " was read as the most you want to spend " + describe_period(clause_period.period_days)
            + ", from the words \"" + instruction_amount.clause + "\". Is that right?"
        )
    return AmountReading(
        budget_candidate = BudgetCandidate(
            limit_chf = instruction_amount.amount,
            period_days = clause_period.period_days,
            inclusive = bound != "exclusive_upper",
            reading_question = reading_question,
        ),
        unclear_question = None,
    )









#### Step 6: Read the rules ####

# Report whether a rule sets a budget, which is an upper limit on the franc amount with the scope period or with a number of days
def rule_gives_period_budget(rule):
    value_is_a_number = isinstance(rule.value, (int, float)) and not isinstance(rule.value, bool)
    return (
        rule.field in AMOUNT_RULE_FIELDS
        and rule.operator in ("<=", "<")
        and value_is_a_number
        and rule.currency in (None, "CHF")
        and (rule.scope == "period" or rule.period_days is not None)
    )



# Turn every rule that sets a budget into a candidate, where the operator < makes it exclusive
# and a rule without a number of days runs over the whole mandate
def read_rule_budgets(hard_rules):
    return tuple(
        BudgetCandidate(
            limit_chf = Decimal(str(rule.value)),
            period_days = rule.period_days,
            inclusive = rule.operator == "<=",
            reading_question = None,
        )
        for rule in hard_rules
        if rule_gives_period_budget(rule)
    )









#### Step 7: Read the period budget ####

# Read the budget from the instruction and the rules, where the smallest amount wins and on equal amounts the exclusive one
def read_period_budget(instruction, hard_rules):
    instruction_amounts, currency_word_without_number = find_instruction_amounts(instruction)
    amount_readings = tuple(
        read_instruction_budget(instruction_amount)
        for instruction_amount in instruction_amounts
        if instruction_amount.scope == "period"
    )
    unclear_questions = tuple(amount_reading.unclear_question for amount_reading in amount_readings if amount_reading.unclear_question is not None)
    instruction_candidates = tuple(amount_reading.budget_candidate for amount_reading in amount_readings if amount_reading.budget_candidate is not None)
    all_candidates = instruction_candidates + read_rule_budgets(hard_rules or ())



    # Answer without a budget, where something that looked like one and could not be read makes the reading unclear
    if not all_candidates:
        return PeriodBudgetReading(
            limit_chf = None,
            period_days = None,
            inclusive = True,
            reading = "unclear" if unclear_questions else "not_stated",
            open_questions = unclear_questions,
        )



    # Take the smallest amount, and on equal amounts the exclusive budget, because False sorts before True
    chosen_candidate = min(all_candidates, key = lambda candidate: (candidate.limit_chf, candidate.inclusive))
    reading_questions = () if chosen_candidate.reading_question is None else (chosen_candidate.reading_question,)



    # Say so when the budgets run over different periods, because only the smallest amount is enforced
    other_budget_descriptions = tuple(dict.fromkeys(
        describe_budget(candidate)
        for candidate in all_candidates
        if candidate.period_days != chosen_candidate.period_days
    ))
    several_periods_questions = ()
    if other_budget_descriptions:
        several_periods_questions = (
            "More than one budget was found, " + describe_budget(chosen_candidate) + " and " + " and ".join(other_budget_descriptions)
            + ". Only " + describe_budget(chosen_candidate) + " is enforced. Is that right?",
        )
    return PeriodBudgetReading(
        limit_chf = chosen_candidate.limit_chf,
        period_days = chosen_candidate.period_days,
        inclusive = chosen_candidate.inclusive,
        reading = "read",
        open_questions = unclear_questions + reading_questions + several_periods_questions,
    )
