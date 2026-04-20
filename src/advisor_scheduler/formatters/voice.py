"""Pure helpers to shorten assistant text for text-to-speech."""

from __future__ import annotations

import re

# Sentence from ``engine._secure_link_sentence`` (URL is a single token, often ends with a period).
_SECURE_LINK_SENTENCE_RE = re.compile(
    r"Secure link for contact details:\s*\S+\s*\.?",
    re.IGNORECASE,
)

_DIGIT_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)

# Placeholder in prompts (not a real code); TTS otherwise mumbles "ex ex ex ex" or skips it.
_BOOKING_FORMAT_PLACEHOLDER_RE = re.compile(r"\bNL-XXXX\b", re.I)
_BOOKING_FORMAT_PLACEHOLDER_SPOKEN = "N, L, dash, then four letters or numbers"


def _expand_booking_format_placeholder_for_voice(s: str) -> str:
    return _BOOKING_FORMAT_PLACEHOLDER_RE.sub(_BOOKING_FORMAT_PLACEHOLDER_SPOKEN, s)


# Standard engine copy (after `` / `` → `` or ``) so TTS returns faster on early turns.
_VOICE_INTRO_RE = re.compile(
    r"Hello! I'm your NextLeap advisor appointment scheduler\.\s*"
    r"This conversation is for informational support only and does not provide investment advice\.\s*"
    r"I can help you book, reschedule, or cancel appointments, check availability, or tell you what to prepare\.\s*"
    r"What can I help you with today\?",
    re.I,
)
_VOICE_TOPIC_MENU_TAIL = (
    r"What topic would you like(?: to discuss| to book for)?\?\s*"
    r"(?:You can choose from:\s*)?"
    r"KYC or Onboarding; SIP or Mandates; Statements or Tax Docs; "
    r"Withdrawals or Timelines; Account Changes or Nominee\.?"
)
_VOICE_TOPIC_PICK_RE = re.compile(
    r"(?:(?:Hello! This conversation is for informational support only and does not provide investment advice\.)\s*)?"
    r"(?:Certainly\.|Sure\. Let's book a slot\.|Great!|Let's book a slot\.)\s*"
    + _VOICE_TOPIC_MENU_TAIL,
    re.I,
)
_VOICE_TOPIC_PREPARE_RE = re.compile(
    r"Which topic would you like preparation guidance for\?\s*"
    r"(?:You can choose from:\s*)?"
    r"KYC or Onboarding; SIP or Mandates; Statements or Tax Docs; "
    r"Withdrawals or Timelines; Account Changes or Nominee\.?",
    re.I,
)

_VOICE_TOPIC_COMPACT = (
    "Which topic? KYC, SIP, statements, withdrawals, or account changes."
)
_VOICE_PREPARE_TOPIC_COMPACT = (
    "Which topic for preparation? KYC, SIP, statements, withdrawals, or account changes."
)
_VOICE_INTRO_COMPACT = (
    "Hi! I'm your NextLeap scheduler. "
    "I can help book, reschedule, cancel, or check availability. "
    "What do you need?"
)


def _compact_voice_boilerplate(s: str) -> str:
    s = _VOICE_INTRO_RE.sub(_VOICE_INTRO_COMPACT, s)
    s = _VOICE_TOPIC_PICK_RE.sub(_VOICE_TOPIC_COMPACT, s)
    s = _VOICE_TOPIC_PREPARE_RE.sub(_VOICE_PREPARE_TOPIC_COMPACT, s)
    return s


def format_for_voice(text: str) -> str:
    """
    Turn assistant reply text into shorter, speakable prose.

    No booking or state logic — only string cleanup for listening.
    """
    if not text:
        return ""
    s = text.strip()
    if not s:
        return ""
    s = _strip_markdown_bold(s)
    s = _bullets_to_spoken_phrases(s)
    s = _collapse_numbered_lines(s)
    s = _replace_slashes_for_speech(s)
    s = _compact_voice_boilerplate(s)
    s = _expand_booking_format_placeholder_for_voice(s)
    s = _normalize_whitespace(s)
    return s.strip()


def _strip_markdown_bold(s: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", s)


def _replace_slashes_for_speech(s: str) -> str:
    """Replace ' / ' (space-slash-space) with ' or ' to prevent TTS pause artifacts."""
    return re.sub(r" / ", " or ", s)


def _bullets_to_spoken_phrases(s: str) -> str:
    lines = s.split("\n")
    out: list[str] = []
    buf: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        if len(buf) == 1:
            out.append(buf[0])
        else:
            out.append("Options: " + "; ".join(buf))
        buf = []

    for line in lines:
        stripped = line.strip()
        m = re.match(r"^[-*]\s+(.+)$", stripped)
        if m:
            buf.append(m.group(1).strip())
            continue
        flush_buf()
        out.append(line)

    flush_buf()
    return "\n".join(out)


def _collapse_numbered_lines(s: str) -> str:
    """Turn '1) foo\\n2) bar' into one line when each line is short numbered item."""
    lines = [ln.rstrip() for ln in s.split("\n")]
    numbered: list[str] = []
    other: list[str] = []

    def flush_numbered() -> None:
        nonlocal numbered
        if len(numbered) >= 2:
            parts = []
            for item in numbered:
                m = re.match(r"^\s*\d+[\).\]]\s*(.+)$", item.strip())
                parts.append(m.group(1).strip() if m else item.strip())
            other.append(" ".join(parts))
        elif numbered:
            other.extend(numbered)
        numbered = []

    for ln in lines:
        if re.match(r"^\s*\d+[\).\]]\s*\S", ln):
            numbered.append(ln)
        else:
            flush_numbered()
            other.append(ln)
    flush_numbered()
    return "\n".join(other)


def _normalize_whitespace(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def expand_booking_code_for_tts(code: str) -> str:
    """
    Spell an NL-XXXX-style code for browser speechSynthesis (letters and digit words).
    Example: NL-HIM6 -> "N, L, dash, H, I, M, six"
    """
    parts: list[str] = []
    for ch in code.strip().upper():
        if ch == "-":
            parts.append("dash")
        elif ch.isalpha():
            parts.append(ch)
        elif ch.isdigit():
            parts.append(_DIGIT_WORDS[int(ch)])
    return ", ".join(parts)


def replace_secure_link_for_spoken(text: str, spoken_line: str) -> str:
    """Replace the engine's secure-link sentence with short TTS-only copy (no raw URL)."""
    if not text or not spoken_line.strip():
        return text
    return _SECURE_LINK_SENTENCE_RE.sub(spoken_line.strip(), text)


def expand_booking_code_in_text(text: str, booking_code: str | None) -> str:
    """Replace occurrences of ``booking_code`` with spelled TTS form (case-insensitive)."""
    if not text or not booking_code:
        return text
    code = booking_code.strip()
    if not code:
        return text
    spoken = expand_booking_code_for_tts(code)
    return re.compile(re.escape(code), re.IGNORECASE).sub(spoken, text)


def build_tts_text(
    voice_display_text: str,
    booking_code: str | None,
    secure_followup_spoken: str,
) -> str:
    """
    Spoken-only string for the voice channel: no raw secure URL, spelled booking codes.
    ``voice_display_text`` should already be passed through ``format_for_voice``.
    """
    t = voice_display_text.strip()
    if not t:
        return ""
    t = replace_secure_link_for_spoken(t, secure_followup_spoken)
    t = _normalize_whitespace(t)
    t = expand_booking_code_in_text(t, booking_code)
    t = _normalize_whitespace(t)
    return t.strip()
