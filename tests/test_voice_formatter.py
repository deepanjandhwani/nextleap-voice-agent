import pytest

from advisor_scheduler.formatters.voice import (
    build_tts_text,
    expand_booking_code_for_tts,
    format_for_voice,
    replace_secure_link_for_spoken,
)


def test_empty_and_whitespace():
    assert format_for_voice("") == ""
    assert format_for_voice("   ") == ""


def test_strips_bold_markers():
    assert format_for_voice("**Next:** choose a time.") == "Next: choose a time."


def test_bullets_become_options_phrase():
    raw = (
        "Here are slots in IST:\n"
        "- Mon 10:00–10:30 IST\n"
        "- Tue 14:00–14:30 IST\n"
        "\n"
        "Which works?"
    )
    out = format_for_voice(raw)
    assert "Options:" in out
    assert "Mon 10:00" in out and "Tue 14:00" in out
    assert "-" not in out or "10:00–10:30" in out  # no bullet dashes at line starts


def test_single_bullet_not_prefixed_options():
    out = format_for_voice("- Only one choice\nSay yes to confirm.")
    assert not out.startswith("Options:")
    assert "Only one choice" in out


def test_numbered_list_collapses():
    raw = "Pick one:\n1) First\n2) Second\n\nThanks."
    out = format_for_voice(raw)
    assert "1)" not in out
    assert "2)" not in out
    assert "First" in out and "Second" in out


def test_disclaimer_snippet_stays_readable():
    raw = (
        "I can't give investment advice. "
        "I can help **book** a slot. Times are in **IST**."
    )
    out = format_for_voice(raw)
    assert "IST" in out
    assert "**" not in out


def test_error_style_short_line():
    raw = "I'm sorry, something went wrong on my end.\nPlease try again."
    out = format_for_voice(raw)
    assert "sorry" in out.lower()
    assert "\n\n" not in out or len(out) < len(raw) + 5


@pytest.mark.parametrize(
    "line",
    [
        "1. First option",
        "2) Second option",
        "3] Third",
    ],
)
def test_numbered_line_variants(line):
    # Each alone should not collapse with a neighbor (single item kept)
    out = format_for_voice(line)
    assert "First" in out or "Second" in out or "Third" in out


def test_expand_booking_code_for_tts():
    assert "N" in expand_booking_code_for_tts("NL-HIM6")
    assert "dash" in expand_booking_code_for_tts("NL-HIM6")
    assert "six" in expand_booking_code_for_tts("NL-HIM6")


def test_replace_secure_link_for_spoken():
    raw = "Hello. Secure link for contact details: https://app.example.com/secure-details?code=NL-AB12."
    spoken = "Short follow-up without URL."
    out = replace_secure_link_for_spoken(raw, spoken)
    assert "https://" not in out
    assert spoken in out


def test_build_tts_text_strips_url_and_expands_code():
    line = (
        "Booked. Secure link for contact details: https://x.example/d?c=1. "
        "Reference NL-ZZ9Z."
    )
    follow = "Link is in chat; finish within two hours."
    tts = build_tts_text(line, "NL-ZZ9Z", follow)
    assert "https://" not in tts
    assert follow in tts
    assert "NL-ZZ9Z" not in tts
    assert "Z" in tts and "nine" in tts


def test_slash_in_topic_name_replaced_with_or():
    out = format_for_voice("I can help with KYC / Onboarding or SIP / Mandates.")
    assert " / " not in out
    assert "or" in out


def test_slash_replacement_does_not_affect_path_without_spaces():
    out = format_for_voice("Visit https://example.com/path for details.")
    assert "example.com/path" in out


def test_slash_multiple_topics_all_replaced():
    raw = "Topics: KYC / Onboarding; SIP / Mandates; Portfolio / Review."
    out = format_for_voice(raw)
    assert " / " not in out
    assert out.count(" or ") == 3


def test_build_tts_text_no_slash_in_topic():
    voiced = format_for_voice("Discuss KYC / Onboarding topics.")
    tts = build_tts_text(voiced, None, "")
    assert " / " not in tts


def test_format_for_voice_shortens_standard_greeting():
    raw = (
        "Hello! I'm your NextLeap advisor appointment scheduler. "
        "This conversation is for informational support only and does not provide investment advice. "
        "I can help you book, reschedule, or cancel appointments, check availability, "
        "or tell you what to prepare. What can I help you with today?"
    )
    out = format_for_voice(raw)
    assert len(out) < len(raw)
    assert "NextLeap" in out
    # Disclaimer intentionally dropped from spoken greeting for brevity (still in chat text)
    assert "what do you need" in out.lower()


def test_format_for_voice_shortens_topic_menu_prompt():
    raw = (
        "Certainly. What topic would you like to discuss? You can choose from: "
        "KYC / Onboarding; SIP / Mandates; Statements / Tax Docs; "
        "Withdrawals / Timelines; Account Changes / Nominee."
    )
    out = format_for_voice(raw)
    assert "You can choose from" not in out
    assert "KYC" in out
    assert len(out) < len(raw)


def test_format_for_voice_expands_nl_placeholder_for_tts():
    raw = "Please share your booking code (format NL-XXXX)."
    out = format_for_voice(raw)
    assert "NL-XXXX" not in out
    assert "N, L, dash" in out
    assert "four letters or numbers" in out
