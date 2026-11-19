from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from wyscout import panel_auth
from wyscout.panel_auth import PanelAuth

FAKE_PASSWORD = 'synthetic test-only panel phrase'


def test_session_expires_and_changing_password_revokes_access():
    now = 1000
    auth = PanelAuth(clock=lambda: now)
    session, error = auth.login(FAKE_PASSWORD, FAKE_PASSWORD)
    assert error is None and auth.authorized(session, FAKE_PASSWORD)
    assert not auth.authorized(session, FAKE_PASSWORD + ' rotated')
    assert not auth.authorized(None, FAKE_PASSWORD)
    now += 8 * 60 * 60
    assert not auth.authorized(session, FAKE_PASSWORD)


def test_failed_logins_are_rate_limited():
    now = 1000
    auth = PanelAuth(clock=lambda: now)
    for _ in range(5):
        assert auth.login('wrong', FAKE_PASSWORD) == (None, 'Nieprawidłowe hasło do panelu.')
    session, error = auth.login(FAKE_PASSWORD, FAKE_PASSWORD)
    assert session is None and error.startswith('Zbyt wiele')
    now += 60
    assert auth.login(FAKE_PASSWORD, FAKE_PASSWORD)[0] is not None


class Stop(Exception):
    pass


class Rerun(Exception):
    pass


class PanelControls:
    """Isolate the gate from Streamlit's server and session lifecycle."""

    def __init__(self):
        self.session_state = {}
        self.messages = []
        self.supplied = ''
        self.submitted = False
        self.logout = False
        self.sidebar = SimpleNamespace(button=lambda _: self.logout)

    def subheader(self, text):
        self.messages.append(text)

    caption = warning = error = subheader

    def form(self, _, *, clear_on_submit):
        assert clear_on_submit
        return nullcontext()

    def text_input(self, _, *, type, max_chars):
        assert type == 'password'
        return self.supplied

    def form_submit_button(self, _, *, type):
        return self.submitted

    def stop(self):
        raise Stop

    def rerun(self):
        raise Rerun


def test_panel_gate_keeps_private_actions_locked_until_login_and_after_logout(monkeypatch):
    ui, auth = PanelControls(), PanelAuth()
    monkeypatch.setattr(panel_auth, 'st', ui)
    monkeypatch.setattr(panel_auth, '_panel_auth', lambda: auth)
    assert panel_auth.panel_access(None) is False
    assert panel_auth.panel_access('too short') is False
    with pytest.raises(Stop):
        panel_auth.panel_access(FAKE_PASSWORD)
    ui.supplied, ui.submitted = 'wrong', True
    with pytest.raises(Stop):
        panel_auth.panel_access(FAKE_PASSWORD)
    assert 'Nieprawidłowe hasło do panelu.' in ui.messages
    assert not panel_auth.panel_is_authorized(FAKE_PASSWORD)
    ui.supplied = FAKE_PASSWORD
    with pytest.raises(Rerun):
        panel_auth.panel_access(FAKE_PASSWORD)
    assert panel_auth.panel_access(FAKE_PASSWORD) is True
    ui.logout = True
    with pytest.raises(Rerun):
        panel_auth.panel_access(FAKE_PASSWORD)
    assert not panel_auth.panel_is_authorized(FAKE_PASSWORD)
