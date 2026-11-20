from pathlib import Path

from streamlit.testing.v1 import AppTest

from wyscout import jobs, panel_auth


def test_manager_can_test_connection_without_an_export_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('WYSCOUT_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('WYSCOUT_ADAPTER_CONFIG', str(tmp_path / 'missing.json'))
    monkeypatch.setenv('WYSCOUT_USERNAME', 'test-user')
    monkeypatch.setenv('WYSCOUT_PASSWORD', '  ')
    monkeypatch.setattr(panel_auth, 'panel_access', lambda _: True)
    monkeypatch.setattr(panel_auth, 'panel_is_authorized', lambda _: True)
    called = []

    def probe(_store, _creds):
        called.append(True)
        return True

    monkeypatch.setattr(jobs, 'probe', probe)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=12).run()
    app.sidebar.radio[0].set_value('Pobieranie').run()

    def button(label):
        return next(item for item in app.button if item.label == label)

    assert not app.exception
    assert button('Test Wyscout Connection').disabled
    assert any('WYSCOUT_PASSWORD' in message.value for message in app.warning)
    monkeypatch.setenv('WYSCOUT_PASSWORD', 'test-only')
    app.run()
    assert not button('Test Wyscout Connection').disabled
    assert button('Pobierz listę rozgrywek').disabled
    assert button('Start Full Download').disabled
    button('Test Wyscout Connection').click().run()
    assert called == [True]
    assert any('Test nie pobierał listy lig' in message.value for message in app.success)
    assert not app.exception
