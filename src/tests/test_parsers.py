"""Text parsing: salary extraction and the dedupe key."""

from job_seeker_ai import parsers


def test_find_salary():
    assert parsers.find_salary("Salary range: €55,000 - €70,000 per year plus bonus") == "€55,000 - €70,000 per year"
    assert parsers.find_salary("no money mentioned") is None
    # an amount with no salary keyword nearby must not be mistaken for pay
    assert parsers.find_salary("budget of up to €1250 for equipment") is None


def test_find_salary_handles_empty_input():
    assert parsers.find_salary(None) is None
    assert parsers.find_salary("") is None


def test_normalize_key_ignores_punctuation_and_case():
    a = parsers.normalize_key("Backend Engineer", "Acme Inc.", "Remote")
    b = parsers.normalize_key("backend  engineer", "ACME INC", "remote")
    assert a == b
    assert a != parsers.normalize_key("Frontend Engineer", "Acme Inc.", "Remote")
