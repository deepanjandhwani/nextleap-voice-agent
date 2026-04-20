from advisor_scheduler.services.booking_service import BookingService


def test_unique_codes():
    bs = BookingService()
    codes = {bs.generate_code() for _ in range(50)}
    assert len(codes) == 50
    assert all(c.startswith("NL-") and len(c) == 7 for c in codes)
