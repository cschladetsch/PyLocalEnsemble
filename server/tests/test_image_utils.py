import pytest
from image import clean_tags, apply_exposure_rules


# ── clean_tags ────────────────────────────────────────────────────────────────

def test_clean_tags_removes_exact_duplicates():
    result = clean_tags("nude, long hair, nude, blue eyes")
    tags = [t.strip() for t in result.split(",")]
    assert tags.count("nude") == 1


def test_clean_tags_weighted_wins_over_plain():
    result = clean_tags("nude, (nude:1.3), blue eyes")
    assert "(nude:1.3)" in result
    assert result.count("nude") == 1


def test_clean_tags_preserves_first_occurrence_order():
    result = clean_tags("(sitting:1.4), armchair, blue eyes, armchair")
    tags = [t.strip() for t in result.split(",")]
    assert tags[0] == "(sitting:1.4)"
    assert tags.count("armchair") == 1


def test_clean_tags_strips_whitespace():
    result = clean_tags("  nude ,  long hair  ,  nude  ")
    for tag in result.split(", "):
        assert tag == tag.strip()


def test_clean_tags_skips_empty_segments():
    result = clean_tags("nude,,blue eyes,")
    assert "" not in [t.strip() for t in result.split(",")]


def test_clean_tags_single_tag():
    assert clean_tags("nude") == "nude"


def test_clean_tags_preserves_multi_word_weighted():
    result = clean_tags("(long blonde hair:1.2), blue eyes, (long blonde hair:1.2)")
    assert result.count("long blonde hair") == 1


# ── apply_exposure_rules ─────────────────────────────────────────────────────

def test_exposure_rules_pov_injected():
    prompt, _ = apply_exposure_rules("show from my pov", "nude", "")
    assert "pov" in prompt.lower()


def test_exposure_rules_first_person_injected():
    prompt, _ = apply_exposure_rules("from first person view", "nude", "")
    assert "pov" in prompt.lower() or "first person" in prompt.lower()


def test_exposure_rules_from_behind_injected():
    prompt, _ = apply_exposure_rules("view from behind", "nude", "")
    assert "behind" in prompt.lower()


def test_exposure_rules_no_match_returns_prompt_unchanged():
    original = "nude, sitting, armchair"
    prompt, neg = apply_exposure_rules("she looks beautiful", original, "ugly")
    assert prompt == original
    assert neg == "ugly"


def test_exposure_rules_negative_passthrough():
    _, neg = apply_exposure_rules("normal text", "nude", "blurry, deformed")
    assert neg == "blurry, deformed"


# ── nudity keyword detection (via generate_image logic) ──────────────────────

def test_nudity_keywords_cover_variants():
    """Spot-check that the explicit_keywords list catches common variants."""
    from image import generate_image
    import inspect
    src = inspect.getsource(generate_image)
    for kw in ("nudity", "fully nude", "naked", "topless"):
        assert kw in src, f"Missing nudity keyword: {kw!r}"
