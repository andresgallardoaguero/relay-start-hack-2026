# Script: item_scope.py
# Purpose: Read from the words of a customer instruction and from its machine-readable rules which kinds of goods it covers and which it rules out
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass
from itertools import accumulate, groupby

from app.policyc.instruction_text import NEGATED_LIMIT_PATTERN, build_alternation_pattern, build_alternation_text, read_words, split_clauses_with_breaks, split_sentences









#### Step 1: Define the words the reader knows ####

# List the phrases that name an item category outright. A phrase is compared word by word in the singular form, so "gift cards" finds "gift card".
CATEGORY_PHRASES_BY_CATEGORY = {
    "books": ("books", "book"),
    "clothing": ("clothing", "clothes", "apparel"),
    "cosmetics": ("cosmetics", "beauty products", "fragrance", "perfume"),
    "dining": ("dining", "restaurant"),
    "electronics": ("electronics",),
    "food_delivery": ("food delivery", "meal delivery", "delivery service", "takeaway"),
    "fuel": ("fuel", "petrol", "charging"),
    "gift_card": ("gift card", "gift voucher", "voucher"),
    "groceries": ("groceries", "grocery", "supermarket"),
    "home_improvement": ("home improvement", "renovation", "materials", "paint", "tools"),
    "hotel": ("hotel", "accommodation"),
    "household": ("household", "cleaning"),
    "membership": ("membership",),
    "sporting_goods": ("sporting goods", "sports gear", "sports equipment"),
    "subscriptions": ("subscription", "subscriptions"),
    "transport": ("transport", "ticket", "transit"),
}



# List the words of item names that say nothing about the kind of goods, because they fit goods of many kinds
GENERIC_WORDS = frozenset((
    "order", "purchase", "item", "set", "selection", "basket", "session", "stay", "stop", "service", "bundle",
    "and", "for", "the", "a", "an", "of", "with", "in", "to", "my", "our",
    "weekly", "monthly", "everyday", "family", "team", "weekend", "city", "day",
    "digital", "personal", "professional", "fresh", "prepared", "delivery", "supply", "accessory", "equipment", "part", "material",
    "room", "work", "care", "plan", "pass", "gift", "inch", "restock", "car", "children", "seasonal", "regional", "extended",
))



# List the words that are a verb when they open a sentence, as in "Book a hotel room", and name no goods there
VERBS_THAT_OPEN_A_SENTENCE = ("book", "order", "fuel", "paint", "charge")



# List the words after which the customer names what is not wanted
NEGATION_PHRASES = ("no", "not", "never", "avoid", "without", "except", "nothing", "don't", "don’t", "do not")



# List the words that start a new request, so a negation from an earlier clause does not reach a clause that holds one of them
ACTION_WORDS = (
    "buy", "order", "renew", "book", "get", "keep", "pay", "spend", "use", "choose", "pick", "take", "make",
    "ask", "approve", "pause", "replace", "restock", "fill",
)



# Name the rule fields that speak about the category of a cart line
ITEM_CATEGORY_RULE_FIELDS = ("authorization.items.item_category", "items.item_category", "item_category")









#### Step 2: Build the patterns and the phrase words ####

# Find one of the verbs as the very first word of a sentence, and only as that exact word, so "Books" is not found
OPENING_VERB_PATTERN = re.compile("^\\W*" + build_alternation_text(VERBS_THAT_OPEN_A_SENTENCE), re.IGNORECASE)
NEGATION_PATTERN = build_alternation_pattern(NEGATION_PHRASES)
ACTION_WORD_PATTERN = build_alternation_pattern(ACTION_WORDS)
BUT_PATTERN = build_alternation_pattern(("but",))



# Turn every category phrase into its words once, as pairs of the category and the words of one phrase
CATEGORY_PHRASE_WORDS = tuple(
    (item_category, read_words(category_phrase))
    for item_category, category_phrases in CATEGORY_PHRASES_BY_CATEGORY.items()
    for category_phrase in category_phrases
)









#### Step 3: Define what the reader returns ####

# Describe the result, where both tuples are sorted and open_questions holds plain sentences for the customer.
# Two empty tuples mean that the instruction is open about the kind of goods, which is the customer's choice.
@dataclass(frozen = True)
class ItemScopeReading:
    allowed_item_categories: tuple
    prohibited_item_categories: tuple
    open_questions: tuple









#### Step 4: Collect the words of the catalogue ####

# Report whether a word of an item name can name a kind of goods, which leaves out numbers, single letters and the generic words.
# A single letter is left over from a name such as "Children's" or "E-book" and means nothing on its own.
def word_can_name_goods(word):
    return len(word) > 1 and not word.isdigit() and word not in GENERIC_WORDS



# Read the first part of a pair
def read_first_of_pair(pair):
    return pair[0]



# Map every telling word of every item name to the sorted item categories it occurs in.
# The catalogue is the only source, so a new catalogue brings its own words.
def build_categories_by_catalogue_word(item_catalogue):
    word_and_category_pairs = sorted({
        (word, catalogue_item.item_category)
        for catalogue_item in item_catalogue.items.values()
        for word in read_words(catalogue_item.item_name)
        if word_can_name_goods(word)
    })
    return {
        word: tuple(item_category for pair_word, item_category in pairs_of_word)
        for word, pairs_of_word in groupby(word_and_category_pairs, key = read_first_of_pair)
    }









#### Step 5: Read the categories of a piece of text ####

# Report whether the words of a phrase stand next to each other, in order, among the words of a text
def words_contain_phrase(text_words, phrase_words):
    phrase_length = len(phrase_words)
    return any(
        text_words[start_position:start_position + phrase_length] == phrase_words
        for start_position in range(len(text_words) - phrase_length + 1)
    )



# Read the categories a piece of text names. A category phrase and a word that occurs in one category are certain.
# A word that occurs in two categories counts only when nothing certain was found, and then it names both.
# A word that occurs in three or more categories names none.
def read_categories_of_text(text, categories_by_catalogue_word):
    text_words = read_words(text)
    categories_of_each_word = tuple(categories_by_catalogue_word.get(word, ()) for word in text_words)
    categories_from_phrases = frozenset(
        item_category
        for item_category, phrase_words in CATEGORY_PHRASE_WORDS
        if words_contain_phrase(text_words, phrase_words)
    )
    categories_from_one_category_words = frozenset(
        word_categories[0]
        for word_categories in categories_of_each_word
        if len(word_categories) == 1
    )
    categories_from_two_category_words = frozenset(
        item_category
        for word_categories in categories_of_each_word
        if len(word_categories) == 2
        for item_category in word_categories
    )
    certain_categories = categories_from_phrases | categories_from_one_category_words
    return certain_categories if certain_categories else categories_from_two_category_words









#### Step 6: Walk through the instruction ####

# Drop a verb that opens a sentence, so "Book a hotel room" does not read as a wish for books
def drop_opening_verb(sentence):
    return OPENING_VERB_PATTERN.sub("", sentence, count = 1)



# Hold what one clause says, and whether a negation is still open after it
@dataclass(frozen = True)
class ClauseScope:
    allowed_categories: frozenset
    prohibited_categories: frozenset
    negation_is_open: bool



# State the scope before the first clause of a sentence, where nothing is said and no negation is open
SCOPE_BEFORE_THE_FIRST_CLAUSE = ClauseScope(frozenset(), frozenset(), False)



# Read the scope of one clause, given the scope of the clause before it and the break that stands between the two.
# A negated limit phrase goes first, because "no more than CHF 30" holds a negation word and says nothing about the goods.
# Only its negation word and its comparison word go, and the words between them stay, so "never buy shoes over CHF 100" keeps the shoes.
# With a negation of its own, the text before it is allowed and the text after it is prohibited, and the negation stays open.
# Without one, the clause inherits an open negation, so "never cosmetics and gift cards" rules out both.
# The negation closes when the break holds "but" or the clause holds an action word, and a closed negation leaves the clause allowed.
def read_clause_scope(scope_of_previous_clause, break_and_clause, categories_by_catalogue_word):
    break_before, clause = break_and_clause
    clause_without_limit_phrases = NEGATED_LIMIT_PATTERN.sub(" \\g<words_between> ", clause)
    first_negation = NEGATION_PATTERN.search(clause_without_limit_phrases)
    if first_negation is not None:
        return ClauseScope(
            allowed_categories = read_categories_of_text(clause_without_limit_phrases[:first_negation.start()], categories_by_catalogue_word),
            prohibited_categories = read_categories_of_text(clause_without_limit_phrases[first_negation.end():], categories_by_catalogue_word),
            negation_is_open = True,
        )



    # Decide whether the negation of an earlier clause reaches this one, and put the categories of the clause on that side
    clause_closes_the_negation = BUT_PATTERN.search(break_before) is not None or ACTION_WORD_PATTERN.search(clause_without_limit_phrases) is not None
    negation_is_inherited = scope_of_previous_clause.negation_is_open and not clause_closes_the_negation
    clause_categories = read_categories_of_text(clause_without_limit_phrases, categories_by_catalogue_word)
    return ClauseScope(
        allowed_categories = frozenset() if negation_is_inherited else clause_categories,
        prohibited_categories = clause_categories if negation_is_inherited else frozenset(),
        negation_is_open = negation_is_inherited,
    )



# Read the scope of every clause of one sentence in order, where each clause knows the scope of the one before it.
# A new sentence starts without an open negation.
def read_sentence_scopes(sentence, categories_by_catalogue_word):
    def read_next_clause_scope(scope_of_previous_clause, break_and_clause):
        return read_clause_scope(scope_of_previous_clause, break_and_clause, categories_by_catalogue_word)

    breaks_and_clauses = split_clauses_with_breaks(drop_opening_verb(sentence))
    scopes_with_the_start = tuple(accumulate(breaks_and_clauses, read_next_clause_scope, initial = SCOPE_BEFORE_THE_FIRST_CLAUSE))
    return scopes_with_the_start[1:]



# Read the allowed and the prohibited categories of the whole instruction, sentence by sentence and clause by clause
def read_instruction_scope(instruction, categories_by_catalogue_word):
    clause_scopes = tuple(
        clause_scope
        for sentence in split_sentences(instruction)
        for clause_scope in read_sentence_scopes(sentence, categories_by_catalogue_word)
    )
    allowed_categories = frozenset().union(*(clause_scope.allowed_categories for clause_scope in clause_scopes))
    prohibited_categories = frozenset().union(*(clause_scope.prohibited_categories for clause_scope in clause_scopes))
    return allowed_categories, prohibited_categories









#### Step 7: Read the rules ####

# Read the categories one rule names, which is a list of texts for the list operators and one text for the two equality operators.
# A value of any other type, such as a number, names no category.
def read_rule_categories(rule, list_operator, single_operator):
    if rule.field not in ITEM_CATEGORY_RULE_FIELDS:
        return frozenset()
    if rule.operator == list_operator and isinstance(rule.value, (tuple, list)):
        return frozenset(value for value in rule.value if isinstance(value, str))
    if rule.operator == single_operator and isinstance(rule.value, str):
        return frozenset((rule.value,))
    return frozenset()



# Hold what all rules say together, and whether their allowed lists contradict each other
@dataclass(frozen = True)
class RuleScope:
    allowed_categories: frozenset
    prohibited_categories: frozenset
    allowed_lists_contradict: bool



# Read the allowed and the prohibited categories of all rules, where every rule on another field is ignored.
# Every rule must hold, so the allowed lists of several rules count only where they overlap, and a rule that names no category is left out.
# When the lists share nothing, the list of the last rule counts, because a rule that was added later is the newer wish.
# Lists that share nothing never end as an empty set, because an empty set means that nothing is stated.
# The prohibited categories of several rules add up.
def read_rule_scope(hard_rules):
    allowed_lists = tuple(
        rule_categories
        for rule_categories in (read_rule_categories(rule, "in", "=") for rule in hard_rules)
        if len(rule_categories) > 0
    )
    prohibited_categories = frozenset().union(*(read_rule_categories(rule, "not_in", "!=") for rule in hard_rules))
    if len(allowed_lists) == 0:
        return RuleScope(frozenset(), prohibited_categories, False)
    shared_categories = frozenset.intersection(*allowed_lists)
    if len(shared_categories) > 0:
        return RuleScope(shared_categories, prohibited_categories, False)
    return RuleScope(allowed_lists[-1], prohibited_categories, True)









#### Step 8: Read which goods the instruction covers ####

# Write a list of categories for the customer, with spaces for underscores
def describe_categories(item_categories):
    return ", ".join(item_category.replace("_", " ") for item_category in sorted(item_categories))



# Read the scope from the instruction and the rules.
# The prohibited categories of both sides add up. Where only one side states allowed categories, that side counts,
# and where both do, only what both allow counts. A prohibited category is never allowed.
def read_item_scope(instruction, hard_rules, item_catalogue):
    categories_by_catalogue_word = build_categories_by_catalogue_word(item_catalogue)
    instruction_allowed, instruction_prohibited = read_instruction_scope(instruction, categories_by_catalogue_word)
    rule_scope = read_rule_scope(tuple(hard_rules or ()))
    rule_allowed = rule_scope.allowed_categories
    prohibited_categories = instruction_prohibited | rule_scope.prohibited_categories



    # Follow the rules when both sides state allowed categories and share none, and tell the customer about it
    both_sides_state_allowed = len(instruction_allowed) > 0 and len(rule_allowed) > 0
    sides_share_no_category = both_sides_state_allowed and len(instruction_allowed & rule_allowed) == 0
    if sides_share_no_category:
        allowed_categories = rule_allowed
        open_questions = (
            "The instruction names " + describe_categories(instruction_allowed) + ", while the stored rules allow only "
            + describe_categories(rule_allowed) + ". The rules were followed. Which goods should this instruction cover?",
        )
    elif both_sides_state_allowed:
        allowed_categories = instruction_allowed & rule_allowed
        open_questions = ()
    else:
        allowed_categories = instruction_allowed | rule_allowed
        open_questions = ()



    # Tell the customer when the stored rules contradict each other. This one question stands alone,
    # because it already asks which goods the instruction should cover.
    if rule_scope.allowed_lists_contradict:
        open_questions = (
            "The stored rules contradict each other, because no kind of goods is allowed by all of them. The last rule was followed, which allows only "
            + describe_categories(rule_allowed) + ". Which goods should this instruction cover?",
        )

    return ItemScopeReading(
        allowed_item_categories = tuple(sorted(allowed_categories - prohibited_categories)),
        prohibited_item_categories = tuple(sorted(prohibited_categories)),
        open_questions = open_questions,
    )
