# Script: test_event_roundtrip.py
# Purpose: Check that the purchase message reader accepts and rejects exactly what the published schema does
# Author: Andrés Gallardo
# Date: September 2026

import copy
import json
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from app.models.events import AuthorizationEvent, InvalidEventError, read_purchase_message









#### Step 1: Define how a test case changes the example message ####

# Describe one change, which either sets a value or removes a field at a path
@dataclass(frozen = True)
class MessageChange:
    action: str
    path: tuple
    new_value: object = None



# Describe one case as a name, the expected outcome and the changes to apply
@dataclass(frozen = True)
class MessageCase:
    name: str
    expected_valid: bool
    changes: tuple



# Build a change that sets the value at a path, creating the field when it does not exist
def set_value(*path, new_value):
    return MessageChange(action = "set", path = path, new_value = new_value)



# Build a change that removes the field at a path
def remove_field(*path):
    return MessageChange(action = "remove", path = path)



# Build one named case from its changes
def message_case(name, expected_valid, *changes):
    return MessageCase(name = name, expected_valid = expected_valid, changes = changes)



# Apply one change to a message in place
def apply_change(message, change):
    parent = message
    for path_part in change.path[:-1]:
        parent = parent[path_part]
    last_path_part = change.path[-1]
    if change.action == "set":
        parent[last_path_part] = copy.deepcopy(change.new_value)
    elif change.action == "remove":
        del parent[last_path_part]
    else:
        raise AssertionError("Unknown action " + change.action)



# Return a changed deep copy of the example message and leave the original untouched
def build_changed_message(example_message, case):
    changed_message = copy.deepcopy(example_message)
    for change in case.changes:
        apply_change(changed_message, change)
    return changed_message



# Report whether the reader accepts a message, together with the problems it listed
def read_and_report(message):
    try:
        read_purchase_message(message)
    except InvalidEventError as invalid_event_error:
        return False, invalid_event_error.problems
    return True, []



# Check that a rejection lists at least one problem and that every problem is plain text
def assert_problems_are_listed(problems):
    assert len(problems) >= 1
    assert all(isinstance(problem, str) and problem != "" for problem in problems)









#### Step 2: Check the example message ####

# Check that the example parses and that nulls, lists and times arrive as expected
def test_example_message_parses(example_message_text):
    event = read_purchase_message(example_message_text)

    assert isinstance(event, AuthorizationEvent)
    assert event.authorization.billing_amount_chf == 20.0
    assert event.authorization.spend_in_period_before_chf is None
    assert event.authorization.spend_in_period_before_chf != 0
    assert event.authorization.delivery_by is None
    assert event.authorization.related_authorization_id is None
    assert event.authorization.related_authorization_status is None
    assert len(event.authorization.items) == 1
    assert event.mandate.hard_rules == ()
    assert event.deadline_at.tzinfo is not None
    assert event.deadline_at.utcoffset() is not None



# Check that a parsed event cannot be changed, at the top level and inside its parts
def test_assigning_to_a_parsed_event_raises(example_message_text):
    event = read_purchase_message(example_message_text)

    with pytest.raises(ValidationError):
        event.request_id = "req_changed"
    with pytest.raises(ValidationError):
        event.authorization.billing_amount_chf = 1.0
    with pytest.raises(ValidationError):
        event.mandate.instruction = "Buy anything."
    assert event.authorization.billing_amount_chf == 20.0



# Check that the cart, the rules and the recent purchases arrive as tuples, which cannot grow or shrink
def test_lists_of_the_message_arrive_as_tuples(example_message_text):
    event = read_purchase_message(example_message_text)

    assert isinstance(event.authorization.items, tuple)
    assert isinstance(event.mandate.hard_rules, tuple)
    assert isinstance(event.context.recent_authorizations, tuple)



# Check that text, bytes and a dictionary give equal objects
def test_text_bytes_and_dictionary_give_equal_objects(example_message_text, example_message):
    event_from_text = read_purchase_message(example_message_text)
    event_from_bytes = read_purchase_message(example_message_text.encode("utf-8"))
    event_from_dictionary = read_purchase_message(example_message)

    assert event_from_text == event_from_bytes
    assert event_from_text == event_from_dictionary



# Check that reading a dictionary leaves the dictionary unchanged
def test_reading_does_not_change_the_dictionary(example_message):
    message_before_reading = copy.deepcopy(example_message)
    read_purchase_message(example_message)

    assert example_message == message_before_reading









#### Step 3: Check that broken input raises the typed error ####

# Check malformed JSON as text and as bytes, and valid JSON that is not an object
@pytest.mark.parametrize(
    "malformed_input",
    [
        "{\"type\": \"authorization.request\",",
        "",
        "not json at all",
        b"\xff\xfe broken bytes",
        "[]",
        "null",
    ],
)
def test_malformed_json_raises_the_typed_error(malformed_input):
    with pytest.raises(InvalidEventError) as raised:
        read_purchase_message(malformed_input)

    assert_problems_are_listed(raised.value.problems)



# Check inputs that are neither text, bytes nor a dictionary
@pytest.mark.parametrize("unsupported_input", [None, 5, 2.5, ["a list"]])
def test_unsupported_input_type_raises_the_typed_error(unsupported_input):
    with pytest.raises(InvalidEventError) as raised:
        read_purchase_message(unsupported_input)

    assert_problems_are_listed(raised.value.problems)



# Check a dictionary that cannot be written as JSON
def test_unserializable_dictionary_raises_the_typed_error(example_message):
    example_message["request_id"] = {"a set cannot be written as JSON"}

    with pytest.raises(InvalidEventError) as raised:
        read_purchase_message(example_message)

    assert_problems_are_listed(raised.value.problems)



# Check that a problem names the full path of the field it is about
def test_problem_names_the_field_path(example_message):
    del example_message["authorization"]["delivery_by"]
    example_message["authorization"]["items"][0]["quantity"] = 0

    with pytest.raises(InvalidEventError) as raised:
        read_purchase_message(example_message)

    assert any(problem.startswith("authorization.delivery_by - ") for problem in raised.value.problems)
    assert any(problem.startswith("authorization.items.0.quantity - ") for problem in raised.value.problems)









#### Step 4: Check that the reader agrees with the published schema ####

# Define a plain valid rule and a plain valid recent authorization to build cases from
PLAIN_RULE = {"field": "billing_amount_chf", "operator": "<=", "value": 200}

PLAIN_RECENT_AUTHORIZATION = {
    "authorization_id": "AU_EXAMPLE_0000",
    "timestamp": "2026-08-12T08:50:00Z",
    "merchant_id": "ME_EXAMPLE_0001",
    "billing_amount_chf": 12.5,
    "status": "approved",
}



# Build a change that puts one rule into the mandate, the plain rule with the given fields on top
def set_single_rule(**rule_fields):
    rule = {**PLAIN_RULE, **rule_fields}
    return set_value("mandate", "hard_rules", new_value = [rule])



# Build a change that puts one recent authorization into the context
def set_single_recent_authorization(**authorization_fields):
    recent_authorization = {**PLAIN_RECENT_AUTHORIZATION, **authorization_fields}
    return set_value("context", "recent_authorizations", new_value = [recent_authorization])



# List every case with the outcome that both validators must reach
AGREEMENT_CASES = [

    # Baselines, so that a rejection below is caused by the named change alone
    message_case("unchanged example", True),
    message_case("one plain rule", True, set_single_rule()),
    message_case("one plain recent authorization", True, set_single_recent_authorization()),

    # Unknown fields at every nesting level
    message_case("extra field at the top level", False, set_value("unexpected_field", new_value = 1)),
    message_case("extra field in authorization", False, set_value("authorization", "unexpected_field", new_value = 1)),
    message_case("extra field in merchant", False, set_value("authorization", "merchant", "unexpected_field", new_value = 1)),
    message_case("extra field in an item", False, set_value("authorization", "items", 0, "unexpected_field", new_value = 1)),
    message_case("extra field in mandate", False, set_value("mandate", "unexpected_field", new_value = 1)),
    message_case("extra field in a rule", False, set_single_rule(unexpected_field = 1)),
    message_case("extra field in context", False, set_value("context", "unexpected_field", new_value = 1)),
    message_case("extra field in a recent authorization", False, set_single_recent_authorization(unexpected_field = 1)),
    message_case("extra field in runtime", False, set_value("runtime", "unexpected_field", new_value = 1)),

    # Required fields that may be null but must never be missing
    message_case("spend_in_period_before_chf removed", False, remove_field("authorization", "spend_in_period_before_chf")),
    message_case("delivery_by removed", False, remove_field("authorization", "delivery_by")),
    message_case("related_authorization_id removed", False, remove_field("authorization", "related_authorization_id")),
    message_case("related_authorization_status removed", False, remove_field("authorization", "related_authorization_status")),
    message_case("approved_spend_in_period_chf removed", False, remove_field("context", "approved_spend_in_period_chf")),
    message_case("approved_spend_in_period_chf null", True, set_value("context", "approved_spend_in_period_chf", new_value = None)),

    # Other required fields and fixed values
    message_case("request_id removed", False, remove_field("request_id")),
    message_case("request_id empty", False, set_value("request_id", new_value = "")),
    message_case("type with another value", False, set_value("type", new_value = "authorization.response")),
    message_case("context_basis with another value", False, set_value("runtime", "context_basis", new_value = "other")),
    message_case("hard_rules removed", False, remove_field("mandate", "hard_rules")),

    # Amounts and counts
    message_case("amount as the text 20.00", False, set_value("authorization", "amount", new_value = "20.00")),
    message_case("amount 0", False, set_value("authorization", "amount", new_value = 0)),
    message_case("amount true", False, set_value("authorization", "amount", new_value = True)),
    message_case("amount negative", False, set_value("authorization", "amount", new_value = -5.0)),
    message_case("amount as the whole number 20", True, set_value("authorization", "amount", new_value = 20)),
    message_case("amount null", False, set_value("authorization", "amount", new_value = None)),
    message_case("delivery_fee 0", True, set_value("authorization", "delivery_fee", new_value = 0)),
    message_case("delivery_fee negative", False, set_value("authorization", "delivery_fee", new_value = -1)),
    message_case("spend_in_period_before_chf 12.5", True, set_value("authorization", "spend_in_period_before_chf", new_value = 12.5)),
    message_case("spend_in_period_before_chf negative", False, set_value("authorization", "spend_in_period_before_chf", new_value = -0.5)),
    message_case("spend_in_period_before_chf as text", False, set_value("authorization", "spend_in_period_before_chf", new_value = "12.5")),
    message_case("recent_attempt_count_10m negative", False, set_value("authorization", "recent_attempt_count_10m", new_value = -1)),
    message_case("recent_attempt_count_10m true", False, set_value("authorization", "recent_attempt_count_10m", new_value = True)),
    message_case("replay_order 0", False, set_value("authorization", "replay_order", new_value = 0)),
    message_case("history_window_minutes 0", False, set_value("runtime", "history_window_minutes", new_value = 0)),
    message_case("recent authorization with amount 0", True, set_single_recent_authorization(billing_amount_chf = 0)),

    # Cart lines
    message_case("quantity 0", False, set_value("authorization", "items", 0, "quantity", new_value = 0)),
    message_case("quantity 1.5", False, set_value("authorization", "items", 0, "quantity", new_value = 1.5)),
    message_case("quantity as text", False, set_value("authorization", "items", 0, "quantity", new_value = "1")),
    message_case("line_no 0", False, set_value("authorization", "items", 0, "line_no", new_value = 0)),
    message_case("unit_price 0", False, set_value("authorization", "items", 0, "unit_price", new_value = 0)),
    message_case("item_details empty", True, set_value("authorization", "items", 0, "item_details", new_value = "")),
    message_case("items as an empty list", False, set_value("authorization", "items", new_value = [])),

    # Text formats
    message_case("merchant_mcc 541", False, set_value("authorization", "merchant", "merchant_mcc", new_value = "541")),
    message_case("merchant_mcc as the number 5411", False, set_value("authorization", "merchant", "merchant_mcc", new_value = 5411)),
    message_case("merchant_country ch", False, set_value("authorization", "merchant", "merchant_country", new_value = "ch")),
    message_case("scenario_id SCEN1", False, set_value("authorization", "scenario_id", new_value = "SCEN1")),
    message_case("customer_device_id empty", True, set_value("authorization", "customer_device_id", new_value = "")),
    message_case("fulfillment_method empty", False, set_value("authorization", "fulfillment_method", new_value = "")),

    # Closed value lists
    message_case("recurring_capable unknown", False, set_value("authorization", "merchant", "recurring_capable", new_value = "unknown")),
    message_case("recurring_capable as a boolean", False, set_value("authorization", "merchant", "recurring_capable", new_value = False)),
    message_case("order_cancellable maybe", False, set_value("authorization", "order_cancellable", new_value = "maybe")),
    message_case("order_cancellable not_applicable", True, set_value("authorization", "order_cancellable", new_value = "not_applicable")),
    message_case("order_returnable as a boolean", False, set_value("authorization", "order_returnable", new_value = True)),
    message_case("related_authorization_status refunded", False, set_value("authorization", "related_authorization_status", new_value = "refunded")),
    message_case(
        "related_authorization_status declined with a related id",
        True,
        set_value("authorization", "related_authorization_status", new_value = "declined"),
        set_value("authorization", "related_authorization_id", new_value = "AU_EXAMPLE_0000"),
    ),
    message_case("currency JPY", False, set_value("authorization", "currency", new_value = "JPY")),
    message_case("initiator_type human", False, set_value("authorization", "initiator_type", new_value = "human")),
    message_case("uncertainty_policy approve", True, set_value("mandate", "uncertainty_policy", new_value = "approve")),
    message_case("uncertainty_policy allow", False, set_value("mandate", "uncertainty_policy", new_value = "allow")),

    # Rules
    message_case("rule with value true", False, set_single_rule(value = True)),
    message_case("rule with value null", False, set_single_rule(value = None)),
    message_case("rule with value as a list of numbers", False, set_single_rule(value = [1, 2])),
    message_case("rule with value as an object", False, set_single_rule(value = {"limit": 200})),
    message_case("rule with value as a list of texts", True, set_single_rule(operator = "in", value = ["a", "b"])),
    message_case("rule with value as a decimal number", True, set_single_rule(value = 199.5)),
    message_case("rule with value as text", True, set_single_rule(operator = "=", value = "groceries")),
    message_case("rule with period_days 0", False, set_single_rule(period_days = 0)),
    message_case("rule with period_days 30", True, set_single_rule(scope = "period", period_days = 30)),
    message_case("rule with the optional fields null", True, set_single_rule(currency = None, scope = None, period_days = None)),
    message_case("rule with currency JPY", False, set_single_rule(currency = "JPY")),
    message_case("rule with an unknown operator", False, set_single_rule(operator = "between")),
]



# Validate every case twice and require both validators to reach the expected outcome
@pytest.mark.parametrize("case", AGREEMENT_CASES, ids = [case.name for case in AGREEMENT_CASES])
def test_reader_agrees_with_the_published_schema(case, example_message, event_schema_validator):
    changed_message = build_changed_message(example_message, case)

    schema_accepts = event_schema_validator.is_valid(changed_message)
    reader_accepts, problems = read_and_report(changed_message)

    assert schema_accepts == case.expected_valid, "The published schema disagrees with the expected outcome"
    assert reader_accepts == case.expected_valid, "The reader disagrees with the expected outcome"
    assert schema_accepts == reader_accepts
    if not case.expected_valid:
        assert_problems_are_listed(problems)



# Check that the optional rule fields default to None when the message leaves them out
def test_missing_optional_rule_fields_become_none(example_message):
    example_message["mandate"]["hard_rules"] = [copy.deepcopy(PLAIN_RULE)]
    event = read_purchase_message(example_message)
    rule = event.mandate.hard_rules[0]

    assert rule.currency is None
    assert rule.scope is None
    assert rule.period_days is None
    assert rule.value == 200
    assert isinstance(rule.value, int)



# Check that a rule value written as a list of texts arrives as a tuple, which cannot be changed
def test_list_of_texts_in_a_rule_arrives_as_a_tuple(example_message):
    example_message["mandate"]["hard_rules"] = [{**PLAIN_RULE, "operator": "in", "value": ["groceries", "household"]}]
    event = read_purchase_message(example_message)

    assert event.mandate.hard_rules[0].value == ("groceries", "household")



# Check that the reader is deliberately stricter than the schema here, because counts are documented as whole numbers
def test_whole_number_written_as_float_is_rejected(example_message, event_schema_validator):
    example_message["authorization"]["items"][0]["quantity"] = 1.0

    schema_accepts = event_schema_validator.is_valid(example_message)
    reader_accepts, problems = read_and_report(example_message)

    assert schema_accepts is True
    assert reader_accepts is False
    assert_problems_are_listed(problems)









#### Step 5: Check the date and time formats with the reader alone ####

# List the format cases, which the schema validator skips because it does not check formats on its own
FORMAT_CASES = [
    message_case("timestamp yesterday", False, set_value("authorization", "timestamp", new_value = "yesterday")),
    message_case("deadline_at without a timezone", False, set_value("deadline_at", new_value = "2026-08-12T09:00:08")),
    message_case("received_at without a timezone", False, set_value("runtime", "received_at", new_value = "2026-08-12T09:00:00")),
    message_case("deadline_at with an offset", True, set_value("deadline_at", new_value = "2026-08-12T11:00:08+02:00")),
    message_case("deadline_at as a number", False, set_value("deadline_at", new_value = 1786525208)),
    message_case("delivery_by 2026-13-40", False, set_value("authorization", "delivery_by", new_value = "2026-13-40")),
    message_case("delivery_by 2026-08-10", True, set_value("authorization", "delivery_by", new_value = "2026-08-10")),
    message_case("delivery_by empty text", False, set_value("authorization", "delivery_by", new_value = "")),
]



# Require the reader to reach the expected outcome for every format case
@pytest.mark.parametrize("case", FORMAT_CASES, ids = [case.name for case in FORMAT_CASES])
def test_reader_checks_date_and_time_formats(case, example_message):
    changed_message = build_changed_message(example_message, case)
    reader_accepts, problems = read_and_report(changed_message)

    assert reader_accepts == case.expected_valid
    if not case.expected_valid:
        assert_problems_are_listed(problems)



# Check that an accepted delivery date arrives as a date and an accepted time keeps its timezone
def test_accepted_dates_and_times_are_typed(example_message):
    example_message["authorization"]["delivery_by"] = "2026-08-10"
    event = read_purchase_message(json.dumps(example_message))

    assert event.authorization.delivery_by.isoformat() == "2026-08-10"
    assert event.authorization.timestamp.utcoffset() is not None
    assert event.runtime.received_at.utcoffset() is not None
