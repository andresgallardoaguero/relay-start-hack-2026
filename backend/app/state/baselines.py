# Script: baselines.py
# Purpose: Load the familiarity and behavior counts of every card, customer and merchant, and answer lookups where unknown and zero stay different
# Author: Andrés Gallardo
# Date: September 2026

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple









#### Step 1: Define the paths and the error ####

# Locate the folder of the two files from the location of this file, so the working directory never matters
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_BASELINES_FOLDER = REPOSITORY_FOLDER / "data" / "processed"
FAMILIARITY_FILE_NAME = "familiarity_baselines.json"
DIRECTORY_FILE_NAME = "merchant_directory.json"



# Say that the counts cannot be used, because a file is missing, unreadable or incomplete
class BaselinesUnavailableError(Exception):
    pass









#### Step 2: Define the frozen objects ####

# Hold the median and the 95th percentile of the amounts of a card, which are both None for a card without active purchases
@dataclass(frozen = True)
class AmountSummary:
    p50: Optional[float]
    p95: Optional[float]



# Hold the same two figures for one merchant category of a card, together with the number of purchases behind them
@dataclass(frozen = True)
class CategoryAmountSummary:
    count: int
    p50: float
    p95: float



# Hold how fast purchase attempts followed each other on a card, whatever their outcome
@dataclass(frozen = True)
class QuickSeries:
    active_attempt_count: int
    attempts_following_within_10_minutes: int
    largest_earlier_attempt_count: int



# Hold what the history says about one card. Familiarity counts approved purchases.
# Behavior counts active purchases, which are approved purchases outside the recurring channel.
@dataclass(frozen = True)
class CardBaseline:
    card_id: str
    customer_id: str
    account_id: str
    online_enabled: bool
    international_enabled: bool
    status: str
    per_transaction_limit_chf: float
    monthly_limit_chf: float
    approved_purchase_count: int
    merchant_purchase_counts: Mapping[str, int]
    active_purchase_count: int
    device_purchase_counts: Mapping[str, int]
    country_purchase_counts: Mapping[str, int]
    merchant_category_purchase_counts: Mapping[str, int]
    local_hour_purchase_counts: Tuple[int, ...]
    amount_chf: AmountSummary
    amount_chf_by_merchant_category: Mapping[str, CategoryAmountSummary]
    quick_series: QuickSeries



# Hold the cards of one customer and the approved purchases per merchant over all of these cards
@dataclass(frozen = True)
class CustomerBaseline:
    customer_id: str
    card_ids: Tuple[str, ...]
    merchant_purchase_counts: Mapping[str, int]



# Hold the directory entry of one merchant, where recurring_capable stays the text "true" or "false"
@dataclass(frozen = True)
class MerchantBaseline:
    merchant_id: str
    merchant_name: str
    normalized_name: str
    merchant_category: str
    merchant_country: str
    recurring_capable: str
    cards_with_approved_purchase: int
    customers_with_approved_purchase: int



# Hold everything the two files carry, and answer the lookups
@dataclass(frozen = True)
class Baselines:
    history_first_timestamp: str
    history_last_timestamp: str
    card_count: int
    customer_count: int
    cards: Mapping[str, CardBaseline]
    customers: Mapping[str, CustomerBaseline]
    merchants: Mapping[str, MerchantBaseline]

    # Return the card, or None for a card the files do not hold
    def get_card(self, card_id):
        return self.cards.get(card_id)

    # Return the customer, or None for a customer the files do not hold
    def get_customer(self, customer_id):
        return self.customers.get(customer_id)

    # Return the merchant, or None for a merchant the files do not hold
    def get_merchant(self, merchant_id):
        return self.merchants.get(merchant_id)

    # Count the approved purchases of a card at a merchant, where None means an unknown card and 0 means a known card that never bought there
    def card_merchant_purchase_count(self, card_id, merchant_id):
        card = self.cards.get(card_id)
        if card is None:
            return None
        return card.merchant_purchase_counts.get(merchant_id, 0)

    # Count the approved purchases of a customer at a merchant over all cards, where None means an unknown customer
    def customer_merchant_purchase_count(self, customer_id, merchant_id):
        customer = self.customers.get(customer_id)
        if customer is None:
            return None
        return customer.merchant_purchase_counts.get(merchant_id, 0)

    # Count the cards with an approved purchase at a merchant, where None means an unknown merchant
    def merchant_card_count(self, merchant_id):
        merchant = self.merchants.get(merchant_id)
        if merchant is None:
            return None
        return merchant.cards_with_approved_purchase

    # Count the customers with an approved purchase at a merchant, where None means an unknown merchant
    def merchant_customer_count(self, merchant_id):
        merchant = self.merchants.get(merchant_id)
        if merchant is None:
            return None
        return merchant.customers_with_approved_purchase

    # List the directory entries of all merchants in the order of their identifiers
    def list_merchants(self):
        return tuple(self.merchants[merchant_id] for merchant_id in sorted(self.merchants))









#### Step 3: Turn the content of the files into the frozen objects ####

# Reduce a merchant name to lowercase letters and digits without accents, so "PixelHarbor" becomes "pixelharbor".
# The directory is built with this function, and a name from a purchase message is reduced with it before the two are compared.
def normalize_merchant_name(merchant_name):
    decomposed_name = unicodedata.normalize("NFKD", merchant_name)
    kept_characters = [
        character.lower()
        for character in decomposed_name
        if character.isalnum() and not unicodedata.combining(character)
    ]
    return "".join(kept_characters)



# Freeze a dictionary of whole numbers, so no caller can change a count
def freeze_counts(counts):
    return MappingProxyType({key: int(count) for key, count in counts.items()})



# Read an amount that may be missing
def read_optional_amount(value):
    if value is None:
        return None
    return float(value)



# Read a switch that must be a real boolean, so the text "false" can never read as true
def read_boolean_switch(value, switch_name):
    if not isinstance(value, bool):
        raise ValueError("The switch " + switch_name + " must be true or false, found " + repr(value))
    return value



# Build the frozen object of one card
def build_card_baseline(card_id, card_entry):
    hour_counts = tuple(int(hour_count) for hour_count in card_entry["local_hour_purchase_counts"])
    if len(hour_counts) != 24:
        raise ValueError("Card " + card_id + " does not carry 24 hour counts")
    return CardBaseline(
        card_id = card_id,
        customer_id = card_entry["customer_id"],
        account_id = card_entry["account_id"],
        online_enabled = read_boolean_switch(card_entry["online_enabled"], "online_enabled"),
        international_enabled = read_boolean_switch(card_entry["international_enabled"], "international_enabled"),
        status = card_entry["status"],
        per_transaction_limit_chf = float(card_entry["per_transaction_limit_chf"]),
        monthly_limit_chf = float(card_entry["monthly_limit_chf"]),
        approved_purchase_count = int(card_entry["approved_purchase_count"]),
        merchant_purchase_counts = freeze_counts(card_entry["merchant_purchase_counts"]),
        active_purchase_count = int(card_entry["active_purchase_count"]),
        device_purchase_counts = freeze_counts(card_entry["device_purchase_counts"]),
        country_purchase_counts = freeze_counts(card_entry["country_purchase_counts"]),
        merchant_category_purchase_counts = freeze_counts(card_entry["merchant_category_purchase_counts"]),
        local_hour_purchase_counts = hour_counts,
        amount_chf = AmountSummary(
            p50 = read_optional_amount(card_entry["amount_chf"]["p50"]),
            p95 = read_optional_amount(card_entry["amount_chf"]["p95"]),
        ),
        amount_chf_by_merchant_category = MappingProxyType({
            merchant_category: CategoryAmountSummary(
                count = int(category_entry["count"]),
                p50 = float(category_entry["p50"]),
                p95 = float(category_entry["p95"]),
            )
            for merchant_category, category_entry in card_entry["amount_chf_by_merchant_category"].items()
        }),
        quick_series = QuickSeries(
            active_attempt_count = int(card_entry["quick_series"]["active_attempt_count"]),
            attempts_following_within_10_minutes = int(card_entry["quick_series"]["attempts_following_within_10_minutes"]),
            largest_earlier_attempt_count = int(card_entry["quick_series"]["largest_earlier_attempt_count"]),
        ),
    )



# Build the frozen object of one customer
def build_customer_baseline(customer_id, customer_entry):
    return CustomerBaseline(
        customer_id = customer_id,
        card_ids = tuple(customer_entry["card_ids"]),
        merchant_purchase_counts = freeze_counts(customer_entry["merchant_purchase_counts"]),
    )



# Build the frozen object of one merchant from its directory entry
def build_merchant_baseline(merchant_id, directory_entry):
    return MerchantBaseline(
        merchant_id = merchant_id,
        merchant_name = directory_entry["merchant_name"],
        normalized_name = directory_entry["normalized_name"],
        merchant_category = directory_entry["merchant_category"],
        merchant_country = directory_entry["merchant_country"],
        recurring_capable = directory_entry["recurring_capable"],
        cards_with_approved_purchase = int(directory_entry["cards_with_approved_purchase"]),
        customers_with_approved_purchase = int(directory_entry["customers_with_approved_purchase"]),
    )



# Build the one object from the content of both files, and refuse content that is empty or disagrees with itself
def build_baselines(familiarity_content, directory_content):

    # Build the three groups of frozen objects
    cards = {
        card_id: build_card_baseline(card_id, card_entry)
        for card_id, card_entry in familiarity_content["cards"].items()
    }
    customers = {
        customer_id: build_customer_baseline(customer_id, customer_entry)
        for customer_id, customer_entry in familiarity_content["customers"].items()
    }
    merchants = {
        merchant_id: build_merchant_baseline(merchant_id, directory_entry)
        for merchant_id, directory_entry in directory_content.items()
    }



    # Refuse empty content, a wrong card or customer count and two files that name different merchants or different buyer counts
    if not cards or not customers or not merchants:
        raise ValueError("A file holds no cards, no customers or no merchants")
    if int(familiarity_content["card_count"]) != len(cards):
        raise ValueError("The stated card count differs from the number of cards")
    if int(familiarity_content["customer_count"]) != len(customers):
        raise ValueError("The stated customer count differs from the number of customers")
    if set(familiarity_content["merchants"]) != set(merchants):
        raise ValueError("The two files name different merchants")
    disagreeing_merchant_ids = sorted(
        merchant_id
        for merchant_id, merchant in merchants.items()
        if int(familiarity_content["merchants"][merchant_id]["cards_with_approved_purchase"]) != merchant.cards_with_approved_purchase
        or int(familiarity_content["merchants"][merchant_id]["customers_with_approved_purchase"]) != merchant.customers_with_approved_purchase
    )
    if disagreeing_merchant_ids:
        raise ValueError("The two files state different card or customer counts for " + ", ".join(disagreeing_merchant_ids))

    return Baselines(
        history_first_timestamp = familiarity_content["history_window"]["first_timestamp"],
        history_last_timestamp = familiarity_content["history_window"]["last_timestamp"],
        card_count = len(cards),
        customer_count = len(customers),
        cards = MappingProxyType(cards),
        customers = MappingProxyType(customers),
        merchants = MappingProxyType(merchants),
    )









#### Step 4: Load the two files ####

# Read the two files and return one frozen object. This is the only place that touches the disk, and it reads these two files and nothing else.
# A missing, unreadable or incomplete file raises BaselinesUnavailableError, and an empty object is never returned.
def load_baselines(baselines_folder = None):

    # Use the default folder when none is given
    if baselines_folder is None:
        baselines_folder = DEFAULT_BASELINES_FOLDER
    familiarity_path = Path(baselines_folder) / FAMILIARITY_FILE_NAME
    directory_path = Path(baselines_folder) / DIRECTORY_FILE_NAME



    # Read both files as JSON
    try:
        familiarity_content = json.loads(familiarity_path.read_text(encoding = "utf-8"))
        directory_content = json.loads(directory_path.read_text(encoding = "utf-8"))
    except (OSError, ValueError) as read_error:
        raise BaselinesUnavailableError("The baseline files in " + str(baselines_folder) + " cannot be read - " + str(read_error)) from read_error



    # Turn the content into the frozen objects
    try:
        return build_baselines(familiarity_content, directory_content)
    except (KeyError, TypeError, ValueError, AttributeError) as content_error:
        raise BaselinesUnavailableError("The baseline files in " + str(baselines_folder) + " are incomplete - " + repr(content_error)) from content_error



# Load the two files of the default folder on the first call and hand back the same object for the rest of the process
@lru_cache(maxsize = None)
def get_default_baselines():
    return load_baselines()
