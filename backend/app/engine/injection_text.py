# Script: injection_text.py
# Purpose: Find sentences in a text written by a shop that are aimed at a shopping agent and not at a buyer, with fixed patterns in English, German, French and Italian
# Author: Andrés Gallardo
# Date: September 2026

import re
import unicodedata
from dataclasses import dataclass

from app.engine.display import clean_shop_text









#### Step 1: Prepare the text ####

# State how many characters of a found sentence are kept, which is longer than a shop name because the customer has to recognize the whole sentence
SHOWN_SENTENCE_LENGTH = 160



# Split a text into sentences after a full stop, a question mark, an exclamation mark or a semicolon that is followed by whitespace, and at every line break
SENTENCE_BOUNDARY_PATTERN = re.compile("(?<=[.!?;])\\s+|[\\r\\n]+")



# Write a text in one form before any pattern looks at it. The compatibility form turns full-width letters, ligatures and similar look-alikes into plain letters,
# and invisible format characters such as a zero-width space are removed, so a word cannot be hidden from the patterns by the way it is typed.
def normalize_shop_text(text):
    text_in_compatibility_form = unicodedata.normalize("NFKC", text)
    return "".join(character for character in text_in_compatibility_form if unicodedata.category(character) != "Cf")



# Split a normalized text into its sentences, without the punctuation that ends them and without empty sentences
def split_into_sentences(normalized_text):
    sentences = (sentence.strip().rstrip(".;!?").strip() for sentence in SENTENCE_BOUNDARY_PATTERN.split(normalized_text))
    return tuple(sentence for sentence in sentences if sentence != "")









#### Step 2: Name the words the patterns share ####

# Join several pattern texts into one pattern that matches any of them, without regard to upper and lower case
def compile_any_of(*pattern_texts):
    return re.compile("|".join("(?:" + pattern_text + ")" for pattern_text in pattern_texts), re.IGNORECASE)



# Name an agent as a noun, in the four languages
AGENT_NOUN_TEXT = "\\b(?:agents?|agenten|agenti|agente|assistants?|assistent(?:en)?|assistenti|assistente|bots?|chatbots?)\\b"



# Name artificial intelligence. The short forms count in upper case only, because "ai" is an everyday word in Italian and in French.
AI_TOKEN_TEXT = (
    "(?:(?<!\\w)(?-i:AI|KI|IA|LLMs?)(?!\\w)"
    "|\\bartificial\\s+intelligence\\b|\\bk[üu]nstliche[rn]?\\s+intelligenz\\b|\\bintelligence\\s+artificielle\\b|\\bintelligenza\\s+artificiale\\b"
    "|\\blanguage\\s+models?\\b|\\bsprachmodell\\w*)"
)



# Name an automated buyer that is not called an agent, as in "automated systems" and "systèmes automatisés"
AUTOMATED_BUYER_TEXT = (
    "(?:\\bautomated\\s+(?:\\w+\\s+)?(?:systems?|buyers?|shoppers?|purchasers?|tools?|programs?|software|scripts?)\\b"
    "|\\bautomatisierte[rn]?\\s+(?:\\w+\\s+)?(?:systeme?|k[äa]ufer|programme?|software)\\b"
    "|\\b(?:syst[èe]mes?|acheteurs?|programmes?|logiciels?)\\s+automatis[ée]s?\\b"
    "|\\b(?:sistem[ai]|acquirenti|programmi|software)\\s+automatizzat[oi]\\b)"
)



# Name whoever a note can be addressed to, which is an agent, artificial intelligence or an automated buyer
ADDRESSEE_TEXT = "(?:" + AGENT_NOUN_TEXT + "|" + AI_TOKEN_TEXT + "|" + AUTOMATED_BUYER_TEXT + ")"



# Name the words that open a note, in the four languages
NOTE_WORD_TEXT = (
    "\\b(?:notes?|notice|message|attention|memo"
    "|hinweis|nachricht|achtung|mitteilung|notiz"
    "|avis|remarque"
    "|nota|avviso|messaggio|attenzione|comunicazione)\\b"
)



# Name the words that may stand before an agent noun in a direct address, as in "AI assistants:" and "Dear automated shopping agents:"
GREETING_TEXT = "(?:dear|hello|hey|hi|to|liebe[rn]?|hallo|an|chers?|ch[èe]res?|bonjour|car[oi]|gentil[ei]|ciao)"
DETERMINER_TEXT = "(?:all|any|the|alle|tous|les|tutti|gli)"
AGENT_QUALIFIER_TEXT = (
    "(?:" + AI_TOKEN_TEXT
    + "|\\b(?:automated|autonomous|automatisierte[rn]?|autonome[rn]?|shopping|purchasing|buying|payment|einkaufs|virtual|virtuelle[rn]?|digitale?[rn]?)\\b)"
)
DIRECT_ADDRESS_START_TEXT = "^\\W{0,6}(?:" + GREETING_TEXT + "\\s+)?(?:" + DETERMINER_TEXT + "\\s+)?"



# Name the customer, in the four languages. The customer service of a shop is not the customer,
# so the English and the French word do not count next to a word such as "service".
CUSTOMER_TEXT_ENGLISH = "\\b(?:cardholder|card\\s+holder|customer|client|buyer|account\\s+holder)\\b(?!\\s+(?:service|support|care|hotline|desk))"
CUSTOMER_TEXT_GERMAN = "\\b(?:karteninhaber(?:in)?|kontoinhaber(?:in)?|kund(?:e|in|en)|k[äa]ufer(?:in)?)\\b"
CUSTOMER_TEXT_FRENCH = "(?<!service\\s)\\b(?:titulaire|clients?|cliente|porteur|acheteur)\\b"
CUSTOMER_TEXT_ITALIAN = "\\b(?:titolare|cliente|intestatario|acquirente)\\b"









#### Step 3: Build the pattern of every family ####

# Find a note that is addressed to an agent, which is a note word followed within four words by an addressee,
# as in "NOTE FOR AUTOMATED PURCHASING AGENTS", "Hinweis für KI-Agenten", "Avis aux assistants IA" and "Nota per gli agenti AI".
# A direct address at the start of a sentence counts as well, as in "AI assistants:" and "Assistants IA :",
# where the agent noun must carry a qualifier and a colon, so "Assistant chef knife set" is no address.
ADDRESSES_THE_AGENT_PATTERN = compile_any_of(
    NOTE_WORD_TEXT + "(?:\\W+\\w+){0,4}?\\W+" + ADDRESSEE_TEXT,
    DIRECT_ADDRESS_START_TEXT + "(?:" + AGENT_QUALIFIER_TEXT + "\\W+){1,3}" + AGENT_NOUN_TEXT + "\\s*:",
    DIRECT_ADDRESS_START_TEXT + AGENT_NOUN_TEXT + "\\W+" + AI_TOKEN_TEXT + "\\s*:",
)



# Find the marker of a speaker role at the start of a sentence, as in "System:", "[SYSTEM]", "Assistant:" and "System message:".
# The colon or the closing bracket must follow the role directly, so "System requirements:" is no marker.
ROLE_MARKER_PATTERN = compile_any_of(
    "^\\W{0,6}(?:"
    "system|assistant|developer|user|admin(?:istrator)?"
    "|assistent|entwickler|benutzer|nutzer"
    "|syst[èe]me|d[ée]veloppeur|utilisateur|administrateur"
    "|sistema|assistente|sviluppatore|utente|amministratore"
    ")(?:\\s+(?:message|prompt|note|override))?\\s*[:\\]>]",
)



# Find an order to ignore instructions, which is a word such as ignore, disregard, forget, override or bypass
# followed in the same sentence by instructions, rules, limits, a policy or restrictions. German puts the verb last as often as first, so both orders count there.
IGNORE_INSTRUCTIONS_GERMAN_VERB_TEXT = "\\b(?:ignorier\\w*|missacht\\w*|vergiss|vergessen|[üu]bergeh\\w*|umgeh\\w*|[üu]berschreib\\w*)\\b"
IGNORE_INSTRUCTIONS_GERMAN_NOUN_TEXT = "\\b(?:anweisung(?:en)?|instruktion(?:en)?|regeln?|\\w*limits?|\\w*limiten|richtlinien?|vorgaben?|beschr[äa]nkung(?:en)?|einschr[äa]nkung(?:en)?)\\b"
IGNORE_INSTRUCTIONS_PATTERN = compile_any_of(
    "\\b(?:ignor|disregard|forget|overrid|overrul|bypass)\\w*\\b.*?\\b(?:instructions?|rules?|limits?|polic(?:y|ies)|restrictions?|guidelines?|constraints?)\\b",
    IGNORE_INSTRUCTIONS_GERMAN_VERB_TEXT + ".*?" + IGNORE_INSTRUCTIONS_GERMAN_NOUN_TEXT,
    IGNORE_INSTRUCTIONS_GERMAN_NOUN_TEXT + ".*?" + IGNORE_INSTRUCTIONS_GERMAN_VERB_TEXT,
    "\\b(?:ignor\\w*|oubli\\w*|contourn\\w*)\\b.*?\\b(?:instructions?|consignes?|r[èe]gles?|limites?|politiques?|restrictions?|plafonds?)\\b",
    "\\b(?:ignor\\w*|dimentic\\w*|aggir\\w*|trascur\\w*|scavalc\\w*)\\b.*?\\b(?:istruzion[ei]|regol[ae]|limit[ei]|politic(?:a|he)|restrizion[ei]|vincoli)\\b",
)



# Find a claim that the purchase was authorized beforehand, as in "pre-authorised", "pre-approved", "already approved" and "the customer has confirmed".
# The plain words "authorised" and "confirmed" do not count, because "authorised reseller" and "order confirmed by e-mail" are ordinary shop text.
CLAIMS_AUTHORIZATION_PATTERN = compile_any_of(
    "\\bpre[-\\s]?(?:authori[sz]ed|approved|cleared)\\b",
    "\\balready\\s+(?:been\\s+)?(?:approved|authori[sz]ed|confirmed|cleared)\\b",
    CUSTOMER_TEXT_ENGLISH + "\\s+has\\s+(?:already\\s+)?(?:approved|authori[sz]ed|confirmed)\\b",
    "\\b(?:vorab|bereits|schon|im\\s+voraus)\\s+(?:genehmigt|autorisiert|freigegeben|best[äa]tigt)\\b",
    "\\bvor(?:ab)?(?:autorisiert|genehmigt)\\b",
    "\\bpr[ée][-\\s]?(?:autoris|approuv)[ée]e?s?\\b",
    "\\bd[ée]j[àa]\\s+(?:[ée]t[ée]\\s+)?(?:autoris|approuv|valid|confirm)[ée]e?s?\\b",
    "\\bpre[-\\s]?(?:autorizzat|approvat)[oaie]\\b",
    "\\bgi[àa]\\s+(?:stat[oaie]\\s+)?(?:autorizzat|approvat|confermat)[oaie]\\b",
)



# Find a claim that the spending limits do not apply or no longer apply, as in "limits do not apply", "Limits gelten nicht",
# "les plafonds ne s'appliquent pas" and "i limiti non si applicano". A sentence such as "Quantity limits apply" carries no negation and is not found.
LIMITS_DO_NOT_APPLY_PATTERN = compile_any_of(
    "\\b(?:limits?|caps?|budgets?|ceilings?)\\b.{0,40}?\\b(?:(?:do|does)\\s+not|don['’]t|doesn['’]t|no\\s+longer|never)\\s+appl(?:y|ies)\\b",
    "\\b(?:limits?|caps?|budgets?|ceilings?)\\b.{0,40}?\\b(?:is|are|was|were|has\\s+been|have\\s+been)\\s+(?:waived|lifted|suspended|removed|disabled)\\b",
    "\\b(?:\\w*limits?|\\w*limiten|obergrenzen?|betragsgrenzen?)\\b.{0,40}?\\b(?:gelten|gilt|greifen|greift)\\b.{0,40}?\\bnicht\\b",
    "\\b(?:gelten|gilt)\\s+keine\\s+(?:\\w+\\s+)?(?:\\w*limits?|\\w*limiten|obergrenzen|betragsgrenzen)\\b",
    "\\b(?:limites?|plafonds?)\\b.{0,40}?\\bne\\s+s['’]appliquent?\\s+(?:pas|plus)\\b",
    "\\b(?:limit[ei]|massimali|tetti)\\b.{0,40}?\\bnon\\s+(?:si\\s+applic(?:a|ano)|valgono|vale)\\b",
)



# Find an order to approve at once or without checks, as in "approve this", "may be approved", "without further checks", "skip the checks",
# "ohne weitere Prüfung", "sans vérification" and "senza ulteriori verifiche"
ORDERS_AN_APPROVAL_PATTERN = compile_any_of(
    "\\bapprove\\s+(?:this|the|it|immediately|now|at\\s+once|right\\s+away)\\b",
    "\\b(?:may|can|should|must|shall)\\s+be\\s+approved\\b",
    "\\bwithout\\s+(?:further|additional|any|more)\\s+(?:checks?|verification|confirmation|review)\\b",
    "\\bskip\\s+(?:the\\s+|all\\s+|any\\s+)?(?:checks?|verification|confirmation|review)\\b",
    "\\b(?:complete|process|authori[sz]e|confirm)\\s+(?:this|the)\\s+(?:payment|purchase|transaction|order)\\s+(?:now|immediately|at\\s+once|right\\s+away)\\b",
    "\\b(?:sofort|umgehend|unverz[üu]glich)\\s+(?:genehmigen|freigeben)\\b",
    "\\bohne\\s+(?:weitere|zus[äa]tzliche|jede)\\s+(?:pr[üu]fung(?:en)?|kontrollen?|r[üu]ckfragen?|best[äa]tigung)\\b",
    "\\b(?:genehmige|genehmigen\\s+sie|gib|geben\\s+sie)\\s+(?:diese[ns]?|die|den)\\s+(?:zahlung|bestellung|transaktion|kauf)\\b",
    "\\bpr[üu]fung(?:en)?\\s+[üu]berspringen\\b",
    "\\bsans\\s+(?:autre\\s+|aucune\\s+|plus\\s+de\\s+)?v[ée]rifications?\\b",
    "\\b(?:approuv|autoris|valid)(?:e|ez|er)\\s+(?:ce|cet|cette|le|la)\\s+(?:paiement|achat|commande|transaction)\\b",
    "\\bapprouv(?:e|ez|er)\\s+(?:maintenant|imm[ée]diatement|tout\\s+de\\s+suite)\\b",
    "\\bsenza\\s+(?:ulteriori|altre|alcuna)\\s+(?:verifiche|verifica|controlli|controllo|conferme|conferma)\\b",
    "\\b(?:approva|autorizza)(?:re|te)?\\s+(?:quest[oa]|il|la)\\s+(?:pagamento|acquisto|ordine|transazione)\\b",
    "\\bapprova(?:re|te)?\\s+(?:subito|ora|adesso|immediatamente)\\b",
)



# Find an urge to pay at once, as in "pay now", "sofort bezahlen", "payez maintenant" and "paga subito".
# A shop may write this to a buyer as well, as in "Pay now and save 10%", so the family is weak and counts only next to another family.
URGES_PAYMENT_PATTERN = compile_any_of(
    "\\bpay\\s+(?:now|immediately|at\\s+once|right\\s+away)\\b",
    "\\b(?:sofort|umgehend|unverz[üu]glich)\\s+(?:be)?zahlen\\b",
    "\\bpaye[zr]?\\s+(?:maintenant|imm[ée]diatement|tout\\s+de\\s+suite)\\b",
    "\\bpaga(?:re|te)?\\s+(?:subito|ora|adesso|immediatamente)\\b",
)



# Find a claim that the customer cannot be asked, as in "the cardholder is unavailable" and "do not ask the customer"
CUSTOMER_UNAVAILABLE_PATTERN = compile_any_of(
    CUSTOMER_TEXT_ENGLISH + ".{0,30}?\\b(?:unavailable|unreachable|not\\s+available|not\\s+reachable|(?:cannot|can\\s+not|can['’]?t)\\s+be\\s+reached)\\b",
    "\\b(?:do\\s+not|don['’]?t|never|no\\s+need\\s+to)\\s+(?:ask|contact|notify|alert|disturb|bother|consult|involve)\\s+(?:the\\s+)?" + CUSTOMER_TEXT_ENGLISH,
    CUSTOMER_TEXT_GERMAN + ".{0,30}?\\b(?:nicht\\s+erreichbar|nicht\\s+verf[üu]gbar|unerreichbar|abwesend)\\b",
    CUSTOMER_TEXT_GERMAN + ".{0,20}?\\bnicht\\s+(?:fragen|kontaktieren|benachrichtigen|st[öo]ren)\\b",
    "\\bfrag\\w*\\s+(?:sie\\s+)?(?:den|die)\\s+" + CUSTOMER_TEXT_GERMAN + "\\s+nicht\\b",
    CUSTOMER_TEXT_FRENCH + ".{0,40}?\\b(?:indisponible|injoignable|pas\\s+disponible|pas\\s+joignable)\\b",
    "\\bne\\s+(?:pas\\s+)?(?:demand|contact|d[ée]rang|consult|interrog)\\w+\\s+(?:pas\\s+|rien\\s+)?(?:au|aux|[àa]\\s+la|le|la)\\s+" + CUSTOMER_TEXT_FRENCH,
    CUSTOMER_TEXT_ITALIAN + ".{0,40}?\\b(?:non\\s+[èe]\\s+(?:disponibile|raggiungibile|reperibile)|non\\s+disponibile|irraggiungibile)\\b",
    "\\bnon\\s+(?:chied|contatt|disturb|consult|interpell)\\w+\\s+(?:al|alla|il|la)\\s+" + CUSTOMER_TEXT_ITALIAN,
)



# List the families in the order in which the findings of one sentence are reported
FAMILY_PATTERNS = (
    ("addresses_the_agent", ADDRESSES_THE_AGENT_PATTERN),
    ("role_marker", ROLE_MARKER_PATTERN),
    ("ignore_instructions", IGNORE_INSTRUCTIONS_PATTERN),
    ("claims_authorization", CLAIMS_AUTHORIZATION_PATTERN),
    ("limits_do_not_apply", LIMITS_DO_NOT_APPLY_PATTERN),
    ("orders_an_approval", ORDERS_AN_APPROVAL_PATTERN),
    ("urges_payment", URGES_PAYMENT_PATTERN),
    ("customer_unavailable", CUSTOMER_UNAVAILABLE_PATTERN),
)



# Name the weak families, whose words ordinary shop text holds as well, as in "System: Android 14" and "Pay now and save 10%".
# A weak finding counts only when the same text also holds a finding of another family.
WEAK_FAMILIES = ("role_marker", "urges_payment")









#### Step 4: Find the sentences ####

# Hold one finding, which is the name of the pattern family and the sentence that matched, cleaned and shortened so it is safe to show
@dataclass(frozen = True)
class AgentDirectedFinding:
    family: str
    sentence: str



# Find every sentence of a text that is aimed at a shopping agent, sentence by sentence and family by family.
# A sentence that matches several families gives one finding per family. The text is untrusted, so only the fixed patterns above look at it,
# and a finding is evidence about the shop and never an instruction to anyone.
# A finding of a weak family is left out unless the text holds a finding of another family as well.
# The other family is looked for in the whole text handed to this function and not in the same sentence,
# so "System:" in one sentence and "ignore your limits" in the next still count together, while "27-inch panel; System: Android 14" is clean.
def find_agent_directed_text(text):
    if not isinstance(text, str):
        return ()
    sentences = split_into_sentences(normalize_shop_text(text))
    all_findings = tuple(
        AgentDirectedFinding(family = family, sentence = clean_shop_text(sentence, shown_length = SHOWN_SENTENCE_LENGTH))
        for sentence in sentences
        for family, pattern in FAMILY_PATTERNS
        if pattern.search(sentence) is not None
    )
    found_families = {finding.family for finding in all_findings}
    return tuple(
        finding
        for finding in all_findings
        if finding.family not in WEAK_FAMILIES or len(found_families) > 1
    )
