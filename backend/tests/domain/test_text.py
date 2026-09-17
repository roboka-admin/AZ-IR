from __future__ import annotations

import pytest

from azir.domain.text import build_search_text, normalize_fa, score, script_of, snippet


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("بقعهٔ شيخ صفی‌الدین", "بقعه شیخ صفی الدین"),      # teh marbuta, arabic yeh, ZWNJ
        ("مسجدِ جامع تبريز", "مسجد جامع تبریز"),             # diacritics, arabic yeh
        ("الکوفة", "الکوفه"),                                # teh marbuta folded for Persian search
        ("تبریز ۱۲۳۴", "تبریز 1234"),                        # persian digits
        ("Tābrīz", "tābrīz"),
    ],
)
def test_normalize_fa(raw: str, expected: str) -> None:
    assert normalize_fa(raw) == expected


def test_zwnj_becomes_a_space() -> None:
    """Documented normalizer contract (ADR-0007): ZWNJ -> space, so tokens stay separable."""
    assert normalize_fa("می‌رود") == "می رود"


def test_zwnj_variants_are_searchable_equivalents() -> None:
    """The compact form is indexed too, so all three spellings match one another."""
    blob = build_search_text("بقعهٔ شیخ صفی‌الدین اردبیلی")
    for query in ("صفی‌الدین", "صفی الدین", "صفیالدین"):
        assert score(normalize_fa(query), blob) > 0.5, query


def test_glued_query_matches_the_raw_name_form() -> None:
    """Repositories score against the stored name, not the search blob (ADR-0007).

    A reader who never types the half-space must still find "صفی‌الدین اردبیلی".
    """
    name = "صفی‌الدین اردبیلی"
    for query in ("صفی‌الدین", "صفی الدین", "صفیالدین", "اردبیلی", "صفی"):
        assert score(query, name) > 0.5, query


def test_build_search_text_covers_all_scripts() -> None:
    blob = build_search_text("تبریز", "Tabriz", "Təbriz", "تیریز")
    assert "تبریز" in blob and "tabriz" in blob and "təbriz" in blob


def test_score_ordering_contract() -> None:
    assert score("تبریز", "تبریز") == 1.0
    assert score("تبر", "تبریز") > score("بری", "تبریز") > score("zzz", "تبریز")
    assert score("صفوی", "دودمان صفویان") > 0.5


def test_script_detection() -> None:
    assert script_of("تبریز") == "Arab"
    assert script_of("Tabriz") == "Latn"
    assert script_of("Թավրիզ") == "Armn"
    assert script_of("Тәбриз") == "Cyrl"


def test_snippet_stays_bounded() -> None:
    text = "شاه اسماعیل در تبریز تاج‌گذاری کرد و دولت صفوی را بنیان گذاشت."
    assert len(snippet(text, "تبریز", width=20)) <= 24
