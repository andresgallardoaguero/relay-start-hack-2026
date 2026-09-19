# Script: g_merchant_type.py
# Purpose: Compare the kind of shop the platform states with the kind of shop the customer's instruction asks for
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import describe_category, quote_shop_text
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the field of the message and the field of the policy that the evidence points to
MERCHANT_CATEGORY_SOURCE = "authorization.merchant.merchant_category"
REQUIRED_FIELD_SOURCE = "policy.expectations.required_merchant_categories"









#### Step 2: Write the kinds of shop for the customer ####

# Write the required kinds of shop as a choice, as in "sporting goods", "books or clothing" and "books, clothing or electronics"
def describe_required_kinds(required_merchant_categories):
    described_kinds = [describe_category(merchant_category) for merchant_category in required_merchant_categories]
    if len(described_kinds) <= 1:
        return "".join(described_kinds)
    return ", ".join(described_kinds[:-1]) + " or " + described_kinds[-1]



# Say which kind of shop this is, with the name of the shop as quoted material, because the shop wrote it
def describe_shop(merchant):
    return quote_shop_text(merchant.merchant_name) + " is a shop for " + describe_category(merchant.merchant_category)









#### Step 3: Define the guard ####

# Check the kind of shop. The guard reads the merchant category the platform states and the two fields of the policy.
# It decides on the category alone. The name of the shop is shown to the customer and never compared with anything.
@dataclass(frozen = True)
class MerchantTypeGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        merchant = decision_input.facts.merchant
        expectations = decision_input.policy.expectations
        required_merchant_categories = tuple(expectations.required_merchant_categories)



        # Skip when the instruction is open about the kind of shop, which is the customer's choice
        if len(required_merchant_categories) == 0:
            requirement_evidence = EvidenceItem(
                fact = "required_merchant_categories",
                value = "not_stated",
                comparator = None,
                threshold = None,
                source = REQUIRED_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (requirement_evidence,))



        # Describe the category of the shop against the required ones, for every verdict alike
        category_evidence = EvidenceItem(
            fact = "merchant_category",
            value = merchant.merchant_category,
            comparator = "in",
            threshold = ", ".join(required_merchant_categories),
            source = MERCHANT_CATEGORY_SOURCE + " against " + REQUIRED_FIELD_SOURCE,
        )



        # Pass a shop of a required kind
        if merchant.merchant_category in required_merchant_categories:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (category_evidence,))



        # Decline another kind of shop when the instruction insists on the kind, as in "only from"
        if expectations.merchant_category_is_strict:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.MERCHANT_TYPE_MISMATCH,
                evidence = (category_evidence,),
                customer_message = (
                    "Declined. " + describe_shop(merchant) + ", and your instruction allows only a shop for "
                    + describe_required_kinds(required_merchant_categories) + "."
                ),
            )



        # Ask the customer otherwise, because the instruction names the kind of shop without insisting on it
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.MERCHANT_TYPE_MISMATCH,
            evidence = (category_evidence,),
            customer_message = (
                describe_shop(merchant) + ", and your instruction asks for a shop for "
                + describe_required_kinds(required_merchant_categories) + ". Approve this seller?"
            ),
        )
