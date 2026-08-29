from api.detect import (
    MAX_COMPANIES,
    build_detection_prompt,
    build_period_prompt,
    parse_detected_companies,
    parse_detected_periods,
)

KNOWN = {"AAPL", "AMZN", "GOOGL", "JNJ", "JPM", "META", "MSFT", "NVDA", "TSLA", "WMT"}
KNOWN_ACCESSIONS = {"0000320193-24-000123", "0000320193-24-000069"}


def test_parses_bare_json():
    raw = '{"tickers": ["MSFT", "AMZN"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT", "AMZN"]


def test_parses_fenced_json():
    raw = '```json\n{"tickers": ["AAPL"]}\n```'
    assert parse_detected_companies(raw, KNOWN) == ["AAPL"]


def test_empty_tickers_list_is_valid():
    assert parse_detected_companies('{"tickers": []}', KNOWN) == []


def test_malformed_json_returns_empty():
    assert parse_detected_companies('{"tickers": [oops}', KNOWN) == []


def test_no_json_object_returns_empty():
    assert parse_detected_companies("I'm not sure.", KNOWN) == []


def test_non_list_tickers_field_returns_empty():
    assert parse_detected_companies('{"tickers": "MSFT"}', KNOWN) == []


def test_unknown_ticker_is_dropped():
    raw = '{"tickers": ["MSFT", "ZZZZ"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT"]


def test_duplicate_tickers_are_deduped():
    raw = '{"tickers": ["MSFT", "MSFT", "AMZN"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT", "AMZN"]


def test_output_is_capped_at_max_companies_in_mention_order():
    raw = '{"tickers": ["AAPL", "AMZN", "GOOGL", "JPM", "META", "MSFT"]}'
    result = parse_detected_companies(raw, KNOWN)
    assert result == ["AAPL", "AMZN", "GOOGL", "JPM"]
    assert len(result) == MAX_COMPANIES


def test_detection_prompt_lists_each_company_and_the_question():
    companies = [
        {"cik": 320193, "ticker": "AAPL", "name": "Apple Inc.", "filings": 13},
        {"cik": 1018724, "ticker": "AMZN", "name": "Amazon.com, Inc.", "filings": 12},
    ]
    prompt = build_detection_prompt("Compare Apple and Amazon's margins.", companies)
    assert "AAPL: Apple Inc." in prompt
    assert "AMZN: Amazon.com, Inc." in prompt
    assert "Compare Apple and Amazon's margins." in prompt


def test_parse_periods_parses_bare_json():
    raw = '{"accessions": ["0000320193-24-000123"]}'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000123"]


def test_parse_periods_parses_fenced_json():
    raw = '```json\n{"accessions": ["0000320193-24-000069"]}\n```'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000069"]


def test_parse_periods_empty_accessions_list_is_valid():
    assert parse_detected_periods('{"accessions": []}', KNOWN_ACCESSIONS) == []


def test_parse_periods_malformed_json_returns_empty():
    assert parse_detected_periods('{"accessions": [oops}', KNOWN_ACCESSIONS) == []


def test_parse_periods_no_json_object_returns_empty():
    assert parse_detected_periods("Not sure which filing.", KNOWN_ACCESSIONS) == []


def test_parse_periods_non_list_field_returns_empty():
    assert parse_detected_periods(
        '{"accessions": "0000320193-24-000123"}', KNOWN_ACCESSIONS
    ) == []


def test_parse_periods_unknown_accession_is_dropped():
    raw = '{"accessions": ["0000320193-24-000123", "0000000000-00-000000"]}'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000123"]


def test_parse_periods_two_periods_for_a_comparison_question():
    raw = (
        '{"accessions": ["0000320193-24-000069", "0000320193-24-000123"]}'
    )
    result = parse_detected_periods(raw, KNOWN_ACCESSIONS)
    assert result == ["0000320193-24-000069", "0000320193-24-000123"]


def test_period_prompt_lists_each_filing_and_the_question():
    filings = [
        {
            "accession": "0000320193-24-000123",
            "form_type": "10-K",
            "filing_date": "2024-11-01",
            "period_end": "2024-09-28",
        },
    ]
    prompt = build_period_prompt(
        "How many RSUs were excluded from Apple's diluted EPS for fiscal 2023?",
        "AAPL",
        filings,
    )
    assert "0000320193-24-000123" in prompt
    assert "10-K" in prompt
    assert "2024-11-01" in prompt
    assert "2024-09-28" in prompt
    assert "AAPL" in prompt
    assert "How many RSUs were excluded" in prompt
