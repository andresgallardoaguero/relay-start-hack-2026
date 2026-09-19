# Script: g_lookalike_merchant.py
# Purpose: Refuse a shop whose name imitates a shop the customer uses, while it is a different seller that no customer has ever bought from
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import quote_shop_text
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name where the evidence comes from, which is the card history of the issuer and the setting of the name similarity
HISTORY_SOURCE = "card history of the issuer"
SIMILARITY_SOURCE = "authorization.merchant.merchant_name against the names of the shops the customer has an approved purchase at, as a character similarity from 0 to 1"
ISSUER_CARD_COUNT_SOURCE = "cards of the issuer with an approved purchase at the shop"



# State how many decimals of a score the evidence keeps
SCORE_DECIMALS = 3









#### Step 2: Build the evidence and the sentence for the customer ####

# Describe that no history was available, so no name could be compared
def build_missing_history_evidence():
    return EvidenceItem(fact = "card_history_is_available", value = False, comparator = None, threshold = None, source = HISTORY_SOURCE)



# Describe the highest similarity that was seen against the threshold, where no score means that the customer has no other shop to compare with
def build_similarity_evidence(familiarity):
    resemblance_score = familiarity.resemblance_score
    return EvidenceItem(
        fact = "highest_name_similarity",
        value = None if resemblance_score is None else round(resemblance_score, SCORE_DECIMALS),
        comparator = "<",
        threshold = familiarity.similarity_threshold,
        source = SIMILARITY_SOURCE,
    )



# Write the refusal, where the name the shop wrote is quoted and the name of the shop the customer uses comes from the directory of the issuer
def build_refusal(shop_name, resembled_shop_name):
    return (
        "Declined. " + quote_shop_text(shop_name) + " looks like " + resembled_shop_name
        + ", a shop you use, but it is a different seller, and no customer has ever bought from it."
    )









#### Step 3: Define the guard ####

# Check for a shop that imitates a shop the customer uses. The guard applies under every instruction, because no customer wants to pay an imitation.
# It reads the familiarity facts only, which state whether the name is very similar to a shop of the customer, while the seller is another one,
# sells the same kind of goods and has no approved purchase on any card of the issuer. It compares no name itself and sees no identifier.
# Shared words are not the test, so honest shops with names such as "Circuit and Pine" and "Parcel and Pine" pass.
@dataclass(frozen = True)
class LookalikeMerchantGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        familiarity = decision_input.familiarity



        # Pass without a history, because a shop can only imitate a shop the customer is known to use.
        # The evidence says that nothing was compared, so the pass never reads as a cleared name.
        if familiarity is None or not familiarity.card_is_known:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_history_evidence(),))



        # Pass when the shop imitates none, with the highest similarity that was seen as evidence
        if familiarity.resembled_shop_name is None:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_similarity_evidence(familiarity),))



        # Decline the imitation, with the score, the threshold and the missing buyers as evidence
        return build_guard_result(
            self,
            GuardVerdict.DECLINE,
            reason_code = ReasonCode.LOOKALIKE_MERCHANT,
            evidence = (
                build_similarity_evidence(familiarity),
                EvidenceItem(fact = "resembled_shop_name", value = familiarity.resembled_shop_name, comparator = None, threshold = None, source = HISTORY_SOURCE),
                EvidenceItem(fact = "issuer_card_count", value = familiarity.issuer_card_count, comparator = ">", threshold = 0, source = ISSUER_CARD_COUNT_SOURCE),
            ),
            customer_message = build_refusal(decision_input.facts.merchant.merchant_name, familiarity.resembled_shop_name),
        )
