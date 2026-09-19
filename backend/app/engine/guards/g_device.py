# Script: g_device.py
# Purpose: Notice a purchase from a device the card has never been used from, as a signal that decides nothing on its own
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict









#### Step 1: Name the sources of the evidence and the signal ####

# Name where the evidence comes from, which is the card history of the issuer and the device the purchase message states
HISTORY_SOURCE = "card history of the issuer"
DEVICE_COUNT_SOURCE = "approved purchases of this card from the device of the purchase, without scheduled recurring payments"
STATED_DEVICE_SOURCE = "device stated by the purchase message"



# Name the signal of a device the card has never been used from. A customer rarely changes devices, so the signal is a strong one.
# It still decides nothing here, because every customer buys a new phone at some point, and a later guard combines it with the other signals.
NEW_DEVICE_SIGNAL = GuardSignal(name = "new_device", strength = "strong")









#### Step 2: Build the evidence ####

# Describe that no history of the card was available, so nothing is known about its devices
def build_missing_history_evidence():
    return EvidenceItem(fact = "card_history_is_available", value = False, comparator = None, threshold = None, source = HISTORY_SOURCE)



# Describe that the purchase states no device, which is a missing fact and no new device
def build_missing_device_evidence():
    return EvidenceItem(fact = "device_is_stated", value = False, comparator = None, threshold = None, source = STATED_DEVICE_SOURCE)



# Describe the purchases of the card from the device, where at least one makes the device a known one
def build_device_count_evidence(device_purchase_count):
    return EvidenceItem(fact = "device_purchase_count", value = device_purchase_count, comparator = ">=", threshold = 1, source = DEVICE_COUNT_SOURCE)









#### Step 3: Define the guard ####

# Check whether the card has been used from the device before. The guard reads the session facts and nothing else, and it sees no identifier.
# It always passes. A new device is a signal for a later guard and never a reason to ask or to decline on its own.
@dataclass(frozen = True)
class DeviceGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        session = decision_input.session



        # Pass without a signal when the history is missing or the card is unknown, because an unknown history is not a new device
        if session is None or not session.card_is_known:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_history_evidence(),))



        # Pass without a signal when the purchase states no device
        if session.device_purchase_count is None:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_device_evidence(),))



        # Pass with the signal for a device the card never used, and without one for a device it used at least once
        signal = NEW_DEVICE_SIGNAL if session.device_purchase_count == 0 else None
        return build_guard_result(
            self,
            GuardVerdict.PASS,
            evidence = (build_device_count_evidence(session.device_purchase_count),),
            signal = signal,
        )
