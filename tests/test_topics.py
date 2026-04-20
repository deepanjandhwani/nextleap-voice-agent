"""Tests for topic matching, including STT transcription variants."""

import pytest

from advisor_scheduler.core.topics import match_topic


@pytest.mark.parametrize(
    "text, expected",
    [
        ("kyc", "KYC / Onboarding"),
        ("KYC", "KYC / Onboarding"),
        ("onboarding", "KYC / Onboarding"),
        # STT variants: letters spoken individually
        ("k y c", "KYC / Onboarding"),
        ("k  y  c", "KYC / Onboarding"),
        ("K Y C", "KYC / Onboarding"),
        # STT variants: phonetic
        ("kay why see", "KYC / Onboarding"),
        ("kay why c", "KYC / Onboarding"),
        # STT variants: with dots
        ("k.y.c", "KYC / Onboarding"),
        ("k.y.c.", "KYC / Onboarding"),
        # "kyc change" should still be account changes
        ("kyc change", "Account Changes / Nominee"),
    ],
)
def test_kyc_stt_variants(text, expected):
    assert match_topic(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("sip", "SIP / Mandates"),
        ("SIP", "SIP / Mandates"),
        ("mandates", "SIP / Mandates"),
        # STT variants: letters spoken individually
        ("s i p", "SIP / Mandates"),
        ("s  i  p", "SIP / Mandates"),
        ("S I P", "SIP / Mandates"),
        # STT variants: phonetic
        ("es eye pee", "SIP / Mandates"),
        # STT variants: with dots
        ("s.i.p", "SIP / Mandates"),
        ("s.i.p.", "SIP / Mandates"),
    ],
)
def test_sip_stt_variants(text, expected):
    assert match_topic(text) == expected


def test_unrecognized_topic_returns_none():
    assert match_topic("retirement planning") is None
    assert match_topic("weather forecast") is None
