from app.golden import load_golden
from app.runner import _stratify


def test_golden_loads_and_has_enough_queries():
    queries = load_golden()
    assert 30 <= len(queries) <= 50


def test_every_query_has_a_unique_id():
    ids = [q.id for q in load_golden()]
    assert len(ids) == len(set(ids))


def test_unanswerable_queries_expect_no_citations():
    for query in load_golden():
        if query.category == "unanswerable":
            assert query.expected_citations == []


def test_a_prefix_limit_would_miss_whole_categories():
    """The reason per_category exists. Guards the assumption, not the implementation."""
    categories = {q.category for q in load_golden()[:15]}
    assert "unanswerable" not in categories


def test_stratify_covers_every_category():
    every = load_golden()
    sampled = _stratify(every, 3)

    assert {q.category for q in sampled} == {q.category for q in every}


def test_stratify_takes_at_most_n_per_category():
    counts: dict[str, int] = {}
    for query in _stratify(load_golden(), 3):
        counts[query.category] = counts.get(query.category, 0) + 1

    assert all(n <= 3 for n in counts.values())


def test_stratify_is_capped_by_what_each_category_actually_has():
    """A category with fewer than N entries contributes all of them, not an error."""
    every = load_golden()
    sampled = _stratify(every, 1000)

    assert len(sampled) == len(every)
