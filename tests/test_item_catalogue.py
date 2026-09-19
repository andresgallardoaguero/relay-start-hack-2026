# Script: test_item_catalogue.py
# Purpose: Check that the item catalogue is built from the published item table, builds identically twice and loads as one object that cannot be changed
# Author: Andrés Gallardo
# Date: September 2026

import inspect
import json
import shutil
from dataclasses import FrozenInstanceError

import pytest

import build_baselines
from app.state import item_catalogue as item_catalogue_module
from app.state.item_catalogue import ItemCatalogueUnavailableError, get_default_item_catalogue, load_item_catalogue









#### Step 1: Build the catalogue once and load it ####

# List the 16 item categories of the published vocabulary, written out here independently of the build
EXPECTED_ITEM_CATEGORIES = (
    "books", "clothing", "cosmetics", "dining", "electronics", "food_delivery", "fuel", "gift_card", "groceries",
    "home_improvement", "hotel", "household", "membership", "sporting_goods", "subscriptions", "transport",
)



# Build the files once into a temporary folder through the functions of the script
@pytest.fixture(scope = "module")
def built_folder(tmp_path_factory):
    output_folder = tmp_path_factory.mktemp("item_catalogue")
    build_baselines.build_and_write_baselines(output_folder = output_folder)
    return output_folder



# Load the built catalogue from the temporary folder, never from the processed data folder
@pytest.fixture(scope = "module")
def loaded_catalogue(built_folder):
    return load_item_catalogue(built_folder)



# Copy the built catalogue file into a fresh folder that a test may damage
def copy_catalogue_file(built_folder, target_folder):
    target_folder.mkdir()
    shutil.copy(built_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME, target_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME)
    return target_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME









#### Step 2: Check the content ####

# Check the number of items and the closed list of item categories, which is sorted
def test_catalogue_holds_66_items_and_16_categories(loaded_catalogue):
    assert len(loaded_catalogue.items) == 66
    assert len(loaded_catalogue.item_categories) == 16
    assert loaded_catalogue.item_categories == EXPECTED_ITEM_CATEGORIES
    assert {catalogue_item.item_category for catalogue_item in loaded_catalogue.items.values()} == set(EXPECTED_ITEM_CATEGORIES)



# Check the first item field by field
def test_first_item_is_fresh_produce_in_groceries(loaded_catalogue):
    first_item = loaded_catalogue.get_item("IT0001")
    assert first_item.item_id == "IT0001"
    assert first_item.item_name == "Fresh produce selection"
    assert first_item.item_category == "groceries"
    assert (first_item.unit_price_min_chf, first_item.unit_price_typical_chf, first_item.unit_price_max_chf) == (12.0, 28.0, 90.0)



# Check that the three prices of every item are numbers in rising order, in the file and in the loaded object
def test_prices_are_numbers_in_rising_order(built_folder, loaded_catalogue):
    file_content = json.loads((built_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME).read_text(encoding = "utf-8"))
    entries_with_a_price_that_is_no_number = [
        item_id
        for item_id, item_entry in file_content.items()
        if not all(isinstance(item_entry[price_column], float) for price_column in build_baselines.ITEM_PRICE_COLUMNS)
    ]
    items_out_of_order = [
        catalogue_item.item_id
        for catalogue_item in loaded_catalogue.items.values()
        if not catalogue_item.unit_price_min_chf <= catalogue_item.unit_price_typical_chf <= catalogue_item.unit_price_max_chf
    ]
    assert entries_with_a_price_that_is_no_number == []
    assert items_out_of_order == []
    assert all(isinstance(catalogue_item.unit_price_typical_chf, float) for catalogue_item in loaded_catalogue.items.values())



# Check that every entry of the file carries exactly the five stated fields, so no description reaches the engine
def test_file_entries_carry_exactly_five_fields(built_folder):
    file_content = json.loads((built_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME).read_text(encoding = "utf-8"))
    field_sets = {tuple(sorted(item_entry)) for item_entry in file_content.values()}
    assert field_sets == {("item_category", "item_name", "unit_price_max_chf", "unit_price_min_chf", "unit_price_typical_chf")}
    assert list(file_content) == sorted(file_content)



# Check that an item the catalogue does not hold gives None
def test_unknown_item_gives_none(loaded_catalogue):
    assert loaded_catalogue.get_item("IT9999") is None









#### Step 3: Check the file and the loader ####

# Check that building twice gives the same bytes
def test_building_twice_gives_identical_bytes(built_folder, tmp_path):
    build_baselines.build_and_write_baselines(output_folder = tmp_path)
    first_bytes = (built_folder / build_baselines.ITEM_CATALOGUE_FILE_NAME).read_bytes()
    second_bytes = (tmp_path / build_baselines.ITEM_CATALOGUE_FILE_NAME).read_bytes()
    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")



# Check that a missing file raises the one clear error
def test_missing_file_raises(tmp_path):
    with pytest.raises(ItemCatalogueUnavailableError):
        load_item_catalogue(tmp_path)



# Check that broken JSON, valid JSON without items and valid JSON of the wrong shape raise the same error, and never give an empty catalogue
@pytest.mark.parametrize("malformed_text", ["{ this is not JSON", "{}", "[]", "{\"IT0001\": \"Fresh produce selection\"}", "{\"IT0001\": {\"item_name\": \"Fresh produce selection\"}}"])
def test_malformed_file_raises(built_folder, tmp_path, malformed_text):
    catalogue_path = copy_catalogue_file(built_folder, tmp_path / "copy")
    catalogue_path.write_text(malformed_text, encoding = "utf-8")
    with pytest.raises(ItemCatalogueUnavailableError):
        load_item_catalogue(tmp_path / "copy")



# Check that a price written as text, a price written as a boolean and prices out of order are refused
@pytest.mark.parametrize(
    "price_column, damaged_value",
    [("unit_price_min_chf", "12.00"), ("unit_price_typical_chf", True), ("unit_price_max_chf", 1.0)],
)
def test_damaged_price_raises(built_folder, tmp_path, price_column, damaged_value):
    catalogue_path = copy_catalogue_file(built_folder, tmp_path / "copy")
    file_content = json.loads(catalogue_path.read_text(encoding = "utf-8"))
    file_content["IT0001"][price_column] = damaged_value
    catalogue_path.write_text(json.dumps(file_content), encoding = "utf-8")
    with pytest.raises(ItemCatalogueUnavailableError):
        load_item_catalogue(tmp_path / "copy")



# Check that the loaded catalogue cannot be changed
def test_loaded_catalogue_is_frozen(loaded_catalogue):
    with pytest.raises(FrozenInstanceError):
        loaded_catalogue.get_item("IT0001").item_category = "cosmetics"
    with pytest.raises(FrozenInstanceError):
        loaded_catalogue.item_categories = ()
    with pytest.raises(TypeError):
        loaded_catalogue.items["IT9999"] = loaded_catalogue.get_item("IT0001")
    assert isinstance(loaded_catalogue.item_categories, tuple)



# Check that the default catalogue is loaded once per process and equals a fresh build
def test_default_catalogue_is_the_same_object_twice(loaded_catalogue):
    first_catalogue = get_default_item_catalogue()
    second_catalogue = get_default_item_catalogue()
    assert first_catalogue is second_catalogue
    assert dict(first_catalogue.items) == dict(loaded_catalogue.items)
    assert first_catalogue.item_categories == loaded_catalogue.item_categories



# Check that only the one loading function of the loader touches the disk
def test_only_the_loading_function_reads_files():
    loader_text = inspect.getsource(item_catalogue_module)
    loading_function_text = inspect.getsource(load_item_catalogue)
    assert loading_function_text in loader_text
    text_outside_the_loading_function = loader_text.replace(loading_function_text, "")
    assert "open(" not in text_outside_the_loading_function
    assert "read_text(" not in text_outside_the_loading_function
    assert loading_function_text.count("read_text(") == 1
    assert "open(" not in loading_function_text
