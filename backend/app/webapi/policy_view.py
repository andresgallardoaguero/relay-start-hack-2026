# Script: policy_view.py
# Purpose: Show a compiled policy to the customer as the rules stored with the platform and the checks the engine adds, before anything is confirmed
# Author: Jonas Lüthi
# Date: September 2026

import json

from app.models.events import MandateRule
from app.models.policy import InternalPolicy









#### Step 1: Build the rules stored with the platform ####

# Name the fields of a rule the way the rule format of the platform names the facts of a purchase message
ORDER_AMOUNT_RULE_FIELD = "authorization.billing_amount_chf"
ITEM_CATEGORY_RULE_FIELD = "authorization.items.item_category"
MERCHANT_CATEGORY_RULE_FIELD = "authorization.merchant.merchant_category"



# Write the limit per order as a rule, or None when the policy holds no limit that was read
def build_order_limit_rule(expectations):
    if expectations.per_order_limit_reading != "read" or expectations.per_order_limit_chf is None:
        return None
    return {
        "field": ORDER_AMOUNT_RULE_FIELD,
        "operator": "<=" if expectations.per_order_limit_inclusive else "<",
        "value": float(expectations.per_order_limit_chf),
        "currency": "CHF",
        "scope": "purchase",
    }



# Write the budget over a period as a rule, or None when the policy holds no budget that was read.
# A budget without a number of days runs over the whole instruction, so its rule carries the scope and no days.
def build_period_budget_rule(expectations):
    if expectations.period_limit_reading != "read" or expectations.period_limit_chf is None:
        return None
    budget_rule = {
        "field": ORDER_AMOUNT_RULE_FIELD,
        "operator": "<=" if expectations.period_limit_inclusive else "<",
        "value": float(expectations.period_limit_chf),
        "currency": "CHF",
        "scope": "period",
    }
    if expectations.period_days is None:
        return budget_rule
    return {**budget_rule, "period_days": int(expectations.period_days)}



# Write a list of categories as a rule with the given field and operator, or None for an empty list.
# The list is sorted, so the same policy always gives the same rule.
def build_category_rule(rule_field, operator, categories):
    if len(categories) == 0:
        return None
    return {
        "field": rule_field,
        "operator": operator,
        "value": sorted(str(category) for category in categories),
    }



# Write the kind of shop as a rule only when the instruction makes it a condition.
# A stored rule is always read back as a condition, so a rule for a kind of shop the instruction merely names
# would turn a question to the customer into a refusal.
def build_merchant_category_rule(expectations):
    if not expectations.merchant_category_is_strict:
        return None
    return build_category_rule(MERCHANT_CATEGORY_RULE_FIELD, "in", expectations.required_merchant_categories)



# Write every part of the policy that can be read back from a rule in the platform's rule format.
# These are the limit per order, the budget over a period, the allowed goods, the ruled-out goods and a kind of shop that is a condition.
# The rules the mandate already carries stay first and unchanged, and a rule that is already there is not written again.
# Every written rule repeats a value of the policy, so it never loosens a restriction and never changes what the instruction alone gives.
def build_hard_rules_from_policy(policy):
    expectations = policy.expectations
    carried_rules = [rule.model_dump(mode = "json", exclude_none = True) for rule in policy.hard_rules]
    possible_rules = (
        build_order_limit_rule(expectations),
        build_period_budget_rule(expectations),
        build_category_rule(ITEM_CATEGORY_RULE_FIELD, "in", expectations.allowed_item_categories),
        build_category_rule(ITEM_CATEGORY_RULE_FIELD, "not_in", expectations.prohibited_item_categories),
        build_merchant_category_rule(expectations),
    )
    new_rules = [
        possible_rule
        for possible_rule in possible_rules
        if possible_rule is not None and possible_rule not in carried_rules
    ]
    return carried_rules + new_rules









#### Step 2: Describe the single checks ####

# Describe one check in plain words
def describe_check(label, detail, stored_with_platform):
    return {"label": label, "detail": detail, "stored_with_platform": stored_with_platform}



# Write a list of categories for the customer, sorted and with spaces for underscores
def describe_categories(categories):
    return ", ".join(sorted(str(category).replace("_", " ") for category in categories))



# Say what happens to an amount over a limit, which asks the customer inside the tolerance and declines beyond it
def describe_overshoot(overshoot_tolerance_share):
    tolerance_percent = int(overshoot_tolerance_share * 100)
    if tolerance_percent > 0:
        return " Up to " + str(tolerance_percent) + " percent over asks you, more declines."
    return " Any amount over declines."



# Describe the limit per order, which always appears, because a missing limit is worth knowing too
def describe_order_limit_check(expectations):
    if expectations.per_order_limit_reading == "read" and expectations.per_order_limit_chf is not None:
        comparison = "at most" if expectations.per_order_limit_inclusive else "under"
        detail = "Each order " + comparison + " CHF " + f"{expectations.per_order_limit_chf:.2f}" + ", delivery included."
        return describe_check("Limit per order", detail + describe_overshoot(expectations.overshoot_tolerance_share), True)
    if expectations.per_order_limit_reading == "not_stated":
        return describe_check("Limit per order", "Your instruction names no limit per order.", False)
    return describe_check("Limit per order", "Your instruction names a limit that could not be read. Every purchase asks you until it is settled.", False)



# Describe the budget over a period, with its number of days or over the whole instruction
def describe_period_budget_check(expectations):
    if expectations.period_limit_reading == "not_stated":
        return None
    if expectations.period_limit_reading != "read" or expectations.period_limit_chf is None:
        return describe_check("Budget over a period", "Your instruction names a budget that could not be read. Every purchase counts as uncertain until it is settled.", False)
    comparison = "at most" if expectations.period_limit_inclusive else "under"
    if expectations.period_days is None:
        period_words = "under this instruction"
    elif expectations.period_days == 1:
        period_words = "over any 1 day"
    else:
        period_words = "over any " + str(expectations.period_days) + " days"
    detail = "All orders together " + comparison + " CHF " + f"{expectations.period_limit_chf:.2f}" + " " + period_words + "."
    return describe_check("Budget over a period", detail + describe_overshoot(expectations.overshoot_tolerance_share), True)



# Describe the goods the instruction covers
def describe_allowed_goods_check(expectations):
    if len(expectations.allowed_item_categories) == 0:
        return None
    return describe_check("Goods covered", "Only these kinds of goods - " + describe_categories(expectations.allowed_item_categories) + ".", True)



# Describe the goods the instruction rules out
def describe_prohibited_goods_check(expectations):
    if len(expectations.prohibited_item_categories) == 0:
        return None
    return describe_check("Goods ruled out", "Never these kinds of goods - " + describe_categories(expectations.prohibited_item_categories) + ".", True)



# Describe the ban on extras
def describe_extras_check(expectations):
    if not expectations.no_addons:
        return None
    return describe_check("Extras", "Nothing you did not ask for may be added to an order.", False)



# Describe the kind of shop, which is stored with the platform only when it is a condition
def describe_merchant_category_check(expectations):
    if len(expectations.required_merchant_categories) == 0:
        return None
    shop_kinds = describe_categories(expectations.required_merchant_categories)
    if expectations.merchant_category_is_strict:
        return describe_check("Kind of shop", "Only shops for " + shop_kinds + ". Any other kind of shop declines.", True)
    return describe_check("Kind of shop", "Preferably shops for " + shop_kinds + ". Any other kind of shop asks you.", False)



# Describe the one thing the instruction asks for, with the words that name it and the attributes it must have, such as its size
def describe_requested_item_check(expectations):
    requested_item = expectations.requested_item
    if requested_item is None:
        return None
    name_words = ""
    if len(requested_item.kind_keywords) > 0:
        name_words = ", named by the words " + ", ".join(requested_item.kind_keywords)
    attribute_words = "".join(
        ", in " + attribute_name + " " + attribute_value
        for attribute_name, attribute_value in sorted(requested_item.attributes.items())
    )
    return describe_check("Requested thing", "One single thing" + name_words + attribute_words + ".", False)



# Describe the return period the order must offer
def describe_return_period_check(expectations):
    if expectations.min_return_days is None:
        return None
    day_word = "day" if expectations.min_return_days == 1 else "days"
    return describe_check("Returns", "The order can be returned for at least " + str(expectations.min_return_days) + " " + day_word + ".", False)



# Describe how familiar the shop has to be, by the bar and by whether it is a condition or a wish
def describe_familiarity_check(expectations):
    if expectations.merchant_familiarity == "any" or expectations.familiarity_bar is None:
        return None
    if expectations.familiarity_bar == "regularly":
        bar_words = "A shop you have used regularly, which means at least " + str(expectations.familiarity_regular_min_purchases) + " approved purchases there."
    else:
        bar_words = "A shop you have used before, which means at least one approved purchase there."
    if expectations.merchant_familiarity == "required":
        strength_words = " This is a condition of your instruction."
    else:
        strength_words = " This is a wish of your instruction and not a condition."
    return describe_check("Familiarity of the shop", bar_words + strength_words, False)



# Describe the closer watch over the session
def describe_session_check(expectations):
    if expectations.session_sensitivity != "high":
        return None
    return describe_check("Watch over the session", "One sign that someone other than you is driving the session is enough to ask you.", False)



# Describe the repeated order. A policy that does not carry the two fields yet gives no check.
def describe_repeated_order_check(expectations):
    duplicate_window_hours = getattr(expectations, "duplicate_window_hours", None)
    duplicate_amount_share = getattr(expectations, "duplicate_amount_share", None)
    if duplicate_window_hours is None or duplicate_amount_share is None:
        return None
    detail = (
        "The same cart at the same shop within " + str(duplicate_window_hours) + " hours of an approved order, at an amount within "
        + str(int(duplicate_amount_share * 100)) + " percent of it, counts as a repeated order."
    )
    return describe_check("Repeated order", detail, False)



# Describe what happens once the one requested thing was bought. A policy that does not carry the field yet gives no check,
# and so does an instruction that asks for no single thing.
def describe_already_bought_check(expectations):
    goal_fulfilled_action = getattr(expectations, "goal_fulfilled_action", None)
    if expectations.requested_item is None or goal_fulfilled_action not in ("note", "step_up"):
        return None
    if goal_fulfilled_action == "note":
        return describe_check("Already bought", "Once the requested thing was bought, a further purchase of it carries a note that says so.", False)
    return describe_check("Already bought", "Once the requested thing was bought, a further purchase of it asks you.", False)



# Describe how uncertainty is handled, which the platform stores next to the rules
def describe_uncertainty_check(uncertainty_policy):
    uncertainty_detail = {
        "ask": "When a check cannot be settled from the facts, the purchase asks you.",
        "decline": "When a check cannot be settled from the facts, the purchase is declined.",
        "approve": "When a check cannot be settled from the facts, the purchase goes ahead. A timeout still never approves.",
    }[uncertainty_policy]
    return describe_check("When uncertain", uncertainty_detail, True)









#### Step 3: Build the check list and the policy answer ####

# Turn the whole policy into the two groups of checks the confirm screen shows, in a fixed order.
# A check is stored with the platform exactly when its value is written as a rule, and so is the uncertainty policy.
# A check the instruction does not state is left out, except the limit per order, and the two checks the engine always runs come last.
def build_check_list(policy):
    expectations = policy.expectations
    possible_checks = (
        describe_order_limit_check(expectations),
        describe_period_budget_check(expectations),
        describe_allowed_goods_check(expectations),
        describe_prohibited_goods_check(expectations),
        describe_extras_check(expectations),
        describe_merchant_category_check(expectations),
        describe_requested_item_check(expectations),
        describe_return_period_check(expectations),
        describe_familiarity_check(expectations),
        describe_session_check(expectations),
        describe_repeated_order_check(expectations),
        describe_already_bought_check(expectations),
        describe_uncertainty_check(policy.uncertainty_policy),
        describe_check("Shop text is data", "Text from the shop can never loosen a decision. An attempt to instruct the agent asks you and is quoted.", False),
        describe_check("One purchase, one decision", "A purchase delivered twice is decided once and counted once. A question counts only after you approve it.", False),
    )
    return [possible_check for possible_check in possible_checks if possible_check is not None]



# Describe the compiled policy for the interface, with the rules, the checks and the open questions
def describe_policy(policy):
    return {
        "instruction": policy.instruction,
        "uncertainty_policy": policy.uncertainty_policy,
        "hard_rules": build_hard_rules_from_policy(policy),
        "checks": build_check_list(policy),
        "open_questions": list(policy.open_questions),
        "expectations": policy.expectations.model_dump(mode = "json"),
    }









#### Step 4: Read a mandate from the interface ####

# Read one rule of the interface the way a purchase message delivers it, which is as JSON.
# The rule model is strict and takes a list of texts only from JSON, so a rule on categories is read the same way on both paths.
def read_mandate_rule(rule):
    return MandateRule.model_validate_json(json.dumps(rule))



# Hold what the compiler needs of a mandate, which is the instruction, the rules and the uncertainty policy
class MandateContent:

    def __init__(self, instruction, hard_rules, uncertainty_policy):
        self.instruction = instruction
        self.hard_rules = tuple(read_mandate_rule(rule) for rule in hard_rules)
        self.uncertainty_policy = uncertainty_policy
