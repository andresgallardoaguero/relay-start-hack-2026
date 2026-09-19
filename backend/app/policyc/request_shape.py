# Script: request_shape.py
# Purpose: Read from the words of a customer instruction whether it asks for one single thing and whether it forbids extras
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass

from app.policyc.instruction_text import build_alternation_pattern, build_alternation_text, split_clauses, split_sentences









#### Step 1: Define the words the reader knows ####

# List the verbs that ask for something, and the words that make the thing after them a single one, as in "buy one" and "get me a"
REQUEST_VERBS = ("buy", "get", "order", "book", "purchase", "find", "reserve")
SINGLE_THING_WORDS = ("one", "a single", "a", "an")



# List the words that turn "a" into a loose amount, as in "a few" and "a couple of", which is not one thing
LOOSE_AMOUNT_WORDS = ("few", "couple", "lot", "number")



# List the words that show the customer already settled on one thing, as in "the monitor I chose"
SETTLED_CHOICE_PHRASES = ("I chose", "I picked", "I selected", "I saved", "I want")



# List the words that ask to replace one thing the customer owns, as in "replace my worn shoes"
REPLACEMENT_PHRASES = ("replace my",)



# List the words that forbid anything beyond what was asked for
NO_ADDONS_PHRASES = (
    "do not add", "don't add", "don’t add", "never add", "nothing else",
    "no add-ons", "no addons", "no extras", "no upgrades",
    "without add-ons", "without extras", "without upgrades", "only what I asked",
)









#### Step 2: Build the patterns ####

# Find a request verb, then an optional "me", then directly a word for a single thing, unless a loose amount word follows
SINGLE_THING_PATTERN = re.compile(
    build_alternation_text(REQUEST_VERBS)
    + "\\s+(?:me\\s+)?"
    + build_alternation_text(SINGLE_THING_WORDS)
    + "(?!\\s+" + build_alternation_text(LOOSE_AMOUNT_WORDS) + ")",
    re.IGNORECASE,
)



# Find the word "the" with a settled choice later in the same piece of text, which the reader hands in clause by clause
SETTLED_CHOICE_PATTERN = re.compile("\\bthe\\b.*" + build_alternation_text(SETTLED_CHOICE_PHRASES), re.IGNORECASE)
REPLACEMENT_PATTERN = build_alternation_pattern(REPLACEMENT_PHRASES)
NO_ADDONS_PATTERN = build_alternation_pattern(NO_ADDONS_PHRASES)









#### Step 3: Define what the reader returns ####

# Describe the result, where open_questions holds plain sentences for the customer.
# Both answers rest on marker words, so a missing marker means false and never a question.
@dataclass(frozen = True)
class RequestShapeReading:
    asks_for_one_thing: bool
    no_addons: bool
    open_questions: tuple









#### Step 4: Read the shape of the request ####

# Report whether one clause of the instruction names a thing the customer already settled on
def clause_names_a_settled_choice(clause):
    return SETTLED_CHOICE_PATTERN.search(clause) is not None



# Read whether the instruction asks for one single thing and whether it forbids extras.
# The settled choice is looked for clause by clause, so "the" in one clause and "I want" in another do not meet.
def read_request_shape(instruction):
    clauses = tuple(
        clause
        for sentence in split_sentences(instruction)
        for clause in split_clauses(sentence)
    )
    asks_for_one_thing = (
        SINGLE_THING_PATTERN.search(instruction) is not None
        or REPLACEMENT_PATTERN.search(instruction) is not None
        or any(clause_names_a_settled_choice(clause) for clause in clauses)
    )
    return RequestShapeReading(
        asks_for_one_thing = asks_for_one_thing,
        no_addons = NO_ADDONS_PATTERN.search(instruction) is not None,
        open_questions = (),
    )
