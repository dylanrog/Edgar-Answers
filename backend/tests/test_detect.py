from api.detect import MAX_COMPANIES, parse_detected_companies

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
