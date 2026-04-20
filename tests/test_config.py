import pytest
from pydantic_settings import SettingsConfigDict

from advisor_scheduler.config import DEFAULT_SECURE_DETAILS_PATH, Settings


class _S(Settings):
    """Settings without loading repo ``.env`` so tests stay deterministic."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def test_resolved_full_secure_url():
    s = _S(secure_details_base_url="https://secure.nextleap.test/details")
    assert s.resolved_secure_details_base_url() == "https://secure.nextleap.test/details"
    assert s.secure_details_url_is_valid()


def test_rejects_example_com_full_url():
    s = _S(secure_details_base_url="https://example.com/details")
    assert s.resolved_secure_details_base_url() is None
    assert not s.secure_details_url_is_valid()


def test_path_with_public_base_url():
    s = _S(
        public_base_url="https://scheduler.example.com",
        secure_details_base_url="/secure-details",
    )
    assert s.resolved_secure_details_base_url() == "https://scheduler.example.com/secure-details"
    assert s.secure_details_url_is_valid()


def test_path_requires_public_base():
    s = _S(secure_details_base_url="/secure-details")
    assert s.resolved_secure_details_base_url() is None


def test_empty_secure_uses_default_path_with_public():
    s = _S(public_base_url="http://127.0.0.1:8000")
    assert s.resolved_secure_details_base_url() == f"http://127.0.0.1:8000{DEFAULT_SECURE_DETAILS_PATH}"


def test_rejects_placeholder_public_host():
    s = _S(
        public_base_url="https://your-domain.com",
        secure_details_base_url="",
    )
    assert s.resolved_secure_details_base_url() is None


@pytest.mark.parametrize(
    "pub,path,expected",
    [
        ("https://a.test", "/x/y", "https://a.test/x/y"),
        ("https://a.test/", "/x/y", "https://a.test/x/y"),
    ],
)
def test_urljoin_variants(pub: str, path: str, expected: str):
    s = _S(public_base_url=pub, secure_details_base_url=path)
    assert s.resolved_secure_details_base_url() == expected


def test_api_port_reads_port_env(monkeypatch):
    monkeypatch.setenv("PORT", "8010")
    s = _S()
    assert s.api_port == 8010


def test_api_host_from_env(monkeypatch):
    monkeypatch.setenv("ADVISOR_API_HOST", "0.0.0.0")
    s = _S()
    assert s.api_host == "0.0.0.0"
