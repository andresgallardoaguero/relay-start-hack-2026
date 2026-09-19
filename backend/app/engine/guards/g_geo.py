# Script: g_geo.py
# Purpose: Notice a purchase at a shop in a country the card has never bought in, as a signal that decides nothing on its own
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict









#### Step 1: Name the sources of the evidence and the signal ####

# Name where the evidence comes from, which is the card history of the issuer
HISTORY_SOURCE = "card history of the issuer"
COUNTRY_COUNT_SOURCE = "approved purchases of this card at shops in the country of the shop, without scheduled recurring payments"



# Name the signal of a country the card has never bought in. A foreign country alone is no signal, because many customers have a favorite shop abroad.
# Only the history of the card itself says what is new, and a later guard combines the signal with the others.
NEW_COUNTRY_SIGNAL = GuardSignal(name = "new_country", strength = "normal")









#### Step 2: Build the evidence ####

# Describe that no history of the card was available, so nothing is known about its countries
def build_missing_history_evidence():
    return EvidenceItem(fact = "card_history_is_available", value = False, comparator = None, threshold = None, source = HISTORY_SOURCE)



# Describe the purchases of the card in the country of the shop, where at least one makes the country a known one
def build_country_count_evidence(shop_country, country_purchase_count):
    return EvidenceItem(
        fact = "country_purchase_count",
        value = country_purchase_count,
        comparator = ">=",
        threshold = 1,
        source = COUNTRY_COUNT_SOURCE + " " + shop_country,
    )









#### Step 3: Define the guard ####

# Check whether the card has bought in the country of the shop before. The guard reads the session facts and nothing else, and it sees no identifier.
# It always passes. A new country is a signal for a later guard and never a reason to ask or to decline on its own.
@dataclass(frozen = True)
class GeoGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        session = decision_input.session



        # Pass without a signal when the history is missing or the card is unknown, because an unknown history is not a new country
        if session is None or not session.card_is_known or session.country_purchase_count is None:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_history_evidence(),))



        # Pass with the signal for a country the card never bought in, and without one for a country it bought in at least once
        signal = NEW_COUNTRY_SIGNAL if session.country_purchase_count == 0 else None
        return build_guard_result(
            self,
            GuardVerdict.PASS,
            evidence = (build_country_count_evidence(session.shop_country, session.country_purchase_count),),
            signal = signal,
        )
