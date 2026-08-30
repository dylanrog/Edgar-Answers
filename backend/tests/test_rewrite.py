from api.rewrite import build_rewrite_prompt, parse_rewritten_query

FALLBACK = "Who achieved more growth, Microsoft or Amazon?"


def test_parses_bare_json():
    raw = '{"query": "Microsoft revenue growth fiscal 2023 to fiscal 2024"}'
    assert (
        parse_rewritten_query(raw, FALLBACK)
        == "Microsoft revenue growth fiscal 2023 to fiscal 2024"
    )


def test_parses_fenced_json():
    raw = '```json\n{"query": "Amazon revenue growth fiscal 2023 to fiscal 2024"}\n```'
    assert (
        parse_rewritten_query(raw, FALLBACK)
        == "Amazon revenue growth fiscal 2023 to fiscal 2024"
    )


def test_malformed_json_falls_back():
    assert parse_rewritten_query('{"query": [oops}', FALLBACK) == FALLBACK


def test_no_json_object_falls_back():
    assert parse_rewritten_query("I'm not sure.", FALLBACK) == FALLBACK


def test_missing_query_field_falls_back():
    assert parse_rewritten_query('{"other": "x"}', FALLBACK) == FALLBACK


def test_non_string_query_field_falls_back():
    assert parse_rewritten_query('{"query": 123}', FALLBACK) == FALLBACK


def test_blank_query_falls_back():
    assert parse_rewritten_query('{"query": "   "}', FALLBACK) == FALLBACK


def test_query_is_stripped():
    assert (
        parse_rewritten_query('{"query": "  Microsoft revenue  "}', FALLBACK)
        == "Microsoft revenue"
    )


def test_rewrite_prompt_names_the_company_and_the_question():
    prompt = build_rewrite_prompt(
        "Who achieved more growth, Microsoft or Amazon?",
        "MSFT",
        "Microsoft Corporation",
    )
    assert "MSFT: Microsoft Corporation" in prompt
    assert "Who achieved more growth, Microsoft or Amazon?" in prompt
