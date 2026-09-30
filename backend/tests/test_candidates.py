from evals.candidates import tail_rows

from pipeline.chunk import count_tokens


def test_tail_rows_returns_numeric_table_rows_past_the_budget():
    filler = "word " * 30
    sentences = [
        (0, filler, None),  # prose, never a candidate
        (1, "Revenue $ 1,000 $ 900", 1),  # a table row inside the budget
        (2, "Header row without numbers", 1),
        (3, "Gaming $ 11,350 $ 10,447", 1),
    ]
    budget = count_tokens(filler) + count_tokens("Revenue $ 1,000 $ 900")
    rows = tail_rows(sentences, budget=budget)
    assert [(sid, text) for sid, text, _ in rows] == [(3, "Gaming $ 11,350 $ 10,447")]
    assert rows[0][2] >= budget


def test_tail_rows_is_empty_when_everything_fits():
    assert tail_rows([(0, "Revenue $ 1,000", 1)], budget=450) == []
