from advisor_scheduler.guards.compliance import compliance_guard


def test_pii_email_blocked():
    r = compliance_guard("reach me at user@example.com")
    assert not r.ok


def test_investment_advice_blocked():
    r = compliance_guard("Should I sell my mutual funds now?")
    assert not r.ok


def test_normal_message_ok():
    r = compliance_guard("Book me Monday morning for KYC")
    assert r.ok
