from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns for obvious PII / identifiers (best-effort; conversation must still avoid asking)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_IN = re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")
_PHONE_GENERIC = re.compile(r"\b\d{10}\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_ACCOUNTISH = re.compile(r"\b(?:folio|account|client)\s*(?:id|no\.?|number)?\s*[:\-]?\s*\d{6,}\b", re.I)

_ADVICE_PATTERNS = [
    re.compile(r"\bshould i (buy|sell|redeem|switch|hold)\b", re.I),
    re.compile(r"\bwhich (fund|stock|scheme)\b", re.I),
    re.compile(r"\bwhere should i invest\b", re.I),
    re.compile(r"\bgive me (investment )?advice\b", re.I),
    re.compile(r"\brecommend (a|an|the)?\s*\w+\s*(fund|stock|scheme)\b", re.I),
]


@dataclass(frozen=True)
class ComplianceResult:
    ok: bool
    """If False, respond with `message` and do not advance state."""

    message: str


def compliance_guard(message: str) -> ComplianceResult:
    """
    Runs on every user turn. Blocks collection/continuing when PII or investment
    advice is detected in the user's message.
    """
    text = message.strip()
    if not text:
        return ComplianceResult(ok=True, message="")

    if _EMAIL.search(text) or _PHONE_IN.search(text) or _PHONE_GENERIC.search(text):
        return ComplianceResult(
            ok=False,
            message=(
                "Please don't share personal or account details here. I can continue "
                "helping with the topic and preferred time, and you can submit contact "
                "details later through the secure link."
            ),
        )

    if _PAN.search(text) or _AADHAAR.search(text) or _ACCOUNTISH.search(text):
        return ComplianceResult(
            ok=False,
            message=(
                "I can't collect or use account identifiers in this chat. "
                "Please share only scheduling preferences here."
            ),
        )

    for pat in _ADVICE_PATTERNS:
        if pat.search(text):
            return ComplianceResult(
                ok=False,
                message=(
                    "I'm not able to provide investment advice here. I can help with "
                    "informational support or booking an advisor appointment."
                ),
            )

    return ComplianceResult(ok=True, message="")
