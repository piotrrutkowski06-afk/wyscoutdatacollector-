import json
from pathlib import Path

import pytest
from playwright.sync_api import Error as BrowserError, sync_playwright

from wyscout.settings import CollectorError
from wyscout.wyscout_adapter import WyscoutAdapter, connect_browser


EMAIL = """<form action='/password' onsubmit='location.href=this.action; return false'>
    <input type='email' data-qa-id='email-input-input'>
    <button type='submit' hidden>Continue</button>
    <script>setTimeout(() => {document.querySelector('button').hidden = false}, 150)</script>
    </form>"""
PASSWORD = """<form action='/home' onsubmit='location.href=this.action; return false'>
    <input type='password' data-qa-id='password-input-input'>
    <button type='submit' name='Continue'>Continue</button></form>"""
HOME = """<a class='gears-button-inner' data='{"command":"switcher_apps"}'
    onclick='document.querySelector("#menu").hidden=false'><span class='ae-sort-thumbnails-big'>Apps</span></a>
    <div id='menu' hidden><!--entry--></div>
    <section id='search' hidden><label>Timeframe</label><label>Competitions scope</label></section>"""
ENTRY = """<div class='drawer-single-app' command='loadApp' title='Advanced Search'
    onclick='document.querySelector("#search").hidden=false'>Advanced Search</div>"""


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        try:
            instance = connect_browser(playwright)
        except BrowserError:
            pytest.skip('Playwright Chromium is not available.')
        with instance:
            yield instance


@pytest.mark.parametrize('stage', ('W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W6-identity', 'W6-frame', 'W7', 'W8', None))
def test_connection_stages_with_no_external_requests(browser, stage, monkeypatch):
    profile_path = Path(__file__).resolve().parents[1] / 'config' / 'wyscout.example.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    origin = 'https://wyscout.hudl.com'
    pages = {'/app/': EMAIL, '/password': PASSWORD, '/home': HOME.replace('<!--entry-->', ENTRY)}
    failures = {
        'W2': ('/app/', ''),
        'W3': ('/app/', EMAIL.replace('>Continue</button>', '>Wrong button</button>')),
        'W4': ('/password', ''),
        'W5': ('/password', PASSWORD.replace("name='Continue'", "name='Wrong'")),
        'W6': ('/home', ''),
        'W7': ('/home', HOME),
        'W8': ('/home', HOME.replace('<!--entry-->', ENTRY.replace('.hidden=false', '.hidden=true'))),
    }
    if stage in failures:
        path, content = failures[stage]
        pages[path] = content
    destinations = {origin + path: content for path, content in pages.items()}
    if stage == 'W6-identity':
        redirect = 'https://identity.hudl.com/result?state=redact-this-test-value'
        destinations[origin + '/password'] = PASSWORD.replace('/home', redirect)
        destinations[redirect] = PASSWORD
    if stage == 'W6-frame':
        destinations[origin + '/home'] = '<iframe src="/embedded"></iframe>'
        destinations[origin + '/embedded'] = HOME

    def serve(route):
        if stage == 'W1' or route.request.url not in destinations:
            route.abort()
        else:
            status = 403 if stage == 'W6' and route.request.url == origin + '/home' else 200
            route.fulfill(status=status, content_type='text/html', body=destinations[route.request.url])

    with browser.new_context() as context:
        context.route('**/*', serve)
        page = context.new_page()
        page.set_default_timeout(1200)
        adapter = WyscoutAdapter(page, profile, 'synthetic-user@example.test', 'synthetic-test-only')
        wait = adapter._wait_for_login_step
        monkeypatch.setattr(adapter, '_wait_for_login_step', lambda choices: wait(choices, timeout=1.2))
        if stage:
            with pytest.raises(CollectorError, match=f"^{stage.split('-')[0]} · ") as error:
                adapter.sign_in()
            message = str(error.value)
            assert all(text not in message for text in ('synthetic-', 'https://', 'Call log', 'redact-this-test-value'))
            if stage == 'W6':
                # Chromium may replace an empty 403 response with its own error document.
                assert 'HTTP 403.' in message
            if stage == 'W6-identity':
                assert 'Ekran: logowanie Hudl.' in message and 'Pole hasła nadal widoczne.' in message
            if stage == 'W6-frame':
                assert 'Ekran: aplikacja Wyscout.' in message
                assert 'Znacznik Wyscout widoczny w osadzonym widoku.' in message
        else:
            adapter.sign_in()
            assert adapter._visible('advanced_ready')
