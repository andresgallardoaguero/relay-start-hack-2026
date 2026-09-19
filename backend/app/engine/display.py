# Script: display.py
# Purpose: Write amounts, categories, cart lines and shop text for the customer, where everything a shop wrote is cleaned and quoted
# Author: Andrés Gallardo
# Date: September 2026

import unicodedata









#### Step 1: Write amounts, categories and lists ####

# Write an amount in Swiss francs with two decimals
def describe_francs(amount_chf):
    return f"CHF {amount_chf:.2f}"



# Write a category name with spaces for underscores
def describe_category(category):
    return category.replace("_", " ")



# Write a list in plain words, as in "groceries", "groceries and household" and "books, clothing and hotel"
def join_in_words(texts):
    if len(texts) <= 1:
        return "".join(texts)
    return ", ".join(texts[:-1]) + " and " + texts[-1]



# Write a list of category names in plain words
def describe_categories(categories):
    return join_in_words([describe_category(category) for category in categories])









#### Step 2: Make shop text safe to show ####

# State how many characters of a text written by a shop the customer gets to read
SHOWN_SHOP_TEXT_LENGTH = 60



# Make a text written by a shop safe to show, such as an item name or the name of the shop.
# The shop wrote it, so it is quoted material and never part of the message itself.
# Control characters and line breaks become spaces, a double quote becomes a single one so the quotation cannot be closed early,
# runs of whitespace shrink to one space, and the text is cut to a fixed length.
def clean_shop_text(text, shown_length = SHOWN_SHOP_TEXT_LENGTH):
    text_without_control_characters = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    text_on_one_line = " ".join(text_without_control_characters.replace("\"", "'").split())
    return text_on_one_line[:shown_length].strip()



# Write a text written by a shop inside double quotes
def quote_shop_text(text):
    return "\"" + clean_shop_text(text) + "\""









#### Step 3: Write the cart lines ####

# Write one cart line with its quoted name, its category and its amount in its own currency
def describe_line(line):
    return quote_shop_text(line.item_name) + " (" + describe_category(line.item_category) + f", {line.currency} {line.line_total:.2f})"



# Write several cart lines, separated by commas
def describe_lines(lines):
    return ", ".join(describe_line(line) for line in lines)
