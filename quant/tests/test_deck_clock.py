"""The US session clock (deck/clock.py): the wall-clock rule and Alpaca's answer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ohcamel_quant.deck.clock import parse_alpaca_clock, rule_clock

# 2026-09-24 is a Thursday; New York is on EDT (UTC-4) until 2026-11-01.


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_open_mid_session():
    c = rule_clock(at("2026-09-24T15:00:00"))                  # 11:00 New York
    assert c.is_open and c.session == "2026-09-24"
    assert c.next_close == "2026-09-24T20:00:00+00:00"          # 16:00 EDT
    assert c.next_open == "2026-09-25T13:30:00+00:00"
    assert c.source == "rule" and "holidays" in c.note


def test_before_the_open_belongs_to_yesterday():
    c = rule_clock(at("2026-09-24T12:00:00"))                  # 08:00 New York
    assert not c.is_open and c.session == "2026-09-23"
    assert c.next_open == "2026-09-24T13:30:00+00:00"


def test_after_the_close_belongs_to_today():
    c = rule_clock(at("2026-09-24T21:00:00"))                  # 17:00 New York
    assert not c.is_open and c.session == "2026-09-24"
    assert c.next_open == "2026-09-25T13:30:00+00:00"


def test_the_close_itself_is_closed():
    assert not rule_clock(at("2026-09-24T20:00:00")).is_open


def test_friday_night_opens_on_monday_and_the_weekend_belongs_to_friday():
    c = rule_clock(at("2026-09-27T16:00:00"))                  # Sunday noon New York
    assert not c.is_open and c.session == "2026-09-25"         # Friday
    assert c.next_open == "2026-09-28T13:30:00+00:00"          # Monday 09:30


def test_winter_time_moves_the_utc_open():
    c = rule_clock(at("2026-12-01T15:00:00"))                  # 10:00 EST
    assert c.is_open and c.next_close == "2026-12-01T21:00:00+00:00"


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        rule_clock(datetime(2026, 9, 24, 15))


def test_alpaca_clock_is_parsed():
    body = {"timestamp": "2026-11-27T12:30:00-05:00", "is_open": True,
            "next_open": "2026-11-30T09:30:00-05:00", "next_close": "2026-11-27T13:00:00-05:00"}
    c = parse_alpaca_clock(body, at("2026-11-27T17:30:00"))    # the day after Thanksgiving: early close
    assert c.is_open and c.source == "alpaca" and c.session == "2026-11-27"
    assert c.next_close == "2026-11-27T18:00:00+00:00"
