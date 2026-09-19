# Script: test_baselines.py
# Purpose: Check that the familiarity and behavior counts match the figures measured in the history, build identically twice and load with unknown and zero kept apart
# Author: Andrés Gallardo
# Date: September 2026

import inspect
import json
import shutil
from dataclasses import FrozenInstanceError
from difflib import SequenceMatcher

import pandas as pd
import pytest

import build_baselines
from app.state import baselines as baselines_module
from app.state.baselines import BaselinesUnavailableError, load_baselines









#### Step 1: Build both files once and load them ####

# Locate the two source folders that the last step reads as text
BACKEND_FOLDER = build_baselines.REPOSITORY_FOLDER / "backend"
BUILD_SCRIPT_PATH = build_baselines.REPOSITORY_FOLDER / "scripts" / "build_baselines.py"



# Name the four cards that the figures below look at most closely
CLOSELY_CHECKED_CARD_IDS = ("CA0001", "CA0011", "CA0023", "CA0039")



# Build both files once into a temporary folder through the functions of the script
@pytest.fixture(scope = "module")
def built_folder(tmp_path_factory):
    output_folder = tmp_path_factory.mktemp("baselines")
    build_baselines.build_and_write_baselines(output_folder = output_folder)
    return output_folder



# Load the built files from the temporary folder, never from the processed data folder
@pytest.fixture(scope = "module")
def loaded_baselines(built_folder):
    return load_baselines(built_folder)









#### Step 2: Check the familiarity figures ####

# Check the approved purchases on the card against all cards of the customer
@pytest.mark.parametrize(
    "merchant_id, card_id, expected_on_card, customer_id, expected_for_customer",
    [
        ("ME0001", "CA0001", 26, "CU0001", 47),
        ("ME0028", "CA0011", 22, "CU0006", 34),
        ("ME0025", "CA0023", 16, "CU0012", 27),
        ("ME0027", "CA0023", 15, "CU0012", 30),
        ("ME0024", "CA0039", 21, "CU0019", 21),
        ("ME0022", "CA0039", 6, "CU0019", 8),
        ("ME0023", "CA0039", 0, "CU0019", 2),
    ],
)
def test_approved_purchases_on_the_card_and_for_the_customer(loaded_baselines, merchant_id, card_id, expected_on_card, customer_id, expected_for_customer):
    assert loaded_baselines.get_card(card_id).customer_id == customer_id
    assert loaded_baselines.card_merchant_purchase_count(card_id, merchant_id) == expected_on_card
    assert loaded_baselines.customer_merchant_purchase_count(customer_id, merchant_id) == expected_for_customer



# Check the number of cards with an approved purchase at five merchants
@pytest.mark.parametrize(
    "merchant_id, expected_card_count",
    [("ME0058", 32), ("ME0026", 29), ("ME0060", 5), ("ME0029", 2), ("ME0059", 0)],
)
def test_cards_with_an_approved_purchase(loaded_baselines, merchant_id, expected_card_count):
    assert loaded_baselines.merchant_card_count(merchant_id) == expected_card_count



# Check the number of customers with an approved purchase at five merchants
@pytest.mark.parametrize(
    "merchant_id, expected_customer_count",
    [("ME0026", 19), ("ME0058", 18), ("ME0060", 5), ("ME0029", 2), ("ME0059", 0)],
)
def test_customers_with_an_approved_purchase(loaded_baselines, merchant_id, expected_customer_count):
    assert loaded_baselines.merchant_customer_count(merchant_id) == expected_customer_count



# Check that exactly two merchants have no card with an approved purchase. One is a merchant nobody bought from.
# The other is a cash machine operator whose history rows are cash withdrawals, which are not purchases.
def test_two_merchants_have_no_card(loaded_baselines):
    merchant_ids_without_cards = [
        merchant.merchant_id
        for merchant in loaded_baselines.list_merchants()
        if merchant.cards_with_approved_purchase == 0
    ]
    assert merchant_ids_without_cards == ["ME0057", "ME0059"]
    assert loaded_baselines.get_merchant("ME0057").merchant_category == "cash_withdrawal"



# Check that the directory lists every merchant once, and that the stated counts match the entries
def test_directory_lists_every_merchant(loaded_baselines):
    assert len(loaded_baselines.list_merchants()) == 58
    assert loaded_baselines.card_count == 41
    assert len(loaded_baselines.cards) == 41
    assert loaded_baselines.customer_count == 20
    assert len(loaded_baselines.customers) == 20









#### Step 3: Check the behavior figures ####

# Check the Swiss local hours of one card, which never buys in the early morning and mostly buys in the evening
def test_local_hours_of_one_card(loaded_baselines):
    hour_counts = loaded_baselines.get_card("CA0023").local_hour_purchase_counts
    assert sum(hour_counts[1:8]) == 0
    assert sum(hour_counts[19:24]) == 74
    assert sum(hour_counts[19:23]) == 69
    assert hour_counts[0] == 5



# Check the countries of two cards, where a country without purchases has no entry at all
def test_countries_of_two_cards(loaded_baselines):
    countries_of_first_card = loaded_baselines.get_card("CA0023").country_purchase_counts
    countries_of_second_card = loaded_baselines.get_card("CA0039").country_purchase_counts
    assert countries_of_first_card["CH"] == 117
    assert countries_of_first_card["IT"] == 22
    assert "GB" not in countries_of_first_card
    assert countries_of_second_card["CH"] == 70
    assert countries_of_second_card["US"] == 21



# Check that one device appears on no card
def test_unseen_device_appears_on_no_card(loaded_baselines):
    cards_with_the_device = [
        card_id
        for card_id, card in loaded_baselines.cards.items()
        if "DVC-4C0E9B" in card.device_purchase_counts
    ]
    assert cards_with_the_device == []



# Check that each of the four cards has exactly three devices
@pytest.mark.parametrize("card_id", CLOSELY_CHECKED_CARD_IDS)
def test_four_cards_have_three_devices(loaded_baselines, card_id):
    assert len(loaded_baselines.get_card(card_id).device_purchase_counts) == 3



# Check that nine cards have the international switch off, and none of the four cards is among them
def test_cards_with_international_switch_off(loaded_baselines):
    card_ids_with_switch_off = [
        card_id
        for card_id, card in loaded_baselines.cards.items()
        if not card.international_enabled
    ]
    assert len(card_ids_with_switch_off) == 9
    assert set(card_ids_with_switch_off).isdisjoint(CLOSELY_CHECKED_CARD_IDS)



# Check that the hour counts and the country counts of every card add up to its active purchases
def test_hour_and_country_counts_add_up(loaded_baselines):
    cards_that_do_not_add_up = [
        card_id
        for card_id, card in loaded_baselines.cards.items()
        if len(card.local_hour_purchase_counts) != 24
        or sum(card.local_hour_purchase_counts) != card.active_purchase_count
        or sum(card.country_purchase_counts.values()) != card.active_purchase_count
    ]
    assert cards_that_do_not_add_up == []
    assert sum(card.active_purchase_count for card in loaded_baselines.cards.values()) == 3803



# Check that a summer and a winter timestamp are read in Swiss local time, two hours and one hour ahead of UTC
def test_summer_and_winter_time():
    utc_timestamps = pd.Series(pd.to_datetime(["2026-08-14T02:14:00Z", "2026-01-14T02:14:00Z"], utc = True))
    local_hours = build_baselines.convert_to_swiss_local_hour(utc_timestamps).tolist()
    assert local_hours == [4, 3]



# Check the ten minute window on one crafted card. An attempt exactly ten minutes earlier counts,
# an attempt at the same moment does not, and an attempt ten minutes and one second earlier does not.
def test_ten_minute_window_edges():
    utc_timestamps = pd.Series(pd.to_datetime(
        [
            "2026-03-02T12:00:00Z",
            "2026-03-02T12:10:00Z",
            "2026-03-02T12:10:00Z",
            "2026-03-02T12:20:01Z",
            "2026-03-02T12:20:02Z",
        ],
        utc = True,
    ))
    earlier_attempt_counts = build_baselines.count_earlier_attempts_within_window(utc_timestamps).tolist()
    assert earlier_attempt_counts == [0, 1, 1, 0, 1]









#### Step 4: Check the files and the loader ####

# Check that building twice gives the same bytes
def test_building_twice_gives_identical_files(built_folder, tmp_path):
    build_baselines.build_and_write_baselines(output_folder = tmp_path)
    for file_name in (build_baselines.FAMILIARITY_FILE_NAME, build_baselines.DIRECTORY_FILE_NAME, build_baselines.ITEM_CATALOGUE_FILE_NAME):
        assert (built_folder / file_name).read_bytes() == (tmp_path / file_name).read_bytes()



# Check that unknown and zero stay different
def test_unknown_stays_different_from_zero(loaded_baselines):
    assert loaded_baselines.get_card("CA9999") is None
    assert loaded_baselines.get_customer("CU9999") is None
    assert loaded_baselines.get_merchant("ME9999") is None
    assert loaded_baselines.card_merchant_purchase_count("CA9999", "ME0001") is None
    assert loaded_baselines.customer_merchant_purchase_count("CU9999", "ME0001") is None
    assert loaded_baselines.merchant_card_count("ME9999") is None
    assert loaded_baselines.merchant_customer_count("ME9999") is None
    assert loaded_baselines.card_merchant_purchase_count("CA0001", "ME9999") == 0
    assert loaded_baselines.customer_merchant_purchase_count("CU0001", "ME9999") == 0



# Check that the loaded counts cannot be changed
def test_loaded_objects_are_frozen(loaded_baselines):
    with pytest.raises(FrozenInstanceError):
        loaded_baselines.get_card("CA0001").status = "blocked"
    with pytest.raises(TypeError):
        loaded_baselines.get_card("CA0001").merchant_purchase_counts["ME0001"] = 0



# Check that a missing file raises the one clear error
@pytest.mark.parametrize("missing_file_name", [build_baselines.FAMILIARITY_FILE_NAME, build_baselines.DIRECTORY_FILE_NAME])
def test_missing_file_raises(built_folder, tmp_path, missing_file_name):
    shutil.copytree(built_folder, tmp_path / "copy")
    (tmp_path / "copy" / missing_file_name).unlink()
    with pytest.raises(BaselinesUnavailableError):
        load_baselines(tmp_path / "copy")



# Check that a file with broken JSON and a file with valid JSON but missing content raise the same error
@pytest.mark.parametrize("malformed_text", ["{ this is not JSON", "{}", "[]"])
@pytest.mark.parametrize("malformed_file_name", [build_baselines.FAMILIARITY_FILE_NAME, build_baselines.DIRECTORY_FILE_NAME])
def test_malformed_file_raises(built_folder, tmp_path, malformed_file_name, malformed_text):
    shutil.copytree(built_folder, tmp_path / "copy")
    (tmp_path / "copy" / malformed_file_name).write_text(malformed_text, encoding = "utf-8")
    with pytest.raises(BaselinesUnavailableError):
        load_baselines(tmp_path / "copy")



# Check that a switch written as the text "false" is refused, because any non-empty text would otherwise read as true
def test_switch_written_as_text_raises(built_folder, tmp_path):
    shutil.copytree(built_folder, tmp_path / "copy")
    familiarity_path = tmp_path / "copy" / build_baselines.FAMILIARITY_FILE_NAME
    familiarity_content = json.loads(familiarity_path.read_text(encoding = "utf-8"))
    familiarity_content["cards"]["CA0001"]["international_enabled"] = "false"
    familiarity_path.write_text(json.dumps(familiarity_content), encoding = "utf-8")
    with pytest.raises(BaselinesUnavailableError):
        load_baselines(tmp_path / "copy")



# Check the normalized names of the two merchants with nearly the same name, and how alike they are
def test_normalized_names_of_the_lookalike_pair(loaded_baselines):
    first_name = loaded_baselines.get_merchant("ME0022").normalized_name
    second_name = loaded_baselines.get_merchant("ME0059").normalized_name
    assert first_name == "pixelharbor"
    assert second_name == "pixelharbour"
    assert round(SequenceMatcher(None, first_name, second_name).ratio(), 3) == 0.957



# Check that accents, spaces and punctuation disappear from a normalized name
def test_normalized_name_drops_accents_and_punctuation():
    assert build_baselines.normalize_merchant_name("PixelHarbor") == "pixelharbor"
    assert build_baselines.normalize_merchant_name("Café Zürich & Co. 24") == "cafezurichco24"









#### Step 5: Check the source files ####

# Check that no backend file names the reference decisions, leaving out the installed packages of the virtual environment
def test_backend_never_names_the_reference_decisions():
    forbidden_text = "purchase_verdicts"
    backend_files_naming_it = [
        str(python_path)
        for python_path in sorted(BACKEND_FOLDER.rglob("*.py"))
        if ".venv" not in python_path.parts
        and forbidden_text in python_path.read_text(encoding = "utf-8", errors = "ignore")
    ]
    assert backend_files_naming_it == []



# Check that the build script depends on nothing but the history and the three reference tables
def test_build_script_names_no_other_source():
    script_text = BUILD_SCRIPT_PATH.read_text(encoding = "utf-8")
    assert "purchase_attempts" not in script_text
    assert "scenario" not in script_text



# Check that only the one loading function of the loader touches the disk
def test_only_the_loading_function_reads_files():
    loader_text = inspect.getsource(baselines_module)
    loading_function_text = inspect.getsource(load_baselines)
    assert loading_function_text in loader_text
    text_outside_the_loading_function = loader_text.replace(loading_function_text, "")
    assert "open(" not in text_outside_the_loading_function
    assert "read_text(" not in text_outside_the_loading_function
    assert loading_function_text.count("read_text(") == 2
    assert "open(" not in loading_function_text
