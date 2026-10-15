"""Tests af fortolkningen af modtagerkonstanten."""

from ats_framework.helpers.email import parse_recipients


def test_json_list():
    assert parse_recipients('["a@x.dk", "b@x.dk"]') == ["a@x.dk", "b@x.dk"]


def test_separated_string():
    assert parse_recipients("a@x.dk; b@x.dk, ") == ["a@x.dk", "b@x.dk"]
