from __future__ import annotations

import re

ALLOWED_TOPICS: tuple[str, ...] = (
    "KYC / Onboarding",
    "SIP / Mandates",
    "Statements / Tax Docs",
    "Withdrawals / Timelines",
    "Account Changes / Nominee",
)

_TOPIC_RULES: list[tuple[str, list[str]]] = [
    # "kyc change" must be checked before generic \bkyc\b (onboarding vs account updates).
    ("Account Changes / Nominee", [r"kyc change", r"account changes?", r"nominee"]),
    ("KYC / Onboarding", [r"\bkyc\b", r"onboarding"]),
    ("SIP / Mandates", [r"\bsip\b", r"mandates?", r"mandate"]),
    ("Statements / Tax Docs", [r"statements?", r"tax", r"docs?"]),
    ("Withdrawals / Timelines", [r"withdrawals?", r"timelines?"]),
]


def match_topic(text: str) -> str | None:
    tl = text.lower()
    for topic, pats in _TOPIC_RULES:
        for p in pats:
            if re.search(p, tl):
                return topic
    return None


def topics_menu() -> str:
    return (
        "KYC / Onboarding; SIP / Mandates; Statements / Tax Docs; "
        "Withdrawals / Timelines; Account Changes / Nominee."
    )
