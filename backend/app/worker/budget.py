# Script: budget.py
# Purpose: Measure how much of the answer deadline is left for the engine, with a reserve kept for sending the answer
# Author: Jonas Lüthi
# Date: September 2026

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone









#### Step 1: Read the clock ####

# Read the real clock in UTC, which a test replaces to fix the time
def read_utc_clock():
    return datetime.now(timezone.utc)









#### Step 2: Define the budget ####

# Hold the moments that bound one decision.
# The engine may run until the earlier of two moments, the platform deadline minus the reserve for the answer,
# and the moment the engine's own budget after arrival runs out. A late arrival therefore gets only what is left.
@dataclass(frozen = True)
class DeadlineBudget:

    received_at: datetime
    deadline_at: datetime
    engine_budget_ms: int
    post_reserve_ms: int



    # Name the moment the engine has to be done
    @property
    def engine_deadline_at(self):
        platform_bound = self.deadline_at - timedelta(milliseconds = self.post_reserve_ms)
        own_bound = self.received_at + timedelta(milliseconds = self.engine_budget_ms)
        return min(platform_bound, own_bound)



    # Measure the seconds the engine still has, never below zero
    def seconds_left_for_engine(self, now = None):
        if now is None:
            now = read_utc_clock()
        return max(0.0, (self.engine_deadline_at - now) / timedelta(seconds = 1))



    # Measure the seconds until the platform deadline, negative once it has passed
    def seconds_until_deadline(self, now = None):
        if now is None:
            now = read_utc_clock()
        return (self.deadline_at - now) / timedelta(seconds = 1)



    # Say whether the platform deadline has passed
    def is_past_deadline(self, now = None):
        return self.seconds_until_deadline(now) <= 0.0
