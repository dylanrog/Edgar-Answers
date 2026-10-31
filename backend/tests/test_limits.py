import pytest

from api.limits import AskLimiter, RateLimited, client_ip


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_per_ip_limit_then_refuses():
    limiter = AskLimiter(per_ip_per_hour=2, daily_cap=100, clock=Clock())
    limiter.check("1.1.1.1")
    limiter.check("1.1.1.1")
    with pytest.raises(RateLimited) as refused:
        limiter.check("1.1.1.1")
    assert "2 questions" in refused.value.message
    assert 0 < refused.value.retry_after <= 3601


def test_per_ip_limit_is_per_ip():
    limiter = AskLimiter(per_ip_per_hour=1, daily_cap=100, clock=Clock())
    limiter.check("1.1.1.1")
    limiter.check("2.2.2.2")  # a different visitor is unaffected


def test_hour_window_slides():
    clock = Clock()
    limiter = AskLimiter(per_ip_per_hour=1, daily_cap=100, clock=clock)
    limiter.check("1.1.1.1")
    clock.now += 3600.5
    limiter.check("1.1.1.1")  # the first hit has aged out


def test_refusal_does_not_consume_quota():
    # A client retrying in a loop must not push its own window forward forever.
    clock = Clock()
    limiter = AskLimiter(per_ip_per_hour=1, daily_cap=100, clock=clock)
    limiter.check("1.1.1.1")
    for _ in range(5):
        with pytest.raises(RateLimited):
            limiter.check("1.1.1.1")
    clock.now += 3600.5
    limiter.check("1.1.1.1")


def test_daily_cap_is_site_wide_and_resets_at_utc_midnight():
    clock = Clock(now=86400.0 * 20_000 + 100)  # 100 s past a UTC midnight
    limiter = AskLimiter(per_ip_per_hour=100, daily_cap=2, clock=clock)
    limiter.check("1.1.1.1")
    limiter.check("2.2.2.2")
    with pytest.raises(RateLimited) as refused:
        limiter.check("3.3.3.3")
    assert "today" in refused.value.message
    assert refused.value.retry_after == 86400 - 100 + 1
    clock.now += 86400
    limiter.check("3.3.3.3")


def test_client_ip_prefers_the_configured_proxy_header():
    headers = {"fly-client-ip": "203.0.113.7"}
    assert client_ip(headers, peer="10.0.0.1", header="Fly-Client-IP") == "203.0.113.7"


def test_client_ip_ignores_the_header_when_not_configured():
    # Off Fly, nothing overwrites the header, so trusting it would let any
    # caller choose their own quota bucket.
    headers = {"fly-client-ip": "203.0.113.7"}
    assert client_ip(headers, peer="10.0.0.1", header=None) == "10.0.0.1"


def test_client_ip_falls_back_to_peer_then_unknown():
    assert client_ip({}, peer="10.0.0.1", header="Fly-Client-IP") == "10.0.0.1"
    assert client_ip({}, peer=None, header=None) == "unknown"


def test_ipv6_visitors_share_a_bucket_per_slash_64():
    # Fly passes the visitor's full IPv6 address, and anyone with an ordinary
    # /64 can rotate through it (privacy extensions do this unprompted) --
    # keyed per address, each rotation would be a fresh quota.
    a = client_ip({"fly-client-ip": "2001:db8:1:2::a"}, peer=None, header="Fly-Client-IP")
    b = client_ip({"fly-client-ip": "2001:db8:1:2:ffff::b"}, peer=None, header="Fly-Client-IP")
    other = client_ip({"fly-client-ip": "2001:db8:1:3::a"}, peer=None, header="Fly-Client-IP")
    assert a == b
    assert a != other


def test_ipv4_mapped_ipv6_is_the_ipv4_address():
    assert client_ip({}, peer="::ffff:203.0.113.7", header=None) == "203.0.113.7"


def test_ipv4_and_unparseable_values_pass_through():
    assert client_ip({}, peer="203.0.113.7", header=None) == "203.0.113.7"
    assert client_ip({}, peer="testclient", header=None) == "testclient"
