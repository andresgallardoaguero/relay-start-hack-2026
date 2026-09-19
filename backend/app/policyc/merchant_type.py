# Script: merchant_type.py
# Purpose: Read from the words of a customer instruction and from its machine-readable rules which kind of shop it asks for
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.policyc.instruction_text import build_alternation_pattern, split_clauses, split_sentences









#### Step 1: Define the words the reader knows ####

# List the words that mean a shop without saying which kind, so they name a kind only with a type phrase directly before them
SHOP_WORDS = ("retailer", "shop", "store", "seller", "merchant")



# List the type phrases that name a merchant category when they stand directly before a shop word, as in "sports retailer".
# A type phrase alone names no kind of shop, so "sports socks" and "book a room" say nothing about the shop.
TYPE_PHRASES_BY_MERCHANT_CATEGORY = {
    "sporting_goods": ("sports", "sporting goods"),
    "groceries": ("grocery",),
    "electronics": ("electronics",),
    "clothing": ("clothing", "fashion"),
    "books": ("book",),
    "home_improvement": ("hardware", "diy", "home improvement"),
    "pet_care": ("pet",),
}



# List the shop words that carry their kind in themselves, as in "supermarket"
TYPED_SHOP_WORDS_BY_MERCHANT_CATEGORY = {
    "groceries": ("supermarket", "grocer"),
    "books": ("bookshop", "bookstore"),
    "food_delivery": ("delivery service",),
    "dining": ("restaurant",),
    "health": ("pharmacy",),
    "fuel": ("petrol station", "fuel station", "gas station", "charging station"),
}



# List the words that make the kind of shop a condition, as in "only from a sports retailer"
STRICT_WORDS = ("only", "must", "exclusively")



# Name the rule fields that speak about the category of the shop
MERCHANT_CATEGORY_RULE_FIELDS = ("authorization.merchant.merchant_category", "merchant.merchant_category", "merchant_category")









#### Step 2: Build the patterns ####

# Write the plural form of a shop phrase, so "pharmacy" gives "pharmacies" and "delivery service" gives "delivery services"
def build_plural_form(shop_phrase):
    if shop_phrase.endswith("y"):
        return shop_phrase[:-1] + "ies"
    return shop_phrase + "s"



# List the singular and the plural form of every given shop phrase
def list_both_forms(shop_phrases):
    return tuple(shop_phrases) + tuple(build_plural_form(shop_phrase) for shop_phrase in shop_phrases)



# List every phrase that names one merchant category, which is each type phrase before each shop word and each shop word that carries the kind
def list_phrases_of_category(merchant_category):
    type_phrases = TYPE_PHRASES_BY_MERCHANT_CATEGORY.get(merchant_category, ())
    typed_shop_words = TYPED_SHOP_WORDS_BY_MERCHANT_CATEGORY.get(merchant_category, ())
    type_phrases_before_shop_words = tuple(
        type_phrase + " " + shop_word
        for type_phrase in type_phrases
        for shop_word in list_both_forms(SHOP_WORDS)
    )
    return type_phrases_before_shop_words + list_both_forms(typed_shop_words)



# Build one pattern per merchant category, in the sorted order of the categories
MERCHANT_CATEGORIES_THE_READER_KNOWS = tuple(sorted(set(TYPE_PHRASES_BY_MERCHANT_CATEGORY) | set(TYPED_SHOP_WORDS_BY_MERCHANT_CATEGORY)))
PATTERNS_BY_MERCHANT_CATEGORY = {
    merchant_category: build_alternation_pattern(list_phrases_of_category(merchant_category))
    for merchant_category in MERCHANT_CATEGORIES_THE_READER_KNOWS
}
STRICT_WORD_PATTERN = build_alternation_pattern(STRICT_WORDS)









#### Step 3: Define what the reader returns ####

# Describe the result, where the categories are sorted and open_questions holds plain sentences for the customer.
# An empty tuple means that the instruction is open about the kind of shop, which is the customer's choice.
@dataclass(frozen = True)
class MerchantTypeReading:
    required_merchant_categories: tuple
    is_strict: bool
    open_questions: tuple



# Hold what one clause says, which is the kinds of shop it names and whether it makes them a condition
@dataclass(frozen = True)
class ClauseMerchantType:
    merchant_categories: frozenset
    is_strict: bool









#### Step 4: Read the instruction ####

# Read the kinds of shop one clause names. A hyphen counts as a space, so "sporting-goods store" is found.
# The clause is strict when it names a kind and also holds one of the strict words as a whole word.
def read_clause_merchant_type(clause):
    clause_without_hyphens = clause.replace("-", " ")
    merchant_categories = frozenset(
        merchant_category
        for merchant_category, category_pattern in PATTERNS_BY_MERCHANT_CATEGORY.items()
        if category_pattern.search(clause_without_hyphens) is not None
    )
    names_a_kind = len(merchant_categories) > 0
    return ClauseMerchantType(
        merchant_categories = merchant_categories,
        is_strict = names_a_kind and STRICT_WORD_PATTERN.search(clause_without_hyphens) is not None,
    )



# Read the kinds of shop of the whole instruction, clause by clause, where one strict clause makes the reading strict.
# Words about familiarity, such as "a shop I use regularly", hold no type phrase and therefore name no kind.
def read_instruction_merchant_type(instruction):
    clause_readings = tuple(
        read_clause_merchant_type(clause)
        for sentence in split_sentences(instruction)
        for clause in split_clauses(sentence)
    )
    merchant_categories = frozenset().union(*(clause_reading.merchant_categories for clause_reading in clause_readings))
    is_strict = any(clause_reading.is_strict for clause_reading in clause_readings)
    return merchant_categories, is_strict









#### Step 5: Read the rules ####

# Read the categories one rule names, which is a list of texts for the operator in and one text for the operator =.
# A rule on another field, with another operator or with a value of another type names no category.
def read_rule_merchant_categories(rule):
    if rule.field not in MERCHANT_CATEGORY_RULE_FIELDS:
        return frozenset()
    if rule.operator == "in" and isinstance(rule.value, (tuple, list)):
        return frozenset(value for value in rule.value if isinstance(value, str))
    if rule.operator == "=" and isinstance(rule.value, str):
        return frozenset((rule.value,))
    return frozenset()



# Hold what all rules say together, and whether their lists contradict each other
@dataclass(frozen = True)
class RuleMerchantType:
    merchant_categories: frozenset
    lists_contradict: bool



# Read the categories of all rules together. Every rule must hold, so the lists of several rules count only where they overlap,
# and a rule that names no category is left out.
# When the lists share nothing, the list of the last rule counts, because a rule that was added later is the newer wish.
# Lists that share nothing never end as an empty set, because an empty set means that nothing is stated.
def read_rules_merchant_categories(hard_rules):
    category_lists = tuple(
        rule_categories
        for rule_categories in (read_rule_merchant_categories(rule) for rule in hard_rules)
        if len(rule_categories) > 0
    )
    if len(category_lists) == 0:
        return RuleMerchantType(frozenset(), False)
    shared_categories = frozenset.intersection(*category_lists)
    if len(shared_categories) > 0:
        return RuleMerchantType(shared_categories, False)
    return RuleMerchantType(category_lists[-1], True)









#### Step 6: Read which kind of shop the instruction asks for ####

# Write a list of categories for the customer, with spaces for underscores
def describe_categories(merchant_categories):
    return ", ".join(merchant_category.replace("_", " ") for merchant_category in sorted(merchant_categories))



# Read the kind of shop from the instruction and the rules. A rule is always strict, because it was stored as a condition.
# Where only one side names a kind, that side counts. Where both do, only what both name counts.
# Where both do and share nothing, the rules count and the customer is told about it.
def read_merchant_type(instruction, hard_rules):
    instruction_categories, instruction_is_strict = read_instruction_merchant_type(instruction)
    rule_merchant_type = read_rules_merchant_categories(tuple(hard_rules or ()))
    rule_categories = rule_merchant_type.merchant_categories
    rules_name_a_kind = len(rule_categories) > 0
    both_sides_name_a_kind = len(instruction_categories) > 0 and rules_name_a_kind
    shared_categories = instruction_categories & rule_categories



    # Follow the rules when both sides name a kind and share none, and tell the customer about it
    if both_sides_name_a_kind and len(shared_categories) == 0:
        required_categories = rule_categories
        open_questions = (
            "The instruction asks for a shop for " + describe_categories(instruction_categories) + ", while the stored rules allow only shops for "
            + describe_categories(rule_categories) + ". The rules were followed. Which kind of shop should this instruction ask for?",
        )
    elif both_sides_name_a_kind:
        required_categories = shared_categories
        open_questions = ()
    else:
        required_categories = instruction_categories | rule_categories
        open_questions = ()



    # Tell the customer when the stored rules contradict each other. This one question stands alone,
    # because it already asks which kind of shop the instruction should ask for.
    if rule_merchant_type.lists_contradict:
        open_questions = (
            "The stored rules contradict each other, because no kind of shop is allowed by all of them. The last rule was followed, which allows only shops for "
            + describe_categories(rule_categories) + ". Which kind of shop should this instruction ask for?",
        )

    return MerchantTypeReading(
        required_merchant_categories = tuple(sorted(required_categories)),
        is_strict = rules_name_a_kind or instruction_is_strict,
        open_questions = open_questions,
    )
