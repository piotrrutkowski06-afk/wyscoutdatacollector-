from collections import deque
from dataclasses import dataclass
from hmac import compare_digest, digest
from secrets import token_bytes
from threading import Lock
from time import monotonic

import streamlit as st

MIN_PASSWORD_LENGTH = 20
_MAX_PASSWORD_LENGTH = 256
_SESSION_KEY = '_wyscout_panel_session'
_WINDOW_SECONDS = 60
_MAX_FAILURES = 5
_SESSION_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class PanelSession:
    expires_at: float
    proof: bytes


class PanelAuth:
    """Rate limit new logins per app process; keep authentication in the Streamlit session."""

    def __init__(self, clock=monotonic):
        self._clock = clock
        self._proof_key = token_bytes(32)
        self._failures = deque()
        self._lock = Lock()

    def _proof(self, password):
        return digest(self._proof_key, password.encode('utf-8'), 'sha256')

    def login(self, supplied, expected):
        with self._lock:
            now = self._clock()
            while self._failures and self._failures[0] <= now - _WINDOW_SECONDS:
                self._failures.popleft()
            if len(self._failures) >= _MAX_FAILURES:
                return None, 'Zbyt wiele prób logowania. Odczekaj minutę i spróbuj ponownie.'
            proof = self._proof(expected)
            if not compare_digest(self._proof(supplied), proof):
                self._failures.append(now)
                return None, 'Nieprawidłowe hasło do panelu.'
            return PanelSession(now + _SESSION_SECONDS, proof), None

    def authorized(self, session, expected):
        return (isinstance(session, PanelSession)
                and self._clock() < session.expires_at
                and compare_digest(session.proof, self._proof(expected)))


@st.cache_resource
def _panel_auth():
    return PanelAuth()


def _configured(password):
    return bool(password and len(password.strip()) >= MIN_PASSWORD_LENGTH and len(password) <= _MAX_PASSWORD_LENGTH)


def panel_is_authorized(password):
    return _configured(password) and _panel_auth().authorized(st.session_state.get(_SESSION_KEY), password)


def panel_access(password):
    if panel_is_authorized(password):
        if st.sidebar.button('Wyloguj się'):
            st.session_state.clear()
            st.rerun()
        return True

    st.session_state.pop(_SESSION_KEY, None)
    if not password:
        return False
    if not _configured(password):
        st.warning(f'Hasło panelu w Secrets musi mieć od {MIN_PASSWORD_LENGTH} do {_MAX_PASSWORD_LENGTH} znaków.')
        return False

    st.subheader('Logowanie do panelu')
    st.caption('Wpisz hasło administratora panelu. To nie jest hasło do Wyscout.')
    with st.form('panel_login', clear_on_submit=True):
        supplied = st.text_input('Hasło administratora', type='password', max_chars=_MAX_PASSWORD_LENGTH)
        submitted = st.form_submit_button('Zaloguj się', type='primary')
    if submitted:
        session, error = _panel_auth().login(supplied, password)
        if session is not None:
            st.session_state[_SESSION_KEY] = session
            st.rerun()
        st.error(error)
    st.stop()
