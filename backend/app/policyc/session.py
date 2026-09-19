# Script: session.py
# Purpose: Read from the words of a customer instruction whether the customer asks to watch who is driving the session
# Author: Andrés Gallardo
# Date: September 2026

import re
from dataclasses import dataclass

from app.policyc.instruction_text import build_alternation_pattern, build_alternation_text, split_sentences









#### Step 1: Define the words the reader knows ####

# List the words that name another person, as in "someone else" and "somebody other than me".
# A pronoun such as "someone" needs one of the additions, so "buy a gift for someone" names nobody who acts in place of the customer.
OTHER_PERSON_PRONOUNS = ("someone", "somebody", "anyone", "anybody")
OTHER_PERSON_ADDITIONS = ("else", "other than me", "other than myself", "who is not me", "who isn't me", "who isn’t me", "that is not me")
OTHER_PERSON_NOUNS = ("a stranger", "another person", "a thief", "a fraudster")



# List the words that say a person is acting in the session, as in "is driving the session" and "seems to be using my card"
ACTING_WORDS = ("driving", "using", "controlling", "operating", "shopping", "ordering", "buying", "spending", "paying", "in control", "logged in", "behind")



# List the words of appearance, as in "does not look like me" and "seems to be someone else"
APPEARANCE_WORDS = ("look", "looks", "seem", "seems", "appear", "appears", "feel", "feels", "sound", "sounds")



# List the words that say something was taken over, and the things of the customer that can be taken over.
# A sentence needs one of each, so "do not buy stolen goods" and "order a card game" say nothing about the session.
TAKEOVER_WORDS = ("hijacked", "hacked", "compromised", "stolen", "taken over", "took over", "impersonated", "impersonating")
CUSTOMER_ASSET_WORDS = ("account", "card", "session", "phone", "device", "laptop", "login", "agent", "wallet", "profile")



# List the words that ask to watch for something without saying what, as in "stop anything suspicious".
# Such a sentence cannot be read as a wish about the session, so the customer is asked what was meant.
VAGUE_WATCH_WORDS = ("suspicious", "fraud", "fraudulent", "unusual activity", "strange activity", "odd activity", "fishy")









#### Step 2: Build the patterns ####

# Find a negation, as the word "not" or as the ending of "doesn't" and "isn’t"
NEGATION_TEXT = "(?:\\bnot|n['’]t)"



# Find another person who acts, with at most three words between the person and the acting word, as in "someone else seems to be using my card".
# Only whitespace may separate the words, and the acting word has to follow the person,
# so "buy from someone else if my shop is closed" is not found.
OTHER_PERSON_TEXT = (
    "(?:" + build_alternation_text(OTHER_PERSON_PRONOUNS) + "\\s+" + build_alternation_text(OTHER_PERSON_ADDITIONS)
    + "|" + build_alternation_text(OTHER_PERSON_NOUNS) + ")"
)
OTHER_PERSON_ACTING_PATTERN = re.compile(
    OTHER_PERSON_TEXT + "(?:\\s+[^\\W_]+(?:['’][^\\W_]+)?){0,3}?\\s+" + build_alternation_text(ACTING_WORDS),
    re.IGNORECASE,
)



# Find a purchase that does not look like the customer, in the three forms "does not look like me", "looks like it is not me" and "looks unlike me"
NOT_LOOKING_LIKE_ME_PATTERN = re.compile(
    NEGATION_TEXT + "\\s+" + build_alternation_text(APPEARANCE_WORDS) + "\\s+(?:like|to\\s+be)\\s+(?:me|myself)\\b",
    re.IGNORECASE,
)
LOOKING_LIKE_NOT_ME_PATTERN = re.compile(
    build_alternation_text(APPEARANCE_WORDS) + "\\s+(?:like|as\\s+if|as\\s+though)\\s+(?:it|this|that)(?:\\s+(?:is|was)|['’]s)?(?:\\s+not|n['’]t)\\s+(?:me|myself)\\b",
    re.IGNORECASE,
)
LOOKING_UNLIKE_ME_PATTERN = re.compile(
    build_alternation_text(APPEARANCE_WORDS) + "\\s+unlike\\s+(?:me|myself)\\b",
    re.IGNORECASE,
)



# Find the customer missing behind an action, as in "if it is not me shopping" and "when it isn't me who is ordering"
NOT_ME_ACTING_PATTERN = re.compile(
    NEGATION_TEXT + "\\s+(?:really\\s+|actually\\s+)?(?:me|myself)\\s+(?:(?:who|that)(?:\\s+is|['’]s)\\s+)?" + build_alternation_text(ACTING_WORDS),
    re.IGNORECASE,
)



# Find the takeover words, the things of the customer and the vague watch words
TAKEOVER_PATTERN = build_alternation_pattern(TAKEOVER_WORDS)
CUSTOMER_ASSET_PATTERN = build_alternation_pattern(CUSTOMER_ASSET_WORDS)
VAGUE_WATCH_PATTERN = build_alternation_pattern(VAGUE_WATCH_WORDS)



# List the patterns of which one alone says that the customer asks to watch the session
WATCH_THE_SESSION_PATTERNS = (
    OTHER_PERSON_ACTING_PATTERN,
    NOT_LOOKING_LIKE_ME_PATTERN,
    LOOKING_LIKE_NOT_ME_PATTERN,
    LOOKING_UNLIKE_ME_PATTERN,
    NOT_ME_ACTING_PATTERN,
)









#### Step 3: Define what the reader returns ####

# Describe the result. session_sensitivity is "high" when the instruction asks to watch who is driving the session, and "normal" otherwise.
# open_questions holds plain sentences for the customer.
@dataclass(frozen = True)
class SessionReading:
    session_sensitivity: str
    open_questions: tuple









#### Step 4: Read the instruction ####

# Report whether one sentence asks to watch who is driving the session, through one of the phrasings or through a takeover of a thing of the customer
def sentence_asks_to_watch_the_session(sentence):
    if any(watch_pattern.search(sentence) is not None for watch_pattern in WATCH_THE_SESSION_PATTERNS):
        return True
    return TAKEOVER_PATTERN.search(sentence) is not None and CUSTOMER_ASSET_PATTERN.search(sentence) is not None



# Read how closely the session is to be watched. One sentence that asks for it is enough, because a closer watch only ever asks the customer more often.
# A sentence that asks to watch for something vague, as in "stop anything suspicious", leaves the watch at normal and becomes an open question.
def read_session_sensitivity(instruction):
    sentences = split_sentences(instruction)
    if any(sentence_asks_to_watch_the_session(sentence) for sentence in sentences):
        return SessionReading(session_sensitivity = "high", open_questions = ())



    # Ask the customer what was meant when a sentence names something vague to watch for
    if any(VAGUE_WATCH_PATTERN.search(sentence) is not None for sentence in sentences):
        return SessionReading(
            session_sensitivity = "normal",
            open_questions = (
                "The instruction asks to watch for something suspicious, which could not be read as a wish about who is driving the session. "
                "Should one strong sign, such as a device you have never used, be enough to ask you?",
            ),
        )
    return SessionReading(session_sensitivity = "normal", open_questions = ())
