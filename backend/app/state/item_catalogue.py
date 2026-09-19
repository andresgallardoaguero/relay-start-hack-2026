# Script: item_catalogue.py
# Purpose: Load the catalogue of items with their names, their categories and their price ranges, as one object that cannot be changed
# Author: Andrés Gallardo
# Date: September 2026

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Tuple









#### Step 1: Define the path and the error ####

# Locate the folder of the file from the location of this file, so the working directory never matters
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_ITEM_CATALOGUE_FOLDER = REPOSITORY_FOLDER / "data" / "processed"
ITEM_CATALOGUE_FILE_NAME = "item_catalogue.json"



# Say that the catalogue cannot be used, because the file is missing, unreadable or incomplete
class ItemCatalogueUnavailableError(Exception):
    pass









#### Step 2: Define the frozen objects ####

# Hold one item of the catalogue, where the three prices are the lowest, the typical and the highest price of one unit in Swiss francs
@dataclass(frozen = True)
class CatalogueItem:
    item_id: str
    item_name: str
    item_category: str
    unit_price_min_chf: float
    unit_price_typical_chf: float
    unit_price_max_chf: float



# Hold every item by its identifier, and the closed list of item categories in alphabetical order
@dataclass(frozen = True)
class ItemCatalogue:
    items: Mapping[str, CatalogueItem]
    item_categories: Tuple[str, ...]

    # Return the item, or None for an item the catalogue does not hold
    def get_item(self, item_id):
        return self.items.get(item_id)









#### Step 3: Turn the content of the file into the frozen objects ####

# Read a text that must be present, so a missing name or category never becomes an empty one
def read_required_text(value, field_name):
    if not isinstance(value, str) or value == "":
        raise ValueError("The field " + field_name + " must be a text with at least one character, found " + repr(value))
    return value



# Read a price that must be a real number, so neither the text "12.00" nor a boolean passes as a price
def read_price(value, field_name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("The price " + field_name + " must be a number, found " + repr(value))
    return float(value)



# Build the frozen object of one item, and refuse prices that are not in rising order
def build_catalogue_item(item_id, item_entry):
    catalogue_item = CatalogueItem(
        item_id = item_id,
        item_name = read_required_text(item_entry["item_name"], "item_name"),
        item_category = read_required_text(item_entry["item_category"], "item_category"),
        unit_price_min_chf = read_price(item_entry["unit_price_min_chf"], "unit_price_min_chf"),
        unit_price_typical_chf = read_price(item_entry["unit_price_typical_chf"], "unit_price_typical_chf"),
        unit_price_max_chf = read_price(item_entry["unit_price_max_chf"], "unit_price_max_chf"),
    )
    if not catalogue_item.unit_price_min_chf <= catalogue_item.unit_price_typical_chf <= catalogue_item.unit_price_max_chf:
        raise ValueError("The prices of item " + item_id + " are not in rising order")
    return catalogue_item



# Build the one object from the content of the file, and refuse content that holds no items
def build_item_catalogue(catalogue_content):
    items = {
        item_id: build_catalogue_item(item_id, item_entry)
        for item_id, item_entry in catalogue_content.items()
    }
    if not items:
        raise ValueError("The file holds no items")
    return ItemCatalogue(
        items = MappingProxyType(items),
        item_categories = tuple(sorted({catalogue_item.item_category for catalogue_item in items.values()})),
    )









#### Step 4: Load the file ####

# Read the file and return one frozen object. This is the only place that touches the disk, and it reads this one file and nothing else.
# A missing, unreadable or incomplete file raises ItemCatalogueUnavailableError, and an empty catalogue is never returned.
def load_item_catalogue(item_catalogue_folder = None):

    # Use the default folder when none is given
    if item_catalogue_folder is None:
        item_catalogue_folder = DEFAULT_ITEM_CATALOGUE_FOLDER
    item_catalogue_path = Path(item_catalogue_folder) / ITEM_CATALOGUE_FILE_NAME



    # Read the file as JSON
    try:
        catalogue_content = json.loads(item_catalogue_path.read_text(encoding = "utf-8"))
    except (OSError, ValueError) as read_error:
        raise ItemCatalogueUnavailableError("The item catalogue in " + str(item_catalogue_folder) + " cannot be read - " + str(read_error)) from read_error



    # Turn the content into the frozen objects
    try:
        return build_item_catalogue(catalogue_content)
    except (KeyError, TypeError, ValueError, AttributeError) as content_error:
        raise ItemCatalogueUnavailableError("The item catalogue in " + str(item_catalogue_folder) + " is incomplete - " + repr(content_error)) from content_error



# Load the catalogue of the default folder on the first call and hand back the same object for the rest of the process
@lru_cache(maxsize = None)
def get_default_item_catalogue():
    return load_item_catalogue()
