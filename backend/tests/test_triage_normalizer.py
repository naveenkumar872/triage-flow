
import pytest

from agents.triage_classification_agent import (
    PRIORITY_MAP,
    CATEGORY_VALID,
    SENTIMENT_VALID,
    TEAM_VALID,
)


# =========================================================
# PRIORITY_MAP — canonical mappings
# =========================================================

def test_priority_p1_direct():
    assert PRIORITY_MAP["p1"] == "P1"

def test_priority_critical_maps_to_p1():
    assert PRIORITY_MAP["critical"] == "P1"

def test_priority_urgent_maps_to_p1():
    assert PRIORITY_MAP["urgent"] == "P1"

def test_priority_p2_direct():
    assert PRIORITY_MAP["p2"] == "P2"

def test_priority_high_maps_to_p2():
    assert PRIORITY_MAP["high"] == "P2"

def test_priority_p3_direct():
    assert PRIORITY_MAP["p3"] == "P3"

def test_priority_medium_maps_to_p3():
    assert PRIORITY_MAP["medium"] == "P3"

def test_priority_moderate_maps_to_p3():
    assert PRIORITY_MAP["moderate"] == "P3"

def test_priority_p4_direct():
    assert PRIORITY_MAP["p4"] == "P4"

def test_priority_low_maps_to_p4():
    assert PRIORITY_MAP["low"] == "P4"

def test_priority_minor_maps_to_p4():
    assert PRIORITY_MAP["minor"] == "P4"


# =========================================================
# PRIORITY_MAP — unknown value falls back to P3
# (simulates what the agent does: PRIORITY_MAP.get(raw, "P3"))
# =========================================================

def test_priority_unknown_defaults_to_p3():
    unknown = "super_critical"
    result = PRIORITY_MAP.get(unknown, "P3")
    assert result == "P3"

def test_priority_empty_string_defaults_to_p3():
    result = PRIORITY_MAP.get("", "P3")
    assert result == "P3"


# =========================================================
# CATEGORY_VALID — expected values present
# =========================================================

@pytest.mark.parametrize("category", [
    "billing", "technical", "account", "data", "feature_request", "general"
])
def test_category_valid_contains(category):
    assert category in CATEGORY_VALID


def test_category_unknown_clamps_to_general():
    unknown = "shipping"
    result = unknown if unknown in CATEGORY_VALID else "general"
    assert result == "general"

def test_category_empty_clamps_to_general():
    result = "" if "" in CATEGORY_VALID else "general"
    assert result == "general"


# =========================================================
# SENTIMENT_VALID — expected values present
# =========================================================

@pytest.mark.parametrize("sentiment", [
    "angry", "frustrated", "neutral", "positive"
])
def test_sentiment_valid_contains(sentiment):
    assert sentiment in SENTIMENT_VALID


def test_sentiment_unknown_clamps_to_neutral():
    unknown = "ecstatic"
    result = unknown if unknown in SENTIMENT_VALID else "neutral"
    assert result == "neutral"

def test_sentiment_empty_clamps_to_neutral():
    result = "" if "" in SENTIMENT_VALID else "neutral"
    assert result == "neutral"


# =========================================================
# TEAM_VALID — expected values present
# =========================================================

@pytest.mark.parametrize("team", [
    "engineering", "billing", "account_management", "support", "security"
])
def test_team_valid_contains(team):
    assert team in TEAM_VALID


def test_team_unknown_clamps_to_support():
    unknown = "marketing"
    result = unknown if unknown in TEAM_VALID else "support"
    assert result == "support"

def test_team_empty_clamps_to_support():
    result = "" if "" in TEAM_VALID else "support"
    assert result == "support"
