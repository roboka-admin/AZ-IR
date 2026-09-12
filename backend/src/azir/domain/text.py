"""Text normalization for Persian/Arabic script (ADR-0007).

PostgreSQL's default text-search configuration is useless for Persian: it does not know about
ZWNJ (half-space), the Arabic/Persian letter variants, diacritics or the tatweel. Everything a
user might type is folded here, and the same folding is mirrored in SQL (see migration
``0003``) so that index and query agree.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

ZWNJ = "\u200c"
ZWJ = "\u200d"
TATWEEL = "\u0640"

_LETTER_MAP: Final[dict[str, str | int | None]] = {
    "\u064a": "\u06cc",  # ARABIC LETTER YEH      -> PERSIAN YEH
    "\u0649": "\u06cc",  # ALEF MAKSURA          -> PERSIAN YEH
    "\u0643": "\u06a9",  # ARABIC LETTER KAF     -> PERSIAN KEHEH
    "\u0629": "\u0647",  # TEH MARBUTA           -> HEH
    "\u06c0": "\u0647",  # HEH WITH YEH ABOVE    -> HEH
    "\u0623": "\u0627",  # ALEF WITH MADDA ABOVE -> ALEF
    "\u0625": "\u0627",  # ALEF WITH HAMZA BELOW -> ALEF
    "\u0622": "\u0627",  # ALEF WITH MADDA       -> ALEF
    "\u0671": "\u0627",  # ALEF WASLA            -> ALEF
    "\u0624": "\u0648",  # WAW WITH HAMZA        -> WAW
    "\u0626": "\u06cc",  # YEH WITH HAMZA        -> YEH
    "\u06be": "\u0647",  # HEH DOACHASHMEE       -> HEH
    "\u06d5": "\u0647",  # AE                    -> HEH (search fold)
    "\u06c7": "\u0648",  # U                     -> WAW
    "\u06c6": "\u0648",  # OE                    -> WAW
    "\u06c8": "\u0648",  # YU                    -> WAW
}

_DIGITS: Final[dict[str, str | int | None]] = {
    "\u06f0": "0", "\u06f1": "1", "\u06f2": "2", "\u06f3": "3", "\u06f4": "4",
    "\u06f5": "5", "\u06f6": "6", "\u06f7": "7", "\u06f8": "8", "\u06f9": "9",
    "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
    "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
}

_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670\u0653-\u0655\u0640]")
_DEFINITE_ARTICLE = re.compile(r"(?<=\s)ال(?=\w)")
_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[«»\"'`()\\[\]{}.,:;!؟?،\-_/\\|+=*&^%$#@~•·]")


def fold_letters(text: str) -> str:
    return text.translate(str.maketrans(_LETTER_MAP))


def normalize_fa(text: str, *, drop_article: bool = False, half_space: str = "space") -> str:
    """Canonical search form of a Persian/Arabic-script string.

    Order matters and is part of the contract: NFKC → remove diacritics/tatweel → unify
    ZWJ/ZWNJ → fold letter variants → digits → punctuation → whitespace → lowercase.

    ``half_space`` decides what a ZWNJ becomes: ``"space"`` (default) keeps words separable,
    ``"join"`` glues the two halves together, which is how many people type names without the
    half-space at all ("صفیالدین" for "صفی‌الدین"). Both forms are indexed (ADR-0007).
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = _DIACRITICS.sub("", out)
    joiner = "" if half_space == "join" else " "
    out = out.replace(ZWNJ, joiner).replace(ZWJ, joiner)
    out = fold_letters(out)
    out = out.translate(str.maketrans(_DIGITS))
    out = _PUNCT.sub(" ", out)
    if drop_article:
        out = _DEFINITE_ARTICLE.sub("", out)
    out = _WHITESPACE.sub(" ", out).strip().lower()
    return out


def normalize_any(text: str) -> str:
    """Normalization used for non-Persian scripts (Latin, Armenian, Georgian...)."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKD", text)
    out = "".join(ch for ch in out if not unicodedata.combining(ch))
    out = out.translate(str.maketrans(_DIGITS))
    out = _PUNCT.sub(" ", out)
    return _WHITESPACE.sub(" ", out).strip().lower()


def build_search_text(*parts: str | None, drop_article: bool = True) -> str:
    """Concatenate every searchable form of an entity into one folded string."""
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        folded = normalize_fa(part, drop_article=drop_article) or normalize_any(part)
        if folded and folded not in chunks:
            chunks.append(folded)
        # Half-space (ZWNJ) is written as space, nothing, or a real space depending on the
        # keyboard, so index the joined variant too: all three spellings then match.
        if ZWNJ in part or ZWJ in part:
            joined = normalize_fa(part, drop_article=drop_article, half_space="join")
            if joined and joined not in chunks:
                chunks.append(joined)
        raw = normalize_any(part)
        if raw and raw not in chunks:
            chunks.append(raw)
    return " ".join(chunks)


def score(query: str, haystack: str) -> float:
    """Cheap relevance score used by the fixtures driver and as a tie-breaker everywhere.

    0.0 .. 1.0; the PostGIS adapter uses ``pg_trgm`` similarity plus ts_rank, but the ordering
    contract (exact prefix > prefix > substring > token) is the same.
    """
    q = normalize_fa(query, drop_article=True)
    if not q or not haystack:
        return 0.0
    h = normalize_fa(haystack, drop_article=True)
    if h == q:
        return 1.0
    if h.startswith(q):
        return 0.9
    tokens = h.split()
    if any(t == q for t in tokens):
        return 0.85
    if any(t.startswith(q) for t in tokens):
        return 0.75
    if q in h:
        return 0.6
    return 0.0


def snippet(text: str, query: str, width: int = 140) -> str:
    if not text:
        return ""
    q = normalize_fa(query)
    idx = normalize_fa(text).find(q)
    if idx < 0:
        return text[:width].strip() + ("…" if len(text) > width else "")
    # Map the normalized index back approximately onto the original text.
    start = max(0, min(idx, len(text) - 1) - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def has_persian(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def script_of(text: str) -> str:
    """Coarse BCP-47 script subtag inference (used to tag name variants)."""
    if not text:
        return "Latn"
    if has_persian(text):
        return "Arab"
    if any("\u0530" <= ch <= "\u058f" for ch in text):
        return "Armn"
    if any("\u10a0" <= ch <= "\u10ff" or "\u2d00" <= ch <= "\u2d2f" for ch in text):
        return "Geor"
    if any("\u0400" <= ch <= "\u04ff" for ch in text):
        return "Cyrl"
    return "Latn"
