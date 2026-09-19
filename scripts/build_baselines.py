# Script: build_baselines.py
# Purpose: Count from the card history how familiar every shop, device, country, hour and amount is, and write the counts and the item catalogue as three JSON files
# Author: Andrés Gallardo
# Date: September 2026

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd









#### Step 1: Define the paths, the expected sizes and the two populations ####

# Locate the repository folders from the location of this file
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
BACKEND_FOLDER = REPOSITORY_FOLDER / "backend"
CASE_DATA_FOLDER = REPOSITORY_FOLDER / "data" / "raw" / "2026-09-18_viseca-2026" / "data"
DEFAULT_OUTPUT_FOLDER = REPOSITORY_FOLDER / "data" / "processed"



# Make the backend importable, so the directory is built with the same name function the backend compares names with
if str(BACKEND_FOLDER) not in sys.path:
    sys.path.insert(0, str(BACKEND_FOLDER))

from app.state.baselines import normalize_merchant_name



# Name the three files this script writes
FAMILIARITY_FILE_NAME = "familiarity_baselines.json"
DIRECTORY_FILE_NAME = "merchant_directory.json"
ITEM_CATALOGUE_FILE_NAME = "item_catalogue.json"



# State the row counts the published files must have
EXPECTED_HISTORY_ROW_COUNT = 4701
EXPECTED_CARD_COUNT = 41
EXPECTED_ACCOUNT_COUNT = 31
EXPECTED_MERCHANT_COUNT = 58
EXPECTED_ITEM_COUNT = 66
EXPECTED_ACTIVE_PURCHASE_COUNT = 3803



# Define the approved purchases, which measure familiarity. Refunds and cash withdrawals are not purchases.
APPROVED_PURCHASE_FILTER = "status == 'approved' and transaction_type == 'purchase'"



# Define the active purchases, which measure behavior. A scheduled recurring payment runs by itself, often at night,
# and says nothing about when or where the customer shops, so the recurring channel is left out.
ACTIVE_PURCHASE_FILTER = APPROVED_PURCHASE_FILTER + " and channel != 'recurring'"



# Define the active attempts, which measure purchase speed only. An attempt counts toward speed whatever its outcome.
ACTIVE_ATTEMPT_FILTER = "transaction_type == 'purchase' and channel != 'recurring'"



# Read the hour of a purchase in Swiss local time, and look ten minutes back for earlier attempts
LOCAL_TIME_ZONE = "Europe/Zurich"
QUICK_SERIES_WINDOW_MINUTES = 10
HOURS_OF_THE_DAY = list(range(24))



# Keep only the history columns the counts need, which leaves the customer profile text unread
HISTORY_COLUMNS = [
    "authorization_id", "card_id", "timestamp", "transaction_type", "status", "billing_amount_chf",
    "merchant_id", "merchant_category", "merchant_country", "channel", "recurring", "customer_device_id",
]



# Translate the lowercase text of a switch into a boolean
BOOLEAN_BY_TEXT = {"true": True, "false": False}



# State how many cards the purchase speed block of the console summary lists
QUICK_SERIES_SUMMARY_CARD_COUNT = 5



# Name the three price columns of the item table, from the lowest to the highest price of one unit
ITEM_PRICE_COLUMNS = ["unit_price_min_chf", "unit_price_typical_chf", "unit_price_max_chf"]









#### Step 2: Load the published tables and convert their columns explicitly ####

# Hold the converted history, the cards with their account, the accounts and the merchants
@dataclass(frozen = True)
class SourceTables:
    history: pd.DataFrame
    cards_with_accounts: pd.DataFrame
    accounts: pd.DataFrame
    merchants: pd.DataFrame



# Read one published table with every cell as text, so "false" and an empty cell stay exactly as written
def read_case_table(file_name, data_folder):
    return pd.read_csv(data_folder / file_name, dtype = str, keep_default_na = False)



# Read the hour of the day in Swiss local time from UTC timestamps, which follows summer and winter time
def convert_to_swiss_local_hour(utc_timestamps):
    return utc_timestamps.dt.tz_convert(LOCAL_TIME_ZONE).dt.hour



# Load the four tables, check their sizes and join them on identifiers only
def load_source_tables(data_folder = CASE_DATA_FOLDER):

    # Read the four tables as text
    raw_history = read_case_table("authorization_history.csv", data_folder)
    raw_cards = read_case_table("cards.csv", data_folder)
    raw_accounts = read_case_table("accounts.csv", data_folder)
    merchants = read_case_table("merchants.csv", data_folder)



    # Check the row counts against the published figures
    assert len(raw_history) == EXPECTED_HISTORY_ROW_COUNT, "Expected 4701 history rows, found " + str(len(raw_history))
    assert len(raw_cards) == EXPECTED_CARD_COUNT, "Expected 41 cards, found " + str(len(raw_cards))
    assert len(raw_accounts) == EXPECTED_ACCOUNT_COUNT, "Expected 31 accounts, found " + str(len(raw_accounts))
    assert len(merchants) == EXPECTED_MERCHANT_COUNT, "Expected 58 merchants, found " + str(len(merchants))
    assert raw_cards["card_id"].is_unique, "A card identifier appears twice"
    assert raw_accounts["account_id"].is_unique, "An account identifier appears twice"
    assert merchants["merchant_id"].is_unique, "A merchant identifier appears twice"
    assert merchants["recurring_capable"].isin(["true", "false"]).all(), "A merchant has a recurring_capable value other than true or false"



    # Turn the two account limits into numbers
    accounts = (
        raw_accounts
        .loc[:, ["account_id", "customer_id", "per_transaction_limit_chf", "monthly_limit_chf"]]
        .assign(per_transaction_limit_chf = lambda table: pd.to_numeric(table["per_transaction_limit_chf"], errors = "raise"))
        .assign(monthly_limit_chf = lambda table: pd.to_numeric(table["monthly_limit_chf"], errors = "raise"))
    )



    # Join every card to its account for the customer and the limits, and turn the two switches into booleans
    cards_with_accounts = (
        raw_cards
        .loc[:, ["card_id", "account_id", "status", "online_enabled", "international_enabled"]]
        .merge(accounts, on = "account_id", how = "left", validate = "many_to_one", indicator = "account_join")
        .assign(online_enabled = lambda table: table["online_enabled"].map(BOOLEAN_BY_TEXT))
        .assign(international_enabled = lambda table: table["international_enabled"].map(BOOLEAN_BY_TEXT))
        .sort_values("card_id")
        .reset_index(drop = True)
    )
    assert (cards_with_accounts["account_join"] == "both").all(), "A card has no matching account"
    assert cards_with_accounts["online_enabled"].notna().all(), "A card has an online switch other than true or false"
    assert cards_with_accounts["international_enabled"].notna().all(), "A card has an international switch other than true or false"



    # Convert the amount and the timestamp of the history, read the Swiss local hour and attach the customer of the card
    card_owners = cards_with_accounts.loc[:, ["card_id", "account_id", "customer_id"]]
    history = (
        raw_history
        .loc[:, HISTORY_COLUMNS]
        .assign(billing_amount_chf = lambda table: pd.to_numeric(table["billing_amount_chf"], errors = "raise"))
        .assign(timestamp_utc = lambda table: pd.to_datetime(table["timestamp"], utc = True))
        .assign(local_hour = lambda table: convert_to_swiss_local_hour(table["timestamp_utc"]))
        .merge(card_owners, on = "card_id", how = "left", validate = "many_to_one", indicator = "card_join")
    )
    assert len(history) == EXPECTED_HISTORY_ROW_COUNT, "The join changed the number of history rows"
    assert history["authorization_id"].is_unique, "A history identifier appears twice"
    assert history["timestamp_utc"].notna().all(), "A history timestamp could not be read"
    assert history["billing_amount_chf"].notna().all(), "A history amount could not be read"
    assert (history["card_join"] == "both").all(), "A history row uses a card that cards.csv does not hold"
    assert history["merchant_id"].isin(merchants["merchant_id"]).all(), "A history row uses a merchant that merchants.csv does not hold"

    return SourceTables(
        history = history.drop(columns = ["card_join"]),
        cards_with_accounts = cards_with_accounts.drop(columns = ["account_join"]),
        accounts = accounts,
        merchants = merchants.sort_values("merchant_id").reset_index(drop = True),
    )



# Load the item table, check its size and turn the three prices into numbers.
# The description of an item is prose for human readers and is left unread.
def load_item_table(data_folder = CASE_DATA_FOLDER):
    raw_items = read_case_table("items.csv", data_folder)
    assert len(raw_items) == EXPECTED_ITEM_COUNT, "Expected 66 items, found " + str(len(raw_items))
    assert raw_items["item_id"].is_unique, "An item identifier appears twice"
    items = (
        raw_items
        .loc[:, ["item_id", "item_name", "item_category"] + ITEM_PRICE_COLUMNS]
        .assign(unit_price_min_chf = lambda table: pd.to_numeric(table["unit_price_min_chf"], errors = "raise"))
        .assign(unit_price_typical_chf = lambda table: pd.to_numeric(table["unit_price_typical_chf"], errors = "raise"))
        .assign(unit_price_max_chf = lambda table: pd.to_numeric(table["unit_price_max_chf"], errors = "raise"))
        .sort_values("item_id")
        .reset_index(drop = True)
    )
    assert (items["item_name"] != "").all(), "An item has no name"
    assert (items["item_category"] != "").all(), "An item has no category"
    assert items[ITEM_PRICE_COLUMNS].notna().all().all(), "An item price could not be read"
    assert (items["unit_price_min_chf"] <= items["unit_price_typical_chf"]).all(), "An item has a lowest price above its typical price"
    assert (items["unit_price_typical_chf"] <= items["unit_price_max_chf"]).all(), "An item has a typical price above its highest price"
    return items









#### Step 3: Select the populations and measure how fast attempts follow each other ####

# Hold the three row selections that every count is taken from
@dataclass(frozen = True)
class Populations:
    approved_purchases: pd.DataFrame
    active_purchases: pd.DataFrame
    active_attempts: pd.DataFrame



# Count, for every attempt of one card in time order, the earlier attempts inside the ten minutes before it.
# An earlier attempt counts when current minus 10 minutes <= earlier timestamp < current timestamp.
def count_earlier_attempts_within_window(card_timestamps):
    timestamp_values = card_timestamps.dt.tz_convert(None).to_numpy()
    window_starts = timestamp_values - np.timedelta64(QUICK_SERIES_WINDOW_MINUTES, "m")
    first_position_inside_window = np.searchsorted(timestamp_values, window_starts, side = "left")
    first_position_at_current_time = np.searchsorted(timestamp_values, timestamp_values, side = "left")
    return pd.Series(first_position_at_current_time - first_position_inside_window, index = card_timestamps.index)



# Select the three populations from the history
def select_populations(history):

    # Select the approved purchases and the active purchases with the filters stated at the top
    approved_purchases = history.query(APPROVED_PURCHASE_FILTER)
    active_purchases = history.query(ACTIVE_PURCHASE_FILTER)
    assert len(active_purchases) == EXPECTED_ACTIVE_PURCHASE_COUNT, "Expected 3803 active purchases, found " + str(len(active_purchases))



    # Put the active attempts of every card in time order, with ties ordered by identifier, and count the earlier attempts
    active_attempts = (
        history
        .query(ACTIVE_ATTEMPT_FILTER)
        .sort_values(["card_id", "timestamp_utc", "authorization_id"])
        .reset_index(drop = True)
        .assign(earlier_attempt_count = lambda table: table.groupby("card_id")["timestamp_utc"].transform(count_earlier_attempts_within_window))
        .assign(follows_within_window = lambda table: table["earlier_attempt_count"] >= 1)
    )

    return Populations(
        approved_purchases = approved_purchases,
        active_purchases = active_purchases,
        active_attempts = active_attempts,
    )









#### Step 4: Build the count tables that every card, customer and merchant entry reads from ####

# Count the rows per owner and key, and return one dictionary of counts per owner
def build_count_lookup(table, owner_column, key_column):
    counts = (
        table
        .groupby([owner_column, key_column])
        .size()
        .reset_index(name = "purchase_count")
    )
    return {
        owner_id: dict(zip(owner_rows[key_column], owner_rows["purchase_count"].astype(int).tolist()))
        for owner_id, owner_rows in counts.groupby(owner_column)
    }



# Count the rows per owner, and return one whole number per owner
def build_row_count_lookup(table, owner_column):
    row_counts = table.groupby(owner_column).size()
    return dict(zip(row_counts.index, row_counts.astype(int).tolist()))



# Take the middle amount of a group of purchases
def median_amount(amounts):
    return amounts.quantile(0.50)



# Take the amount that 95 percent of a group of purchases stay at or below
def percentile_95_amount(amounts):
    return amounts.quantile(0.95)



# Summarize the amounts per group as the count, the median and the 95th percentile, rounded to two places
def summarize_amounts(table, group_columns):
    return (
        table
        .groupby(group_columns)["billing_amount_chf"]
        .agg(count = "count", p50 = median_amount, p95 = percentile_95_amount)
        .round({"p50": 2, "p95": 2})
        .reset_index()
    )



# Count the active purchases per Swiss local hour, as one list of 24 whole numbers per card
def build_local_hour_lookup(active_purchases, card_ids):
    hour_table = (
        active_purchases
        .groupby(["card_id", "local_hour"])
        .size()
        .unstack("local_hour", fill_value = 0)
        .reindex(index = card_ids, columns = HOURS_OF_THE_DAY, fill_value = 0)
    )
    return {
        card_id: [int(hour_count) for hour_count in hour_counts]
        for card_id, hour_counts in zip(hour_table.index, hour_table.to_numpy().tolist())
    }



# Summarize how fast attempts follow each other, as three whole numbers per card
def build_quick_series_lookup(active_attempts, card_ids):
    quick_series_table = (
        active_attempts
        .groupby("card_id")
        .agg(
            active_attempt_count = ("authorization_id", "count"),
            attempts_following_within_10_minutes = ("follows_within_window", "sum"),
            largest_earlier_attempt_count = ("earlier_attempt_count", "max"),
        )
        .reindex(card_ids, fill_value = 0)
        .rename_axis("card_id")
        .reset_index()
    )
    return {
        quick_series_row["card_id"]: {
            "active_attempt_count": int(quick_series_row["active_attempt_count"]),
            "attempts_following_within_10_minutes": int(quick_series_row["attempts_following_within_10_minutes"]),
            "largest_earlier_attempt_count": int(quick_series_row["largest_earlier_attempt_count"]),
        }
        for quick_series_row in quick_series_table.to_dict("records")
    }



# Hold every lookup a card entry reads from, each keyed by card
@dataclass(frozen = True)
class CardLookups:
    approved_purchase_counts: dict
    merchant_purchase_counts: dict
    active_purchase_counts: dict
    device_purchase_counts: dict
    country_purchase_counts: dict
    merchant_category_purchase_counts: dict
    local_hour_purchase_counts: dict
    amounts: dict
    amounts_by_merchant_category: dict
    quick_series: dict



# Build all lookups of the cards from the three populations
def build_card_lookups(populations, card_ids):

    # Leave out the purchases without a device, which are the purchases in a store or at a cash machine
    active_purchases_with_device = populations.active_purchases.query("customer_device_id != ''")



    # Summarize the amounts of the active purchases per card, and per card and merchant category
    amount_rows = summarize_amounts(populations.active_purchases, ["card_id"]).to_dict("records")
    amounts_by_category = summarize_amounts(populations.active_purchases, ["card_id", "merchant_category"])

    return CardLookups(
        approved_purchase_counts = build_row_count_lookup(populations.approved_purchases, "card_id"),
        merchant_purchase_counts = build_count_lookup(populations.approved_purchases, "card_id", "merchant_id"),
        active_purchase_counts = build_row_count_lookup(populations.active_purchases, "card_id"),
        device_purchase_counts = build_count_lookup(active_purchases_with_device, "card_id", "customer_device_id"),
        country_purchase_counts = build_count_lookup(populations.active_purchases, "card_id", "merchant_country"),
        merchant_category_purchase_counts = build_count_lookup(populations.active_purchases, "card_id", "merchant_category"),
        local_hour_purchase_counts = build_local_hour_lookup(populations.active_purchases, card_ids),
        amounts = {
            amount_row["card_id"]: {"p50": float(amount_row["p50"]), "p95": float(amount_row["p95"])}
            for amount_row in amount_rows
        },
        amounts_by_merchant_category = {
            card_id: {
                category_row["merchant_category"]: {
                    "count": int(category_row["count"]),
                    "p50": float(category_row["p50"]),
                    "p95": float(category_row["p95"]),
                }
                for category_row in card_rows.to_dict("records")
            }
            for card_id, card_rows in amounts_by_category.groupby("card_id")
        },
        quick_series = build_quick_series_lookup(populations.active_attempts, card_ids),
    )



# Count, per merchant, the distinct cards or customers with at least one approved purchase there, which is 0 for a merchant nobody bought from
def count_distinct_buyers_per_merchant(approved_purchases, merchant_ids, buyer_column):
    buyer_counts = (
        approved_purchases
        .groupby("merchant_id")[buyer_column]
        .nunique()
        .reindex(merchant_ids, fill_value = 0)
    )
    return dict(zip(buyer_counts.index, buyer_counts.astype(int).tolist()))









#### Step 5: Build the content of the three files ####

# Write a UTC timestamp the way the published files write it
def format_utc_timestamp(utc_timestamp):
    return utc_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")



# Build the entry of one card, where a card without history keeps zero counts, empty dictionaries and empty amounts
def build_card_entry(card_record, card_lookups):
    card_id = card_record["card_id"]
    return {
        "customer_id": card_record["customer_id"],
        "account_id": card_record["account_id"],
        "online_enabled": bool(card_record["online_enabled"]),
        "international_enabled": bool(card_record["international_enabled"]),
        "status": card_record["status"],
        "per_transaction_limit_chf": float(card_record["per_transaction_limit_chf"]),
        "monthly_limit_chf": float(card_record["monthly_limit_chf"]),
        "approved_purchase_count": card_lookups.approved_purchase_counts.get(card_id, 0),
        "merchant_purchase_counts": card_lookups.merchant_purchase_counts.get(card_id, {}),
        "active_purchase_count": card_lookups.active_purchase_counts.get(card_id, 0),
        "device_purchase_counts": card_lookups.device_purchase_counts.get(card_id, {}),
        "country_purchase_counts": card_lookups.country_purchase_counts.get(card_id, {}),
        "merchant_category_purchase_counts": card_lookups.merchant_category_purchase_counts.get(card_id, {}),
        "local_hour_purchase_counts": card_lookups.local_hour_purchase_counts[card_id],
        "amount_chf": card_lookups.amounts.get(card_id, {"p50": None, "p95": None}),
        "amount_chf_by_merchant_category": card_lookups.amounts_by_merchant_category.get(card_id, {}),
        "quick_series": card_lookups.quick_series[card_id],
    }



# Build the familiarity file, with one entry per card, per customer and per merchant
def build_familiarity_baselines(source_tables, populations):

    # List the identifiers the file must cover, also those without any history
    card_ids = source_tables.cards_with_accounts["card_id"].tolist()
    customer_ids = sorted(source_tables.accounts["customer_id"].unique())
    merchant_ids = source_tables.merchants["merchant_id"].tolist()



    # Build the lookups of the cards, the customers and the merchants
    card_lookups = build_card_lookups(populations, card_ids)
    customer_merchant_purchase_counts = build_count_lookup(populations.approved_purchases, "customer_id", "merchant_id")
    card_ids_by_customer = {
        customer_id: sorted(customer_cards["card_id"])
        for customer_id, customer_cards in source_tables.cards_with_accounts.groupby("customer_id")
    }
    cards_per_merchant = count_distinct_buyers_per_merchant(populations.approved_purchases, merchant_ids, "card_id")
    customers_per_merchant = count_distinct_buyers_per_merchant(populations.approved_purchases, merchant_ids, "customer_id")



    # Assemble the file, where every value comes from the history and the three reference tables alone
    return {
        "history_window": {
            "first_timestamp": format_utc_timestamp(source_tables.history["timestamp_utc"].min()),
            "last_timestamp": format_utc_timestamp(source_tables.history["timestamp_utc"].max()),
        },
        "card_count": len(card_ids),
        "customer_count": len(customer_ids),
        "cards": {
            card_record["card_id"]: build_card_entry(card_record, card_lookups)
            for card_record in source_tables.cards_with_accounts.to_dict("records")
        },
        "customers": {
            customer_id: {
                "card_ids": card_ids_by_customer.get(customer_id, []),
                "merchant_purchase_counts": customer_merchant_purchase_counts.get(customer_id, {}),
            }
            for customer_id in customer_ids
        },
        "merchants": {
            merchant_id: {
                "cards_with_approved_purchase": cards_per_merchant[merchant_id],
                "customers_with_approved_purchase": customers_per_merchant[merchant_id],
            }
            for merchant_id in merchant_ids
        },
    }



# Build the merchant directory, with one entry per merchant, where the name is reduced to lowercase letters and digits without accents
def build_merchant_directory(source_tables, populations):
    merchant_ids = source_tables.merchants["merchant_id"].tolist()
    cards_per_merchant = count_distinct_buyers_per_merchant(populations.approved_purchases, merchant_ids, "card_id")
    customers_per_merchant = count_distinct_buyers_per_merchant(populations.approved_purchases, merchant_ids, "customer_id")
    return {
        merchant_record["merchant_id"]: {
            "merchant_name": merchant_record["merchant_name"],
            "normalized_name": normalize_merchant_name(merchant_record["merchant_name"]),
            "merchant_category": merchant_record["merchant_category"],
            "merchant_country": merchant_record["merchant_country"],
            "recurring_capable": merchant_record["recurring_capable"],
            "cards_with_approved_purchase": cards_per_merchant[merchant_record["merchant_id"]],
            "customers_with_approved_purchase": customers_per_merchant[merchant_record["merchant_id"]],
        }
        for merchant_record in source_tables.merchants.to_dict("records")
    }



# Build the item catalogue, with one entry per item that carries its name, its category and its three prices as numbers
def build_item_catalogue(items):
    return {
        item_record["item_id"]: {
            "item_name": item_record["item_name"],
            "item_category": item_record["item_category"],
            "unit_price_min_chf": float(item_record["unit_price_min_chf"]),
            "unit_price_typical_chf": float(item_record["unit_price_typical_chf"]),
            "unit_price_max_chf": float(item_record["unit_price_max_chf"]),
        }
        for item_record in items.to_dict("records")
    }









#### Step 6: Write the three files and verify them by reading them back ####

# Hold what one build produced, for the console summary
@dataclass(frozen = True)
class BuildResult:
    source_tables: SourceTables
    populations: Populations
    familiarity_baselines: dict
    merchant_directory: dict
    item_catalogue: dict
    written_paths: tuple



# Write one file with sorted keys and a final newline as UTF-8 bytes, so two runs give the same bytes on every system
def write_json_file(content, file_path):
    json_text = json.dumps(content, sort_keys = True, indent = 2, ensure_ascii = False) + "\n"
    file_path.write_bytes(json_text.encode("utf-8"))



# Build the three files from the published tables, write them and check what was written
def build_and_write_baselines(output_folder = DEFAULT_OUTPUT_FOLDER, data_folder = CASE_DATA_FOLDER):

    # Load the tables and build the content of the three files
    source_tables = load_source_tables(data_folder)
    populations = select_populations(source_tables.history)
    familiarity_baselines = build_familiarity_baselines(source_tables, populations)
    merchant_directory = build_merchant_directory(source_tables, populations)
    item_catalogue = build_item_catalogue(load_item_table(data_folder))



    # Write the three files into the output folder
    output_folder = Path(output_folder)
    output_folder.mkdir(parents = True, exist_ok = True)
    familiarity_path = output_folder / FAMILIARITY_FILE_NAME
    directory_path = output_folder / DIRECTORY_FILE_NAME
    item_catalogue_path = output_folder / ITEM_CATALOGUE_FILE_NAME
    write_json_file(familiarity_baselines, familiarity_path)
    write_json_file(merchant_directory, directory_path)
    write_json_file(item_catalogue, item_catalogue_path)



    # Read both files back and check the number of entries
    familiarity_readback = json.loads(familiarity_path.read_text(encoding = "utf-8"))
    directory_readback = json.loads(directory_path.read_text(encoding = "utf-8"))
    assert sorted(familiarity_readback) == ["card_count", "cards", "customer_count", "customers", "history_window", "merchants"], "The familiarity file has unexpected top-level keys"
    assert familiarity_readback["card_count"] == EXPECTED_CARD_COUNT, "The familiarity file states a wrong card count"
    assert familiarity_readback["customer_count"] == len(familiarity_readback["customers"]), "The familiarity file states a wrong customer count"
    merchants_missing_a_count = sorted(
        merchant_id
        for merchant_id, merchant_entry in familiarity_readback["merchants"].items()
        if sorted(merchant_entry) != ["cards_with_approved_purchase", "customers_with_approved_purchase"]
        or merchant_entry["customers_with_approved_purchase"] > merchant_entry["cards_with_approved_purchase"]
        or directory_readback[merchant_id]["customers_with_approved_purchase"] != merchant_entry["customers_with_approved_purchase"]
    )
    assert not merchants_missing_a_count, "The customer counts of these merchants are missing or inconsistent - " + ", ".join(merchants_missing_a_count)
    assert len(familiarity_readback["cards"]) == EXPECTED_CARD_COUNT, "The familiarity file does not hold 41 cards"
    assert len(familiarity_readback["customers"]) == source_tables.accounts["customer_id"].nunique(), "The familiarity file does not hold every customer"
    assert len(familiarity_readback["merchants"]) == EXPECTED_MERCHANT_COUNT, "The familiarity file does not hold 58 merchants"
    assert len(directory_readback) == EXPECTED_MERCHANT_COUNT, "The merchant directory does not hold 58 merchants"
    assert familiarity_readback == familiarity_baselines, "The familiarity file reads back differently from what was built"
    assert directory_readback == merchant_directory, "The merchant directory reads back differently from what was built"



    # Read the item catalogue back and check the number of entries, the fields of every entry and the type of every price
    item_catalogue_readback = json.loads(item_catalogue_path.read_text(encoding = "utf-8"))
    expected_item_fields = sorted(["item_name", "item_category"] + ITEM_PRICE_COLUMNS)
    items_with_unexpected_content = sorted(
        item_id
        for item_id, item_entry in item_catalogue_readback.items()
        if sorted(item_entry) != expected_item_fields
        or not all(isinstance(item_entry[price_column], float) for price_column in ITEM_PRICE_COLUMNS)
    )
    assert len(item_catalogue_readback) == EXPECTED_ITEM_COUNT, "The item catalogue does not hold 66 items"
    assert not items_with_unexpected_content, "These items carry unexpected fields or a price that is not a number - " + ", ".join(items_with_unexpected_content)
    assert item_catalogue_readback == item_catalogue, "The item catalogue reads back differently from what was built"

    return BuildResult(
        source_tables = source_tables,
        populations = populations,
        familiarity_baselines = familiarity_baselines,
        merchant_directory = merchant_directory,
        item_catalogue = item_catalogue,
        written_paths = (familiarity_path, directory_path, item_catalogue_path),
    )









#### Step 7: Print the summary, read the command line and run ####

# Print the summary of the build in labeled blocks
def print_build_summary(build_result):

    # Show the sizes of the loaded tables and of the populations
    history = build_result.source_tables.history
    population_sizes = pd.DataFrame({
        "population": ["history rows", "approved purchases", "active purchases", "active attempts of any status"],
        "rows": [
            len(history),
            len(build_result.populations.approved_purchases),
            len(build_result.populations.active_purchases),
            len(build_result.populations.active_attempts),
        ],
    })
    print("--- Rows per population ---")
    print(population_sizes.to_string(index = False))
    print()



    # Show how many approved purchases carry a recurring agreement on a channel other than the recurring channel
    recurring_flag_elsewhere = build_result.populations.approved_purchases.query("recurring == 'true' and channel != 'recurring'")
    print("--- Approved purchases with recurring true on a channel other than recurring ---")
    print(len(recurring_flag_elsewhere))
    print()



    # Show the window of the history and the number of entries per section
    familiarity_baselines = build_result.familiarity_baselines
    item_categories = {item_entry["item_category"] for item_entry in build_result.item_catalogue.values()}
    entry_counts = pd.DataFrame({
        "section": ["cards", "customers", "merchants", "merchant directory", "item catalogue", "item categories"],
        "entries": [
            len(familiarity_baselines["cards"]),
            len(familiarity_baselines["customers"]),
            len(familiarity_baselines["merchants"]),
            len(build_result.merchant_directory),
            len(build_result.item_catalogue),
            len(item_categories),
        ],
    })
    print("--- History window ---")
    print(familiarity_baselines["history_window"]["first_timestamp"] + " to " + familiarity_baselines["history_window"]["last_timestamp"])
    print()
    print("--- Entries per section ---")
    print(entry_counts.to_string(index = False))
    print()



    # Show the purchase speed figures of the cards with the most attempts following within ten minutes
    quick_series_table = (
        pd.DataFrame([
            {"card_id": card_id, **card_entry["quick_series"]}
            for card_id, card_entry in familiarity_baselines["cards"].items()
        ])
        .sort_values(["attempts_following_within_10_minutes", "card_id"], ascending = [False, True])
        .head(QUICK_SERIES_SUMMARY_CARD_COUNT)
    )
    print("--- The five cards with the most attempts following within ten minutes ---")
    print(quick_series_table.to_string(index = False))
    print()



    # Show the merchants nobody bought from
    merchants_without_cards = sorted(
        merchant_id
        for merchant_id, merchant_entry in familiarity_baselines["merchants"].items()
        if merchant_entry["cards_with_approved_purchase"] == 0
    )
    print("--- Merchants without any approved purchase ---")
    print(", ".join(merchants_without_cards) if merchants_without_cards else "none")
    print()

    print("--- Files written ---")
    print("\n".join(str(written_path) for written_path in build_result.written_paths))
    print()



# Read the command line options
def parse_command_line(command_line_arguments = None):
    parser = argparse.ArgumentParser(description = "Build the familiarity and behavior counts from the card history, and the item catalogue.")
    parser.add_argument("--output-folder", default = str(DEFAULT_OUTPUT_FOLDER), help = "The folder the three JSON files are written into")
    return parser.parse_args(command_line_arguments)



# Build the three files from the command line options and print the summary
def main(command_line_arguments = None):
    options = parse_command_line(command_line_arguments)
    build_result = build_and_write_baselines(output_folder = Path(options.output_folder))
    print_build_summary(build_result)
    return build_result



if __name__ == "__main__":
    main()
