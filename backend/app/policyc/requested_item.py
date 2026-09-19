# Script: requested_item.py
# Purpose: Read from the words of a customer instruction which one thing it asks for, as the words that name the thing and the size it must have
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.engine.shop_text import find_stated_sizes
from app.policyc.instruction_text import NEGATED_LIMIT_PATTERN, build_alternation_text, read_words, split_clauses, split_sentences
from app.policyc.item_scope import CATEGORY_PHRASE_WORDS, NEGATION_PATTERN, VERBS_THAT_OPEN_A_SENTENCE, build_categories_by_catalogue_word
from app.policyc.request_shape import REQUEST_VERBS









#### Step 1: Define the words the reader knows ####

# Collect every word of every category phrase, in singular form. Such a word names a kind of goods and never one thing,
# so "grocery" in "one ordinary grocery item" and "hotel" in "a hotel room" are left to the covered categories.
CATEGORY_PHRASE_WORD_SET = frozenset(
    phrase_word
    for item_category, phrase_words in CATEGORY_PHRASE_WORDS
    for phrase_word in phrase_words
)



# Find a verb as the very first word of a sentence, and only as that exact word, so "Book a hotel room" and "Order a book" lose their verb and "Books" stays
OPENING_VERB_PATTERN = re.compile("^\\W*" + build_alternation_text(tuple(sorted(set(VERBS_THAT_OPEN_A_SENTENCE + REQUEST_VERBS)))), re.IGNORECASE)









#### Step 2: Define what the reader returns ####

# Describe the result. kind_keywords holds the words that name the thing, in lower case, in singular form and in the order of the instruction.
# attributes maps the name of an attribute to the value the thing must have, and holds "size" when the instruction states one.
# Empty keywords and empty attributes mean that the instruction names no particular thing, which is the customer's choice.
@dataclass(frozen = True)
class RequestedItemReading:
    kind_keywords: tuple
    attributes: Mapping[str, str]
    open_questions: tuple



# State the reading of an instruction that does not ask for one thing
EMPTY_REQUESTED_ITEM_READING = RequestedItemReading(kind_keywords = (), attributes = MappingProxyType({}), open_questions = ())









#### Step 3: Read the words that name the thing ####

# Read the words of one clause that name goods, without repeats. A word names goods when it is a telling word of a catalogue item name
# and belongs to no category phrase. Only the text before a negation counts, so "a helmet, not shoes" and "a monitor without a stand" keep the wanted thing.
# A negated limit phrase goes first, because "for no more than CHF 100" holds a negation word and says nothing about the goods.
def read_clause_keywords(clause, catalogue_words):
    clause_without_limit_phrases = NEGATED_LIMIT_PATTERN.sub(" \\g<words_between> ", clause)
    first_negation = NEGATION_PATTERN.search(clause_without_limit_phrases)
    wanted_text = clause_without_limit_phrases if first_negation is None else clause_without_limit_phrases[:first_negation.start()]
    return tuple(dict.fromkeys(
        word
        for word in read_words(wanted_text)
        if word in catalogue_words and word not in CATEGORY_PHRASE_WORD_SET
    ))



# Read the keywords of the first clause of the instruction that names goods, where a verb that opens a sentence is dropped first
def read_kind_keywords(instruction, catalogue_words):
    keywords_of_each_clause = (
        read_clause_keywords(clause, catalogue_words)
        for sentence in split_sentences(instruction)
        for clause in split_clauses(OPENING_VERB_PATTERN.sub("", sentence, count = 1))
    )
    return next((clause_keywords for clause_keywords in keywords_of_each_clause if len(clause_keywords) > 0), ())









#### Step 4: Read the requested item ####

# Read the thing an instruction asks for. An instruction that does not ask for one thing gives no keywords and no attributes.
# The size is read with the pattern that also reads the sentence of a shop, so both sides are written in the same form.
# An instruction that states two different sizes gives no size and an open question, because a size that cannot be read exactly is missing.
def read_requested_item(instruction, item_catalogue, asks_for_one_thing):
    if not asks_for_one_thing:
        return EMPTY_REQUESTED_ITEM_READING
    catalogue_words = frozenset(build_categories_by_catalogue_word(item_catalogue))
    stated_sizes = find_stated_sizes(instruction)
    size_attributes = {"size": stated_sizes[0]} if len(stated_sizes) == 1 else {}
    size_questions = (
        ("The instruction names more than one size, which are " + " and ".join(stated_sizes) + ". Which size should the item have?",)
        if len(stated_sizes) > 1
        else ()
    )
    return RequestedItemReading(
        kind_keywords = read_kind_keywords(instruction, catalogue_words),
        attributes = MappingProxyType(size_attributes),
        open_questions = size_questions,
    )
