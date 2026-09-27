"""Which lessons talk through Asterisk (решение пользователя 27.09.2026): only those with the
«звонок на телефон» box, and only while telephony is up."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.telephony import service as telephony


def test_for_session_needs_both_telephony_and_the_phone_box(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(telephony, "telephony_active", lambda: True)
    assert telephony.for_session(SimpleNamespace(phone_calls=True)) is True
    assert telephony.for_session(SimpleNamespace(phone_calls=False)) is False
    assert telephony.for_session(None) is False
    monkeypatch.setattr(telephony, "telephony_active", lambda: False)
    assert telephony.for_session(SimpleNamespace(phone_calls=True)) is False
