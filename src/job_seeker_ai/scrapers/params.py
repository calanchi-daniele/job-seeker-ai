"""LinkedIn's own search facet codes.

``searches.json`` is written in friendly names so a human can edit it; the
site only understands opaque codes like ``f_WT=2``. This module is the only
place that knows the mapping, which is also what keeps the search definition
file portable if a second site is ever added.

``f_TPR`` (posted within) is special: the site only offers fixed day
buckets, so a requested window is widened to the smallest bucket that covers
it.
"""

# friendly name -> (site's query key, {friendly value: site code})
PARAM_ALIASES = {
    "work_type": ("f_WT", {"onsite": "1", "remote": "2", "hybrid": "3"}),
    "posted_within": ("f_TPR", {"day": "r86400", "week": "r604800", "month": "r2592000"}),
    "employment_type": (
        "f_JT",
        {
            "full_time": "F",
            "part_time": "P",
            "contract": "C",
            "temporary": "T",
            "internship": "I",
            "volunteer": "V",
            "other": "O",
        },
    ),
    "experience_levels": (
        "f_E",
        {
            "internship": "1",
            "entry": "2",
            "associate": "3",
            "mid_senior": "4",
            "director": "5",
            "executive": "6",
        },
    ),
}

# Ordered ascending; pick the smallest bucket that covers the requested gap.
TPR_BUCKETS = [
    (1, "r86400"),
    (7, "r604800"),
    (30, "r2592000"),
]


def translate_params(raw):
    """Friendly search params -> the site's own query params.

    Unknown keys pass through untouched, so extra query params can be
    hand-added in searches.json without touching this code.
    """
    out = {}
    for k, v in raw.items():
        if k not in PARAM_ALIASES:
            out[k] = v
            continue
        code_key, mapping = PARAM_ALIASES[k]
        values = v if isinstance(v, list) else [v]
        out[code_key] = ",".join(mapping[x] for x in values)
    return out


def tpr_for_days(days):
    """Smallest ``f_TPR`` bucket that covers ``days`` since the last run."""
    for bucket_days, tpr in TPR_BUCKETS:
        if days <= bucket_days:
            return tpr
    return TPR_BUCKETS[-1][1]
