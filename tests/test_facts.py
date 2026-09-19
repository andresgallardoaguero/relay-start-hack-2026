# Script: test_facts.py
# Purpose: Check that amounts become exact decimals with half-even rounding, that the cart lines and the shop are read exactly and that the fact sheet of a purchase cannot be changed
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from decimal import Decimal

import pytest

from app.engine.facts import FactSheet, LineFact, MerchantFact, build_fact_sheet, read_true_or_false, to_money
from app.engine.shop_text import LineTextFacts
from app.models.events import read_purchase_message









#### Step 1: Check the conversion of one number ####

# Check that a number becomes an exact decimal with two places, without the binary noise of a float
@pytest.mark.parametrize(
    "value, expected_money",
    [
        (44.5, Decimal("44.50")),
        (8925.73, Decimal("8925.73")),
        (0.1 + 0.2, Decimal("0.30")),
        (20, Decimal("20.00")),
        ("2.665", Decimal("2.66")),
        ("2.675", Decimal("2.68")),
    ],
)
def test_number_becomes_exact_money(value, expected_money):
    money = to_money(value)
    assert money == expected_money
    assert isinstance(money, Decimal)
    assert money.as_tuple().exponent == -2









#### Step 2: Check the fact sheet ####

# Check the amounts of the example message field by field
def test_fact_sheet_of_the_example_message(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    assert isinstance(fact_sheet, FactSheet)
    assert fact_sheet.billing_amount_chf == Decimal("20.00")
    assert fact_sheet.amount == Decimal("20.00")
    assert fact_sheet.items_subtotal == Decimal("18.00")
    assert fact_sheet.delivery_fee == Decimal("2.00")
    assert fact_sheet.currency == "CHF"



# Check that a fact sheet cannot be changed after it was built
def test_fact_sheet_is_frozen(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact_sheet.billing_amount_chf = Decimal("1.00")









#### Step 3: Check the cart lines ####

# Check the one cart line of the example message field by field, and that it carries no identifier
def test_line_of_the_example_message(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    assert fact_sheet.lines == (
        LineFact(
            line_no = 1,
            item_name = "Example grocery item",
            item_category = "groceries",
            quantity = 1,
            unit_price = Decimal("18.00"),
            currency = "CHF",
            line_total = Decimal("18.00"),
            text_facts = LineTextFacts(size = None, return_days = None, final_sale = False, return_policy_not_stated = False),
            name_words = ("example", "grocery", "item"),
        ),
    )
    assert [line_field.name for line_field in dataclasses.fields(LineFact)] == [
        "line_no", "item_name", "item_category", "quantity", "unit_price", "currency", "line_total", "text_facts", "name_words",
    ]



# Check that the total of a line is the exact unit price times the quantity, in the currency of the line
def test_line_with_quantity_three(example_message):
    example_message["authorization"]["items"][0]["quantity"] = 3
    example_message["authorization"]["items"][0]["unit_price"] = 19.99
    example_message["authorization"]["items"][0]["currency"] = "EUR"
    line = build_fact_sheet(read_purchase_message(example_message)).lines[0]
    assert line.quantity == 3
    assert line.unit_price == Decimal("19.99")
    assert line.line_total == Decimal("59.97")
    assert line.line_total.as_tuple().exponent == -2
    assert line.currency == "EUR"



# Check that a cart line and the tuple of lines cannot be changed after they were built
def test_lines_are_frozen(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    assert isinstance(fact_sheet.lines, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact_sheet.lines[0].item_category = "electronics"









#### Step 4: Check the shop ####

# Check the shop of the example message field by field, and that it carries no identifier
def test_merchant_of_the_example_message(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    assert fact_sheet.merchant == MerchantFact(
        merchant_name = "Example Market",
        merchant_category = "groceries",
        merchant_country = "CH",
        recurring_capable = False,
    )
    assert [merchant_field.name for merchant_field in dataclasses.fields(MerchantFact)] == ["merchant_name", "merchant_category", "merchant_country", "recurring_capable"]



# Check that the two texts of the message become real booleans, so the text "false" is never read as true
@pytest.mark.parametrize("recurring_capable_text, expected_boolean", [("true", True), ("false", False)])
def test_recurring_capable_becomes_a_real_boolean(example_message, recurring_capable_text, expected_boolean):
    example_message["authorization"]["merchant"]["recurring_capable"] = recurring_capable_text
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    assert fact_sheet.merchant.recurring_capable is expected_boolean



# Check that nothing but the exact texts "true" and "false" is read, neither another spelling nor a value that is no text
@pytest.mark.parametrize("unreadable_value", ["True", "FALSE", "yes", "1", "", " true", True, False, 1, 0, None])
def test_anything_but_true_and_false_is_refused(unreadable_value):
    with pytest.raises(ValueError, match = "true or false"):
        read_true_or_false(unreadable_value)



# Check that the shop cannot be changed after it was built
def test_merchant_is_frozen(example_message):
    fact_sheet = build_fact_sheet(read_purchase_message(example_message))
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact_sheet.merchant.recurring_capable = True
