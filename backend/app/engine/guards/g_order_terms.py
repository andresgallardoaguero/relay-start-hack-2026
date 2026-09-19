# Script: g_order_terms.py
# Purpose: Compare the return terms of an order with the number of return days the customer asked for, where the number of days comes from the facts of the shop text
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import quote_shop_text
from app.engine.guards.base import build_guard_result
from app.engine.guards.g_item_match import find_requested_lines
from app.engine.shop_text import find_model_line, pick_stricter_result, settle_line_facts
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the parts of the message and the field of the policy that the evidence points to
CART_LINES_SOURCE = "authorization.items"
RETURNABLE_FLAG_SOURCE = "authorization.order_returnable"
MIN_RETURN_DAYS_FIELD_SOURCE = "policy.expectations.min_return_days"









#### Step 2: Hold the facts of one judged line ####

# Hold one cart line together with its settled facts, which are the facts of the shop text and the optional facts of a language model
@dataclass(frozen = True)
class JudgedLine:
    line: object
    settled_facts: object



# Report whether the return days of a judged line can be read and reach the minimum, where exactly the minimum reaches it.
# A line whose shop leaves the return policy open never reaches it, whatever else the text says.
def line_reaches_the_minimum(judged_line, min_return_days):
    settled_facts = judged_line.settled_facts
    return (
        settled_facts.return_days is not None
        and settled_facts.return_days >= min_return_days
        and not settled_facts.return_policy_not_stated
        and not settled_facts.final_sale
    )



# Report whether the return days of a judged line can be read and lie below the minimum
def line_is_below_the_minimum(judged_line, min_return_days):
    return judged_line.settled_facts.return_days is not None and judged_line.settled_facts.return_days < min_return_days









#### Step 3: Build the evidence ####

# Describe the return flag of the order against the value that allows a return
def build_flag_evidence(returnable_flag):
    return EvidenceItem(
        fact = "order_returnable",
        value = returnable_flag,
        comparator = "=",
        threshold = "true",
        source = RETURNABLE_FLAG_SOURCE,
    )



# Describe the minimum alone, for an order that is judged without looking at its lines
def build_minimum_evidence(min_return_days):
    return EvidenceItem(
        fact = "min_return_days",
        value = min_return_days,
        comparator = None,
        threshold = None,
        source = MIN_RETURN_DAYS_FIELD_SOURCE,
    )



# Describe the return days of one judged line against the minimum, where the value is None when no number of days can be read
def build_return_days_evidence(judged_line, min_return_days):
    return EvidenceItem(
        fact = "return_days",
        value = judged_line.settled_facts.return_days,
        comparator = ">=",
        threshold = min_return_days,
        source = CART_LINES_SOURCE + " line " + str(judged_line.line.line_no) + " facts of the shop text against " + MIN_RETURN_DAYS_FIELD_SOURCE,
    )



# Describe a judged line that is sold without returns
def build_final_sale_evidence(judged_line):
    return EvidenceItem(
        fact = "final_sale",
        value = True,
        comparator = "=",
        threshold = False,
        source = CART_LINES_SOURCE + " line " + str(judged_line.line.line_no) + " facts of the shop text",
    )









#### Step 4: Write the messages ####

# Write the number of days the customer asked for
def describe_minimum(min_return_days):
    return "at least " + str(min_return_days) + " days"



# Tell the customer why the order was declined, with the strongest reason only.
# An order that cannot be returned at all comes first, then a final sale, then return days below the minimum.
def build_decline_message(flag_says_no, final_sale_lines, short_lines, min_return_days):
    if flag_says_no:
        return "Declined. The shop states that this order cannot be returned, and you asked for " + describe_minimum(min_return_days) + " to return it."
    if final_sale_lines:
        quoted_names = ", ".join(quote_shop_text(judged_line.line.item_name) for judged_line in final_sale_lines)
        return "Declined. " + quoted_names + " is sold as a final sale without returns, and you asked for " + describe_minimum(min_return_days) + " to return it."
    shortest_return_days = min(judged_line.settled_facts.return_days for judged_line in short_lines)
    return "Declined. Returns are accepted within " + str(shortest_return_days) + " days, and you asked for at least " + str(min_return_days) + "."



# Ask the customer about return terms that cannot be settled.
# With readable days on every line only the confirmation of the order is missing, and otherwise the shop does not state its return policy.
def build_question(every_line_reaches_the_minimum, min_return_days):
    if every_line_reaches_the_minimum:
        return "The shop does not confirm that this order can be returned, and you asked for " + describe_minimum(min_return_days) + ". Approve anyway?"
    return "The shop does not state its return policy, and you asked for " + describe_minimum(min_return_days) + ". Approve anyway?"









#### Step 5: Define the guard ####

# Check the return terms of the order. The guard reads the return flag of the order, the text facts of the cart lines,
# the optional facts of a language model, and from the policy the minimum of return days and the requested item.
# The return flag alone never passes, because it is true for a return period of any length, so the number of days must be readable as well.
# A term the instruction does not mention is never checked.
# The order is judged twice, on the shop text alone and with the facts of the model, and the stricter result is the answer.
@dataclass(frozen = True)
class OrderTermsGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        return pick_stricter_result(
            self.judge_order(decision_input, None),
            self.judge_order(decision_input, decision_input.extracted_facts),
        )

    # Judge the order with the facts of the given model, or on the shop text alone when no model is given
    def judge_order(self, decision_input, model_extraction):
        expectations = decision_input.policy.expectations
        min_return_days = expectations.min_return_days
        returnable_flag = decision_input.event.authorization.order_returnable



        # Skip when the instruction asks for no return period
        if min_return_days is None:
            terms_evidence = EvidenceItem(
                fact = "min_return_days",
                value = "not_stated",
                comparator = None,
                threshold = None,
                source = MIN_RETURN_DAYS_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (terms_evidence,))



        # Skip an order for which a return means nothing, such as a digital good
        flag_evidence = build_flag_evidence(returnable_flag)
        if returnable_flag == "not_applicable":
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (flag_evidence, build_minimum_evidence(min_return_days)))



        # Judge the lines that are the requested thing, or every line when nothing was requested, each with its settled facts.
        # The fact of the shop text comes first, and the fact of the model counts only where the text says nothing.
        judged_lines = tuple(
            JudgedLine(
                line = line,
                settled_facts = settle_line_facts(line.text_facts, find_model_line(model_extraction, line.line_no)),
            )
            for line in find_requested_lines(decision_input.facts.lines, expectations.requested_item)
        )
        final_sale_lines = tuple(judged_line for judged_line in judged_lines if judged_line.settled_facts.final_sale)
        short_lines = tuple(judged_line for judged_line in judged_lines if line_is_below_the_minimum(judged_line, min_return_days))
        evidence = (
            (flag_evidence,)
            + tuple(build_return_days_evidence(judged_line, min_return_days) for judged_line in judged_lines)
            + tuple(build_final_sale_evidence(judged_line) for judged_line in final_sale_lines)
        )



        # Decline an order that cannot be returned, a final sale and return days below the minimum
        flag_says_no = returnable_flag == "false"
        if flag_says_no or final_sale_lines or short_lines:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.RETURN_TERMS_UNMET,
                evidence = evidence,
                customer_message = build_decline_message(flag_says_no, final_sale_lines, short_lines, min_return_days),
            )



        # Skip a cart without any line that is the requested thing. It is a wrong purchase, which the item match declines, and it has no line to judge here.
        if len(judged_lines) == 0:
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (flag_evidence, build_minimum_evidence(min_return_days)))



        # Pass only when the order is confirmed as returnable and every judged line states return days at or above the minimum
        every_line_reaches_the_minimum = all(line_reaches_the_minimum(judged_line, min_return_days) for judged_line in judged_lines)
        if returnable_flag == "true" and every_line_reaches_the_minimum:
            return build_guard_result(self, GuardVerdict.PASS, evidence = evidence)



        # Leave everything else to the customer's uncertainty policy, which is an unknown flag, a return policy the shop leaves open and days that cannot be read
        return build_guard_result(
            self,
            GuardVerdict.UNCERTAIN,
            reason_code = ReasonCode.RETURN_TERMS_UNKNOWN,
            evidence = evidence,
            customer_message = build_question(every_line_reaches_the_minimum, min_return_days),
        )
