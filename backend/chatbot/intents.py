"""Intent matching for the support chatbot.

DESIGN NOTE — why there is no language model here.

The brief says "never hallucinate or invent information". A generative model
can always invent, no matter how it's prompted; the best you get is that it
does so less often. This resolver instead maps a question to a *known
answer* — an FAQ row, or a specific database query — and returns "I don't
know, here's support" when nothing matches.

That makes fabrication structurally impossible rather than merely
discouraged, which is the right trade for a support bot whose answers users
will act on (prices, listing status, account details).
"""
import re

# ---------------------------------------------------------------------
# User-specific intents. Each maps to a handler in service.py that runs a
# scoped database query. Patterns are deliberately narrow: a false match
# here means answering the wrong question with real-looking data, which is
# worse than falling through to the FAQ search.
# ---------------------------------------------------------------------
# "How do I delete my listing?" is a how-to question (FAQ), while "show me
# my listings" is a data request. They share most of their words, so a
# how-to phrasing disqualifies the user-data path outright — answering the
# wrong one means showing real data in response to a question about
# procedure, which reads as a non sequitur.
HOWTO_PREFIX = re.compile(
    r"\b(how (do|can|to)|where (do|can)|what (are|is) the (steps|process)|"
    r"steps to|guide|tutorial)\b"
)

# A question about a problem or a change is also a how-to question, even
# with "my" in it: "my listing is not showing", "why was my ad removed",
# "change my phone number". Answering those with the user's data (a list of
# their listings, their phone number) misses the point.
NOT_A_LOOKUP = re.compile(
    r"\b(why|not|isn t|isnt|can t|cant|cannot|don t|dont|doesn t|doesnt|"
    r"won t|wont|nahi|change|update|edit|delete|remove|removed|hide|category|"
    r"read|private|privacy|monitored|safe|secure|wants?|asking|asks|asked|share)\b"
)

USER_INTENTS = [
    ("my_listings", [
        r"\bmy (products?|listings?|ads?|items?)\b",
        r"\bwhat (products?|listings?|ads?|items?) (have|did) i\b",
        r"\bwhat (have|did) i (list|post|sell)",
        r"\b(show|list|see|view) (me )?my (products?|listings?|ads?|items?)",
        r"\bhow many (products?|listings?|ads?|items?) (have|did) i\b",
    ]),
    ("my_active_listings", [
        r"\b(active|live) listings?\b",
        r"\bwhich of my listings? (are|is) (currently )?active\b",
    ]),
    ("my_sold_listings", [
        r"\b(sold|completed) listings?\b",
        r"\bwhat have i sold\b",
    ]),
    ("latest_listing_status", [
        r"\bstatus of my (latest|last|newest|recent)\b",
        r"\bmy (latest|last|newest) (listing|product|ad)\b",
    ]),
    ("my_email", [
        r"\bmy (registered )?e-?mail\b",
        r"\bwhat('s| is) my e-?mail\b",
    ]),
    ("my_account", [
        r"\bmy (account|profile) (info|information|details)\b",
        r"\bwhat information is (associated with|on) my account\b",
        r"\bmy (phone|number|pincode|location)\b",
    ]),
    # Only questions about the user's OWN messages. The bare words "chat" or
    # "message" used to match here too, so "are chats private?" or "can I
    # report someone from chat" got a message count instead of an answer.
    ("my_inquiries", [
        r"\b(inquir|enquir)(y|ies)\b",
        r"\bmy (messages?|chats?|inbox)\b",
        r"\b(new|unread|any) (messages?|chats?)\b",
        r"\bhow many (messages?|chats?)\b",
        r"\b(did|have|has) (i|anyone|someone|anybody) (got|get|gotten|receive|received|message|messaged|contact|contacted)\b",
        r"\bhas anyone (contacted|messaged|asked)\b",
    ]),
    ("my_emi_applications", [
        r"\bmy (emi|loan) (status|applications?|requests?)\b",
        r"\b(show|check|see|view|track) (me )?my (emi|loan)s?\b",
        r"^my emis?$",
        r"\bemi (status|application)\b",
        r"\bloan (status|application)\b",
        r"\bstatus of my (emi|loan)\b",
        r"\bis my (emi|loan)( application)? (approved|accepted|rejected|sanctioned|done)\b",
    ]),
    ("my_saved", [
        r"\b(saved|favourite|favorite|wishlist)\b",
    ]),
    ("listing_price", [
        r"\b(price|cost) of my\b",
    ]),
]

# Words carrying no retrieval value — dropped before scoring so "how do i
# post a product" matches on "post"/"product", not "how"/"do"/"i".
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "do", "does",
    "did", "how", "what", "when", "where", "which", "who", "why", "can", "i",
    "me", "my", "you", "your", "to", "of", "in", "on", "for", "with", "and",
    "or", "it", "this", "that", "there", "here", "please", "help", "would",
    "should", "could", "will", "get", "got", "have", "has", "if", "at", "as",
    "from", "about", "any", "some",
    # Hinglish (Hindi typed in English letters): question and filler words
    # only. Words that carry meaning ("nahi", "paise", "bechna") are kept.
    "kaise", "kese", "kaisa", "kya", "kyaa", "hai", "hain", "he", "ho", "hota",
    "hoti", "hote", "ka", "ke", "ki", "ko", "se", "pe", "par", "mein", "mai",
    "main", "mujhe", "muje", "mera", "meri", "mere", "apna", "apni", "apne",
    "aur", "ya", "yeh", "ye", "woh", "wo", "kar", "karu", "karun", "karna",
    "karni", "karein", "kare", "karo", "kr", "skta", "sakta", "sakti",
    "sakte", "raha", "rahi", "rahe", "tha", "thi", "the", "bhi", "koi",
    "kuch", "kab", "kahan", "kaha", "kyun", "kyu", "kyon", "kaun", "liye",
    "lie", "wala", "wali", "wale", "ji", "bhai", "plz", "pls", "batao",
    "bataye", "bataiye", "btao", "chahiye", "chahta", "chahti", "kaunse",
    "kaunsa", "konsa", "konse", "kitna", "kitne", "kitni",
}


def normalise(text):
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


# Marketplace words that mean the same thing to a user but not to a string
# comparison. "How do I post a listing?" must reach "How do I list an item
# for sale?" — those share no words at all.
SYNONYM_GROUPS = [
    # "create", "add" and "upload" used to be in this group too, which made
    # "create an account", "add photos" and "upload documents" all count as
    # selling ("my account is blocked from posting" -> "How do I create an
    # account?"). Selling FAQs list those words in their own keywords.
    {"sell", "post", "list", "advertise", "publish"},
    {"buy", "purchase", "order", "get"},
    # NOTE: no generic-noun group here (listing/ad/product/item).
    # Those words appear in almost every FAQ, so treating them as
    # equivalent made unrelated entries match — "promote my ad" scored
    # highest against "How do I buy a product". Stemming already handles
    # listing/listings; it's the VERBS that actually discriminate.
    {"emi", "instalment", "installment", "finance", "financing", "loan", "credit"},
    {"delete", "remove", "cancel", "takedown"},
    {"edit", "change", "update", "modify"},
    {"password", "login", "signin", "credential"},
    # scam/fraud deliberately NOT grouped with safe/safety: "is this safe?"
    # and "report a scam" are different intents, and grouping them made
    # "how do I report a scam" match the safety advice instead of the
    # reporting instructions.
    {"safe", "safety", "secure"},
    {"scam", "fraud", "fake", "suspicious"},
    {"report", "complain", "complaint", "flag"},
    {"message", "chat", "contact", "inquiry", "enquiry"},
    {"promote", "boost", "feature", "highlight"},
]
SYNONYM_INDEX = {}
for _g in SYNONYM_GROUPS:
    for _w in _g:
        SYNONYM_INDEX.setdefault(_w, set()).update(_g)


def stem(word):
    """Crude suffix stripping — enough to make listing/listings/list and
    mobiles/mobile the same token.

    The "es" rule only applies after s/x/z/ch/sh (buses, boxes, watches).
    Applying it generally turned "mobiles" into "mobil" while "mobile"
    stayed "mobile", so the two never matched — which is exactly how
    "how many mobiles near me" ended up unanswerable.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es") and word[-3:-2] in ("s", "x", "z") or \
       (len(word) > 5 and word.endswith(("ches", "shes"))):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        # Applied again so "listings" -> "listing" -> "list" reaches the
        # same stem as "listing" does directly. One pass left them different.
        return stem(word[:-1])
    return word


def tokens(text, expand=False):
    """Token set for scoring. With expand=True, each token also contributes
    its synonyms — used on the FAQ side so one FAQ covers many phrasings
    without listing every variant in its keywords."""
    raw = {w for w in normalise(text).split() if w and w not in STOPWORDS and len(w) > 1}
    out = set()
    for w in raw:
        out.add(w)
        out.add(stem(w))
        if expand:
            for syn in SYNONYM_INDEX.get(w, ()):
                out.add(syn)
                out.add(stem(syn))
    return out


def match_user_intent(message):
    """Returns an intent name if the message is clearly about the user's own
    data, else None. Checked before the FAQ search because "how do I delete
    my listing" (FAQ) and "show me my listings" (data) are different
    questions that share most of their words."""
    text = normalise(message)

    # A how-to phrasing is always a procedure question, never a data lookup.
    if HOWTO_PREFIX.search(text) or NOT_A_LOOKUP.search(text):
        return None

    for intent, patterns in USER_INTENTS:
        for pat in patterns:
            if re.search(pat, text):
                return intent
    return None


def _concept(word):
    """One word the user typed, with its stem and synonyms: the forms that
    count as a match for it."""
    out = {word, stem(word)}
    for w in (word, stem(word)):
        for syn in SYNONYM_INDEX.get(w, ()):
            out.add(syn)
            out.add(stem(syn))
    return out


def score_faq(message, faq):
    """Overlap score between the question and one FAQ row.

    Deliberately simple and explainable: if support later asks "why did the
    bot answer that?", the answer is visible in the numbers rather than
    buried in a model's weights.

    Each word the user typed counts ONCE, whether it matches directly, by
    stem or by synonym. It used to count every expanded form, so "upload"
    (one word, 8 synonyms) outweighed "RC", "insurance" and "PUC" together,
    and "what can I sell" matched "How do I create an account?" through
    the synonym "create".
    """
    words = list(dict.fromkeys(
        w for w in normalise(message).split()
        if w and w not in STOPWORDS and len(w) > 1))
    if not words:
        return 0.0

    kw = getattr(faq, "keywords", "") or ""
    q_raw, q_exp = tokens(faq.question), tokens(faq.question, expand=True)
    k_raw, k_exp = tokens(kw), tokens(kw, expand=True)
    a_raw = tokens(faq.answer)

    def match(word, raw, expanded):
        # The exact word beats the same word with a different ending, which
        # beats a synonym. Otherwise "what can I sell" ties between "What can
        # I sell?" and "How many photos can I add?" (add = sell), and "where
        # are my deals" between "Where can I see my deals?" and "Can I cancel
        # a deal?".
        if word in raw:
            return 1.0
        if stem(word) in raw:
            return 0.95
        if _concept(word) & expanded:
            return 0.8
        return 0.0

    # The question text is the strongest signal, keywords next, and the
    # answer body weakest — an incidental word deep in an answer shouldn't
    # outrank a direct question match.
    score = 0.0
    for w in words:
        score += (3.0 * match(w, q_raw, q_exp)
                  + 2.0 * match(w, k_raw, k_exp)
                  + 0.5 * match(w, a_raw, a_raw))
    # Normalise by question length so a short FAQ isn't penalised against a
    # verbose one.
    return score / len(words)


# Below this, a match is treated as coincidence and the bot says it doesn't
# know. Tuned so a single shared common word can't trigger an answer.
FAQ_CONFIDENCE_THRESHOLD = 0.9
