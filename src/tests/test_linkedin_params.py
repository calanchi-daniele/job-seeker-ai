"""LinkedIn search-URL construction and "posted within" window widening."""

from job_seeker_ai import config
from job_seeker_ai.scrapers import linkedin
from job_seeker_ai.scrapers import params as site_params


def test_search_url_appends_start_param():
    assert linkedin.search_url({"keywords": "py", "x": None}, 25).endswith("start=25")


def test_search_url_drops_empty_values():
    url = linkedin.search_url({"keywords": "py", "f_WT": None, "f_E": ""}, 0)
    assert "f_WT" not in url and "f_E" not in url
    assert "keywords=py" in url


def test_job_url_is_built_from_the_site_id():
    assert linkedin.job_url("12345") == "https://www.linkedin.com/jobs/view/12345/"


def test_translate_params_maps_friendly_names_to_site_codes():
    assert site_params.translate_params({"work_type": "remote"}) == {"f_WT": "2"}
    assert site_params.translate_params({"posted_within": "week"}) == {"f_TPR": "r604800"}


def test_translate_params_joins_list_values():
    assert site_params.translate_params({"work_type": ["remote", "hybrid"]}) == {"f_WT": "2,3"}


def test_translate_params_passes_unknown_keys_through():
    assert site_params.translate_params({"keywords": "py", "custom": "x"}) == {"keywords": "py", "custom": "x"}


def test_tpr_for_days_picks_the_smallest_covering_bucket():
    assert site_params.tpr_for_days(1) == "r86400"
    assert site_params.tpr_for_days(3) == "r604800"
    assert site_params.tpr_for_days(30) == "r2592000"
    # beyond the widest bucket, clamp rather than return None
    assert site_params.tpr_for_days(365) == "r2592000"


def test_effective_tpr_uses_configured_value_on_first_run(con):
    con.execute("DELETE FROM meta WHERE key=?", (config.LAST_RUN_META_KEY,))
    con.commit()
    assert linkedin.effective_tpr(con, "r2592000") == "r2592000"


def test_effective_tpr_widens_after_a_long_pause(con):
    from job_seeker_ai import repositories

    repositories.set_meta(con, config.LAST_RUN_META_KEY, "2020-01-01T00:00:00+00:00")
    assert linkedin.effective_tpr(con, "r604800") == "r2592000"
