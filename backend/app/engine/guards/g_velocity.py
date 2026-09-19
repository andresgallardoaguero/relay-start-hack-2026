# Script: g_velocity.py
# Purpose: Notice a purchase that follows several other purchase attempts within ten minutes, as a signal that decides nothing on its own
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict









#### Step 1: Name the sources of the evidence and the signal ####

# Name where the evidence comes from, which is the count of the purchase message against the number of the policy
RECENT_ATTEMPTS_SOURCE = "authorization.recent_attempt_count_10m against policy.expectations.velocity_min_recent_attempts"
MISSING_FACTS_SOURCE = "session facts of the purchase"



# Name the signal of a quick series of purchase attempts. Most quick series of honest customers are tickets and repeated tries,
# so the signal is weak alone and a later guard combines it with the other signals.
QUICK_SERIES_SIGNAL = GuardSignal(name = "quick_series", strength = "normal")









#### Step 2: Build the evidence ####

# Describe that the session facts were not available, so the recent attempts are not known
def build_missing_facts_evidence():
    return EvidenceItem(fact = "session_facts_are_available", value = False, comparator = None, threshold = None, source = MISSING_FACTS_SOURCE)



# Describe the purchase attempts of the ten minutes before the purchase against the number that makes a quick series
def build_recent_attempts_evidence(recent_attempt_count, min_recent_attempts):
    return EvidenceItem(
        fact = "recent_attempt_count",
        value = recent_attempt_count,
        comparator = "<",
        threshold = min_recent_attempts,
        source = RECENT_ATTEMPTS_SOURCE,
    )









#### Step 3: Define the guard ####

# Check how many purchase attempts came in the ten minutes before the purchase. The guard reads the session facts and the policy, and it sees no identifier.
# It always passes. A quick series is a signal for a later guard and never a reason to ask or to decline on its own.
@dataclass(frozen = True)
class VelocityGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        session = decision_input.session
        min_recent_attempts = decision_input.policy.expectations.velocity_min_recent_attempts



        # Pass without a signal when the session facts are missing, because a missing count is not a quick series
        if session is None:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_facts_evidence(),))



        # Pass with the signal when the attempts reach the number of the policy, where a count exactly at the number reaches it
        signal = QUICK_SERIES_SIGNAL if session.recent_attempt_count >= min_recent_attempts else None
        return build_guard_result(
            self,
            GuardVerdict.PASS,
            evidence = (build_recent_attempts_evidence(session.recent_attempt_count, min_recent_attempts),),
            signal = signal,
        )
