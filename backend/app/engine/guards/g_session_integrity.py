# Script: g_session_integrity.py
# Purpose: Combine the signs that someone other than the customer is driving the session, and ask the customer or decline when enough of them come together
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict, ReasonCode









#### Step 1: Name the guards that give signals, the own signal and the sources of the evidence ####

# List the guards whose signals are combined, in the order they run. A guard that did not run or that noticed nothing adds nothing.
SIGNAL_GUARD_IDS = ("merchant_familiarity", "device", "velocity", "geo", "usual_purchase_pattern")



# Name the signal of a purchase at an hour of the day at which the card has never been used, in Swiss local time.
# The hour is judged against the history of the card itself. A customer who shops at night has night hours in the history and gives no signal at night.
UNUSUAL_HOUR_SIGNAL = GuardSignal(name = "unusual_hour", strength = "normal")



# Name where the evidence comes from
SIGNAL_COUNT_SOURCE = "distinct signals of this guard and of the guards " + ", ".join(SIGNAL_GUARD_IDS)
ASK_COUNT_SOURCE = SIGNAL_COUNT_SOURCE + " against policy.expectations.session_ask_signal_count"
DECLINE_COUNT_SOURCE = SIGNAL_COUNT_SOURCE + " against policy.expectations.session_decline_signal_count"
SENSITIVITY_SOURCE = "policy.expectations.session_sensitivity"
HOUR_COUNT_SOURCE = "approved purchases of this card in the Swiss local hour of the purchase, without scheduled recurring payments"
HISTORY_COUNT_SOURCE = "approved purchases of this card behind the hour counts against policy.expectations.hour_min_history_purchases"
CHANNEL_SOURCE = "authorization.channel"
OWN_SIGNAL_SOURCE = "this guard"



# State the order in which the customer reads what was noticed, which is the hour, the device, the shop, the country and the quick series.
# A signal with another name comes last, in the order of its name.
MESSAGE_ORDER_OF_SIGNALS = ("unusual_hour", "new_device", "unfamiliar_merchant", "new_country", "quick_series")









#### Step 2: Collect the signals ####

# Hold one distinct signal, with the strongest strength any guard gave it and the guards that gave it
@dataclass(frozen = True)
class NoticedSignal:
    name: str
    strength: str
    given_by: tuple



# Report whether the hour of the purchase is one the card never shops at. The card must be known, its history must hold enough purchases
# to say something about its hours, the hour must hold no purchase at all, and the purchase must not be a scheduled recurring payment,
# which runs by itself at any hour.
def hour_is_unusual(session, hour_min_history_purchases):
    if session is None or not session.card_is_known:
        return False
    if session.history_purchase_count is None or session.hour_purchase_count is None:
        return False
    if session.is_recurring_channel:
        return False
    return session.history_purchase_count >= hour_min_history_purchases and session.hour_purchase_count == 0



# List every signal with the guard that gave it, as pairs. The own signal of the hour comes first, then the signals of the earlier guards.
def list_given_signals(earlier_results, own_signal):
    own_pairs = () if own_signal is None else ((OWN_SIGNAL_SOURCE, own_signal),)
    earlier_pairs = tuple(
        (signal_guard_id, earlier_results[signal_guard_id].signal)
        for signal_guard_id in SIGNAL_GUARD_IDS
        if signal_guard_id in earlier_results and earlier_results[signal_guard_id].signal is not None
    )
    return own_pairs + earlier_pairs



# Reduce the given signals to distinct ones by their name, so the same signal from two guards counts once.
# A signal is strong when any guard gave it as strong.
def collect_distinct_signals(given_signals):
    signal_names = tuple(dict.fromkeys(signal.name for giving_guard, signal in given_signals))
    return tuple(
        NoticedSignal(
            name = signal_name,
            strength = "strong" if any(signal.name == signal_name and signal.strength == "strong" for giving_guard, signal in given_signals) else "normal",
            given_by = tuple(giving_guard for giving_guard, signal in given_signals if signal.name == signal_name),
        )
        for signal_name in signal_names
    )









#### Step 3: Build the evidence ####

# Describe the count of distinct signals against the count that asks and against the count that declines
def build_count_evidence(signal_count, expectations):
    return (
        EvidenceItem(fact = "signal_count", value = signal_count, comparator = "<", threshold = expectations.session_ask_signal_count, source = ASK_COUNT_SOURCE),
        EvidenceItem(fact = "signal_count", value = signal_count, comparator = "<", threshold = expectations.session_decline_signal_count, source = DECLINE_COUNT_SOURCE),
        EvidenceItem(fact = "session_sensitivity", value = expectations.session_sensitivity, comparator = None, threshold = None, source = SENSITIVITY_SOURCE),
    )



# Describe one noticed signal by its name, with its strength and the guards that gave it
def build_signal_evidence(noticed_signal):
    return EvidenceItem(
        fact = "signal",
        value = noticed_signal.name,
        comparator = None,
        threshold = None,
        source = noticed_signal.strength + " signal given by " + ", ".join(noticed_signal.given_by),
    )



# Describe what the history of the card says about the hour of the purchase, which is empty without session facts of a known card
def build_hour_evidence(session, hour_min_history_purchases):
    if session is None or not session.card_is_known:
        return ()
    return (
        EvidenceItem(fact = "local_hour", value = session.local_hour, comparator = None, threshold = None, source = "authorization.timestamp in Swiss local time"),
        EvidenceItem(fact = "hour_purchase_count", value = session.hour_purchase_count, comparator = ">=", threshold = 1, source = HOUR_COUNT_SOURCE),
        EvidenceItem(fact = "history_purchase_count", value = session.history_purchase_count, comparator = ">=", threshold = hour_min_history_purchases, source = HISTORY_COUNT_SOURCE),
        EvidenceItem(fact = "is_recurring_channel", value = session.is_recurring_channel, comparator = "=", threshold = False, source = CHANNEL_SOURCE),
    )









#### Step 4: Write the sentences for the customer ####

# Write what one signal means in plain words, as a part of the sentence "This purchase was made ...".
# The shop is one the customer has not bought from, unless the history shows purchases there with another card or too few of them.
def describe_signal(signal_name, session, familiarity):
    if signal_name == "unusual_hour" and session is not None:
        return "at " + session.local_time_text + ", an hour you never shop at"
    if signal_name == "unusual_hour":
        return "at an hour you never shop at"
    if signal_name == "new_device":
        return "from a device you have never used"
    if signal_name == "unfamiliar_merchant":
        customer_has_bought_there = familiarity is not None and familiarity.customer_purchase_count is not None and familiarity.customer_purchase_count > 0
        return "at a shop you have rarely used with this card" if customer_has_bought_there else "at a shop you have not bought from"
    if signal_name == "new_country":
        return "in a country you have never bought in"
    if signal_name == "quick_series":
        return "in a quick series of purchase attempts"
    return "in a way that differs from your usual purchases"



# Put the noticed signals into the order the customer reads them in, where a signal with an unknown name comes last
def order_signals_for_the_message(noticed_signals):
    return tuple(sorted(
        noticed_signals,
        key = lambda noticed_signal: (
            MESSAGE_ORDER_OF_SIGNALS.index(noticed_signal.name) if noticed_signal.name in MESSAGE_ORDER_OF_SIGNALS else len(MESSAGE_ORDER_OF_SIGNALS),
            noticed_signal.name,
        ),
    ))



# Write the sentence that lists what was noticed, as in "It was made at 04.14, an hour you never shop at, from a device you have never used."
def describe_what_was_noticed(opening, noticed_signals, session, familiarity):
    descriptions = tuple(
        describe_signal(noticed_signal.name, session, familiarity)
        for noticed_signal in order_signals_for_the_message(noticed_signals)
    )
    return opening + " " + ", ".join(descriptions) + "."



# Write the message of a decline
def build_decline_message(noticed_signals, session, familiarity):
    return "Declined. This purchase does not look like you. " + describe_what_was_noticed("It was made", noticed_signals, session, familiarity)



# Write the question to the customer, which says what was noticed and nothing about the other checks, because a later check can still ask
def build_question(noticed_signals, session, familiarity):
    return describe_what_was_noticed("This purchase was made", noticed_signals, session, familiarity) + " Is this you?"









#### Step 5: Define the guard ####

# Combine the signals. The guard reads the session facts, the familiarity facts for the wording, the policy and the results of the earlier guards,
# and it sees no identifier. No signal decides alone. The count of distinct signals at or above the declining count of the policy declines,
# and at or above the asking count it asks. Below the asking count the purchase passes, unless the instruction asks to watch the session
# and one of the signals is a strong one, which asks the customer and never declines.
@dataclass(frozen = True)
class SessionIntegrityGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        session = decision_input.session
        familiarity = decision_input.familiarity
        expectations = decision_input.policy.expectations



        # Collect the distinct signals, with the own signal of an hour the card never shops at
        own_signal = UNUSUAL_HOUR_SIGNAL if hour_is_unusual(session, expectations.hour_min_history_purchases) else None
        noticed_signals = collect_distinct_signals(list_given_signals(earlier_results, own_signal))
        signal_count = len(noticed_signals)
        evidence = (
            build_count_evidence(signal_count, expectations)
            + tuple(build_signal_evidence(noticed_signal) for noticed_signal in noticed_signals)
            + build_hour_evidence(session, expectations.hour_min_history_purchases)
        )



        # Decline when the count reaches the declining count of the policy, where a count exactly at the number reaches it
        if signal_count >= expectations.session_decline_signal_count:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.SESSION_ANOMALY,
                evidence = evidence,
                signal = own_signal,
                customer_message = build_decline_message(noticed_signals, session, familiarity),
            )



        # Ask the customer when the count reaches the asking count, or when the instruction asks to watch the session and a strong signal was noticed
        has_strong_signal = any(noticed_signal.strength == "strong" for noticed_signal in noticed_signals)
        watches_the_session = expectations.session_sensitivity == "high"
        if signal_count >= expectations.session_ask_signal_count or (watches_the_session and has_strong_signal):
            return build_guard_result(
                self,
                GuardVerdict.STEP_UP,
                reason_code = ReasonCode.SESSION_ANOMALY,
                evidence = evidence,
                signal = own_signal,
                customer_message = build_question(noticed_signals, session, familiarity),
            )



        # Pass everything else, and keep the own signal in the result so the record shows it
        return build_guard_result(self, GuardVerdict.PASS, evidence = evidence, signal = own_signal)
