"""Tests for image/prompt.py: structured template parsing and tag building."""
import pytest
from image.prompt import _parse_template, _build_tags, _detect_action, _detect_accessories, _CAMERA_MAP


# ── _parse_template ───────────────────────────────────────────────────────────

GOOD_TEMPLATE = """\
ACTION: reading book
BODY: hands
CAMERA: front view
POSE: sitting
EXTRA: looking down
SETTING: library
LIGHTING: soft lighting"""


def test_parse_clamps_to_four_words():
    raw = "ACTION: very slowly removing top garment carefully now"
    r = _parse_template(raw)
    assert len(r.get("ACTION", "").split()) <= 4


def test_parse_drops_prose_with_pronoun():
    raw = "EXTRA: fingers touching me gently\nSETTING: bedroom"
    r = _parse_template(raw)
    assert "EXTRA" not in r          # "me" triggers prose filter
    assert r.get("SETTING") == "bedroom"


def test_parse_missing_fields_returns_empty_dict():
    r = _parse_template("Nothing useful here.")
    assert r == {}


def test_parse_setting_meta_commentary_dropped():
    r = _parse_template("SETTING: somewhere with no specification\nLIGHTING: not specified but assumed")
    assert "SETTING" not in r
    assert "LIGHTING" not in r


def test_parse_setting_real_value_kept():
    r = _parse_template("SETTING: bedroom\nLIGHTING: soft lighting")
    assert r["SETTING"] == "bedroom"
    assert r["LIGHTING"] == "soft lighting"


def test_parse_trailing_period_stripped():
    raw = "POSE: standing."
    r = _parse_template(raw)
    assert r["POSE"] == "standing"


def test_parse_drops_apostrophe_tag():
    raw = "EXTRA: nature's hidden treasures visible\nSETTING: forest"
    r = _parse_template(raw)
    assert "EXTRA" not in r
    assert r.get("SETTING") == "forest"


def test_parse_drops_first_person_prose():
    raw = "POSE: kneeling I am standing"
    r = _parse_template(raw)
    assert "POSE" not in r


def test_parse_drops_or_uncertainty():
    raw = "POSE: kneeling or bent over"
    r = _parse_template(raw)
    assert "POSE" not in r


def test_parse_strips_dangling_preposition():
    raw = "SETTING: mossy forest clearing with"
    r = _parse_template(raw)
    assert r.get("SETTING") == "mossy forest clearing"


def test_prop_gets_weight():
    tags = _tags({"PROP": "banana"})
    assert "(banana:1.4)" in tags


def test_prop_none_excluded():
    tags = _tags({"PROP": "none"})
    assert "none" not in tags


def test_prop_empty_excluded():
    tags = _tags({})
    # no PROP key → nothing added
    assert "none" not in tags


# ── _build_tags ───────────────────────────────────────────────────────────────

def _tags(fields, appearance=""):
    return _build_tags(fields, appearance)


def test_body_added_when_not_in_action():
    tags = _tags({"ACTION": "kneeling", "BODY": "mouth"})
    assert "(mouth:1.3)" in tags


def test_camera_front_view_adds_close_up():
    tags = _tags({"CAMERA": "front view"})
    assert "(front view:1.4)" in tags
    assert "(close-up torso:1.3)" in tags


def test_camera_from_behind_maps_correctly():
    tags = _tags({"CAMERA": "from behind"})
    assert "(from behind:1.4)" in tags
    assert "(back view:1.3)" in tags


def test_appearance_not_included():
    """Appearance tags lead the prompt for stronger identity consistency."""
    tags = _tags({"ACTION": "standing"}, appearance="long blonde hair, blue eyes")
    assert tags.startswith("long blonde hair, blue eyes")


def test_setting_and_lighting_have_no_weight():
    tags = _tags({"SETTING": "bedroom", "LIGHTING": "soft lighting"})
    assert "bedroom" in tags
    assert "soft lighting" in tags
    # They should appear as plain text, not weighted
    assert "(bedroom:" not in tags
    assert "(soft lighting:" not in tags


def test_pose_gets_weight():
    tags = _tags({"POSE": "kneeling"})
    assert "(kneeling:1.3)" in tags


def test_extra_gets_lowest_weight():
    tags = _tags({"EXTRA": "hands on hips"})
    assert "(hands on hips:1.1)" in tags


# ── last-clause extraction (inline logic from alice.py) ───────────────────────

import re

def _last_clause(msg):
    """Replicate the clause-reordering logic from alice.py.

    Last clause moves to front (end-state priority); earlier clauses
    appended after so the LLM retains full context.
    """
    parts = [p.strip() for p in re.split(r'\b(?:and|then)\b|[;]', msg, flags=re.I) if p.strip()]
    return ", ".join([parts[-1]] + parts[:-1]) if len(parts) > 1 else msg


def test_last_clause_then_split():
    result = _last_clause("kneel then suck it")
    assert result.startswith("suck it")
    assert "kneel" in result


def test_last_clause_semicolon_split():
    result = _last_clause("turn around; spread your legs")
    assert result.startswith("spread your legs")
    assert "turn around" in result


def test_detect_finger_in_mouth():
    actions, body, camera, _ = _detect_action("put your finger in your mouth")
    assert actions[0] == "one finger in mouth"
    assert body   == "mouth"
    assert camera == "face level"


def test_detect_finger_in_mouth_reversed():
    # "suck your finger" — mouth word comes first
    actions, body, camera, _ = _detect_action("suck your finger slowly")
    assert actions[0] == "one finger in mouth"
    assert camera == "face level"


def test_detect_bend_over():
    actions, body, camera, _ = _detect_action("bend over and show me")
    assert actions[0] == "bent over"
    assert camera == "from behind"


def test_detect_spread_legs():
    actions, body, camera, _ = _detect_action("spread your legs wide")
    assert actions[0] == "spreading legs"
    assert camera == "from below"


def test_detect_riding():
    actions, _, camera, _ = _detect_action("sit on me and ride")
    assert actions[0] == "riding"
    assert camera == "from below"


def test_detect_riding_horse_excluded():
    # "ride" with animal context must not trigger cowgirl position
    assert _detect_action("I want to ride a horse") is None
    assert _detect_action("let's go horse riding") is None


def test_detect_no_match_returns_none():
    assert _detect_action("hello how are you") is None


def test_last_clause_preserves_all_context():
    """No clause is dropped — all parts appear in the output."""
    msg = "take off shoes and sit down and spread legs"
    result = _last_clause(msg)
    assert "spread legs" in result
    assert "take off shoes" in result
    assert "sit down" in result


# ── remaining _detect_action patterns ────────────────────────────────────────

def test_detect_kneeling():
    actions, body, camera, _ = _detect_action("kneel before me")
    assert actions[0] == "kneeling"
    assert camera == "front view"

def test_detect_stroking():
    actions, body, camera, _ = _detect_action("stroke it for me")
    assert actions[0] == "stroking penis"
    assert camera == "front view"

def test_detect_kissing():
    actions, body, camera, _ = _detect_action("kiss me deeply")
    assert actions[0] == "kissing"
    assert camera == "face level"

def test_detect_strip():
    actions, body, camera, _ = _detect_action("strip for me")
    assert actions[0] == "disrobing"
    assert camera == "front view"

def test_detect_undress():
    actions, _, _, _ = _detect_action("slowly undress")
    assert actions[0] == "disrobing"

def test_detect_lick_finger():
    actions, body, camera, _ = _detect_action("lick your finger")
    assert actions[0] == "one finger in mouth"
    assert body == "mouth"

def test_detect_tongue_finger():
    actions, body, _, _ = _detect_action("run your finger along your tongue")
    assert actions[0] == "one finger in mouth"
    assert body == "mouth"


# ── _sanitize_tags edge cases ─────────────────────────────────────────────────

from image.prompt import _sanitize_tags, _clean_raw

def test_sanitize_drops_too_long():
    result = _sanitize_tags("a very long tag with five words, short")
    assert result == ["short"]

def test_sanitize_empty_input():
    assert _sanitize_tags("") == []

def test_sanitize_allows_hyphens():
    result = _sanitize_tags("close-up")
    assert "close-up" in result

def test_camera_map_has_all_keys():
    from image.prompt import _CAMERA_MAP
    expected = {"front view", "close-up", "from behind", "from below", "face level"}
    assert expected == set(_CAMERA_MAP.keys())

def test_accessory_glasses():
    assert "wearing glasses" in _detect_accessories("put on your glasses")

def test_accessory_spectacles():
    assert "wearing glasses" in _detect_accessories("wear your spectacles")

def test_accessory_sunglasses():
    assert "wearing sunglasses" in _detect_accessories("put on sunglasses")

def test_accessory_hat():
    assert "wearing hat" in _detect_accessories("put on your hat")

def test_accessory_choker():
    assert "choker necklace" in _detect_accessories("wear your choker")

def test_accessory_stockings():
    assert "thigh-high stockings" in _detect_accessories("put on your stockings")

def test_accessory_heels():
    assert "high heels" in _detect_accessories("put on heels and dance")

def test_accessory_multiple():
    result = _detect_accessories("put on glasses and heels")
    assert "wearing glasses" in result
    assert "high heels" in result

def test_accessory_injected_into_build_tags():
    tags = _build_tags({"ACCESSORIES": ["wearing glasses", "high heels"]}, "")
    assert "(wearing glasses:1.3)" in tags
    assert "(high heels:1.3)" in tags
