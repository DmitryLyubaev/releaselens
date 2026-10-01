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


def test_the_preregistered_selection():
    """Spec §8 names these ten queries. A golden-set edit that moves them breaks the study.

    Inserting, reordering or recategorising an entry changes what per_category=2 selects
    without changing anything that reads as a study parameter, and the run would then
    measure a different set from the one that was pre-registered.
    """
    selection = _stratify(load_golden(), 2)

    assert [q.id for q in selection] == [
        "gq-001", "gq-002", "gq-014", "gq-015", "gq-022",
        "gq-023", "gq-029", "gq-030", "gq-036", "gq-037",
    ]
    assert [q.id for q in selection if q.must_contain] == [
        "gq-001", "gq-002", "gq-022", "gq-023", "gq-029", "gq-030",
    ]
    assert [q.category for q in selection if q.id in ("gq-036", "gq-037")] == [
        "unanswerable", "unanswerable",
    ]


def test_stratify_is_capped_by_what_each_category_actually_has():
    """A category with fewer than N entries contributes all of them, not an error."""
    every = load_golden()
    sampled = _stratify(every, 1000)

    assert len(sampled) == len(every)
