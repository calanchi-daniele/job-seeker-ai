"""The review website's routes.

Skipped, not failed, when the web extra is not installed: the web layer is an
optional step and ``pip install -e .[dev]`` is what brings it in.
"""

import pytest

pytest.importorskip("fastapi", reason="web extra not installed (pip install -e .[dev])")
pytest.importorskip("jinja2", reason="web extra not installed (pip install -e .[dev])")
pytest.importorskip("httpx", reason="web extra not installed (pip install -e .[dev])")

from urllib.parse import urlencode  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from job_seeker_ai import config, criteria_loaders  # noqa: E402
from job_seeker_ai.web.app import app  # noqa: E402


def _seed(con):
    """Idempotent: every test in this module seeds the same two jobs into the
    one shared throwaway database, so re-seeding must not collide.

    ``scraped_at`` is explicit so the "newest first" ordering is deterministic.
    """
    con.executemany(
        "INSERT OR REPLACE INTO jobs (job_id,title,company,location,status,scraped_at) VALUES (?,?,?,?,?,?)",
        [
            ("web-hi", "Senior C# Engineer", "Acme", "Remote", "active", "2026-01-02 00:00:00"),
            ("web-lo", "Java Developer", "Beta", "Rome", "active", "2026-01-01 00:00:00"),
        ],
    )
    con.execute(
        "INSERT OR IGNORE INTO job_sources (job_id,site,source_id,url) VALUES ('web-hi','linkedin','s1','https://x/1')"
    )
    con.commit()


def test_list_renders_seeded_jobs(con):
    _seed(con)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text


def test_list_renders_the_source_link(con):
    _seed(con)
    response = TestClient(app).get("/")
    assert "https://x/1" in response.text


def test_list_is_newest_first(con):
    _seed(con)
    body = TestClient(app).get("/").text
    assert body.index("Senior C# Engineer") < body.index("Java Developer")


def test_list_labels_the_ordering_as_added(con):
    """The old "posted" sort label was misleading - it ordered by scraped_at."""
    _seed(con)
    response = TestClient(app).get("/")
    assert "order: Added" in response.text
    assert 'name="sort"' not in response.text
    assert "salary est." not in response.text
    assert "min params" not in response.text


def test_removed_query_params_are_ignored(con):
    """min_cv/min_params/sort used to filter and order the list; they are gone
    with the score columns, and must not break the page."""
    _seed(con)
    response = TestClient(app).get("/", params={"min_cv": "5", "min_params": "5", "sort": "cv_score"})
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text


def _set_verdict(con, job_id, value):
    con.execute("UPDATE jobs SET deal_breakers_result = ? WHERE job_id = ?", (value, job_id))
    con.commit()


def _reset(con):
    """Empty the shared database.

    The list page renders *every* row, and earlier test modules leave jobs with
    verdicts of their own, so a pill can only be counted exactly on its own.
    """
    con.execute("DELETE FROM job_sources")
    con.execute("DELETE FROM jobs")
    con.commit()


def test_deal_breaker_pill_is_pending_when_null(con):
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", None)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--pending"' in body
    assert ">Pending<" in body


def test_deal_breaker_pill_is_ok_when_zero(con):
    """0 means it passed - which used to render as a blank cell, because 0 is
    falsy and the template did `result or ''`."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 0)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--ok"' in body
    assert ">OK<" in body


def test_deal_breaker_pill_is_fail_when_one(con):
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--fail"' in body
    assert ">Fail<" in body


def test_deal_breaker_pill_states_are_distinct_per_row(con):
    """One row failing must not paint the others."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)
    _set_verdict(con, "web-lo", 0)

    body = TestClient(app).get("/").text

    assert body.count(">Fail<") == 1
    assert body.count(">OK<") == 1
    assert body.count(">Pending<") == 0


def test_deal_breaker_pill_has_a_tooltip(con):
    """Colour alone is not an accessible label; each pill states its meaning."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)

    body = TestClient(app).get("/").text

    assert "Fails at least one deal breaker" in body


def test_stylesheet_defines_the_pill_modifiers(con):
    """The pills are only coloured if the stylesheet keeps these rules."""
    css = TestClient(app).get("/static/css/style.css").text

    for modifier in (".pill--ok", ".pill--fail", ".pill--pending"):
        assert modifier in css


def test_status_filter_excludes_active_jobs(con):
    _seed(con)
    response = TestClient(app).get("/", params={"status": "rejected"})
    assert "Senior C# Engineer" not in response.text


def test_unknown_job_redirects_back_to_the_list(con):
    response = TestClient(app, follow_redirects=False).get("/jobs/does-not-exist")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_detail_page_renders_the_job(con):
    _seed(con)
    response = TestClient(app).get("/jobs/web-hi")
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text
    assert "Acme" in response.text


def test_invalidate_then_reactivate(con):
    _seed(con)
    client = TestClient(app, follow_redirects=False)

    assert client.post("/jobs/web-hi/invalidate").status_code == 303
    assert con.execute("SELECT status FROM jobs WHERE job_id='web-hi'").fetchone()[0] == "invalidated"

    assert client.post("/jobs/web-hi/reactivate").status_code == 303
    assert con.execute("SELECT status FROM jobs WHERE job_id='web-hi'").fetchone()[0] == "active"


def test_static_stylesheet_is_served(con):
    response = TestClient(app).get("/static/css/style.css")
    assert response.status_code == 200
    assert "font-family" in response.text


# --- the settings page ---------------------------------------------------


@pytest.fixture(autouse=True)
def _throwaway_files(tmp_path, monkeypatch):
    """The settings pages write four real files. Point all of them at a temp
    directory so no test can touch the project's own .env or criteria."""
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "MUST_WORDS_PATH", tmp_path / "must_words.md")
    monkeypatch.setattr(config, "FORBIDDEN_WORDS_PATH", tmp_path / "forbidden_words.md")
    monkeypatch.setattr(config, "DEAL_BREAKERS_PATH", tmp_path / "deal_breakers.md")


def test_the_sidebar_links_to_every_settings_page():
    for path in ("/settings", "/settings/filters", "/settings/deal-breakers"):
        body = TestClient(app).get(path).text
        assert 'class="settings__nav"' in body
        assert 'href="/settings"' in body
        assert 'href="/settings/filters"' in body
        assert 'href="/settings/deal-breakers"' in body


def test_the_sidebar_marks_the_page_you_are_on():
    for path, label in (
        ("/settings", "General"),
        ("/settings/filters", "Filters"),
        ("/settings/deal-breakers", "Deal Breakers"),
    ):
        body = TestClient(app).get(path).text
        assert body.count('class="is-active"') == 1, path
        assert f'<a href="{path}" class="is-active">{label}</a>' in body, path


def test_the_two_pages_are_independent():
    """General owns .env, Filters owns the word lists; neither shows the other."""
    general = TestClient(app).get("/settings").text
    filters = TestClient(app).get("/settings/filters").text

    assert 'name="LLM_MODEL"' in general
    assert "filter-columns" not in general
    assert "filter-columns" in filters
    assert 'name="LLM_MODEL"' not in filters


def test_settings_renders_one_input_per_managed_key():
    body = TestClient(app).get("/settings").text

    for key in (
        "STATE_PATH",
        "SEARCHES_PATH",
        "CRITERIA_DIR",
        "JOBS_DB_PATH",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_MAX_CONCURRENCY",
    ):
        assert f'name="{key}"' in body, key


def test_the_api_key_is_masked():
    """A password input, so the value reads as dots rather than in the clear."""
    body = TestClient(app).get("/settings").text

    assert 'type="password" name="LLM_API_KEY"' in body
    assert 'type="text" name="LLM_API_KEY"' not in body


def test_nothing_else_is_masked():
    body = TestClient(app).get("/settings").text

    assert body.count('type="password"') == 1


def test_the_login_credentials_are_gone_from_the_page():
    """LI_EMAIL and LI_PASSWORD were removed from the app, not merely hidden."""
    body = TestClient(app).get("/settings").text

    assert "LI_EMAIL" not in body
    assert "LI_PASSWORD" not in body


def test_settings_shows_the_values_already_on_disk():
    config.ENV_PATH.write_text("LLM_MODEL=on-disk\n", encoding="utf-8")

    body = TestClient(app).get("/settings").text

    assert 'value="on-disk"' in body


def test_saving_writes_one_line_per_managed_key():
    client = TestClient(app, follow_redirects=False)

    response = client.post("/settings", data={"LLM_BASE_URL": "https://api.example.test/v1", "LLM_MODEL": "m"})

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"
    assert config.ENV_PATH.read_text(encoding="utf-8").splitlines() == [
        "STATE_PATH=",
        "SEARCHES_PATH=",
        "CRITERIA_DIR=",
        "JOBS_DB_PATH=",
        "LLM_BASE_URL=https://api.example.test/v1",
        "LLM_API_KEY=",
        "LLM_MODEL=m",
        "LLM_MAX_CONCURRENCY=",
    ]


def test_saving_round_trips_through_the_page():
    client = TestClient(app)

    client.post("/settings", data={"LLM_MODEL": "round-trip"})

    assert 'value="round-trip"' in client.get("/settings").text


def test_a_key_the_page_does_not_manage_is_carried_over():
    """The form shows only this project's variables; a hand-added line survives."""
    config.ENV_PATH.write_text("MY_OWN_KEY=keep-me\n", encoding="utf-8")

    TestClient(app).post("/settings", data={"LLM_MODEL": "m"})

    assert "MY_OWN_KEY=keep-me" in config.ENV_PATH.read_text(encoding="utf-8")


def test_the_form_cannot_introduce_a_variable():
    TestClient(app).post("/settings", data={"LLM_MODEL": "m", "EVIL": "1"})

    assert "EVIL" not in config.ENV_PATH.read_text(encoding="utf-8")


def test_the_write_target_is_fixed_and_never_taken_from_the_request(tmp_path):
    """A form field must not be able to point the write somewhere else."""
    elsewhere = tmp_path / "somewhere-else.env"

    TestClient(app).post("/settings", data={"LLM_MODEL": "m", "path": str(elsewhere), "env_path": str(elsewhere)})

    assert not elsewhere.exists()
    assert config.ENV_PATH.exists()


def test_the_pages_carry_no_instructional_text():
    """The redesign keeps each form to fields and a Save button."""
    for path in ("/settings", "/settings/filters", "/settings/deal-breakers"):
        body = TestClient(app).get(path).text
        assert "<textarea" not in body, path
        for marker in ('class="hint"', 'class="notice"', "restart", "Restart"):
            assert marker not in body, (path, marker)


# --- the filters section -------------------------------------------------


def _seed_lists(must=".NET OR C#\nbackend OR platform\n", forbidden="blockchain\nninja\n"):
    config.MUST_WORDS_PATH.write_text(must, encoding="utf-8")
    config.FORBIDDEN_WORDS_PATH.write_text(forbidden, encoding="utf-8")


def _post_lists(client, url, **lists):
    """Post repeated fields, which httpx will not encode from a list of tuples."""
    body = urlencode([(name, value) for name, values in lists.items() for value in values])
    return client.post(url, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})


def test_filters_renders_both_columns():
    body = TestClient(app).get("/settings/filters").text

    assert "Must Contain" in body
    assert "Forbidden" in body


def test_the_two_columns_split_the_width_evenly():
    """The 50/50 split is a stylesheet contract, not an inline style."""
    css = TestClient(app).get("/static/css/style.css").text

    assert ".filter-columns" in css
    assert "grid-template-columns: 1fr 1fr" in css


def test_the_save_button_is_right_aligned_with_a_top_margin():
    css = TestClient(app).get("/static/css/style.css").text

    line = next(rule for rule in css.splitlines() if rule.startswith(".actions {"))
    assert "justify-content: flex-end" in line
    assert "margin-top: 15px" in line


def test_filters_renders_one_row_per_rule_line():
    _seed_lists()

    body = TestClient(app).get("/settings/filters").text

    assert 'value=".NET OR C#"' in body
    assert 'value="backend OR platform"' in body
    assert 'value="blockchain"' in body
    assert 'value="ninja"' in body


def test_a_column_with_no_file_still_renders_its_controls():
    """A fresh checkout has neither file; the column must still be usable."""
    body = TestClient(app).get("/settings/filters").text

    assert body.count("data-add") >= 2
    assert "<template>" in body


def test_every_row_has_a_remove_button_and_both_columns_are_addressable():
    _seed_lists()

    body = TestClient(app).get("/settings/filters").text

    assert body.count('data-group') == 2
    assert body.count('data-rows') == 2
    # two seeded rows per column, plus the blank template row in each
    assert body.count("data-remove") == 6


def test_the_settings_script_is_served_and_wired_to_the_hooks():
    response = TestClient(app).get("/static/js/settings.js")

    assert response.status_code == 200
    for hook in ("data-group", "data-rows", '"[data-add]"', '"[data-remove]"'):
        assert hook in response.text, hook


def test_saving_filters_overwrites_both_files():
    _seed_lists()

    response = _post_lists(
        TestClient(app, follow_redirects=False),
        "/settings/filters",
        must_words=["only-this"],
        forbidden_words=["not-that"],
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/filters?saved=1"
    assert config.MUST_WORDS_PATH.read_text(encoding="utf-8") == "only-this\n"
    assert config.FORBIDDEN_WORDS_PATH.read_text(encoding="utf-8") == "not-that\n"


def test_saving_filters_preserves_the_order_of_the_rows():
    _post_lists(TestClient(app), "/settings/filters", must_words=["third", "first", "second"])

    lines = config.MUST_WORDS_PATH.read_text(encoding="utf-8").splitlines()
    assert lines == ["third", "first", "second"]


def test_saving_filters_drops_blank_rows():
    """An emptied row is not a rule, so it must not become a blank line."""
    _post_lists(TestClient(app), "/settings/filters", must_words=["kept", "   ", ""])

    assert config.MUST_WORDS_PATH.read_text(encoding="utf-8") == "kept\n"


def test_saving_filters_can_empty_a_list():
    _seed_lists()

    TestClient(app).post("/settings/filters", data={"forbidden_words": "still-here"})

    assert config.MUST_WORDS_PATH.read_text(encoding="utf-8") == ""
    assert config.FORBIDDEN_WORDS_PATH.read_text(encoding="utf-8") == "still-here\n"


def test_the_saved_marker_sits_left_of_the_button():
    """Feedback, not instruction: it belongs beside the button."""
    client = TestClient(app)
    client.post("/settings", data={"LLM_MODEL": "x"})

    body = client.get("/settings", params={"saved": "1"}).text

    assert body.index("Saved") < body.index(">Save<")


def test_filters_round_trip_through_the_page():
    client = TestClient(app)
    _post_lists(client, "/settings/filters", must_words=["round-trip-rule"])

    assert 'value="round-trip-rule"' in client.get("/settings/filters").text


# --- the deal breakers section -------------------------------------------

BREAKERS = """- Remote: must be fully remote.
- Salary: at least 60k.

--- GENERAL EVALUATION PRINCIPLES ---
- Remote: PREFER REMOTE - prefer the most permissive option.

--- EXAMPLES OF CORRECT SCORING ---
- Remote: 0 - Requires residence in Spain.
- Salary: 2 - Range of 60,000 - 75,000.
"""


def _seed_breakers(text=BREAKERS):
    config.DEAL_BREAKERS_PATH.write_text(text, encoding="utf-8")


def _post_pairs(client, url, pairs):
    """Post fields in an explicit order: the nesting is carried by that order."""
    body = urlencode(pairs)
    return client.post(url, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})


def _pairs_for(items):
    """The fields the page would submit for ``items``, in document order."""
    pairs = []
    for item in items:
        pairs += [("key", item["key"]), ("description", item["description"])]
        for principle in item["principles"]:
            pairs += [("principle_title", principle["title"]), ("principle_text", principle["principle"])]
        for example in item["examples"]:
            pairs += [("example_score", example["score"]), ("example_text", example["example"])]
    return pairs


def test_deal_breakers_renders_one_card_per_item():
    _seed_breakers()

    body = TestClient(app).get("/settings/deal-breakers").text

    # two loaded cards, plus the blank one every page ships inside <template>
    assert body.count('class="breaker"') == 3
    assert 'value="Remote"' in body
    assert 'value="must be fully remote."' in body
    assert 'value="Salary"' in body


def test_deal_breakers_renders_the_sub_items_under_their_parent():
    _seed_breakers()

    body = TestClient(app).get("/settings/deal-breakers").text

    assert 'value="PREFER REMOTE"' in body
    assert 'value="prefer the most permissive option."' in body
    assert 'value="Requires residence in Spain."' in body
    assert 'value="Range of 60,000 - 75,000."' in body


def test_each_deal_breaker_has_add_and_remove_controls_at_both_levels():
    """One group per card and per sub-list; one add per group. The blank card lives
    inside a <template> and carries its own two sub-templates, hence 7 of each."""
    _seed_breakers()

    body = TestClient(app).get("/settings/deal-breakers").text

    assert body.count("data-add") == 7
    assert body.count("data-group") == 7
    assert body.count("<template>") == 7


def test_an_empty_deal_breakers_file_still_renders_the_page():
    body = TestClient(app).get("/settings/deal-breakers").text

    # only the blank template card: the page is still usable with no file
    assert body.count('class="breaker"') == 1
    assert "data-add" in body


def test_saving_deal_breakers_writes_the_flattened_format():
    response = _post_pairs(
        TestClient(app, follow_redirects=False),
        "/settings/deal-breakers",
        [
            ("key", "Remote"),
            ("description", "must be fully remote"),
            ("principle_title", "PREFER"),
            ("principle_text", "most permissive"),
            ("example_score", "0"),
            ("example_text", "residence in Spain"),
            ("key", "Salary"),
            ("description", "at least 60k"),
            ("example_score", "2"),
            ("example_text", "60,000 - 75,000"),
        ],
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/deal-breakers?saved=1"
    assert config.DEAL_BREAKERS_PATH.read_text(encoding="utf-8") == (
        "- Remote: must be fully remote\n"
        "- Salary: at least 60k\n"
        "\n"
        "--- GENERAL EVALUATION PRINCIPLES ---\n"
        "- Remote: PREFER - most permissive\n"
        "\n"
        "--- EXAMPLES OF CORRECT SCORING ---\n"
        "- Remote: 0 - residence in Spain\n"
        "- Salary: 2 - 60,000 - 75,000\n"
    )


def test_a_sub_item_joins_the_parent_it_follows():
    _post_pairs(
        TestClient(app),
        "/settings/deal-breakers",
        [
            ("key", "A"),
            ("description", ""),
            ("example_score", "1"),
            ("example_text", "a one"),
            ("key", "B"),
            ("description", ""),
            ("example_score", "2"),
            ("example_text", "b two"),
        ],
    )

    written = config.DEAL_BREAKERS_PATH.read_text(encoding="utf-8")
    assert "- A: 1 - a one" in written
    assert "- B: 2 - b two" in written


def test_a_card_without_a_key_is_not_written():
    """It could not own sub-items on the way back in, so it is not persisted."""
    _post_pairs(
        TestClient(app),
        "/settings/deal-breakers",
        [("key", ""), ("description", "orphan"), ("key", "Kept"), ("description", "d")],
    )

    written = config.DEAL_BREAKERS_PATH.read_text(encoding="utf-8")
    assert "orphan" not in written
    assert "- Kept: d" in written


def test_saving_the_loaded_state_leaves_the_file_unchanged():
    """Load then save must be a round trip - the page is an editor, not a rewrite."""
    _seed_breakers()
    items = criteria_loaders.parse_deal_breakers(BREAKERS)

    _post_pairs(TestClient(app), "/settings/deal-breakers", _pairs_for(items))

    assert config.DEAL_BREAKERS_PATH.read_text(encoding="utf-8") == criteria_loaders.format_deal_breakers(items)


def test_deal_breakers_round_trip_through_the_page():
    _seed_breakers()
    items = criteria_loaders.parse_deal_breakers(BREAKERS)
    client = TestClient(app)

    _post_pairs(client, "/settings/deal-breakers", _pairs_for(items))

    body = client.get("/settings/deal-breakers").text
    assert 'value="PREFER REMOTE"' in body
    assert 'value="Range of 60,000 - 75,000."' in body
