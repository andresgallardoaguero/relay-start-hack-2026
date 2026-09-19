# Script: base.py
# Purpose: Define what every guard receives, what every guard must offer, how a guard builds its result and the placeholder for a guard without logic
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from app.engine.facts import FactSheet
from app.models.decision import GuardFamily, GuardResult, GuardVerdict, ReasonCode
from app.models.events import AuthorizationEvent
from app.models.policy import InternalPolicy
from app.state.familiarity import FamiliarityFacts
from app.state.session import SessionFacts









#### Step 1: Define the input of a decision ####

# Hold everything a guard may look at, where facts carries the amounts as exact decimals,
# state is the memory of the run, and familiarity holds what the history says about the shop, as counts and names without any identifier.
# session holds what the history of the card says about the device, the hour and the country of the purchase, as counts without any identifier.
# extracted_facts holds the facts a language model read from the sentences of the shop, or None when no model ran.
# It is an optional extra, and a guard that reads it may only become stricter through it.
@dataclass(frozen = True)
class DecisionInput:
    event: AuthorizationEvent
    policy: InternalPolicy
    facts: FactSheet
    state: Any = None
    familiarity: Optional[FamiliarityFacts] = None
    session: Optional[SessionFacts] = None
    extracted_facts: Any = None









#### Step 2: Define what every guard must offer ####

# Describe a guard, which carries its number, its id and its family and answers one purchase with one result.
# earlier_results maps the id of every guard that already ran to its result and cannot be changed.
# A guard fills in its own number, id and family, and the pipeline fills in the running time.
class Guard(Protocol):

    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input: DecisionInput, earlier_results: Mapping[str, GuardResult]) -> GuardResult:
        ...









#### Step 3: Build the result of a guard ####

# Build a result under the number, the id and the family of the guard, so no guard has to repeat them
def build_guard_result(guard, verdict, reason_code = None, evidence = (), signal = None, note = None, customer_message = None):
    return GuardResult(
        guard_number = guard.guard_number,
        guard_id = guard.guard_id,
        family = guard.family,
        verdict = verdict,
        reason_code = reason_code,
        evidence = list(evidence),
        signal = signal,
        note = note,
        customer_message = customer_message,
    )









#### Step 4: Define the placeholder for a guard without logic ####

# Stand in for a guard that is not built yet, which answers SKIP and therefore never changes a decision
@dataclass(frozen = True)
class PlaceholderGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    # Answer SKIP with the reason that the guard is not built, without evidence and without a message
    def check(self, decision_input, earlier_results):
        return build_guard_result(self, GuardVerdict.SKIP, reason_code = ReasonCode.GUARD_NOT_BUILT)
