"""The job-sites search document: the legend-driven schema and the merging write.

``_legend`` is both documentation and the schema the settings page builds from, so
these tests cover the two things that would quietly break a user's file: a field
list that hides a parameter, and a save that drops the legend.
"""

import json

import pytest

from job_seeker_ai import job_sites

DOCUMENT = {
    "_legend": {
        "_comment": "fixed value lists live here",
        "work_type": ["onsite", "remote"],
        "posted_within": ["day", "week"],
    },
    "searches": [
        {"name": "a", "pages": 1, "params": {"keywords": "k", "location": "L", "work_type": "remote"}},
    ],
}


def test_parameter_keys_put_the_data_first_and_the_legend_last():
    """keywords and location are not in the legend; a legend-only list would hide
    the two parameters that matter most."""
    assert job_sites.parameter_keys(DOCUMENT) == ["keywords", "location", "work_type", "posted_within"]


def test_the_legend_comment_is_not_a_parameter():
    assert "_comment" not in job_sites.parameter_keys(DOCUMENT)


def test_a_legend_list_becomes_options():
    card = job_sites.search_cards(DOCUMENT)[0]
    work_type = next(field for field in card["fields"] if field["key"] == "work_type")

    assert work_type["options"] == ["onsite", "remote"]
    assert work_type["selected"] == ["remote"]


def test_a_free_text_parameter_has_no_options():
    card = job_sites.search_cards(DOCUMENT)[0]
    keywords = next(field for field in card["fields"] if field["key"] == "keywords")

    assert keywords["options"] is None
    assert keywords["value"] == "k"


def test_a_list_valued_parameter_is_marked_multi_select():
    """experience_levels is list-valued on LinkedIn; a single select would drop all
    but one choice."""
    document = {
        "_legend": {"experience_levels": ["entry", "senior"]},
        "searches": [{"name": "a", "params": {"experience_levels": ["entry", "senior"]}}],
    }

    field = job_sites.search_cards(document)[0]["fields"][0]

    assert field["multiple"] is True
    assert field["selected"] == ["entry", "senior"]


def test_multiple_is_declared_in_the_legend_not_read_off_the_stored_value():
    """A search that has never set experience_levels still gets a multi-select,
    otherwise the first choice made in the form is the only one it can ever hold."""
    document = {
        "_legend": {"_multiple": ["experience_levels"], "experience_levels": ["entry", "senior"]},
        "searches": [{"name": "a", "params": {}}],
    }

    field = job_sites.search_cards(document)[0]["fields"][0]

    assert field["multiple"] is True
    assert field["selected"] == []


def test_one_stored_value_still_shows_as_selected_in_a_multi_select():
    document = {
        "_legend": {"_multiple": ["work_type"], "work_type": ["onsite", "remote"]},
        "searches": [{"name": "a", "params": {"work_type": "remote"}}],
    }

    field = job_sites.search_cards(document)[0]["fields"][0]

    assert field["multiple"] is True
    assert field["selected"] == ["remote"]


def test_a_parameter_missing_from_multiple_stays_single_choice():
    """posted_within is one time bucket, not a set."""
    document = {
        "_legend": {"_multiple": ["work_type"], "posted_within": ["day", "week"]},
        "searches": [{"name": "a", "params": {"posted_within": "day"}}],
    }

    field = job_sites.search_cards(document)[0]["fields"][0]

    assert field["multiple"] is False
    assert field["value"] == "day"


def test_the_multiple_key_is_not_a_parameter():
    document = {"_legend": {"_multiple": ["work_type"], "work_type": ["onsite"]}, "searches": [{"name": "a"}]}

    assert job_sites.parameter_keys(document) == ["work_type"]


def test_a_blank_card_carries_the_same_fields():
    card = job_sites.blank_card(DOCUMENT)

    assert card["name"] == ""
    assert [field["key"] for field in card["fields"]] == job_sites.parameter_keys(DOCUMENT)
    assert all(field["selected"] == [] and field["value"] == "" for field in card["fields"])


def test_a_missing_document_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        job_sites.load_document(tmp_path / "absent.json")


def test_the_editor_tolerates_a_missing_document(tmp_path):
    """The scrape step must fail loudly; the settings page has to open anyway."""
    assert job_sites.load_document(tmp_path / "absent.json", missing_ok=True) == {}


def test_merge_creates_the_document_when_there_is_no_file(tmp_path):
    merged = json.loads(job_sites.merge_searches([{"name": "a", "pages": 1, "params": {}}], tmp_path / "new.json"))

    assert merged["searches"][0]["name"] == "a"
    assert "_legend" not in merged


def test_merge_keeps_the_legend_and_any_other_top_level_key(tmp_path):
    path = tmp_path / "linkedin.json"
    path.write_text(json.dumps({**DOCUMENT, "notes": "keep me"}), encoding="utf-8")

    merged = json.loads(job_sites.merge_searches([{"name": "b", "pages": 2, "params": {}}], path))

    assert merged["_legend"] == DOCUMENT["_legend"]
    assert merged["notes"] == "keep me"
    assert merged["searches"] == [{"name": "b", "pages": 2, "params": {}}]


def test_merge_writes_trailing_newline_and_keeps_non_ascii(tmp_path):
    path = tmp_path / "linkedin.json"
    path.write_text(json.dumps(DOCUMENT), encoding="utf-8")

    text = job_sites.merge_searches([{"name": "città", "pages": 1, "params": {}}], path)

    assert text.endswith("\n")
    assert "città" in text
