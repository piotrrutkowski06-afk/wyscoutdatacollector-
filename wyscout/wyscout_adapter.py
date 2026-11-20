"""Playwright UI integration. Unobserved DOM details must be supplied in a verified profile."""
from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout, sync_playwright

from .login_diagnostics import DIAGNOSTICS_VERSION, PostPasswordDiagnostics
from .settings import ROOT, CollectorError, SignInRequired, adapter_file

LOGIN_URL = 'https://wyscout.hudl.com/app/'
RESULT_RANGE = re.compile(r'^\s*\d[\d, .\u00a0\u202f]*\s*[-–]\s*\d[\d, .\u00a0\u202f]*\s+of\s+(\d[\d, .\u00a0\u202f]*)\s*$', re.I)


def _allowed(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (443, None)
            and any(host == root or host.endswith('.' + root) for root in ('hudl.com', 'wyscout.com')))


def load_profile(*, for_connection=False):
    path = adapter_file()
    if for_connection and not path.exists() and not os.getenv('WYSCOUT_ADAPTER_CONFIG'):
        path = ROOT / 'config' / 'wyscout.example.json'
    try:
        with path.open(encoding='utf-8') as f:
            result = json.load(f)
    except FileNotFoundError:
        raise CollectorError('Brak profilu interfejsu Wyscout. Zrzuty pokazują etykiety, ale nie strukturę strony.') from None
    except (OSError, ValueError):
        raise CollectorError('Nie można odczytać profilu interfejsu Wyscout.') from None
    if not isinstance(result, dict) or not isinstance(result.get('selectors'), dict):
        raise CollectorError('W profilu interfejsu brakuje sekcji selectors.')
    return result


def connect_browser(playwright, *, headed=False):
    channel = os.getenv('WYSCOUT_CHROMIUM') or shutil.which('chromium')
    return playwright.chromium.launch(headless=not headed, executable_path=channel)


class WyscoutAdapter:
    def __init__(self, page, profile, username, password, checkpoint=lambda: None, *, headed=False, on_w6_preview=None):
        self.page = page
        self.profile = profile
        self.username, self.password = username, password
        self.checkpoint = checkpoint
        self.headed = headed
        self.on_w6_preview = on_w6_preview
        self.batch = None
        self.settings = None
        self.season = None

    @contextmanager
    def step(self, name):
        self.checkpoint()
        try:
            yield
        except BrowserError:
            if self._visible('login_start') or self._visible('password') or self._visible('username'):
                raise SignInRequired('Sesja wygasła. Zaloguj się ponownie do Wyscout.') from None
            raise CollectorError(f'{name}: nie znaleziono elementu lub strona nie odpowiedziała. Sprawdź profil interfejsu.') from None

    def loc(self, key, *, optional=False, scope=None):
        selector = self.profile['selectors'].get(key)
        if not isinstance(selector, str) or not selector.strip():
            if optional:
                return None
            raise CollectorError(f'Brak potwierdzonego selektora w profilu interfejsu: {key}.')
        return (self.page if scope is None else scope).locator(selector)

    def _visible(self, key, *, scope=None):
        try:
            locator = self.loc(key, optional=True, scope=scope)
            return locator is not None and locator.is_visible()
        except BrowserError:
            return False

    def _read(self, key):
        return ' '.join(self.loc(key).inner_text().split())

    def _option(self, scope, value):
        self.loc(scope).get_by_text(value, exact=True).click()

    def _official_origin(self):
        if not _allowed(self.page.url):
            raise CollectorError('Przekierowanie poza domeny Hudl i Wyscout. Zatrzymano logowanie.')

    def _wait_for_login_step(self, choices, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.checkpoint()
            for key in choices:
                if self._visible(key):
                    return key
            self.page.wait_for_timeout(250)
        raise BrowserTimeout('Nie rozpoznano oczekiwanego ekranu.')

    @contextmanager
    def _login_stage(self, message, *, details=None):
        self.checkpoint()
        try:
            yield
        except BrowserError:
            # Playwright errors can contain the login URL or values from the form.
            if details is not None:
                try:
                    message = f'{message} {details()}'
                except BrowserError:
                    pass
            raise CollectorError(message) from None

    def _post_password_details(self, ready, diagnostics):
        url = self.page.url
        hostname = urlsplit(url).hostname
        if hostname == 'identity.hudl.com' and _allowed(url):
            screen = 'logowanie Hudl'
        elif hostname == 'wyscout.hudl.com' and _allowed(url):
            screen = 'aplikacja Wyscout'
        elif _allowed(url):
            screen = 'inna strona Hudl/Wyscout'
        else:
            screen = 'poza rozpoznaną domeną Hudl/Wyscout'
        parts = [f'Raport {DIAGNOSTICS_VERSION}. Ekran: {screen}.']
        if diagnostics.document_status is not None:
            parts.append(f'Z ostatniego otwarcia strony: HTTP {diagnostics.document_status}.')
        if _allowed(url):
            if self._visible('password'):
                parts.append('Pole hasła nadal widoczne.')
            elif self._visible('username'):
                parts.append('Pole e-mail ponownie widoczne.')
            else:
                parts.append('Nie widać rozpoznanych pól logowania.')
            parts.extend(self._render_summary())
            if any(
                frame.parent_frame is not None and _allowed(frame.url)
                and any(self._visible(key, scope=frame) for key in ready)
                for frame in self.page.frames
            ):
                parts.append('Znacznik Wyscout widoczny w osadzonym widoku.')
        parts.append(diagnostics.summary())
        return ' '.join(parts)

    def _render_summary(self):
        parts = []
        try:
            state, text_visible = self.page.locator('html').evaluate(
                '(html) => [html.ownerDocument.readyState, Boolean(html.ownerDocument.body?.innerText.trim())]',
                timeout=1000,
            )
            if state in ('loading', 'interactive', 'complete'):
                parts.append(f'Stan dokumentu: {state}.')
            parts.append('Widoczny tekst strony: ' + ('tak.' if text_visible else 'nie.'))
        except BrowserError:
            parts.append('Stan dokumentu: nieodczytany.')

        labels = ('Platform', 'Main countries', 'International Competitions', 'Competitions scope', 'Timeframe')
        seen = []
        for label in labels:
            try:
                if self.page.get_by_text(label, exact=True).filter(visible=True).count():
                    seen.append(label)
            except BrowserError:
                pass
        if self._visible('app_switcher_fallback'):
            seen.append('ikona z kafelkami')
        parts.append('Widoczne elementy Wyscout: ' + (', '.join(seen) if seen else 'brak') + '.')
        return parts

    def _capture_w6_preview(self):
        def safe_page():
            url = self.page.url
            return (_allowed(url) and urlsplit(url).hostname == 'wyscout.hudl.com'
                    and not any(self._visible(key) for key in ('username', 'password', 'mfa', 'captcha')))

        try:
            if not safe_page():
                return None
            masks = [frame.locator('input, textarea, select, [contenteditable]') for frame in self.page.frames]
            jpeg = self.page.screenshot(type='jpeg', quality=78, mask=masks, mask_color='#222222', timeout=3500)
            if safe_page():
                return jpeg
        except BrowserError:
            pass
        return None

    def _w6_details(self, ready, diagnostics):
        if self.on_w6_preview is not None:
            self.on_w6_preview(self._capture_w6_preview())
        return self._post_password_details(ready, diagnostics)

    def _submit_password(self, ready, challenge):
        diagnostics = PostPasswordDiagnostics(self.page, _allowed)
        with diagnostics.observe():
            with self._login_stage('W5 · Nie udało się użyć pola hasła lub wysłać formularza.'):
                self.loc('password').fill(self.password)
                self._official_origin()
                self.loc('login_submit').click()
            with self._login_stage(
                'W6 · Po wysłaniu formularza hasła nie rozpoznano strony głównej ani wyniku logowania.',
                details=lambda: self._w6_details(ready, diagnostics),
            ):
                return self._wait_for_login_step(challenge + ready)

    def sign_in(self):
        ready = ('advanced_ready', 'signed_in', 'app_switcher')
        challenge = ('mfa', 'captcha', 'login_error')
        form = ('username', 'password')
        target = self.profile.get('login_url', LOGIN_URL)
        if not _allowed(target):
            raise CollectorError('Adres logowania musi należeć do Hudl lub Wyscout i używać HTTPS.')
        with self._login_stage('W1 · Nie otwarto strony logowania Wyscout.'):
            self.page.goto(target, wait_until='domcontentloaded')
        with self._login_stage('W2 · Strona się otworzyła, ale nie rozpoznano formularza Hudl.'):
            phase = self._wait_for_login_step(challenge + ready + ('login_start',) + form)
            if phase == 'login_start':
                self._official_origin()
                self.loc('login_start').click()
                phase = self._wait_for_login_step(challenge + ready + form)
        if phase == 'username':
            self._official_origin()
            if not self.username or not self.password:
                raise CollectorError('Brak WYSCOUT_USERNAME lub WYSCOUT_PASSWORD w Secrets.')
            with self._login_stage('W3 · Nie udało się użyć pola e-mail lub przycisku Continue.'):
                self.loc('username').fill(self.username)
                phase = self._wait_for_login_step(challenge + ('password', 'username_next'))
                if phase == 'username_next':
                    self._official_origin()
                    self.loc('username_next').click()
            if phase == 'username_next':
                with self._login_stage('W4 · Po Continue na ekranie e-maila nie rozpoznano pola hasła.'):
                    phase = self._wait_for_login_step(challenge + ('password',))
        if phase == 'password':
            self._official_origin()
            if not self.password:
                raise CollectorError('Brak WYSCOUT_PASSWORD w Secrets.')
            phase = self._submit_password(ready, challenge)
        if phase == 'login_error':
            raise CollectorError('Hudl/Wyscout wyświetlił komunikat o błędzie logowania.')
        if phase in ('mfa', 'captcha'):
            if not self.headed:
                raise CollectorError('Hudl/Wyscout wymaga ręcznego potwierdzenia. Dokończ je w trybie z widocznym oknem Chromium.')
            with self._login_stage('Nie potwierdzono zakończenia ręcznej weryfikacji w Hudl/Wyscout.'):
                phase = self._wait_for_login_step(('login_error',) + ready, timeout=240)
            if phase == 'login_error':
                raise CollectorError('Hudl/Wyscout wyświetlił komunikat o błędzie logowania.')
        self._official_origin()
        self._open_advanced_search()

    def _open_advanced_search(self):
        if self._visible('advanced_ready'):
            return
        with self._login_stage('W7 · Rozpoznano zalogowany widok, ale nie udało się otworzyć kafelka Advanced Search.'):
            entry = self.loc('advanced_search_entry', optional=True)
            if entry is None:
                entry = self.page.get_by_text('Advanced search', exact=False)
            if not entry.is_visible():
                switcher = self.loc('app_switcher', optional=True)
                if not self._visible('app_switcher') and self._visible('signed_in'):
                    fallback = self.loc('app_switcher_fallback', optional=True)
                    if fallback is not None:
                        switcher = fallback
                if switcher is not None:
                    switcher.click()
            self._official_origin()
            entry.click()
        with self._login_stage('W8 · Kliknięto kafelek, ale nie rozpoznano otwartego widoku Advanced Search.'):
            self.loc('advanced_ready').wait_for(state='visible')
        self._official_origin()

    def _guard(self):
        if not self._visible('advanced_ready'):
            if any(self._visible(key) for key in ('login_start', 'username', 'password')):
                raise SignInRequired('Sesja Wyscout wygasła.')
            raise CollectorError('Nie można potwierdzić otwarcia widoku Advanced Search.')
        self._official_origin()

    def competitions(self):
        with self.step('Lista rozgrywek'):
            if self.profile.get('competition_list_complete') is not True:
                raise CollectorError('Profil nie potwierdza pełnej listy rozgrywek.')
            self._guard()
            self.loc('competition_control').click()
            options = self.loc('competition_options')
            options.first.wait_for(state='visible')
            options_list = [' '.join(t.split()) for t in options.all_inner_texts() if t.strip()]
            self.loc('competition_close').click()
            if not options_list:
                raise CollectorError('Wyscout nie zwrócił dostępnych rozgrywek.')
            if len(set(options_list)) != len(options_list):
                raise CollectorError('Lista zawiera nierozróżnialne nazwy rozgrywek. Nie zapisano jej jako pełnej.')
            return options_list

    def _set_optional(self, settings):
        self.loc('reset_filters').click()  # verified reset of prior position, statistical filters, and partition
        if self._visible('reset_confirm'):
            self.loc('reset_confirm').click()
        if settings.saved_filter:
            self.loc('saved_filter_control').click()
            self._option('saved_filter_menu', settings.saved_filter)
        if settings.min_minutes is not None:
            minutes = self.loc('minutes_input')
            minutes.fill(str(settings.min_minutes))
            self.loc('minutes_apply').click()

    def configure(self, batch, settings):
        self.batch, self.settings = batch, settings
        with self.step('Ustawianie filtrów wyszukiwania'):
            self._guard()
            self._set_optional(settings)
            self.loc('competition_control').click()
            search = self.loc('competition_search', optional=True)
            if search is not None:
                search.fill(batch.league)
            self._option('competition_menu', batch.league)
            if self._visible('competition_apply'):
                self.loc('competition_apply').click()
            self.loc('timeframe_control').click()
            self._option('timeframe_menu', batch.timeframe)
            self.loc('position_control').click()
            main = self.loc('main_position_checkbox')
            main.set_checked(True)
            position = self.profile.get('positions', {}).get(batch.position)
            if not isinstance(position, dict) or not position.get('option') or not position.get('active'):
                raise CollectorError(f'Niepotwierdzone mapowanie pozycji {batch.position}.')
            self.page.locator(position['option']).click()
            if not self.page.locator(position['active']).is_visible() or not main.is_checked():
                raise CollectorError('Nie potwierdzono pozycji głównej w oknie wyboru.')
            confirm = self.loc('position_confirm', optional=True)
            if confirm is None:
                confirm = self.page.get_by_text('OK, DONE', exact=True)
            confirm.click()
            self.loc('layout_control').click()
            self.loc('layout_menu').get_by_text('Custom', exact=True).hover()
            self._option('custom_layout_menu', batch.layout)
            self._wait_results()
            self.season = self._actual_season()
            self.validate()
            return self.season

    def _wait_results(self):
        self.page.wait_for_timeout(350)
        self.loc('results_ready').wait_for(state='visible')
        busy = self.loc('results_loading', optional=True)
        if busy is not None:
            busy.wait_for(state='hidden')

    def _actual_season(self):
        season = self._read('active_season')
        if not re.search(r'\b(?:19|20)\d{2}\b', season):
            raise CollectorError('Nie można potwierdzić rzeczywistego sezonu ligi (np. 2025/26 lub 2026).')
        return season

    def validate(self):
        b = self.batch
        self._guard()
        labels = [('active_competition', b.league), ('active_timeframe', b.timeframe), ('active_layout', b.layout)]
        if self.settings.saved_filter:
            labels.append(('active_saved_filter', self.settings.saved_filter))
        if self.settings.min_minutes is not None:
            labels.append(('active_min_minutes', str(self.settings.min_minutes)))
        for key, expected in labels:
            if self._read(key).casefold() != expected.casefold():
                raise CollectorError(f'Nie potwierdzono aktywnego ustawienia: {expected}.')
        active = self.profile.get('positions', {}).get(b.position, {}).get('summary')
        if not active or not self.page.locator(active).is_visible() or not self.loc('main_only_active').is_visible():
            raise CollectorError('Nie potwierdzono, że aktywny jest wyłącznie filtr żądanej pozycji głównej.')
        if self._actual_season() != self.season:
            raise CollectorError('Rzeczywisty sezon zmienił się podczas eksportu.')

    def count(self):
        with self.step('Odczyt liczby wyników'):
            self._wait_results()
            self.validate()
            if self._visible('empty_results'):
                return 0
            raw = self._read('result_count')
            match = RESULT_RANGE.fullmatch(raw)
            if match:
                raw = match[1]
            if re.fullmatch(r'[0-9]+(?:[ .,\u00a0\u202f][0-9]{3})*', raw):
                return int(re.sub(r'[^0-9]', '', raw))
            raise CollectorError('Nie można odczytać dokładnej liczby wyników. Skrócone liczniki nie wystarczają.')

    def partition_bounds(self):
        partition = self.profile.get('birth_partition', {})
        if not partition.get('permitted') or not partition.get('inclusive'):
            raise CollectorError('Co najmniej 500 wyników. Brak potwierdzonego, dozwolonego filtra podziału.')
        try:
            return date.fromisoformat(partition['min']), date.fromisoformat(partition['max'])
        except (KeyError, TypeError, ValueError):
            raise CollectorError('Nieprawidłowa konfiguracja przedziałów daty urodzenia.') from None

    def set_interval(self, start, end):
        with self.step('Ustawianie przedziału dat urodzenia'):
            self.loc('dob_control').click()
            fmt = self.profile['birth_partition'].get('input_format', '%Y-%m-%d')
            left, right = start.strftime(fmt), end.strftime(fmt)
            self.loc('dob_start').fill(left)
            self.loc('dob_end').fill(right)
            self.loc('dob_apply').click()
            if (self._read('active_dob_start'), self._read('active_dob_end')) != (left, right):
                raise CollectorError('Nie potwierdzono granic przedziału dat urodzenia.')
        return self.count()

    def export(self, destination: Path, expected: int):
        with self.step('Export to Excel'):
            if self.count() != expected:
                raise CollectorError('Liczba wyników zmieniła się tuż przed eksportem.')
            button = self.loc('export_button', optional=True)
            if button is None:
                button = self.page.get_by_text('Export to Excel', exact=True)
            with self.page.expect_download(timeout=45_000) as event:
                button.click()
            event.value.save_as(destination)


@contextmanager
def open_adapter(username, password, checkpoint=lambda: None, *, profile=None, on_w6_preview=None):
    if profile is None:
        profile = load_profile()
    headed = os.getenv('WYSCOUT_HEADED') == '1'
    with sync_playwright() as playwright:
        try:
            browser = connect_browser(playwright, headed=headed)
        except BrowserError:
            raise CollectorError('Nie udało się uruchomić Chromium. Sprawdź instalację Playwright i biblioteki systemowe.') from None
        try:
            context = browser.new_context(accept_downloads=True, locale='en-US')
            page = context.new_page()
            page.set_default_timeout(12_000)
            page.set_default_navigation_timeout(35_000)
            adapter = WyscoutAdapter(page, profile, username, password, checkpoint,
                                     headed=headed, on_w6_preview=on_w6_preview)
            adapter.sign_in()
            yield adapter
        finally:
            browser.close()
