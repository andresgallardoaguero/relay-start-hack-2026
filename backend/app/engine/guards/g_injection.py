# Script: g_injection.py
# Purpose: Notice text of a shop that is aimed at the shopping agent and not at a buyer, put it to the customer with the sentence quoted, and never let it loosen anything
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import clean_shop_text
from app.engine.guards.base import build_guard_result
from app.engine.injection_text import SHOWN_SENTENCE_LENGTH
from app.llm.schemas import ModelExtraction
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence and the fixed sentences ####

# Name the four texts of the shop together, the facts of the language model and the field of the policy that the evidence points to
SHOP_TEXTS_SOURCE = "shop name, item names, item sentences and purchase description"
MODEL_FLAG_SOURCE = "language model facts"
ACTION_FIELD_SOURCE = "policy.expectations.injection_action"



# State the note that reaches the customer with every finding, whatever the final decision is
AGENT_DIRECTED_TEXT_NOTE = "The shop's text contains a sentence aimed at your shopping assistant. It had no effect on this decision."



# Map the action of the policy to the verdict, where anything that is not the known refusal asks the customer
VERDICT_BY_ACTION = {
    "step_up": GuardVerdict.STEP_UP,
    "decline": GuardVerdict.DECLINE,
}









#### Step 2: Read the flag of the language model ####

# Read the quotes of a language model that suspects text aimed at the agent, cleaned like every other text a shop wrote.
# No model, anything that is not the answer of the model module, a model call without facts and a model that suspects nothing all give None. A model that suspects something without a quote gives an empty tuple.
# The flag can only add a finding, so a model never removes a finding of the fixed patterns.
def read_model_quotes(model_extraction):
    model_facts = model_extraction.facts if isinstance(model_extraction, ModelExtraction) else None
    if model_facts is None or model_facts.injection_suspected is not True:
        return None
    cleaned_quotes = tuple(clean_shop_text(model_quote, shown_length = SHOWN_SENTENCE_LENGTH) for model_quote in model_facts.injection_quotes)
    return tuple(cleaned_quote for cleaned_quote in cleaned_quotes if cleaned_quote != "")









#### Step 3: Build the evidence ####

# Say that none of the four texts of the shop holds a sentence aimed at the agent
def build_nothing_found_evidence():
    return EvidenceItem(
        fact = "agent_directed_text",
        value = "none_found",
        comparator = None,
        threshold = None,
        source = SHOP_TEXTS_SOURCE,
    )



# Describe one finding of the fixed patterns, with the cleaned sentence, the family of the pattern, the field of the message and the cart line where there is one
def build_finding_evidence(agent_directed_text_fact):
    line_text = "" if agent_directed_text_fact.line_no is None else " line " + str(agent_directed_text_fact.line_no)
    return EvidenceItem(
        fact = "agent_directed_text in the " + agent_directed_text_fact.field_name,
        value = agent_directed_text_fact.sentence,
        comparator = "matches",
        threshold = agent_directed_text_fact.family,
        source = agent_directed_text_fact.source + line_text,
    )



# Describe the flag of the language model, with one evidence item per cleaned quote and a single item when the model gave no quote
def build_model_flag_evidence(model_quotes):
    shown_quotes = model_quotes if len(model_quotes) > 0 else (None,)
    return tuple(
        EvidenceItem(
            fact = "injection_suspected",
            value = model_quote,
            comparator = "=",
            threshold = True,
            source = MODEL_FLAG_SOURCE,
        )
        for model_quote in shown_quotes
    )



# Describe the action of the policy, which turns a finding into a question or into a refusal
def build_action_evidence(injection_action):
    return EvidenceItem(
        fact = "injection_action",
        value = injection_action,
        comparator = None,
        threshold = None,
        source = ACTION_FIELD_SOURCE,
    )









#### Step 4: Write the message ####

# Pick the sentence the customer reads, which is the first finding of the fixed patterns and otherwise the first quote of the model
def pick_shown_sentence(agent_directed_text_facts, model_quotes):
    if len(agent_directed_text_facts) > 0:
        return agent_directed_text_facts[0].sentence
    return model_quotes[0] if model_quotes else None



# Tell the customer about the sentence, inside double quotes because the shop wrote it. The sentence is already cleaned, so it holds no double quote of its own.
# Under a refusal the message starts with "Declined." and asks nothing.
def build_customer_message(shown_sentence, verdict):
    quoted_sentence = "" if shown_sentence is None else ", \"" + shown_sentence + "\""
    finding_text = "The shop's text contains a sentence aimed at your shopping assistant" + quoted_sentence + "."
    if verdict == GuardVerdict.DECLINE:
        return "Declined. " + finding_text + " It had no effect, and your settings refuse a purchase whose shop writes such text."
    return finding_text + " It had no effect on this decision. Approve this purchase?"









#### Step 5: Define the guard ####

# Notice text of a shop that tries to instruct the shopping agent. The guard reads the findings of the fixed patterns in the four texts of the shop,
# the optional flag of a language model, and the action of the policy.
# The facts of the purchase decide in both directions. Such a text never loosens a decision, because this guard can only pass, ask or refuse,
# and the text does not refuse a purchase on its own either, unless the policy says so. A finding is evidence about the good faith of the shop,
# so the customer is asked with the sentence quoted, and every other guard still judges the purchase on its facts.
@dataclass(frozen = True)
class InjectionGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        agent_directed_text_facts = decision_input.facts.agent_directed_text
        model_quotes = read_model_quotes(decision_input.extracted_facts)



        # Pass when no pattern found anything and no model suspects anything
        if len(agent_directed_text_facts) == 0 and model_quotes is None:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_nothing_found_evidence(),))



        # Ask the customer about a finding, or refuse when the policy says so. An action the guard does not know asks, which is the milder of the two and never a pass.
        injection_action = decision_input.policy.expectations.injection_action
        verdict = VERDICT_BY_ACTION.get(injection_action, GuardVerdict.STEP_UP)
        evidence = (
            tuple(build_finding_evidence(agent_directed_text_fact) for agent_directed_text_fact in agent_directed_text_facts)
            + (() if model_quotes is None else build_model_flag_evidence(model_quotes))
            + (build_action_evidence(injection_action),)
        )
        return build_guard_result(
            self,
            verdict,
            reason_code = ReasonCode.PROMPT_INJECTION,
            evidence = evidence,
            note = AGENT_DIRECTED_TEXT_NOTE,
            customer_message = build_customer_message(pick_shown_sentence(agent_directed_text_facts, model_quotes), verdict),
        )
