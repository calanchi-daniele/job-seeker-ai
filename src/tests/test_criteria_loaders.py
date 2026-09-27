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
