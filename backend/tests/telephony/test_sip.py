"""SIP accounts: password storage, endpoint naming, the generated PJSIP endpoints file."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.telephony import settings as telephony_settings
from app.telephony import sip


def test_password_roundtrip_and_tamper():
    token = sip.encrypt_password("s3cret-пароль")
    assert token != "s3cret-пароль"
    assert sip.decrypt_password(token) == "s3cret-пароль"
    assert sip.decrypt_password(token[:-3] + "xyz") is None
    assert sip.decrypt_password("not-a-token") is None


def test_new_passwords_differ():
    assert sip.new_password() != sip.new_password()
    assert len(sip.new_password()) >= 20


@pytest.mark.parametrize(
    ("login", "expected"),
    [("student1", "student1"), ("Ivanov.II", "ivanovii"), ("op_12-a", "op_12-a"), ("иван", "")],
)
def test_endpoint_login(login: str, expected: str):
    assert sip.endpoint_login(login) == expected


def test_render_endpoints_uses_templates_and_codecs():
    config = telephony_settings.defaults()
    config.webrtc_codecs = ["opus", "alaw"]
    config.phone_codecs = ["g722"]
    text = sip.render_endpoints(
        [sip.SipAccount("student1", "pw-1"), sip.SipAccount("x", "pw-2")], config
    )
    assert "[stu-student1](webrtc-endpoint)" in text
    assert "[stu-student1](webrtc-auth)" in text
    assert "[stu-student1](webrtc-aor)" in text
    assert "[phone-student1](phone-endpoint)" in text
    assert "password=pw-1" in text
    assert "allow=opus,alaw" in text
    assert "allow=g722" in text
    assert text.count("password=pw-2") == 2


def test_write_endpoints_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "asterisk_config_dir", str(tmp_path / "gen"))
    path = sip.write_endpoints([sip.SipAccount("s", "p")], telephony_settings.defaults())
    assert path == tmp_path / "gen" / sip.ENDPOINTS_FILE
    assert "[stu-s](webrtc-endpoint)" in path.read_text("utf-8")
    assert not path.with_suffix(".tmp").exists()


def test_write_endpoints_without_folder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "asterisk_config_dir", None)
    assert sip.write_endpoints([], telephony_settings.defaults()) is None


def test_settings_merge_and_codec_validation():
    merged = telephony_settings.merge({"ring_timeout_seconds": 20, "unknown": 1})
    assert merged.ring_timeout_seconds == 20
    assert merged.webrtc_codecs == telephony_settings.DEFAULT_WEBRTC_CODECS
    assert telephony_settings.clean_codecs([" Opus", "alaw"]) == ["opus", "alaw"]
    with pytest.raises(ValueError):
        telephony_settings.clean_codecs(["mp3"])
    with pytest.raises(ValueError):
        telephony_settings.clean_codecs([])
