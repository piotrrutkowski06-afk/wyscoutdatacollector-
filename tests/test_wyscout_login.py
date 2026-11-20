from io import BytesIO
import json
from pathlib import Path

from PIL import Image
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
PLATFORM = '<header>Platform</header><nav><button>Main countries</button><button>International Competitions</button></nav>'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        try:
            instance = connect_browser(playwright)
        except BrowserError:
            pytest.skip('Playwright Chromium is not available.')
        with instance:
            yield instance


@pytest.mark.parametrize(
    'stage',
    ('W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W6-identity', 'W6-frame', 'W6-platform-only', 'W6-cloud',
     'W6-preview', 'W7', 'W7-platform', 'W8', 'platform-grid', None),
)
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
        'W6-platform-only': ('/home', '<header>Platform</header>'),
        'W6-cloud': ('/home', '''<header>Platform</header><p>private-page-value</p>
            <script>
              fetch('/account-data?state=private-request-value');
              throw new Error('private-exception-value');
            </script>
            <script src='https://cdn.example.test/private-script-value.js'></script>'''),
        'W6-preview': ('/home', '''<header>Unknown screen</header>
            <input value="private-form-value"
              style="position:absolute;left:20px;top:50px;width:100px;height:36px;background:#fff">'''),
        'W7': ('/home', HOME),
        'W7-platform': ('/home', PLATFORM),
        'W8': ('/home', HOME.replace('<!--entry-->', ENTRY.replace('.hidden=false', '.hidden=true'))),
    }
    if stage in failures:
        path, content = failures[stage]
        pages[path] = content
    if stage == 'platform-grid':
        pages['/home'] = PLATFORM + pages['/home'].replace(' data=\'{"command":"switcher_apps"}\'', '')
    destinations = {origin + path: content for path, content in pages.items()}
    if stage == 'W6-cloud':
        destinations[origin + '/account-data?state=private-request-value'] = 'private-response-value'
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
            denied = (
                (stage == 'W6' and route.request.url == origin + '/home')
                or (stage == 'W6-cloud' and '/account-data?' in route.request.url)
            )
            status = 403 if denied else 200
            route.fulfill(status=status, content_type='text/html', body=destinations[route.request.url])

    with browser.new_context() as context:
        context.route('**/*', serve)
        page = context.new_page()
        page.set_default_timeout(1200)
        previews = []
        opted_in = stage in ('W6-preview', 'W6-identity', 'W8', None)
        adapter = WyscoutAdapter(page, profile, 'synthetic-user@example.test', 'synthetic-test-only',
                                 on_w6_preview=previews.append if opted_in else None)
        if not opted_in:
            monkeypatch.setattr(adapter, '_capture_w6_preview', lambda: pytest.fail('Capture without opt-in'))
        wait = adapter._wait_for_login_step
        monkeypatch.setattr(adapter, '_wait_for_login_step', lambda choices: wait(choices, timeout=1.2))
        if stage in failures or stage in ('W1', 'W6-identity', 'W6-frame'):
            with pytest.raises(CollectorError, match=f"^{stage.split('-')[0]} · ") as error:
                adapter.sign_in()
            message = str(error.value)
            assert all(text not in message for text in (
                'synthetic-', 'https://', 'Call log', 'redact-this-test-value', 'private-',
            ))
            if stage == 'W6':
                # Chromium may replace an empty 403 response with its own error document.
                assert 'HTTP 403.' in message
            if stage == 'W6-identity':
                assert 'Ekran: logowanie Hudl.' in message and 'Pole hasła nadal widoczne.' in message
            if stage == 'W6-frame':
                assert 'Ekran: aplikacja Wyscout.' in message
                assert 'Znacznik Wyscout widoczny w osadzonym widoku.' in message
            if stage == 'W6-cloud':
                assert 'Raport SC-W6-1.' in message
                assert 'Stan dokumentu: complete.' in message and 'Widoczny tekst strony: tak.' in message
                assert 'Widoczne elementy Wyscout: Platform.' in message
                assert 'API/Hudl/Wyscout HTTP 403 ×1' in message
                assert 'Przerwane żądania: skrypty/pozostałe ×1' in message
                assert 'Błędy JavaScript: 1.' in message
            if stage == 'W6-preview':
                assert len(previews) == 1
                with Image.open(BytesIO(previews[0])) as screenshot:
                    assert screenshot.format == 'JPEG'
                    assert all(25 <= channel <= 45 for channel in screenshot.getpixel((72, 68)))
            elif stage == 'W6-identity':
                assert previews == [None]
            else:
                assert not previews
        else:
            adapter.sign_in()
            assert adapter._visible('advanced_ready')
            assert not previews
