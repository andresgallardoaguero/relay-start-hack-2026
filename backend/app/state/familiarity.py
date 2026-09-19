# Script: familiarity.py
# Purpose: Look up how familiar the shop of one purchase is to the card, to the customer and to all customers, and whether its name imitates a shop the customer uses
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from app.state.baselines import normalize_merchant_name









#### Step 1: Define the familiarity facts ####

# Hold what the history says about the shop of one purchase, as plain counts and names. No identifier is kept, so no guard can decide on one.
# card_purchase_count and customer_purchase_count count approved purchases at the shop, on this card and on all cards of the customer
# the card belongs to. Both are None for a card the history does not hold, and 0 for a known card that never bought there.
# issuer_card_count and issuer_customer_count count the cards and the customers of the issuer with an approved purchase at the shop,
# and a shop the history does not hold gives 0.
# resembled_shop_name names the shop of the customer that the shop of the purchase imitates, and stays None when it imitates none.
# resemblance_score is the similarity to that shop, from 0 to 1. Without such a shop it is the highest similarity to any other shop of the customer,
# and it is None when the customer has no other shop. similarity_threshold is the score at or above which a name counts as very similar.
@dataclass(frozen = True)
class FamiliarityFacts:
    card_is_known: bool
    card_purchase_count: Optional[int]
    customer_purchase_count: Optional[int]
    issuer_card_count: int
    issuer_customer_count: int
    resembled_shop_name: Optional[str]
    resemblance_score: Optional[float]
    similarity_threshold: float



# Hold one shop of the customer next to the shop of the purchase, with the similarity of the two names
@dataclass(frozen = True)
class NameComparison:
    shop_name: str
    score: float
    has_same_category: bool









#### Step 2: Compare the names ####

# Measure how similar two normalized names are, character by character, from 0 for nothing shared to 1 for equal names.
# The name of the purchase always comes first, so the same pair always gives the same score.
def measure_name_similarity(normalized_purchase_name, normalized_known_name):
    return SequenceMatcher(None, normalized_purchase_name, normalized_known_name, autojunk = False).ratio()



# Compare the name of the purchase with every other shop the customer has at least one approved purchase at.
# The shop of the purchase itself is left out by its identifier, so a shop never resembles itself.
# A shop of the customer that the directory does not hold has no name to compare and is left out.
def compare_with_shops_of_customer(purchase_shop_identifier, normalized_purchase_name, purchase_category, customer, baselines):
    shops_of_customer = [
        baselines.get_merchant(shop_identifier)
        for shop_identifier, purchase_count in sorted(customer.merchant_purchase_counts.items())
        if purchase_count >= 1 and shop_identifier != purchase_shop_identifier
    ]
    return tuple(
        NameComparison(
            shop_name = known_shop.merchant_name,
            score = measure_name_similarity(normalized_purchase_name, known_shop.normalized_name),
            has_same_category = known_shop.merchant_category == purchase_category,
        )
        for known_shop in shops_of_customer
        if known_shop is not None
    )



# Pick the comparison with the highest score, or None when there is none
def pick_best_comparison(name_comparisons):
    if not name_comparisons:
        return None
    return max(name_comparisons, key = lambda name_comparison: name_comparison.score)









#### Step 3: Build the familiarity facts of one purchase ####

# Build the facts for one purchase message. The card and the shop are taken from the purchase, and the customer is the one the history
# names as the holder of the card, never the one the mandate names.
# A shop imitates another when the score is at or above lookalike_name_similarity, the identifiers differ, the categories are equal,
# and the shop of the purchase has no approved purchase on any card of the issuer. An honest shop with a similar name has customers, an imitation has none.
def build_familiarity_facts(event, baselines, lookalike_name_similarity):
    card_identifier = event.authorization.card_id
    purchase_shop = event.authorization.merchant
    purchase_shop_identifier = purchase_shop.merchant_id
    similarity_threshold = float(lookalike_name_similarity)



    # Count the cards and the customers of the issuer that bought at the shop, where a shop missing from the directory gives 0
    issuer_card_count = baselines.merchant_card_count(purchase_shop_identifier) or 0
    issuer_customer_count = baselines.merchant_customer_count(purchase_shop_identifier) or 0



    # Answer for an unknown card without any count of its own and without a shop to resemble
    card = baselines.get_card(card_identifier)
    if card is None:
        return FamiliarityFacts(
            card_is_known = False,
            card_purchase_count = None,
            customer_purchase_count = None,
            issuer_card_count = issuer_card_count,
            issuer_customer_count = issuer_customer_count,
            resembled_shop_name = None,
            resemblance_score = None,
            similarity_threshold = similarity_threshold,
        )



    # Count the approved purchases on the card. A card whose holder the history does not hold has its own purchases only,
    # which is the lowest number the holder can have, and no other shops to resemble.
    card_purchase_count = card.merchant_purchase_counts.get(purchase_shop_identifier, 0)
    customer = baselines.get_customer(card.customer_id)
    if customer is None:
        return FamiliarityFacts(
            card_is_known = True,
            card_purchase_count = card_purchase_count,
            customer_purchase_count = card_purchase_count,
            issuer_card_count = issuer_card_count,
            issuer_customer_count = issuer_customer_count,
            resembled_shop_name = None,
            resemblance_score = None,
            similarity_threshold = similarity_threshold,
        )



    # Compare the name with the other shops of the customer, and keep the best imitation when the shop of the purchase has no buyer at all
    name_comparisons = compare_with_shops_of_customer(
        purchase_shop_identifier = purchase_shop_identifier,
        normalized_purchase_name = normalize_merchant_name(purchase_shop.merchant_name),
        purchase_category = purchase_shop.merchant_category,
        customer = customer,
        baselines = baselines,
    )
    imitations = tuple(
        name_comparison
        for name_comparison in name_comparisons
        if name_comparison.score >= similarity_threshold and name_comparison.has_same_category and issuer_card_count == 0
    )
    best_imitation = pick_best_comparison(imitations)
    best_comparison = best_imitation if best_imitation is not None else pick_best_comparison(name_comparisons)

    return FamiliarityFacts(
        card_is_known = True,
        card_purchase_count = card_purchase_count,
        customer_purchase_count = customer.merchant_purchase_counts.get(purchase_shop_identifier, 0),
        issuer_card_count = issuer_card_count,
        issuer_customer_count = issuer_customer_count,
        resembled_shop_name = best_imitation.shop_name if best_imitation is not None else None,
        resemblance_score = best_comparison.score if best_comparison is not None else None,
        similarity_threshold = similarity_threshold,
    )
