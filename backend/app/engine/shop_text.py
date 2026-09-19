# Script: shop_text.py
# Purpose: Read the size, the return days and a final sale from the sentence a shop wrote about one cart line, as typed facts and with fixed patterns only
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from typing import Optional

from app.models.decision import GuardVerdict









#### Step 1: Build the patterns ####

# Describe one size value, which is a number with at most one decimal, as in "43" and "44.5", or a letter size from "XXXS" to "XXXL"
SIZE_VALUE_TEXT = "(?:\\d{1,3}(?:[.,]\\d)?|x{0,3}[sl]|m)"



# Find a stated size, which is the word "size" and then a size value, as in "size 43", "size 44.5", "size M" and "size XL".
# The value must end where it ends, so neither "size Medium" nor "size 4344" nor "size 43.55" is read as a shorter value.
STATED_SIZE_PATTERN = re.compile("\\bsize\\s+(" + SIZE_VALUE_TEXT + ")(?!\\w|[.,]\\d)", re.IGNORECASE)
SIZE_VALUE_PATTERN = re.compile(SIZE_VALUE_TEXT, re.IGNORECASE)



# Find a number of return days after a return word, as in "returns accepted within 30 days", "return within 14 days" and "returnable for 30 days".
# The number must be whole and must stand directly before the word "days", so "within 14.5 days" is not found.
RETURN_DAYS_AFTER_PATTERN = re.compile(
    "\\breturn(?:s|ed|able)?\\s+(?:(?:is\\s+|are\\s+)?(?:accepted|possible|allowed)\\s+)?(?:within|for|up\\s+to)\\s+(\\d+)(?:\\s+|-)days?\\b",
    re.IGNORECASE,
)



# Find a number of return days before a return word, as in "30 day returns" and "14-day return window"
RETURN_DAYS_BEFORE_PATTERN = re.compile("(?<![\\d.,])(\\d+)(?:\\s+|-)days?\\s+returns?\\b", re.IGNORECASE)



# Find the words of a sale without returns, which are "final sale", "all sales final", "no returns", "non-returnable" and "not returnable"
FINAL_SALE_PATTERN = re.compile(
    "\\bfinal\\s+sale\\b|\\ball\\s+sales\\s+(?:are\\s+)?final\\b|\\bno\\s+returns\\b|\\bnon[-\\s]?returnable\\b|\\bnot\\s+returnable\\b",
    re.IGNORECASE,
)



# Find the words of a shop that leaves its return policy open, as in "return policy not stated" and "returns not stated"
RETURN_POLICY_NOT_STATED_PATTERN = re.compile("\\breturns?(?:\\s+policy)?(?:\\s+(?:is|are))?\\s+not\\s+stated\\b", re.IGNORECASE)



# State the smallest and the largest number of return days that counts as readable
SMALLEST_RETURN_DAYS = 0
LARGEST_RETURN_DAYS = 3650









#### Step 2: Define the facts ####

# Hold what the sentence of one cart line says, as typed facts and never as text, so no word of the shop leaves this file.
# size is a size value in upper case, return_days a whole number of days, and both are None when the sentence gives no exact value.
# A fact is unclear when the sentence speaks about it and cannot be read exactly, as with two different sizes.
# An unclear fact is missing for good, and nothing else may fill it.
@dataclass(frozen = True)
class LineTextFacts:
    size: Optional[str]
    return_days: Optional[int]
    final_sale: bool
    return_policy_not_stated: bool
    size_is_unclear: bool = False
    return_days_are_unclear: bool = False



# State the facts of a sentence that says nothing
EMPTY_LINE_TEXT_FACTS = LineTextFacts(size = None, return_days = None, final_sale = False, return_policy_not_stated = False)



# Hold the facts of one cart line after the sentence and an optional language model were combined
@dataclass(frozen = True)
class SettledLineFacts:
    size: Optional[str]
    return_days: Optional[int]
    final_sale: bool
    return_policy_not_stated: bool









#### Step 3: Read the sizes ####

# Write a size value in one form, in upper case, with a point as the decimal sign and without a decimal of zero, so "44,5" equals "44.5" and "43.0" equals "43"
def normalize_size_value(size_value):
    size_in_upper_case = size_value.strip().upper().replace(",", ".")
    return size_in_upper_case[:-2] if size_in_upper_case.endswith(".0") else size_in_upper_case



# Read a text that should be nothing but a size value, such as "43" or "xl", and return None for anything else.
# A size that does not come from the sentence goes through here, so no free text is ever taken for a size.
def read_size_value(size_value):
    if not isinstance(size_value, str) or SIZE_VALUE_PATTERN.fullmatch(size_value.strip()) is None:
        return None
    return normalize_size_value(size_value)



# Find every distinct size a text states, sorted, where an empty tuple means that the text states none
def find_stated_sizes(text):
    if not isinstance(text, str):
        return ()
    return tuple(sorted({normalize_size_value(size_value) for size_value in STATED_SIZE_PATTERN.findall(text)}))









#### Step 4: Read the facts of one sentence ####

# Report whether a number of days lies inside the readable range
def return_days_are_in_range(return_days):
    return SMALLEST_RETURN_DAYS <= return_days <= LARGEST_RETURN_DAYS



# Read the facts of the sentence of one cart line. The sentence is untrusted, so only the fixed patterns above look at it.
# A sentence that states two different sizes or two different numbers of return days gives None for that fact,
# and so does a number of days outside the range, because a fact that cannot be read exactly is missing.
def read_line_text_facts(text):
    if not isinstance(text, str):
        return EMPTY_LINE_TEXT_FACTS
    stated_sizes = find_stated_sizes(text)
    stated_return_days = tuple(sorted({
        int(days_text)
        for days_text in RETURN_DAYS_AFTER_PATTERN.findall(text) + RETURN_DAYS_BEFORE_PATTERN.findall(text)
    }))
    size_is_exact = len(stated_sizes) == 1
    return_days_are_exact = len(stated_return_days) == 1 and return_days_are_in_range(stated_return_days[0])
    return LineTextFacts(
        size = stated_sizes[0] if size_is_exact else None,
        return_days = stated_return_days[0] if return_days_are_exact else None,
        final_sale = FINAL_SALE_PATTERN.search(text) is not None,
        return_policy_not_stated = RETURN_POLICY_NOT_STATED_PATTERN.search(text) is not None,
        size_is_unclear = len(stated_sizes) > 1,
        return_days_are_unclear = len(stated_return_days) > 0 and not return_days_are_exact,
    )









#### Step 5: Combine the sentence with a language model ####

# Find the facts a language model gave for one cart line, by the number of the line.
# No model, a model call without facts, no entry for the line and two entries for the same line all give None.
def find_model_line(model_extraction, line_no):
    model_facts = None if model_extraction is None else model_extraction.facts
    if model_facts is None:
        return None
    lines_with_the_number = tuple(model_line for model_line in model_facts.lines if model_line.line_no == line_no)
    return lines_with_the_number[0] if len(lines_with_the_number) == 1 else None



# Read the return days of a model, where only a whole number inside the range counts and a boolean is no number
def read_model_return_days(model_return_days):
    if isinstance(model_return_days, bool) or not isinstance(model_return_days, int) or not return_days_are_in_range(model_return_days):
        return None
    return model_return_days



# Settle one fact. The fact of the sentence comes first, and the fact of the model counts only where the sentence says nothing about it.
# Where both exist and differ the fact is missing, and a fact the sentence left unclear stays missing.
# A model can therefore add a fact and never remove or change one.
def settle_one_fact(text_value, text_is_unclear, model_value):
    if text_is_unclear:
        return None
    if text_value is None:
        return model_value
    if model_value is None or model_value == text_value:
        return text_value
    return None



# Settle the facts of one cart line. A final sale counts when the sentence or the model says so, and a model never takes one back.
# Whether the shop leaves its return policy open is read from the sentence alone.
def settle_line_facts(text_facts, model_line = None):
    model_size = None if model_line is None else read_size_value(model_line.size)
    model_return_days = None if model_line is None else read_model_return_days(model_line.return_days)
    model_says_final_sale = model_line is not None and model_line.final_sale is True
    return SettledLineFacts(
        size = settle_one_fact(text_facts.size, text_facts.size_is_unclear, model_size),
        return_days = settle_one_fact(text_facts.return_days, text_facts.return_days_are_unclear, model_return_days),
        final_sale = text_facts.final_sale or model_says_final_sale,
        return_policy_not_stated = text_facts.return_policy_not_stated,
    )









#### Step 6: Let a language model only make a verdict stricter ####

# Rank the verdicts from the mildest to the strictest, where a skip and a pass both let the purchase through
VERDICT_STRICTNESS = {
    GuardVerdict.SKIP: 0,
    GuardVerdict.PASS: 0,
    GuardVerdict.UNCERTAIN: 1,
    GuardVerdict.STEP_UP: 2,
    GuardVerdict.DECLINE: 3,
}



# Pick the stricter of two results of the same guard, one judged on the facts of the shop sentences alone and one judged on the settled facts.
# The result with the model wins only when it is strictly stricter, and it then brings its own evidence and message.
# On a tie the result on the sentences alone stands, so a language model can add a reason to refuse and never a reason to approve.
def pick_stricter_result(result_on_the_text_alone, result_with_the_model):
    if VERDICT_STRICTNESS[result_with_the_model.verdict] > VERDICT_STRICTNESS[result_on_the_text_alone.verdict]:
        return result_with_the_model
    return result_on_the_text_alone
