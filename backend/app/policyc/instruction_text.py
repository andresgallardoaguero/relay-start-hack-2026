# Script: instruction_text.py
# Purpose: Share the text helpers of the instruction readers, which build phrase patterns and split a text into sentences, clauses and words
# Author: Andrés Gallardo
# Date: September 2026

import re









#### Step 1: Build the patterns of phrases ####

# Build the pattern of one phrase, where any whitespace may separate its words.
# A word boundary guards each end that is a letter or a digit, so "under" is not found inside "understand".
def build_phrase_pattern_text(phrase):
    escaped_phrase = "\\s+".join(re.escape(word) for word in phrase.split(" "))
    leading_boundary = "\\b" if phrase[0].isalnum() else ""
    trailing_boundary = "\\b" if phrase[-1].isalnum() else ""
    return leading_boundary + escaped_phrase + trailing_boundary



# Build the pattern text that finds any of the phrases, longest first, so "swiss francs" wins over "francs"
def build_alternation_text(phrases):
    phrases_longest_first = sorted(phrases, key = len, reverse = True)
    return "(?:" + "|".join(build_phrase_pattern_text(phrase) for phrase in phrases_longest_first) + ")"



# Build the compiled pattern that finds any of the phrases in upper or lower case
def build_alternation_pattern(phrases):
    return re.compile(build_alternation_text(phrases), re.IGNORECASE)









#### Step 2: Split a text into clauses ####

# Find the places where one clause ends, which are sentence punctuation, a comma with a space and the words "and" and "but".
# A comma takes a following "and" or "but" with it, so no clause starts with that word.
# The word "or" never ends a clause, because "CHF 20 or less" has to stay together.
CLAUSE_BREAK_PATTERN = re.compile("[.!?;:](?=\\s|$)|,\\s+(?:(?:and|but)\\s+)?|\\s+and\\s+|\\s+but\\s+", re.IGNORECASE)



# Report whether two spans of text overlap
def spans_overlap(first_span, second_span):
    return first_span[0] < second_span[1] and second_span[0] < first_span[1]



# Find the span of every clause break of a text.
# A break that overlaps a protected span is left out, so neither the point in "Fr. 50" nor a comma in a number splits an amount.
def find_clause_break_spans(text, protected_spans = ()):
    return tuple(
        break_match.span()
        for break_match in CLAUSE_BREAK_PATTERN.finditer(text)
        if not any(spans_overlap(break_match.span(), protected_span) for protected_span in protected_spans)
    )



# Split a text into its clauses without empty clauses, each as a pair of the break that stands before it and the clause itself.
# The break of the first clause is empty. The break tells a reader whether a clause was joined with "and" or set apart with "but".
def split_clauses_with_breaks(text):
    clause_break_spans = find_clause_break_spans(text)
    clause_starts = (0,) + tuple(break_end for break_start, break_end in clause_break_spans)
    clause_ends = tuple(break_start for break_start, break_end in clause_break_spans) + (len(text),)
    breaks_before = ("",) + tuple(text[break_start:break_end] for break_start, break_end in clause_break_spans)
    breaks_and_clauses = tuple(
        (break_before, text[clause_start:clause_end].strip())
        for break_before, clause_start, clause_end in zip(breaks_before, clause_starts, clause_ends)
    )
    return tuple((break_before, clause) for break_before, clause in breaks_and_clauses if clause != "")



# Split a text into its clauses, without the breaks between them and without empty clauses
def split_clauses(text):
    return tuple(clause for break_before, clause in split_clauses_with_breaks(text))









#### Step 3: Split a text into sentences ####

# Find the end of a sentence, which is a point, an exclamation mark or a question mark that whitespace or the end of the text follows.
# The point in "CHF 45.50" is followed by a digit, so the amount stays whole.
SENTENCE_END_PATTERN = re.compile("[.!?]+(?=\\s|$)")



# Split a text into its sentences, without the closing punctuation and without empty sentences
def split_sentences(text):
    sentences = tuple(sentence.strip() for sentence in SENTENCE_END_PATTERN.split(text))
    return tuple(sentence for sentence in sentences if sentence != "")









#### Step 4: Read the words of a text ####

# Find one word, which is a run of letters and digits
WORD_PATTERN = re.compile("[^\\W_]+")



# Reduce a word to a crude singular form, so "groceries" becomes "grocery" and "shoes" becomes "shoe".
# A short word and a word that ends in "ss" stay as they are, so "less" and "pass" are not cut.
def read_singular_form(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word



# Read the words of a text in lower case and in their singular form.
# Every hyphen becomes a space first, so "road-running" gives the two words "road" and "running".
def read_words(text):
    text_without_hyphens = text.lower().replace("-", " ")
    return tuple(read_singular_form(word) for word in WORD_PATTERN.findall(text_without_hyphens))









#### Step 5: Find a negated limit phrase ####

# List the negation words that can open a limit phrase, and the comparison words that can close it.
# A negated "more than" caps an amount, as in "no more than CHF 30". A negated "less than" sets a minimum, as in "no less than CHF 30".
# A negated "later than" speaks about a date and bounds no amount.
LIMIT_NEGATION_WORDS = ("no", "not", "never", "don't", "don’t")
NEGATED_UPPER_LIMIT_WORDS = ("more than", "over", "above", "exceed")
NEGATED_LOWER_LIMIT_WORDS = ("less than", "under", "below")
NEGATED_DATE_LIMIT_WORDS = ("later than",)



# Find a negation word, then at most two other words, then a comparison word, all as whole words in upper or lower case.
# It finds "no more than", "not exceed", "don't go over", "do not go over", "never pay more than" and "no later than".
# Only whitespace may separate the words, so a comma ends the search and "no upgrades, over the weekend" is not found.
# The words in between are kept as a group of their own, because they may name goods, as in "never buy shoes over CHF 100".
NEGATED_LIMIT_PATTERN = re.compile(
    "(?P<negation_word>" + build_alternation_text(LIMIT_NEGATION_WORDS) + ")"
    + "(?P<words_between>(?:\\s+[^\\W_]+(?:['’][^\\W_]+)?){0,2}?)"
    + "\\s+(?P<limit_word>" + build_alternation_text(NEGATED_UPPER_LIMIT_WORDS + NEGATED_LOWER_LIMIT_WORDS + NEGATED_DATE_LIMIT_WORDS) + ")",
    re.IGNORECASE,
)



# Read what the first negated limit phrase of a text says about an amount, which is "upper", "lower" or None.
# None means that the text holds no such phrase or that the phrase speaks about a date.
def read_negated_limit_kind(text):
    negated_limit = NEGATED_LIMIT_PATTERN.search(text)
    if negated_limit is None:
        return None
    limit_word = " ".join(negated_limit.group("limit_word").lower().split())
    if limit_word in NEGATED_UPPER_LIMIT_WORDS:
        return "upper"
    if limit_word in NEGATED_LOWER_LIMIT_WORDS:
        return "lower"
    return None
