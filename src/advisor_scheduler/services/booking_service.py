from __future__ import annotations

import secrets
import string
from typing import Callable

from advisor_scheduler.types.models import Booking, BookingStatus, Slot


class BookingService:
    """In-memory booking store; survives session timeout (per architecture)."""

    def __init__(self, rng: Callable[[int], str] | None = None) -> None:
        self._by_code: dict[str, Booking] = {}
        self._rng = rng or self._default_code

    @staticmethod
    def _default_code(_: int) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(4))

    def generate_code(self) -> str:
        for _ in range(200):
            suffix = self._rng(4)
            code = f"NL-{suffix}"
            if code not in self._by_code:
                return code
        raise RuntimeError("Could not allocate unique booking code")

    def create_booking(
        self,
        *,
        topic: str,
        status: BookingStatus,
        slot: Slot | None,
        requested_day: str | None,
        requested_time_window: str | None,
        previous_slot_label: str | None = None,
    ) -> Booking:
        code = self.generate_code()
        b = Booking(
            code=code,
            topic=topic,
            status=status,
            slot=slot,
            requested_day=requested_day,
            requested_time_window=requested_time_window,
            previous_slot_label=previous_slot_label,
        )
        self._by_code[code] = b
        return b

    def get(self, code: str) -> Booking | None:
        return self._by_code.get(code.upper())

    def update_booking(self, booking: Booking) -> None:
        self._by_code[booking.code] = booking

    def cache_booking(self, booking: Booking) -> Booking:
        self.update_booking(booking)
        return booking
