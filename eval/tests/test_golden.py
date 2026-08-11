from app.golden import load_golden


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
