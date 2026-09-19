# Script: familiarity.py
# Purpose: Read from the words of a customer instruction whether the shop has to be one the customer used before or one the customer uses regularly
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from typing import Optional

from app.policyc.instruction_text import WORD_PATTERN, build_alternation_pattern, build_alternation_text, split_clauses, split_sentences









#### Step 1: Define the words the reader knows ####

# List the words that name a place to buy from, in the singular. "where" and "somewhere" count, as in "where I normally shop".
PLACE_WORDS = ("shop", "store", "seller", "retailer", "merchant", "grocer", "supermarket", "pharmacy", "bookshop", "bookstore", "restaurant", "place", "site", "website")
OPEN_PLACE_WORDS = ("where", "somewhere", "anywhere", "wherever")



# List the words that describe a shop as one the customer keeps using, as in "my usual shop", and as one the customer knows, as in "a trusted seller"
REGULAR_SHOP_ADJECTIVES = ("usual", "regular", "go-to")
KNOWN_SHOP_ADJECTIVES = ("familiar", "known", "trusted")



# List the words that say how the customer stands to the shop in a phrase such as "a shop I use regularly".
# A word of repeated use sets the bar at regular use. A word of use or of knowing the shop sets the bar at one earlier purchase.
REPEATED_USE_WORDS = ("regularly", "normally", "usually", "often", "always", "frequently", "routinely", "weekly")
USE_WORDS = ("use", "used", "shop", "shopped", "buy", "bought", "order", "ordered", "purchase", "purchased", "visit", "visited", "go")
KNOWING_WORDS = ("know", "trust")



# List the words that turn a condition into a wish, as in "preferably from a shop I know"
SOFTENING_PHRASES = ("preferably", "if possible", "where possible", "when possible", "ideally", "prefer", "would be nice")



# Order the two bars and the two strengths, so the stricter one wins when an instruction states both
BAR_RANK = {"before": 1, "regularly": 2}
STRENGTH_RANK = {"preferred": 1, "required": 2}









#### Step 2: Build the patterns ####

# Write the plural form of a place word, so "pharmacy" gives "pharmacies"
def build_plural_form(place_word):
    if place_word.endswith("y"):
        return place_word[:-1] + "ies"
    return place_word + "s"



# List every place word in the singular and in the plural
ALL_PLACE_WORDS = PLACE_WORDS + tuple(build_plural_form(place_word) for place_word in PLACE_WORDS)



# Find a place followed by what the customer says about it, as in "a shop I use regularly", "shops that we have used before" and "somewhere I've shopped".
# Everything after "I" or "we" up to the end of the clause is kept as the words about the shop.
PLACE_WITH_STATEMENT_PATTERN = re.compile(
    build_alternation_text(ALL_PLACE_WORDS + OPEN_PLACE_WORDS)
    + "(?:\\s+(?:that|which))?"
    + "\\s+(?:I|we)(?:['’](?:ve|d))?\\b"
    + "(?P<words_about_the_shop>.*)",
    re.IGNORECASE,
)



# Find an adjective directly before a place word, with at most one word between them, as in "my usual shop" and "my regular online grocer"
def build_adjective_pattern(adjectives):
    return re.compile(
        build_alternation_text(adjectives) + "(?:\\s+[^\\W_]+)?\\s+" + build_alternation_text(ALL_PLACE_WORDS),
        re.IGNORECASE,
    )

REGULAR_SHOP_PATTERN = build_adjective_pattern(REGULAR_SHOP_ADJECTIVES)
KNOWN_SHOP_PATTERN = build_adjective_pattern(KNOWN_SHOP_ADJECTIVES)



# Find a negation in the words about the shop, as in "a shop I have never used" and "shops I haven't used"
NEGATION_PATTERN = re.compile("\\b(?:not|never|no)\\b|n['’]t\\b", re.IGNORECASE)
SOFTENING_PATTERN = build_alternation_pattern(SOFTENING_PHRASES)









#### Step 3: Define what the reader returns ####

# Describe the result. merchant_familiarity is "required" when the instruction makes a familiar shop a condition, "preferred" when it is a wish,
# and "any" when the instruction says nothing about it. familiarity_bar is "regularly", "before" or None,
# and open_questions holds plain sentences for the customer.
@dataclass(frozen = True)
class FamiliarityReading:
    merchant_familiarity: str
    familiarity_bar: Optional[str]
    open_questions: tuple



# Hold what one clause says, which is the bar it states, whether it is only a wish and whether it speaks about shops the customer has not used
@dataclass(frozen = True)
class ClauseFamiliarity:
    familiarity_bar: Optional[str]
    is_softened: bool
    is_negated: bool









#### Step 4: Read one clause ####

# Read the bar from the words about the shop. Repeated use needs a word of use next to it, so "shops I usually avoid" states no bar.
def read_bar_from_words_about_the_shop(words_about_the_shop):
    words = tuple(word.lower() for word in WORD_PATTERN.findall(words_about_the_shop))
    names_use = any(word in USE_WORDS for word in words)
    names_repeated_use = any(word in REPEATED_USE_WORDS for word in words)
    names_knowing = any(word in KNOWING_WORDS for word in words)
    if names_use and names_repeated_use:
        return "regularly"
    if names_use or names_knowing:
        return "before"
    return None



# Read what one clause says about the familiarity of the shop. A hyphen stays, so "go-to" is found.
# is_softened_by_next_clause is True when the clause after it holds nothing but a softening phrase, as in "from a shop I know, if possible".
def read_clause_familiarity(clause, is_softened_by_next_clause):
    is_softened = is_softened_by_next_clause or SOFTENING_PATTERN.search(clause) is not None



    # Read a place with a statement about it first, because it carries the most words
    place_with_statement = PLACE_WITH_STATEMENT_PATTERN.search(clause)
    if place_with_statement is not None:
        words_about_the_shop = place_with_statement.group("words_about_the_shop")
        familiarity_bar = read_bar_from_words_about_the_shop(words_about_the_shop)
        is_negated = familiarity_bar is not None and NEGATION_PATTERN.search(words_about_the_shop) is not None
        if familiarity_bar is not None:
            return ClauseFamiliarity(familiarity_bar = None if is_negated else familiarity_bar, is_softened = is_softened, is_negated = is_negated)



    # Read an adjective before a place word, where "usual" sets the bar at regular use and "trusted" at one earlier purchase
    if REGULAR_SHOP_PATTERN.search(clause) is not None:
        return ClauseFamiliarity(familiarity_bar = "regularly", is_softened = is_softened, is_negated = False)
    if KNOWN_SHOP_PATTERN.search(clause) is not None:
        return ClauseFamiliarity(familiarity_bar = "before", is_softened = is_softened, is_negated = False)
    return ClauseFamiliarity(familiarity_bar = None, is_softened = False, is_negated = False)



# Report whether a clause holds nothing but a softening phrase
def is_only_a_softening_phrase(clause):
    return SOFTENING_PATTERN.fullmatch(clause.strip()) is not None









#### Step 5: Read the instruction ####

# Read the clauses of one sentence, each together with the knowledge of whether the clause after it softens it
def read_sentence_familiarity(sentence):
    clauses = split_clauses(sentence)
    next_clauses = clauses[1:] + ("",)
    return tuple(
        read_clause_familiarity(clause, is_only_a_softening_phrase(next_clause))
        for clause, next_clause in zip(clauses, next_clauses)
    )



# Read how familiar the shop has to be. Where several clauses state a bar, a condition wins over a wish and regular use wins over one earlier purchase,
# because a shop that meets the stricter reading meets the other one too.
# A clause about shops the customer has not used states no bar, and the customer is asked what was meant.
def read_familiarity(instruction):
    clause_readings = tuple(
        clause_reading
        for sentence in split_sentences(instruction)
        for clause_reading in read_sentence_familiarity(sentence)
    )
    stated_readings = tuple(clause_reading for clause_reading in clause_readings if clause_reading.familiarity_bar is not None)
    open_questions = ()
    if any(clause_reading.is_negated for clause_reading in clause_readings):
        open_questions = (
            "The instruction speaks about shops you have not used, which could not be read as a wish about how familiar the shop has to be. "
            "Should the agent keep to shops you have used before?",
        )



    # Answer that any shop will do when no clause states a bar
    if not stated_readings:
        return FamiliarityReading(merchant_familiarity = "any", familiarity_bar = None, open_questions = open_questions)



    # Take the strictest reading, first by condition over wish and then by the bar
    strictest_reading = max(
        stated_readings,
        key = lambda clause_reading: (
            STRENGTH_RANK["preferred" if clause_reading.is_softened else "required"],
            BAR_RANK[clause_reading.familiarity_bar],
        ),
    )
    return FamiliarityReading(
        merchant_familiarity = "preferred" if strictest_reading.is_softened else "required",
        familiarity_bar = strictest_reading.familiarity_bar,
        open_questions = open_questions,
    )
