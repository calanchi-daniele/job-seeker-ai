"""Reading the hand-edited criteria files.

Regression cover for the encoding bug: ``read_text()`` without an explicit
encoding uses the *locale* encoding (cp1252 on Windows), which silently turned
the ``€`` in criteria/deal_breakers.md into ``â‚¬`` before it reached the LLM
prompt.
"""

from pathlib import Path

from job_seeker_ai import config, criteria_loaders


def test_deal_breakers_keep_the_euro_sign():
    text = criteria_loaders.load_deal_breakers()
    assert "€" in text
    assert "€65,000" in text


def test_deal_breakers_are_not_mojibake():
    text = criteria_loaders.load_deal_breakers()
    # what cp1252 produced for the UTF-8 bytes of "€"
    assert "â‚¬" not in text
    assert "\ufffd" not in text


def test_deal_breakers_prompt_material_is_complete():
    text = criteria_loaders.load_deal_breakers()
    assert "Remote from Italy" in text
    assert "Match min salary" in text


def test_rule_lines_decode_utf8(tmp_path):
    path = tmp_path / "rules.md"
    path.write_text("€uro OR stipendio\nremote\n", encoding="utf-8")

    assert criteria_loaders.load_rule_lines(path) == [["€uro", "stipendio"], ["remote"]]


def test_rule_lines_split_on_or_and_lowercase(tmp_path):
    path = tmp_path / "rules.md"
    path.write_text(".NET OR C#\n  Remote  \n\n", encoding="utf-8")

    # " OR " splits alternatives, blank lines are dropped, everything lowered
    assert criteria_loaders.load_rule_lines(path) == [[".net", "c#"], ["remote"]]


def test_missing_rule_file_yields_no_rules(tmp_path):
    """An absent forbidden_words.md rejects nothing, rather than raising."""
    assert criteria_loaders.load_rule_lines(tmp_path / "nope.md") == []


def test_shipped_criteria_files_are_all_readable():
    """Guards the real files shipped with the project, not just fixtures."""
    for name in ("must_words.md", "forbidden_words.md", "deal_breakers.md"):
        path: Path = config.CRITERIA_DIR / name
        assert path.exists(), path
        assert path.read_text(encoding="utf-8")


# --- the deal-breaker structure the settings page edits ------------------

SAMPLE = """- Alpha: first description
- Beta: second description

--- GENERAL EVALUATION PRINCIPLES ---
- Alpha: ONE - the first principle
- Alpha: TWO - the second principle
- Beta: ONE - beta's only principle

--- EXAMPLES OF CORRECT SCORING ---
- Alpha: 0 - a zero example
- Beta: 2 - a two example with 60,000 - 75,000 inside
"""


def test_parse_groups_sub_items_under_their_key():
    items = criteria_loaders.parse_deal_breakers(SAMPLE)

    assert [item["key"] for item in items] == ["Alpha", "Beta"]
    assert items[0]["description"] == "first description"
    assert [principle["title"] for principle in items[0]["principles"]] == ["ONE", "TWO"]
    assert items[0]["principles"][1]["principle"] == "the second principle"
    assert [example["score"] for example in items[1]["examples"]] == ["2"]


def test_parse_splits_a_sub_item_on_its_first_dash_only():
    """A range such as "60,000 - 75,000" keeps its own dash in the text."""
    items = criteria_loaders.parse_deal_breakers(SAMPLE)

    assert items[1]["examples"][0]["example"] == "a two example with 60,000 - 75,000 inside"


def test_parse_ignores_lines_that_are_not_list_items():
    items = criteria_loaders.parse_deal_breakers("prose\n- Only: kept\n\nmore prose\n")

    assert len(items) == 1
    assert items[0]["key"] == "Only"


def test_parse_keeps_an_orphan_sub_item_as_its_own_deal_breaker():
    """Losing a rule silently is worse than showing a card with a gap in it."""
    orphans = SAMPLE.replace("- Alpha: first description\n", "").replace("- Beta: second description\n", "")

    items = criteria_loaders.parse_deal_breakers(orphans)

    assert [item["key"] for item in items] == ["Alpha", "Beta"]
    assert all(item["description"] == "" for item in items)
    assert len(items[0]["principles"]) == 2


def test_format_writes_the_three_regions():
    text = criteria_loaders.format_deal_breakers(criteria_loaders.parse_deal_breakers(SAMPLE))

    assert text == SAMPLE


def test_round_trip_is_lossless():
    items = criteria_loaders.parse_deal_breakers(SAMPLE)

    assert criteria_loaders.parse_deal_breakers(criteria_loaders.format_deal_breakers(items)) == items


def test_format_drops_a_deal_breaker_with_no_key():
    """Without a key it could not own sub-items on the way back in."""
    text = criteria_loaders.format_deal_breakers(
        [{"key": " ", "description": "orphan", "principles": [], "examples": []}]
    )

    assert "orphan" not in text
    assert criteria_loaders.PRINCIPLES_HEADER in text


def test_format_drops_an_entirely_empty_sub_item():
    text = criteria_loaders.format_deal_breakers(
        [{"key": "K", "description": "d", "principles": [{"title": "", "principle": ""}], "examples": []}]
    )

    assert "- K: d" in text
    assert "- K:  -" not in text


def test_a_multiline_field_is_flattened_onto_one_line():
    """The description is a textarea, so a newline must not become a stray line."""
    text = criteria_loaders.format_deal_breakers(
        [{"key": "K", "description": "first line\nsecond line", "principles": [], "examples": []}]
    )

    assert "first line second line" in text
    assert criteria_loaders.parse_deal_breakers(text)[0]["description"] == "first line second line"


def test_the_shipped_deal_breakers_round_trip():
    """Load then save must leave the real file byte-identical, bar trailing spaces."""
    text = criteria_loaders.load_deal_breakers()
    normalised = "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"

    assert criteria_loaders.format_deal_breakers(criteria_loaders.parse_deal_breakers(text)) == normalised
