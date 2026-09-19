# Script: test_familiarity_facts.py
# Purpose: Check the familiarity facts of a purchase on the real card history, on the three levels of card, customer and all customers, and the lookalike rule
# Author: Andrés Gallardo
# Date: September 2026

import copy
import dataclasses
from types import MappingProxyType

import pytest

import build_baselines
from app.models.events import read_purchase_message
from app.state.baselines import get_default_baselines, load_baselines, normalize_merchant_name
from app.state.familiarity import FamiliarityFacts, build_familiarity_facts, measure_name_similarity









#### Step 1: Define the shared helpers ####

# State the similarity the settings use by default, and the names of the fields that a fact sheet without identifiers may carry
DEFAULT_SIMILARITY = "0.85"
EXPECTED_FIELD_NAMES = [
    "card_is_known", "card_purchase_count", "customer_purchase_count", "issuer_card_count", "issuer_customer_count",
    "resembled_shop_name", "resemblance_score", "similarity_threshold",
]



# Load the real counts once for all tests
@pytest.fixture(scope = "module")
def baselines():
    return load_baselines()



# Find the directory entry of a shop by its name, which must exist exactly once
def find_shop_by_name(baselines, shop_name):
    matching_shops = [shop for shop in baselines.list_merchants() if shop.merchant_name == shop_name]
    assert len(matching_shops) == 1, "Expected exactly one shop named " + shop_name
    return matching_shops[0]



# Copy the example message onto another card and another shop, and read it through the strict reader
def build_event(example_message, card, shop_identifier, shop_name, shop_category):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["card_id"] = card
    changed_message["authorization"]["merchant"]["merchant_id"] = shop_identifier
    changed_message["authorization"]["merchant"]["merchant_name"] = shop_name
    changed_message["authorization"]["merchant"]["merchant_category"] = shop_category
    return read_purchase_message(changed_message)



# Build the event of a purchase at a shop of the directory, with the name and the category the directory states
def build_event_at_directory_shop(example_message, card, shop):
    return build_event(example_message, card, shop.merchant_id, shop.merchant_name, shop.merchant_category)



# Copy the counts with another number of cards with an approved purchase for one shop
def replace_card_count_of_shop(baselines, shop, cards_with_approved_purchase):
    changed_shop = dataclasses.replace(shop, cards_with_approved_purchase = cards_with_approved_purchase)
    changed_shops = {**baselines.merchants, shop.merchant_id: changed_shop}
    return dataclasses.replace(baselines, merchants = MappingProxyType(changed_shops))









#### Step 2: Check the three levels of familiarity ####

# Check a shop the card uses, with 6 purchases on the card, 8 on all cards of the customer, and 9 cards and 5 customers of the issuer
def test_shop_the_card_uses_gives_all_three_levels(example_message, baselines):
    shop = baselines.get_merchant("ME0022")
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines, DEFAULT_SIMILARITY)
    assert facts.card_is_known is True
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (6, 8)
    assert (facts.issuer_card_count, facts.issuer_customer_count) == (9, 5)
    assert facts.resembled_shop_name is None



# Check a shop the customer uses with another card only, where the card count is 0 and the customer count is 2
def test_shop_used_with_another_card_gives_zero_on_the_card(example_message, baselines):
    shop = baselines.get_merchant("ME0023")
    assert shop.merchant_name == "Circuit and Pine"
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines, DEFAULT_SIMILARITY)
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (0, 2)



# Check a shop that is new to the customer and widely used by the others, with 29 cards and 19 customers
def test_shop_new_to_the_customer_is_widely_used_by_others(example_message, baselines):
    shop = find_shop_by_name(baselines, "RainThread")
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0023", shop), baselines, DEFAULT_SIMILARITY)
    assert facts.card_is_known is True
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (0, 0)
    assert (facts.issuer_card_count, facts.issuer_customer_count) == (29, 19)
    assert facts.resembled_shop_name is None



# Check that an unknown card gives None for both of its counts, keeps the counts of the issuer and has no shop to resemble
def test_unknown_card_gives_none_counts(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA_UNKNOWN", shop), baselines, DEFAULT_SIMILARITY)
    assert facts.card_is_known is False
    assert facts.card_purchase_count is None
    assert facts.customer_purchase_count is None
    assert facts.resembled_shop_name is None
    assert facts.resemblance_score is None
    widely_used_shop = find_shop_by_name(baselines, "RainThread")
    facts_at_widely_used_shop = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA_UNKNOWN", widely_used_shop), baselines, DEFAULT_SIMILARITY)
    assert (facts_at_widely_used_shop.issuer_card_count, facts_at_widely_used_shop.issuer_customer_count) == (29, 19)



# Check that a shop missing from the directory gives 0 on every level for a known card
def test_unknown_shop_gives_zero_counts(example_message, baselines):
    event = build_event(example_message, "CA0039", "SHOP_UNKNOWN", "Quartz Lantern Supplies", "electronics")
    facts = build_familiarity_facts(event, baselines, DEFAULT_SIMILARITY)
    assert facts.card_is_known is True
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (0, 0)
    assert (facts.issuer_card_count, facts.issuer_customer_count) == (0, 0)
    assert facts.resembled_shop_name is None



# Check that the customer comes from the card in the history and never from the mandate
def test_customer_comes_from_the_card_and_not_from_the_mandate(example_message, baselines):
    shop = baselines.get_merchant("ME0023")
    changed_message = copy.deepcopy(example_message)
    changed_message["mandate"]["customer_id"] = "CU_SOMEBODY_ELSE"
    facts = build_familiarity_facts(build_event_at_directory_shop(changed_message, "CA0039", shop), baselines, DEFAULT_SIMILARITY)
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (0, 2)



# Check that the facts are frozen and carry no identifier, by their field names and by their values
def test_facts_are_frozen_and_carry_no_identifier(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines, DEFAULT_SIMILARITY)
    assert [field.name for field in dataclasses.fields(FamiliarityFacts)] == EXPECTED_FIELD_NAMES
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.card_purchase_count = 99
    text_values = [value for value in dataclasses.asdict(facts).values() if isinstance(value, str)]
    assert text_values == ["PixelHarbor"]









#### Step 3: Check the lookalike rule ####

# Check that the shop nobody has bought from resembles the shop the customer uses, with a score of 0.957
def test_lookalike_resembles_the_shop_the_customer_uses(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines, DEFAULT_SIMILARITY)
    assert (facts.card_purchase_count, facts.customer_purchase_count) == (0, 0)
    assert (facts.issuer_card_count, facts.issuer_customer_count) == (0, 0)
    assert facts.resembled_shop_name == "PixelHarbor"
    assert round(facts.resemblance_score, 3) == 0.957
    assert facts.similarity_threshold == 0.85



# Check the threshold at its edge, where a score exactly at the threshold resembles and a threshold just above the score does not
def test_score_exactly_at_the_threshold_resembles(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    event = build_event_at_directory_shop(example_message, "CA0039", shop)
    exact_score = measure_name_similarity("pixelharbour", "pixelharbor")
    facts_at_the_threshold = build_familiarity_facts(event, baselines, exact_score)
    facts_above_the_score = build_familiarity_facts(event, baselines, exact_score + 0.001)
    assert facts_at_the_threshold.resembled_shop_name == "PixelHarbor"
    assert facts_above_the_score.resembled_shop_name is None
    assert round(facts_above_the_score.resemblance_score, 3) == 0.957



# Check that two honest shops that share words do not resemble each other, at 0.667 and with different categories
def test_shops_that_share_words_do_not_resemble_each_other(example_message, baselines):
    circuit_and_pine = find_shop_by_name(baselines, "Circuit and Pine")
    parcel_and_pine = find_shop_by_name(baselines, "Parcel and Pine")
    assert round(measure_name_similarity(circuit_and_pine.normalized_name, parcel_and_pine.normalized_name), 3) == 0.667
    assert circuit_and_pine.merchant_category != parcel_and_pine.merchant_category
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", circuit_and_pine), baselines, DEFAULT_SIMILARITY)
    assert facts.resembled_shop_name is None



# Check that a shop with the same name and the same identifier never resembles itself, also with no buyer on any card
def test_shop_never_resembles_itself(example_message, baselines):
    shop = baselines.get_merchant("ME0022")
    baselines_without_buyers = replace_card_count_of_shop(baselines, shop, 0)
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines_without_buyers, DEFAULT_SIMILARITY)
    assert facts.issuer_card_count == 0
    assert facts.resembled_shop_name is None
    assert facts.resemblance_score is None or facts.resemblance_score < 0.85



# Check that a very similar name with an approved purchase on some card is an honest shop and no lookalike
def test_similar_name_with_a_buyer_is_no_lookalike(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    baselines_with_one_buyer = replace_card_count_of_shop(baselines, shop, 1)
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, "CA0039", shop), baselines_with_one_buyer, DEFAULT_SIMILARITY)
    assert facts.issuer_card_count == 1
    assert facts.resembled_shop_name is None
    assert round(facts.resemblance_score, 3) == 0.957



# Check that a very similar name in another category is no lookalike
def test_similar_name_in_another_category_is_no_lookalike(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    event = build_event(example_message, "CA0039", shop.merchant_id, shop.merchant_name, "clothing")
    facts = build_familiarity_facts(event, baselines, DEFAULT_SIMILARITY)
    assert facts.resembled_shop_name is None



# Check that a customer who never bought at the imitated shop sees no lookalike, because the rule protects the shops a customer uses
def test_customer_without_the_imitated_shop_sees_no_lookalike(example_message, baselines):
    shop = find_shop_by_name(baselines, "PixelHarbour")
    imitated_shop = find_shop_by_name(baselines, "PixelHarbor")
    cards_without_the_imitated_shop = [
        card_identifier
        for card_identifier, card in sorted(baselines.cards.items())
        if baselines.get_customer(card.customer_id).merchant_purchase_counts.get(imitated_shop.merchant_id, 0) == 0
    ]
    assert len(cards_without_the_imitated_shop) > 0
    facts = build_familiarity_facts(build_event_at_directory_shop(example_message, cards_without_the_imitated_shop[0], shop), baselines, DEFAULT_SIMILARITY)
    assert facts.card_is_known is True
    assert facts.resembled_shop_name is None









#### Step 4: Check the name function and the shared counts ####

# Check that every normalized name of the directory equals what the name function gives, so moving the function changed nothing
def test_normalized_names_of_the_directory_are_unchanged(baselines):
    disagreeing_shops = [
        shop.merchant_name
        for shop in baselines.list_merchants()
        if normalize_merchant_name(shop.merchant_name) != shop.normalized_name
    ]
    assert disagreeing_shops == []
    assert normalize_merchant_name("PixelHarbor") == "pixelharbor"
    assert normalize_merchant_name("Café Zürich & Co. 24") == "cafezurichco24"



# Check that the script that builds the directory uses the very same function
def test_build_script_uses_the_same_name_function():
    assert build_baselines.normalize_merchant_name is normalize_merchant_name



# Check that the default counts are loaded once per process
def test_default_baselines_are_the_same_object_twice():
    assert get_default_baselines() is get_default_baselines()
