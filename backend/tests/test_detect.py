from api.detect import MAX_COMPANIES, build_detection_prompt, parse_detected_companies

KNOWN = {"AAPL", "AMZN", "GOOGL", "JNJ", "JPM", "META", "MSFT", "NVDA", "TSLA", "WMT"}


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
