# Script: order_terms.py
# Purpose: Read from the words of a customer instruction for how many days at least an order must be returnable
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from typing import Optional

from app.policyc.instruction_text import NEGATED_LIMIT_PATTERN, split_clauses, split_sentences
from app.policyc.item_scope import NEGATION_PATTERN









#### Step 1: Build the patterns ####

# Find a word that speaks about returning goods, as in "return", "returned", "returnable" and "send it back"
RETURN_WORD_PATTERN = re.compile("\\breturn(?:s|ed|able|ing)?\\b|\\bsen[dt]\\s+(?:it|them|this|these|those)\\s+back\\b", re.IGNORECASE)



# Find a whole number of days, as in "14 days" and "30-day", where the digits of "14.5 days" are not found
DAYS_PATTERN = re.compile("(?<![\\d.,])(\\d+)(?:\\s+|-)days?\\b", re.IGNORECASE)



# State the smallest and the largest number of days that counts as a return period
SMALLEST_MIN_RETURN_DAYS = 1
LARGEST_MIN_RETURN_DAYS = 3650



# Ask the customer for the number of days, in one plain sentence
RETURN_DAYS_QUESTION = "The instruction asks for returns and states no single readable number of days. For how many days at least should a return be possible?"









#### Step 2: Define what the reader returns ####

# Describe the result, where min_return_days of None means that no return period is enforced
# and open_questions holds plain sentences for the customer
@dataclass(frozen = True)
class OrderTermsReading:
    min_return_days: Optional[int]
    open_questions: tuple



# Hold what one clause with a return word says, which is whether it holds a negation and the numbers of days it names
@dataclass(frozen = True)
class ReturnClause:
    is_negated: bool
    day_numbers: tuple









#### Step 3: Read the clauses that speak about returns ####

# Read one clause that holds a return word. A negated limit phrase goes first, so "no less than 14 days" is no negation.
def read_return_clause(clause):
    clause_without_limit_phrases = NEGATED_LIMIT_PATTERN.sub(" \\g<words_between> ", clause)
    return ReturnClause(
        is_negated = NEGATION_PATTERN.search(clause_without_limit_phrases) is not None,
        day_numbers = tuple(int(days_text) for days_text in DAYS_PATTERN.findall(clause)),
    )



# Read every clause of the instruction that holds a return word, sentence by sentence
def read_return_clauses(instruction):
    return tuple(
        read_return_clause(clause)
        for sentence in split_sentences(instruction)
        for clause in split_clauses(sentence)
        if RETURN_WORD_PATTERN.search(clause) is not None
    )









#### Step 4: Read the order terms ####

# Read the smallest number of return days the instruction asks for. The number must stand in the same clause as the return word.
# A negated clause without a number waives returns, as in "no returns needed", and asks for nothing.
# Every other clause with a return word is a requirement. It is read when all such clauses together name one number of days inside the range
# and none of them holds a negation. Anything else, such as "only if I can send it back", two different numbers
# or a negation next to a number, gives no number and one open question, because a requirement that cannot be read is never dropped silently.
def read_order_terms(instruction):
    return_clauses = read_return_clauses(instruction)
    requirement_clauses = tuple(
        return_clause
        for return_clause in return_clauses
        if not (return_clause.is_negated and len(return_clause.day_numbers) == 0)
    )
    if len(requirement_clauses) == 0:
        return OrderTermsReading(min_return_days = None, open_questions = ())



    # Collect the distinct numbers of days and accept exactly one number inside the range, from clauses without a negation
    distinct_day_numbers = sorted({day_number for return_clause in requirement_clauses for day_number in return_clause.day_numbers})
    a_requirement_is_negated = any(return_clause.is_negated for return_clause in requirement_clauses)
    number_is_readable = (
        not a_requirement_is_negated
        and len(distinct_day_numbers) == 1
        and SMALLEST_MIN_RETURN_DAYS <= distinct_day_numbers[0] <= LARGEST_MIN_RETURN_DAYS
    )
    if not number_is_readable:
        return OrderTermsReading(min_return_days = None, open_questions = (RETURN_DAYS_QUESTION,))
    return OrderTermsReading(min_return_days = distinct_day_numbers[0], open_questions = ())
