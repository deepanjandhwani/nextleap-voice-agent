from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from advisor_scheduler.config import Settings
from advisor_scheduler.llm.response_schema import (
    ALLOWED_STATES,
    DayResolution,
    DayResolutionOutcome,
    GeminiTurnDecision,
)

logger = logging.getLogger(__name__)

_STATE_ALIASES: dict[str, str] = {
    "awaiting_intent": "identify_intent",
    "await_intent": "identify_intent",
    "waiting_intent": "identify_intent",
    "initial": "greeting",
    "start": "greeting",
    "close": "closing",
    "done": "closing",
    "end": "closing",
    "goodbye": "closing",
    "farewell": "closing",
    "identify": "identify_intent",
    "intent": "identify_intent",
    "topic": "collect_topic",
    "time": "collect_time",
    "slots": "offer_slots",
    "confirm": "confirm_slot",
    "waitlist": "offer_waitlist",
    "cancel": "confirm_cancel",
}


class LlmClientError(RuntimeError):
    pass


class LlmClient(Protocol):
    def complete_json(self, prompt: str) -> GeminiTurnDecision:
        ...

    def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
        ...


def _normalize_payload(payload: dict) -> dict:
    """Best-effort fix for common LLM mistakes before Pydantic validation."""
    raw_state = payload.get("next_state")
    if isinstance(raw_state, str) and raw_state not in ALLOWED_STATES:
        mapped = _STATE_ALIASES.get(raw_state.lower().strip())
        if mapped:
            logger.warning("Normalized hallucinated state %r → %r", raw_state, mapped)
            payload["next_state"] = mapped
        else:
            logger.warning("Unknown state %r from LLM, defaulting to identify_intent", raw_state)
            payload["next_state"] = "identify_intent"
    return payload


@dataclass
class GeminiClient:
    settings: Settings

    def _generate_json(self, prompt: str) -> dict:
        if not self.settings.gemini_api_key:
            raise LlmClientError("GEMINI_API_KEY is not configured")

        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as exc:
            raise LlmClientError(
                "Gemini client dependency is missing. Install the llm extra to enable Gemini."
            ) from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.settings.gemini_temperature,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise LlmClientError("Gemini returned an empty response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmClientError("Gemini returned invalid JSON") from exc

    def complete_json(self, prompt: str) -> GeminiTurnDecision:
        payload = self._generate_json(prompt)
        payload = _normalize_payload(payload)
        try:
            return GeminiTurnDecision.model_validate(payload)
        except ValidationError as exc:
            logger.error("Gemini response failed validation: %s\nPayload: %s", exc, payload)
            raise LlmClientError(f"Gemini response failed schema validation: {exc}") from exc

    def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
        payload = self._generate_json(prompt)
        try:
            resolution = DayResolution.model_validate(payload)
        except ValidationError as exc:
            logger.error("Gemini day resolution failed validation: %s\nPayload: %s", exc, payload)
            raise LlmClientError(f"Gemini day resolution failed schema validation: {exc}") from exc
        if resolution.is_ambiguous:
            return DayResolutionOutcome(
                None,
                True,
                resolution.reason,
                resolution.normalized_time_window,
            )
        raw = (resolution.resolved_date_iso or "").strip()
        if not raw:
            return DayResolutionOutcome(
                None,
                False,
                resolution.reason,
                resolution.normalized_time_window,
            )
        try:
            resolved = date.fromisoformat(raw)
        except ValueError:
            return DayResolutionOutcome(None, True, "invalid_iso", resolution.normalized_time_window)
        return DayResolutionOutcome(
            resolved,
            False,
            resolution.reason,
            resolution.normalized_time_window,
        )


@dataclass
class StubLlmClient:
    """Test helper; returns predefined decisions in sequence."""

    responses: list[GeminiTurnDecision]
    day_resolution_responses: list[DayResolutionOutcome] = field(default_factory=list)

    def complete_json(self, prompt: str) -> GeminiTurnDecision:
        if not self.responses:
            raise LlmClientError("No stub Gemini responses remaining")
        return self.responses.pop(0)

    def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
        if not self.day_resolution_responses:
            raise LlmClientError("No stub day resolution responses remaining")
        return self.day_resolution_responses.pop(0)
