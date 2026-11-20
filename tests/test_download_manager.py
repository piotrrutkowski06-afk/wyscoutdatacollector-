from pathlib import Path

from streamlit.testing.v1 import AppTest

from wyscout import jobs, panel_auth
from wyscout.settings import CollectorError


def test_manager_can_test_connection_without_an_export_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('WYSCOUT_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('WYSCOUT_ADAPTER_CONFIG', str(tmp_path / 'missing.json'))
    monkeypatch.setenv('WYSCOUT_USERNAME', 'test-user')
    monkeypatch.setenv('WYSCOUT_PASSWORD', '  ')
    monkeypatch.setattr(panel_auth, 'panel_access', lambda _: True)
    monkeypatch.setattr(panel_auth, 'panel_is_authorized', lambda _: True)
    called = []

    def probe(_store, _creds, *, on_w6_preview, allow_force_login):
        assert on_w6_preview is None
        called.append(allow_force_login)
        return True

    monkeypatch.setattr(jobs, 'probe', probe)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=12).run()
    app.sidebar.radio[0].set_value('Pobieranie').run()

    def button(label):
        return next(item for item in app.button if item.label == label)

    assert not app.exception
    assert any('Diagnostyka połączenia: SC-W6-2' in item.value for item in app.caption)
    assert button('Test Wyscout Connection').disabled
    assert any('WYSCOUT_PASSWORD' in message.value for message in app.warning)
    monkeypatch.setenv('WYSCOUT_PASSWORD', 'test-only')
    app.run()
    assert not button('Test Wyscout Connection').disabled
    assert button('Pobierz listę rozgrywek').disabled
    assert button('Start Full Download').disabled
    button('Test Wyscout Connection').click().run()
    assert called == [False]
    assert any('Test nie pobierał listy lig' in message.value for message in app.success)
    app.checkbox(key='allow_force_login').check().run()
    button('Test Wyscout Connection').click().run()
    assert not app.checkbox(key='allow_force_login').value
    button('Test Wyscout Connection').click().run()
    assert called == [False, True, False]
    assert not app.exception


def test_preview_is_opt_in_and_is_not_replayed_in_another_session(tmp_path, monkeypatch):
    monkeypatch.setenv('WYSCOUT_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('WYSCOUT_ADAPTER_CONFIG', str(tmp_path / 'missing.json'))
    monkeypatch.setenv('WYSCOUT_USERNAME', 'test-user')
    monkeypatch.setenv('WYSCOUT_PASSWORD', 'test-only')
    monkeypatch.setattr(panel_auth, 'panel_access', lambda _: True)
    monkeypatch.setattr(panel_auth, 'panel_is_authorized', lambda _: True)
    captures = []
    test_image = b'\xff\xd8\xff' + b'test-only-image'

    def probe(_store, _creds, *, on_w6_preview, allow_force_login):
        assert not allow_force_login
        captures.append(on_w6_preview is not None)
        if on_w6_preview is not None:
            on_w6_preview(test_image)
        raise CollectorError('W6 · Przykład.')

    monkeypatch.setattr(jobs, 'probe', probe)
    entry = Path(__file__).resolve().parents[1] / 'app.py'
    app = AppTest.from_file(entry, default_timeout=12).run()
    app.sidebar.radio[0].set_value('Pobieranie').run()

    def button(label):
        return next(item for item in app.button if item.label == label)

    button('Test Wyscout Connection').click().run()
    assert not app.get('html')
    next(item for item in app.checkbox if item.label.startswith('Pokaż stronę Wyscout')).check().run()
    button('Test Wyscout Connection').click().run()
    assert captures == [False, True]
    body = app.get('html')[0].proto.body
    assert 'src="data:image/jpeg;base64,' in body and '/media/' not in body

    other = AppTest.from_file(entry, default_timeout=12).run()
    other.sidebar.radio[0].set_value('Pobieranie').run()
    assert not other.get('html') and not any(item.value for item in other.checkbox)
    button('Ukryj podgląd').click().run()
    assert not app.get('html') and not app.exception

    monkeypatch.setattr(panel_auth, 'panel_access', lambda _: False)
    public = AppTest.from_file(entry, default_timeout=12).run()
    public.sidebar.radio[0].set_value('Pobieranie').run()
    assert not public.checkbox and not public.get('html')
