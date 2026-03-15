"""Tests for image/prompt.py: structured template parsing and tag building."""
import pytest
from image.prompt import _parse_template, _build_tags, _detect_action, _NUDITY_MAP, _CAMERA_MAP


# ── _parse_template ───────────────────────────────────────────────────────────

GOOD_TEMPLATE = """\
ACTION: cupping breasts
BODY: breasts
CAMERA: front view
POSE: standing
NUDITY: topless
EXTRA: nipples visible
SETTING: bedroom
LIGHTING: soft lighting"""


def test_parse_all_fields():
    r = _parse_template(GOOD_TEMPLATE)
    assert r["ACTION"]   == "cupping breasts"
    assert r["BODY"]     == "breasts"
    assert r["CAMERA"]   == "front view"
    assert r["POSE"]     == "standing"
    assert r["NUDITY"]   == "topless"
    assert r["EXTRA"]    == "nipples visible"
    assert r["SETTING"]  == "bedroom"
    assert r["LIGHTING"] == "soft lighting"


def test_parse_strips_parenthetical():
    raw = "ACTION: disrobing (transitional, not end state)\nBODY: breasts"
    r = _parse_template(raw)
    assert "(" not in r.get("ACTION", "")
    assert "transitional" not in r.get("ACTION", "")


def test_parse_clamps_to_four_words():
    raw = "ACTION: very slowly removing top garment carefully now"
    r = _parse_template(raw)
    assert len(r.get("ACTION", "").split()) <= 4


def test_parse_drops_prose_with_pronoun():
    raw = "EXTRA: fingers touching me gently\nSETTING: bedroom"
    r = _parse_template(raw)
    assert "EXTRA" not in r          # "me" triggers prose filter
    assert r.get("SETTING") == "bedroom"


def test_parse_case_insensitive_keys():
    raw = "action: kneeling\nBody: breasts"
    r = _parse_template(raw)
    assert "ACTION" in r
    assert "BODY" in r


def test_parse_missing_fields_returns_empty_dict():
    r = _parse_template("Nothing useful here.")
    assert r == {}


def test_parse_trailing_period_stripped():
    raw = "POSE: standing."
    r = _parse_template(raw)
    assert r["POSE"] == "standing"


def test_parse_drops_apostrophe_tag():
    raw = "EXTRA: nature's hidden treasures visible\nSETTING: forest"
    r = _parse_template(raw)
    assert "EXTRA" not in r
    assert r.get("SETTING") == "forest"


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


def test_action_gets_highest_weight():
    tags = _tags({"ACTION": "cupping breasts"})
    assert "(cupping breasts:1.7)" in tags


def test_body_not_repeated_if_in_action():
    tags = _tags({"ACTION": "cupping breasts", "BODY": "breasts"})
    # 'breasts' appears in the action tag; standalone (breasts:1.5) should be absent
    assert "(breasts:1.5)" not in tags


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


def test_nudity_fully_nude_adds_bare_skin():
    tags = _tags({"NUDITY": "fully nude"})
    assert "(nude:1.2)" in tags
    assert "(fully naked:1.2)" in tags


def test_nudity_topless_adds_bare_chest():
    tags = _tags({"NUDITY": "topless"})
    assert "(topless:1.2)" in tags
    assert "(bare chest:1.2)" in tags


def test_nudity_clothed_adds_no_nudity_tags():
    tags = _tags({"NUDITY": "clothed"})
    assert "nude" not in tags
    assert "naked" not in tags
    assert "bare" not in tags


def test_appearance_not_included():
    """generate.py appends appearance separately — _build_tags must not duplicate it."""
    tags = _tags({"ACTION": "standing"}, appearance="long blonde hair, blue eyes")
    assert "long blonde hair" not in tags
    assert "blue eyes" not in tags


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
    tags = _tags({"EXTRA": "nipples visible"})
    assert "(nipples visible:1.1)" in tags


# ── last-clause extraction (inline logic from alice.py) ───────────────────────

import re

def _last_clause(msg):
    """Replicate the clause-reordering logic from alice.py.

    Last clause moves to front (end-state priority); earlier clauses
    appended after so the LLM retains full context.
    """
    parts = [p.strip() for p in re.split(r'\b(?:and|then)\b|[;]', msg, flags=re.I) if p.strip()]
    return ", ".join([parts[-1]] + parts[:-1]) if len(parts) > 1 else msg


def test_last_clause_and_split():
    result = _last_clause("take off your top and cup your breasts")
    assert result.startswith("cup your breasts")
    assert "take off your top" in result


def test_last_clause_then_split():
    result = _last_clause("kneel then suck it")
    assert result.startswith("suck it")
    assert "kneel" in result


def test_last_clause_semicolon_split():
    result = _last_clause("turn around; spread your legs")
    assert result.startswith("spread your legs")
    assert "turn around" in result


def test_last_clause_no_split():
    assert _last_clause("cup your breasts") == "cup your breasts"


def test_last_clause_multiple_ands():
    result = _last_clause("remove top and look at me and squeeze your breasts")
    assert result.startswith("squeeze your breasts")
    assert "remove top" in result
    assert "look at me" in result


# ── _detect_action ────────────────────────────────────────────────────────────

def test_detect_cup_breasts():
    actions, body, camera = _detect_action("cup your breasts in your hands")
    assert actions[0] == "cupping breasts"
    assert body   == "breasts"
    assert camera == "front view"


def test_detect_hold_breasts():
    actions, body, _ = _detect_action("hold your breasts up for me")
    assert actions[0] == "holding breasts"
    assert "cupping breasts" in actions
    assert body   == "breasts"


def test_detect_fellatio():
    actions, body, camera = _detect_action("get on your knees and suck it")
    assert actions[0] == "fellatio"
    assert camera == "face level"


def test_detect_bend_over():
    actions, body, camera = _detect_action("bend over and show me")
    assert actions[0] == "bent over"
    assert camera == "from behind"


def test_detect_spread_legs():
    actions, body, camera = _detect_action("spread your legs wide")
    assert actions[0] == "spreading legs"
    assert camera == "from below"


def test_detect_riding():
    actions, _, camera = _detect_action("sit on me and ride")
    assert actions[0] == "riding"
    assert camera == "from below"


def test_detect_no_match_returns_none():
    assert _detect_action("hello how are you") is None


def test_detect_compound_picks_last_matching():
    # "take off your top and cup your breasts" — after clause reorder,
    # last_user_msg = "cup your breasts in your hands, take off your top"
    actions, _, _ = _detect_action("cup your breasts in your hands, take off your top")
    assert actions[0] == "cupping breasts"


def test_last_clause_preserves_all_context():
    """No clause is dropped — all parts appear in the output."""
    msg = "take off shoes and sit down and spread legs"
    result = _last_clause(msg)
    assert "spread legs" in result
    assert "take off shoes" in result
    assert "sit down" in result
