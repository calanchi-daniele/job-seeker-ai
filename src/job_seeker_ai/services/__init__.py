"""Use-case layer: the code that decides *when* to do something.

Everything here composes a repository call with a scrape/filter/evaluate
call. Keeping this tier separate from both is what lets a route ask for
"enrich the pending jobs" without knowing SQL, and lets a test exercise the
word filter without a browser.

Note that this package does not import any web framework: ``web/`` depends on
``services/``, never the other way round.
"""
