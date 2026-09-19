# Script: order_limit.py
# Purpose: Read the spending limit per order from the words of a customer instruction and from its machine-readable rules
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.policyc.instruction_text import build_alternation_pattern, build_alternation_text, find_clause_break_spans, read_negated_limit_kind, spans_overlap









#### Step 1: Define the words the reader knows ####

# Map every currency word, in lower case, to its currency code
CURRENCY_CODE_BY_WORD = {
    "chf": "CHF",
    "fr.": "CHF",
    "francs": "CHF",
    "swiss francs": "CHF",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "€": "EUR",
    "usd": "USD",
    "dollars": "USD",
    "$": "USD",
    "gbp": "GBP",
    "pounds": "GBP",
    "£": "GBP",
}



# List the words that make an amount an upper bound the amount itself still meets
INCLUSIVE_UPPER_BOUND_PHRASES = (
    "or less", "or under", "at most", "at or below", "at or under", "no more than", "not more than", "not exceed",
    "up to", "maximum", "max", "not go over", "not over", "never over", "not above", "never above", "within",
    "capped at", "cap of", "limit of", "limit is", "budget of", "budget is", "about", "around", "roughly",
)



# List the words that make an amount an upper bound the purchase has to stay under
EXCLUSIVE_UPPER_BOUND_PHRASES = ("under", "below", "less than", "cheaper than")



# List the words that make an amount a lower bound, which can never limit an order
LOWER_BOUND_PHRASES = ("at least", "minimum", "or more", "more than")



# List the words that mean a minimum only directly before the amount, as in "over CHF 50".
# Anywhere else they say nothing about the amount, as in "over the weekend" and "above all".
LOWER_BOUND_WORDS_BEFORE_AMOUNT = ("over", "above")



# List the words that tie an amount to one order, to one unit of something, or to a period or a total
ORDER_SCOPE_PHRASES = (
    "per order", "each order", "every order", "per purchase", "each purchase", "per transaction",
    "per booking", "per basket", "one order", "single order",
)
UNIT_SCOPE_PHRASES = ("per night", "a night", "per item", "each item", "per piece", "per person", "per ticket", "per unit")
PERIOD_SCOPE_PHRASES = (
    "per day", "a day", "daily", "per week", "a week", "each week", "every week", "any week", "weekly",
    "per month", "a month", "monthly", "each month", "every month", "any month", "per year", "a year", "each year", "every year",
    "yearly", "annually", "days", "weeks", "months", "years", "in total", "total", "altogether", "overall", "whole project", "for the project",
)



# List the words that show the customer meant to set a limit, even when no amount can be read
LIMIT_WORDS = (
    "limit", "limits", "budget", "at most", "no more than", "not more than", "up to", "or less",
    "maximum", "max", "under", "below", "cheaper than",
)









#### Step 2: Build the patterns ####

# Describe a number with an optional thousands separator, which is a comma, an apostrophe or a typographic apostrophe,
# and with at most two decimals. A number that runs on into more digits is not readable and is not matched at all.
THOUSANDS_SEPARATORS = (",", "'", "’")
NUMBER_TEXT = "(?:\\d{1,3}(?:[,'’]\\d{3})+|\\d+)(?:\\.\\d{1,2})?(?!\\d|[.,'’]\\d)"
CURRENCY_TEXT = build_alternation_text(CURRENCY_CODE_BY_WORD)



# Find a money amount, which is a number right after a currency word or right before one.
# A number before a currency word does not count when another number follows that word, because the word then belongs to the later number.
AMOUNT_PATTERN = re.compile(
    "(?P<currency_before>" + CURRENCY_TEXT + ")\\s*(?P<number_after>" + NUMBER_TEXT + ")"
    + "|(?<![\\w.,'’])(?P<number_before>" + NUMBER_TEXT + ")\\s*(?P<currency_after>" + CURRENCY_TEXT + ")(?!\\s*\\d)",
    re.IGNORECASE,
)
CURRENCY_PATTERN = re.compile(CURRENCY_TEXT, re.IGNORECASE)



# Check the inclusive words first, then the exclusive ones, then the lower ones,
# because "at or below" contains "below" and "no more than" contains "more than"
UPPER_BOUND_PATTERNS_IN_ORDER = (
    ("inclusive_upper", build_alternation_pattern(INCLUSIVE_UPPER_BOUND_PHRASES)),
    ("exclusive_upper", build_alternation_pattern(EXCLUSIVE_UPPER_BOUND_PHRASES)),
)
LOWER_BOUND_PATTERN = build_alternation_pattern(LOWER_BOUND_PHRASES)



# Map what a negated limit phrase says about an amount to the bound it gives
NEGATED_LIMIT_BOUND_BY_KIND = {"upper": "inclusive_upper", "lower": "lower"}



# Find "over" or "above" at the very end of the text before an amount, with nothing but whitespace after the word
LOWER_BOUND_WORD_BEFORE_AMOUNT_PATTERN = re.compile(build_alternation_text(LOWER_BOUND_WORDS_BEFORE_AMOUNT) + "\\s*$", re.IGNORECASE)



# Check the order words first, then the unit words, then the period words
SCOPE_PATTERNS_IN_ORDER = (
    ("order", build_alternation_pattern(ORDER_SCOPE_PHRASES)),
    ("unit", build_alternation_pattern(UNIT_SCOPE_PHRASES)),
    ("period", build_alternation_pattern(PERIOD_SCOPE_PHRASES)),
)
LIMIT_WORD_PATTERN = build_alternation_pattern(LIMIT_WORDS)









#### Step 3: Define what the reader returns ####

# Describe one money amount found in the instruction, with the clause it stands in and what the clause says about it
@dataclass(frozen = True)
class InstructionAmount:
    amount: Decimal
    currency_code: str
    clause: str
    bound: Optional[str]
    scope: Optional[str]



# Describe one possible limit per order, from the instruction or from a rule
@dataclass(frozen = True)
class LimitCandidate:
    limit_chf: Decimal
    inclusive: bool
    source: str
    phrase: str



# Describe the result, where open_questions holds plain sentences for the customer
@dataclass(frozen = True)
class OrderLimitReading:
    limit_chf: Optional[Decimal]
    inclusive: bool
    reading: str
    source: Optional[str]
    phrase: Optional[str]
    open_questions: tuple









#### Step 4: Find the amounts of the instruction ####

# Return the label of the first pattern that is found in the clause, or None when none is found
def read_first_matching_label(clause, labeled_patterns_in_order):
    return next(
        (label for label, pattern in labeled_patterns_in_order if pattern.search(clause) is not None),
        None,
    )



# Turn the text of a number into an exact decimal, without its thousands separators
def read_number_text(number_text):
    digits_and_point = "".join(character for character in number_text if character not in THOUSANDS_SEPARATORS)
    return Decimal(digits_and_point)



# Read the bound of one amount from its clause, a negated limit phrase first, then the upper bounds and the lower bounds last.
# A negation turns the comparison around, so "don't go over" caps the amount and "no less than" sets a minimum.
# The words "over" and "above" without a negation count only when they stand directly before the amount.
def read_bound(clause, clause_text_before_amount):
    negated_limit_kind = read_negated_limit_kind(clause)
    if negated_limit_kind is not None:
        return NEGATED_LIMIT_BOUND_BY_KIND[negated_limit_kind]
    upper_bound = read_first_matching_label(clause, UPPER_BOUND_PATTERNS_IN_ORDER)
    if upper_bound is not None:
        return upper_bound
    lower_phrase_found = LOWER_BOUND_PATTERN.search(clause) is not None
    lower_word_before_amount = LOWER_BOUND_WORD_BEFORE_AMOUNT_PATTERN.search(clause_text_before_amount) is not None
    return "lower" if lower_phrase_found or lower_word_before_amount else None



# Describe one found amount, where its clause runs from the nearest clause break before it to the nearest one after it
def build_instruction_amount(instruction, amount_match, clause_break_spans):
    amount_start, amount_end = amount_match.span()
    clause_start = max([break_end for break_start, break_end in clause_break_spans if break_end <= amount_start], default = 0)
    clause_end = min([break_start for break_start, break_end in clause_break_spans if break_start >= amount_end], default = len(instruction))
    clause = instruction[clause_start:clause_end].strip()



    # Read the number and the currency from whichever side of the number the currency word stands on
    number_text = amount_match.group("number_after") or amount_match.group("number_before")
    currency_word = amount_match.group("currency_before") or amount_match.group("currency_after")
    currency_key = " ".join(currency_word.lower().split())
    return InstructionAmount(
        amount = read_number_text(number_text),
        currency_code = CURRENCY_CODE_BY_WORD[currency_key],
        clause = clause,
        bound = read_bound(clause, instruction[clause_start:amount_start]),
        scope = read_first_matching_label(clause, SCOPE_PATTERNS_IN_ORDER),
    )



# Find every money amount of the instruction, and whether a currency word stands without a readable number
def find_instruction_amounts(instruction):
    amount_matches = tuple(AMOUNT_PATTERN.finditer(instruction))
    amount_spans = tuple(amount_match.span() for amount_match in amount_matches)

    # Keep a clause break only outside every amount, so neither the point in "Fr. 50" nor a comma in a number splits
    clause_break_spans = find_clause_break_spans(instruction, protected_spans = amount_spans)
    instruction_amounts = tuple(
        build_instruction_amount(instruction, amount_match, clause_break_spans)
        for amount_match in amount_matches
    )



    # Notice a currency word that belongs to no amount, as in "two hundred francs"
    currency_word_without_number = any(
        not any(spans_overlap(currency_match.span(), amount_span) for amount_span in amount_spans)
        for currency_match in CURRENCY_PATTERN.finditer(instruction)
    )
    return instruction_amounts, currency_word_without_number









#### Step 5: Choose the limit the instruction gives ####

# Write an amount for the customer, with its currency code and two decimals
def describe_amount(instruction_amount):
    return f"{instruction_amount.currency_code} {instruction_amount.amount:.2f}"



# Say why an amount cannot bound an order, as one question for the customer, or return None when it can
def describe_why_set_aside(instruction_amount):
    if instruction_amount.currency_code != "CHF":
        return describe_amount(instruction_amount) + " is not in Swiss francs, so it cannot serve as the limit per order. What is the most one order may cost in CHF?"
    if instruction_amount.bound == "lower":
        return describe_amount(instruction_amount) + " reads as a minimum and not as a limit. Is there an amount one order must not exceed?"
    if instruction_amount.scope == "unit":
        return describe_amount(instruction_amount) + " is a price for one unit, such as one night or one item, and not a limit for the whole order. What is the most one order may cost?"
    return None



# Choose the limit among the amounts of the instruction and collect the questions for the customer
def choose_instruction_limit(instruction_amounts):

    # Set aside what cannot bound an order, with one question per amount
    reasons_set_aside = tuple(describe_why_set_aside(instruction_amount) for instruction_amount in instruction_amounts)
    set_aside_questions = tuple(reason for reason in reasons_set_aside if reason is not None)
    remaining_amounts = tuple(
        instruction_amount
        for instruction_amount, reason in zip(instruction_amounts, reasons_set_aside)
        if reason is None
    )



    # Group the remaining amounts from the most to the least specific statement about one order.
    # An amount for a period or a total also bounds a single order, so the last group counts when nothing better exists.
    amounts_by_group = (
        ("order scope", tuple(found for found in remaining_amounts if found.scope == "order")),
        ("upper bound without scope", tuple(found for found in remaining_amounts if found.scope is None and found.bound is not None)),
        ("no bound word", tuple(found for found in remaining_amounts if found.scope is None and found.bound is None)),
        ("period scope", tuple(found for found in remaining_amounts if found.scope == "period")),
    )
    chosen_group = next(((group_name, group_amounts) for group_name, group_amounts in amounts_by_group if group_amounts), None)
    if chosen_group is None:
        return None, set_aside_questions



    # Take the smallest amount of the first group that is not empty, and on equal amounts the exclusive one
    group_name, group_amounts = chosen_group
    chosen_amount = min(group_amounts, key = lambda found: (found.amount, found.bound != "exclusive_upper"))
    limit_candidate = LimitCandidate(
        limit_chf = chosen_amount.amount,
        inclusive = chosen_amount.bound != "exclusive_upper",
        source = "instruction",
        phrase = chosen_amount.clause,
    )



    # State the reading as a question when the words leave room for another one
    reading_is_a_guess = group_name in ("no bound word", "period scope") or len(group_amounts) > 1
    if not reading_is_a_guess:
        return limit_candidate, set_aside_questions
    reading_question = describe_amount(chosen_amount) + " was read as the most one order may cost, from the words \"" + chosen_amount.clause + "\". Is that right?"
    return limit_candidate, set_aside_questions + (reading_question,)









#### Step 6: Read the rules ####

# Report whether a rule limits the amount of one purchase in Swiss francs
def rule_gives_order_limit(rule):
    value_is_a_number = isinstance(rule.value, (int, float)) and not isinstance(rule.value, bool)
    return (
        rule.field in ("authorization.billing_amount_chf", "billing_amount_chf")
        and rule.operator in ("<=", "<")
        and value_is_a_number
        and rule.scope in (None, "purchase")
        and rule.currency in (None, "CHF")
        and rule.period_days is None
    )



# Turn every rule that limits one purchase into a candidate, where the operator < makes the limit exclusive.
# Every other rule is ignored here.
def read_rule_limits(hard_rules):
    return tuple(
        LimitCandidate(
            limit_chf = Decimal(str(rule.value)),
            inclusive = rule.operator == "<=",
            source = "hard_rule",
            phrase = rule.field + " " + rule.operator + " " + str(rule.value),
        )
        for rule in hard_rules
        if rule_gives_order_limit(rule)
    )









#### Step 7: Read the limit per order ####

# State the question for an instruction that seems to set a limit without a readable amount
UNREADABLE_LIMIT_QUESTION = "The instruction seems to set a spending limit, but no amount in Swiss francs could be read from it. What is the most one order may cost in CHF?"



# Read the limit per order from the instruction and the rules, where the strictest limit wins
def read_order_limit(instruction, hard_rules):
    instruction_amounts, currency_word_without_number = find_instruction_amounts(instruction)
    instruction_candidate, open_questions = choose_instruction_limit(instruction_amounts)
    instruction_candidates = () if instruction_candidate is None else (instruction_candidate,)
    all_candidates = instruction_candidates + read_rule_limits(hard_rules or ())



    # Take the smallest amount, and on equal amounts the exclusive limit, because False sorts before True
    if all_candidates:
        strictest_candidate = min(all_candidates, key = lambda candidate: (candidate.limit_chf, candidate.inclusive))
        return OrderLimitReading(
            limit_chf = strictest_candidate.limit_chf,
            inclusive = strictest_candidate.inclusive,
            reading = "read",
            source = strictest_candidate.source,
            phrase = strictest_candidate.phrase,
            open_questions = open_questions,
        )



    # Call the reading unclear when something limit-like was there, and make sure the customer is asked about it
    limit_word_without_amount = len(instruction_amounts) == 0 and LIMIT_WORD_PATTERN.search(instruction) is not None
    something_limit_like_was_there = len(open_questions) > 0 or currency_word_without_number or limit_word_without_amount
    if something_limit_like_was_there and len(open_questions) == 0:
        open_questions = (UNREADABLE_LIMIT_QUESTION,)
    return OrderLimitReading(
        limit_chf = None,
        inclusive = True,
        reading = "unclear" if something_limit_like_was_there else "not_stated",
        source = None,
        phrase = None,
        open_questions = open_questions,
    )
