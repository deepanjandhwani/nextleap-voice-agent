from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from advisor_scheduler.config import Settings
from advisor_scheduler.core.session import Session
from advisor_scheduler.llm.gemini_client import LlmClient
from advisor_scheduler.llm.prompt_builder import build_day_resolution_prompt
from advisor_scheduler.types.models import Slot

IST = ZoneInfo("Asia/Kolkata")
_logger = logging.getLogger(__name__)


def validate_resolved_day(resolved: date, ref: datetime) -> bool:
    """True if ``resolved`` is today or a future calendar day in IST (not before ``ref``'s IST date)."""
    today_ist = ref.astimezone(IST).date()
    return resolved >= today_ist


@dataclass(frozen=True)
class DayResolutionResult:
    resolved_date: date | None
    is_ambiguous: bool
    reason: str | None = None
    normalized_time_window: str | None = None

# Architecture mock: weekday (0=Mon .. 6=Sun) -> list of (hour, minute)
_MOCK: dict[int, list[tuple[int, int]]] = {
    0: [(10, 0), (14, 0)],  # Monday
    1: [(11, 0)],  # Tuesday
    2: [(10, 0), (15, 0)],  # Wednesday
    3: [(14, 0)],  # Thursday
}

_DAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

_SPELLED_CARDINALS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
}

_SPELLED_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "twenty first": 21,
    "twenty second": 22,
    "twenty third": 23,
    "twenty fourth": 24,
    "twenty fifth": 25,
    "twenty sixth": 26,
    "twenty seventh": 27,
    "twenty eighth": 28,
    "twenty ninth": 29,
    "thirtieth": 30,
    "thirty first": 31,
}


def _normalize_spelled_numbers(text: str) -> str:
    """Normalize spoken day numbers (1-31) into digits for date regex parsing."""
    phrase_to_number: dict[str, int] = dict(_SPELLED_CARDINALS)
    phrase_to_number.update(_SPELLED_ORDINALS)
    normalized = text
    # Prefer multi-word phrases first (e.g., "twenty fifth" before "fifth").
    for phrase in sorted(phrase_to_number, key=len, reverse=True):
        parts = [re.escape(part) for part in phrase.split()]
        phrase_pattern = r"[-\s]+".join(parts)
        normalized = re.sub(
            rf"\b{phrase_pattern}\b",
            str(phrase_to_number[phrase]),
            normalized,
        )
    return normalized


def _format_ist(dt: datetime) -> str:
    return dt.strftime("%A, %d %b %Y at %H:%M IST")


def _slot_for(anchor: date, hm: tuple[int, int]) -> Slot:
    # anchor is the concrete calendar day for this weekday occurrence
    h, m = hm
    start = datetime(anchor.year, anchor.month, anchor.day, h, m, tzinfo=IST)
    return Slot(start=start, label=_format_ist(start))


def _next_weekday_from(ref: date, weekday: int) -> date:
    delta = (weekday - ref.weekday()) % 7
    return ref + timedelta(days=delta)


def _resolve_explicit_date(
    *,
    day: int,
    month: int,
    year: int | None,
    ref_d: date,
) -> date | None:
    candidate_year = year or ref_d.year
    try:
        candidate = date(candidate_year, month, day)
    except ValueError:
        return None

    if year is not None:
        return candidate

    if candidate >= ref_d:
        return candidate

    try:
        rolled = date(ref_d.year + 1, month, day)
    except ValueError:
        return None
    return rolled


def _apply_explicit_date_boundary(text: str, match_start: int, resolved: date) -> date:
    """Apply inclusive/exclusive phrasing immediately before an explicit calendar date."""
    prefix = text[max(0, match_start - 64) : match_start].lower().rstrip()
    if re.search(r"(?:^|\s)on\s+or\s+after\s*$", prefix):
        return resolved
    if re.search(r"(?:^|\s)after(?:\s+the)?\s*$", prefix):
        return resolved + timedelta(days=1)
    if re.search(r"(?:^|\s)before(?:\s+the)?\s*$", prefix):
        return resolved - timedelta(days=1)
    return resolved


def _parse_explicit_dates(text: str, ref_d: date) -> list[date]:
    patterns = (
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{_MONTH_PATTERN})(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
        rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
        r"\b(?P<day>\d{1,2})[/-](?P<month>\d{1,2})(?:[/-](?P<year>\d{2,4}))?\b",
    )
    out: list[date] = []

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            day = int(match.group("day"))
            month_token = match.group("month").lower()
            month = int(month_token) if month_token.isdigit() else _MONTH_NAMES[month_token]
            year_text = match.groupdict().get("year")
            year = int(year_text) if year_text else None
            if year is not None and year < 100:
                year += 2000
            resolved = _resolve_explicit_date(day=day, month=month, year=year, ref_d=ref_d)
            if resolved is not None:
                resolved = _apply_explicit_date_boundary(text, match.start(), resolved)
            if resolved is not None and resolved not in out:
                out.append(resolved)

    return out


def _has_explicit_past_date(text: str, ref_d: date) -> bool:
    normalized = _normalize_spelled_numbers(text)
    patterns = (
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{_MONTH_PATTERN})(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
        rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
        r"\b(?P<day>\d{1,2})[/-](?P<month>\d{1,2})(?:[/-](?P<year>\d{2,4}))?\b",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            year_text = match.groupdict().get("year")
            if not year_text:
                continue
            day = int(match.group("day"))
            month_token = match.group("month").lower()
            month = int(month_token) if month_token.isdigit() else _MONTH_NAMES[month_token]
            year = int(year_text)
            if year < 100:
                year += 2000
            try:
                resolved = date(year, month, day)
            except ValueError:
                continue
            resolved = _apply_explicit_date_boundary(normalized, match.start(), resolved)
            if resolved < ref_d:
                return True
    return False


def parse_day_token(text: str, ref: datetime) -> tuple[date | None, bool]:
    """
    Returns (resolved_date, ambiguous) where ambiguous means we need clarification.
    """
    t = text.lower()
    normalized = _normalize_spelled_numbers(t)
    ref_d = ref.astimezone(IST).date()

    candidates: list[date] = []

    def _add_candidate(candidate: date) -> None:
        if candidate not in candidates:
            candidates.append(candidate)

    matched_day_after_tomorrow = False
    if re.search(r"\bday after tomorrow\b", t):
        matched_day_after_tomorrow = True
        _add_candidate(ref_d + timedelta(days=2))
    if re.search(r"\bin two days\b", t) or re.search(r"\btwo days from now\b", t):
        matched_day_after_tomorrow = True
        _add_candidate(ref_d + timedelta(days=2))
    if re.search(r"\bday after tmrw\b", t):
        matched_day_after_tomorrow = True
        _add_candidate(ref_d + timedelta(days=2))

    if re.search(r"\btoday\b", t):
        _add_candidate(ref_d)
    if re.search(r"\btomorrow\b", t) and not matched_day_after_tomorrow:
        _add_candidate(ref_d + timedelta(days=1))

    for explicit_date in _parse_explicit_dates(normalized, ref_d):
        _add_candidate(explicit_date)

    for name, weekday in _DAY_NAMES.items():
        if re.search(rf"\b{name}\b", t):
            _add_candidate(_next_weekday_from(ref_d, weekday))

    if len(candidates) == 1:
        only = candidates[0]
        if not validate_resolved_day(only, ref):
            return None, True
        return only, False
    if len(candidates) > 1:
        return None, True

    # vague
    if re.search(r"\b(next week|later|sometime)\b", t):
        return None, True
    if re.search(r"\b(morning|afternoon|evening)\b", t) and not any(
        re.search(rf"\b{n}\b", t) for n in _DAY_NAMES
    ):
        return None, True

    return None, True


def resolve_user_day(message: str, ref: datetime, *, llm: LlmClient, session: Session) -> DayResolutionResult:
    """Resolve one IST calendar day and optional normalized time window."""
    inferred_window = infer_time_window(message)
    resolved, ambiguous = parse_day_token(message, ref)
    if not ambiguous and resolved is not None:
        if not validate_resolved_day(resolved, ref):
            return DayResolutionResult(None, True, "past_date", inferred_window)
        return DayResolutionResult(resolved, False, None, inferred_window)
    if _has_explicit_past_date(message.lower(), ref.astimezone(IST).date()):
        return DayResolutionResult(None, True, "past_date", inferred_window)
    try:
        outcome = llm.resolve_requested_day(build_day_resolution_prompt(session, message))
    except Exception as exc:
        _logger.warning("Day resolution LLM call failed: %s", exc, exc_info=True)
        return DayResolutionResult(None, True, "llm_resolution_failed", inferred_window)
    normalized_window = inferred_window or outcome.normalized_time_window
    if outcome.is_ambiguous or outcome.resolved_date is None:
        return DayResolutionResult(None, True, outcome.reason, normalized_window)
    if not validate_resolved_day(outcome.resolved_date, ref):
        return DayResolutionResult(None, True, "past_date", normalized_window)
    return DayResolutionResult(outcome.resolved_date, False, outcome.reason, normalized_window)


def infer_time_window(text: str) -> str | None:
    tl = text.lower()
    if re.search(r"\b(morning|am)\b", tl):
        return "morning"
    if re.search(r"\b(afternoon|pm)\b", tl) and "evening" not in tl:
        return "afternoon"
    if re.search(r"\b(evening|night)\b", tl):
        return "evening"
    return None


def _window_allows(window: str | None, hour: int) -> bool:
    if window is None:
        return True
    if window == "morning":
        return 9 <= hour < 12
    if window == "afternoon":
        return 12 <= hour < 17
    if window == "evening":
        return 17 <= hour < 21
    return True


class SlotService:
    """Mock availability by default; with ``use_mcp`` uses Calendar free/busy via MCP."""

    def __init__(
        self,
        now_fn: Callable[[], datetime] | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(IST))
        self._settings = settings
        self._mcp_source_cache: Any | None = None
        self._last_mcp_freebusy_ok = True
        self._last_mcp_freebusy_failure: str | None = None

    @property
    def last_mcp_freebusy_ok(self) -> bool:
        """False when the last MCP calendar free/busy query failed (fail-closed; do not trust slot lists)."""
        return self._last_mcp_freebusy_ok

    @property
    def last_mcp_freebusy_failure(self) -> str | None:
        """Stable reason code when ``last_mcp_freebusy_ok`` is False; None if last call succeeded."""
        return self._last_mcp_freebusy_failure

    def _mcp_source(self) -> Any:
        if self._mcp_source_cache is None:
            from advisor_scheduler.integrations.google_workspace.mcp import load_mcp_client_source

            if self._settings is None:
                raise RuntimeError("MCP slot source requested without settings")
            self._mcp_source_cache = load_mcp_client_source(self._settings)
        return self._mcp_source_cache

    def now(self) -> datetime:
        return self._now_fn()

    def matching_slots(
        self,
        *,
        preferred_day: date | None,
        time_window: str | None,
        limit: int = 2,
    ) -> list[Slot]:
        if preferred_day is None:
            return []

        self._last_mcp_freebusy_ok = True
        self._last_mcp_freebusy_failure = None
        s = self._settings
        has_mcp_config = s is not None and s.use_mcp and bool(s.google_calendar_id)
        if has_mcp_config:
            from advisor_scheduler.integrations.google_workspace.mcp import matching_slots_via_mcp

            slots, ok, failure = matching_slots_via_mcp(
                s,
                self._mcp_source(),
                preferred_day=preferred_day,
                time_window=time_window,
                limit=limit,
            )
            self._last_mcp_freebusy_ok = ok
            self._last_mcp_freebusy_failure = failure
            return slots

        wd = preferred_day.weekday()
        if wd not in _MOCK:
            return []

        slots: list[Slot] = []
        for hm in _MOCK[wd]:
            h, _ = hm
            if not _window_allows(time_window, h):
                continue
            slots.append(_slot_for(preferred_day, hm))
            if len(slots) >= limit:
                break
        return slots

    def availability_windows_for_day(self, day: date) -> list[str]:
        """Return human-readable window strings for a day (IST)."""
        self._last_mcp_freebusy_ok = True
        self._last_mcp_freebusy_failure = None
        s = self._settings
        has_mcp_config = s is not None and s.use_mcp and bool(s.google_calendar_id)
        if has_mcp_config:
            from advisor_scheduler.integrations.google_workspace.mcp import availability_labels_via_mcp

            labels, ok, failure = availability_labels_via_mcp(s, self._mcp_source(), day=day, limit=10)
            self._last_mcp_freebusy_ok = ok
            self._last_mcp_freebusy_failure = failure
            return labels
        slots = self.matching_slots(preferred_day=day, time_window=None, limit=10)
        return [s.label for s in slots]
