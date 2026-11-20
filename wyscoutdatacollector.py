import os

import streamlit as st

from wyscout.panel_auth import panel_access, panel_is_authorized
from wyscout.settings import CollectorError, FIELD_LAYOUT, GK_LAYOUT, POSITIONS, TIMEFRAMES, Settings, adapter_file
from wyscout.store import Store

st.set_page_config(page_title='Wyscout Data Collector', layout='wide')


def config_value(name):
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        result = st.secrets.get(name)
        return result if isinstance(result, str) else None
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return None


def errors(action):
    try:
        return action()
    except CollectorError as exc:
        st.error(str(exc))


def file_button(store, job, label):
    if job and job['output']:
        path = store.path(job['output'])
        if path.is_file():
            st.download_button(label, path.read_bytes(), path.name,
                               mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore')
        else:
            st.warning('Wyniku nie ma na dysku. Przywróć kopię katalogu danych.')


def dashboard(store):
    st.header('Dashboard')
    latest_id = store.preference('published') if store else None
    latest = store.get(latest_id) if latest_id else None
    recent = store.history() if store else []
    available = store.preference('available_leagues', []) if store else []
    selected = store.preference('selection', []) if store else []
    a, b, c = st.columns(3)
    a.metric('Dostępne ligi', len(available) if available else '—')
    b.metric('Zapisane ligi', len(selected))
    c.metric('Wiersze ostatniego zadania', recent[0]['downloaded'] if recent else 0)
    x, y = st.columns(2)
    x.metric('Ostatni test logowania', 'Potwierdzony' if store and store.preference('connection_ok') else 'Niepotwierdzone')
    y.metric('Aktualizacja bazy (UTC)', latest['updated'][:10] if latest else '—')
    if latest:
        file_button(store, latest, 'Pobierz aktualną bazę Excel')
    if recent:
        batches = store.batches(recent[0]['id'])
        complete = sum(row['status'] in ('kompletny', 'pusty') for row in batches)
        st.progress(complete / len(batches), text=f'Ostatnie zadanie: {complete} / {len(batches)} sprawdzonych partii')
        st.caption(f"Ostatnie zadanie: {recent[0]['mode']} · {recent[0]['status']}. Szczegóły w sekcji Pobieranie.")
    if not store:
        st.info('Możesz już scalać eksporty w sekcji „Import Excel”. Automatyczne pobieranie wymaga skonfigurowanego dostępu i sprawdzonego profilu Wyscout.')
    elif not recent:
        st.info('Brak zadań. Zacznij od sprawdzenia połączenia i testu jednej ligi.')


def configuration(store):
    st.header('Configuration')
    saved = store.preference('selection', []) if store else []
    available = store.preference('available_leagues', []) if store else []
    previous = st.session_state.get('download_settings')
    scopes = ('Wybrane rozgrywki', 'Wszystkie dostępne rozgrywki', 'Jedna liga – test')
    selection_mode = st.radio('Zakres rozgrywek', scopes, horizontal=True,
                              index=scopes.index(st.session_state.get('download_scope', scopes[0])), key='scope-widget')
    st.session_state['download_scope'] = selection_mode
    if selection_mode == 'Wszystkie dostępne rozgrywki':
        leagues = available
        if not available:
            st.info('Najpierw użyj „Pobierz listę rozgrywek” w sekcji Pobieranie. Test logowania nie pobiera lig.')
        else:
            st.caption(f'Zaplanowano {len(available)} lig ze sprawdzonej listy.')
    else:
        prior = list(previous.leagues) if previous and previous.leagues else saved
        initial = prior[:1] if selection_mode == 'Jedna liga – test' else prior
        options = list(dict.fromkeys((*available, *initial)))
        if available:
            leagues = st.multiselect('Rozgrywki', options, default=initial, accept_new_options=True,
                                    max_selections=1 if selection_mode == 'Jedna liga – test' else None,
                                    key=f'leagues-{selection_mode}')
        else:
            raw = (st.text_input('Dokładna nazwa ligi', initial[0] if initial else '', key='test-league-name')
                   if 'test' in selection_mode else
                   st.text_area('Dokładne nazwy lig, po jednej w wierszu', '\n'.join(initial), key='league-names'))
            leagues = [line.strip() for line in raw.splitlines() if line.strip()]
    leagues = list(dict.fromkeys(item.strip() for item in leagues if item.strip()))
    if store and st.button('Zapisz listę lig', disabled=not leagues):
        store.remember('selection', leagues)
        st.success('Zapisano listę lig.')
    season_options = ('Oba sezony', *TIMEFRAMES)
    previous_choice = previous.timeframes[0] if previous and len(previous.timeframes) == 1 else season_options[0]
    season_choice = st.radio('Timeframe — sezon do eksportu', season_options, index=season_options.index(previous_choice),
                             horizontal=True, help='Dane z różnych sezonów pozostają osobnymi wierszami.',
                             key='timeframe-export-widget')
    timeframes = TIMEFRAMES if season_choice == season_options[0] else (season_choice,)
    field, gk = st.columns(2)
    field_layout = field.text_input('DISPLAY — zawodnicy z pola', previous.field_layout if previous else FIELD_LAYOUT, key='field-widget')
    gk_layout = gk.text_input('DISPLAY — bramkarze', previous.gk_layout if previous else GK_LAYOUT, key='gk-widget')
    with st.expander('Opcjonalne filtry'):
        saved_filter = st.text_input('Zapisany zestaw filtrów', previous.saved_filter if previous else '',
                                     help='To osobna opcja, niezależna od nazw w menu DISPLAY.', key='filter-widget')
        use_minutes = st.checkbox('Ustaw minimalną liczbę minut', bool(previous and previous.min_minutes is not None), key='use-minutes-widget')
        min_minutes = st.number_input('Minimalna liczba minut', min_value=0, step=1,
                                      value=(previous.min_minutes or 0) if previous else 0, disabled=not use_minutes, key='minutes-widget')
    if selection_mode == 'Jedna liga – test':
        initial_positions = previous.positions if previous and st.session_state.get('download_mode') == 'test' else ('GK', 'CB')
        positions = st.multiselect('Pozycje do testu', POSITIONS, default=initial_positions, key='test-positions-widget')
        mode = 'test'
    else:
        mode = st.selectbox('Tryb pobierania', ('Full Download', 'Refresh Database'),
                            index=int(st.session_state.get('download_mode') == 'odświeżanie'), key='download-mode-widget')
        mode = 'odświeżanie' if mode == 'Refresh Database' else 'pełny'
        positions = POSITIONS
        st.caption('Pozycje główne: ' + ', '.join(POSITIONS))
    settings = Settings(tuple(leagues), tuple(timeframes), tuple(positions), field_layout, gk_layout,
                        saved_filter.strip(), int(min_minutes) if use_minutes else None)
    st.session_state['download_settings'] = settings
    st.session_state['download_mode'] = mode
    if leagues and timeframes and positions:
        st.success(f'Zaplanowano {len(leagues) * len(timeframes) * len(positions)} partii.')


@st.fragment(run_every='3s')
def progress(store):
    if not panel_is_authorized(config_value('WYSCOUT_PANEL_PASSWORD')):
        st.rerun()

    from wyscout import jobs

    history = store.history()
    if not history:
        st.caption('Brak zadań do wyświetlenia.')
        return
    active = next((j for j in history if j['status'] == 'w toku'), None) if jobs.running(store) else None
    selected = active or history[0]
    interrupted = not active and selected['status'] == 'w toku'
    rows = store.batches(selected['id'])
    checked = sum(r['status'] in jobs.LIVE_OK for r in rows)
    if selected['status'] in ('ukończono', 'niekompletny') or interrupted:
        st.write(f'**Sprawdzone partie:** {checked} / {len(rows)}')
    st.progress(checked / len(rows), text=f'{checked} / {len(rows)} sprawdzonych partii')
    if interrupted:
        st.warning('Proces został przerwany. Wybierz to zadanie w opcji Resume Download.')
    elif selected['message']:
        st.write(selected['message'])
    if active:
        row = next((r for r in rows if r['status'] == 'pobieranie'), None)
        if row:
            total = row['expected'] if row['expected'] is not None else '—'
            st.write(f"{row['league']} · {row['timeframe']} · {row['position']} · {row['status']} · {row['downloaded'] or 0}/{total}")
        if st.button('Stop Download', disabled=bool(active['stop'])):
            store.stop(active['id'], True)
            st.info('Zatrzymanie nastąpi między operacjami przeglądarki.')
    file_button(store, selected, 'Download Excel — ostatnie zadanie')


def manager(store):
    st.header('Download Manager')
    if store is None:
        st.info('Aby odblokować pobieranie, ustaw WYSCOUT_PANEL_PASSWORD w Secrets i zaloguj się do panelu. '
                'Bez hasła możesz użyć importu istniejących plików.')
        return
    from wyscout import jobs
    from wyscout.wyscout_adapter import load_profile

    keys = ('WYSCOUT_USERNAME', 'WYSCOUT_PASSWORD')
    creds = tuple(config_value(key) for key in keys)
    missing = [key for key, value in zip(keys, creds) if not value or not value.strip()]
    profile = errors(load_profile) if adapter_file().is_file() else None
    if missing:
        st.warning('Brak wpisów w Secrets: ' + ', '.join(missing) + '. Hasło do panelu nie zastępuje danych konta Wyscout.')
    if profile is None:
        st.info('Brak gotowego profilu eksportu Wyscout. Bez ustawienia WYSCOUT_ADAPTER_CONFIG test użyje dołączonego szablonu; eksport wymaga uzupełnionego profilu.')
    is_running = jobs.running(store)
    if st.button('Test Wyscout Connection', disabled=bool(missing) or is_running):
        with st.spinner('Sprawdzanie logowania i otwarcia Advanced Search…'):
            if errors(lambda: jobs.probe(store, creds)):
                st.success('Połączenie potwierdzone: logowanie i otwarcie Advanced Search. Test nie pobierał listy lig.')
    catalog_ready = profile is not None and profile.get('competition_list_complete') is True
    if st.button('Pobierz listę rozgrywek', disabled=bool(missing) or is_running or not catalog_ready):
        with st.spinner('Wczytywanie pełnej listy rozgrywek…'):
            leagues = errors(lambda: jobs.refresh_leagues(store, creds))
        if leagues is not None:
            st.success(f'Zapisano {len(leagues)} rozgrywek. Wybierz zakres w Konfiguracji.')
    if not catalog_ready:
        st.caption('Pełna lista rozgrywek pozostaje niedostępna, dopóki profil eksportu nie potwierdza jej kompletności.')
    ready = not missing and profile is not None
    opts = st.session_state.get('download_settings')
    mode = st.session_state.get('download_mode', 'pełny')
    all_scope = st.session_state.get('download_scope') == 'Wszystkie dostępne rozgrywki'
    saved_leagues = store.preference('available_leagues', []) if all_scope else []
    scope_ready = not all_scope or (catalog_ready and bool(saved_leagues) and opts is not None
                                   and opts.leagues == tuple(saved_leagues))
    action = {'test': 'Test One League', 'pełny': 'Start Full Download', 'odświeżanie': 'Refresh Database'}[mode]
    if not scope_ready:
        st.caption('Dla wszystkich rozgrywek pobierz pełną listę, a następnie ponownie wybierz zakres w Konfiguracji.')
    elif not opts or not opts.leagues:
        st.caption('Wybierz wcześniej ligi i sezony w Konfiguracji.')
    else:
        st.caption(f'{len(opts.leagues)} lig · {len(opts.timeframes)} sezonów do pobrania')
    if st.button(action, type='primary', disabled=not ready or is_running or not scope_ready or not opts or not opts.leagues):
        result = errors(lambda: jobs.start(store, creds, settings=opts, mode=mode))
        if result:
            st.rerun()
    past = {j['id']: f"{j['created'][:19].replace('T', ' ')} UTC · {j['mode']} · {j['status']}" for j in store.history() if j['status'] != 'ukończono'}
    if past and not is_running:
        job = st.selectbox('Zadanie do wznowienia', tuple(past), format_func=past.get)
        if st.button('Resume Download', disabled=not ready):
            result = errors(lambda: jobs.start(store, creds, resume=job))
            if result:
                st.rerun()
    progress(store)


def history_panel(store, diagnostics=False):
    st.header('Diagnostics' if diagnostics else 'Export History')
    if diagnostics:
        st.write('Dane sezonu i układów są skonfigurowane. Współpraca z Wyscout wymaga sprawdzenia widoku po zalogowaniu.')
        st.caption('Konfiguracja selektorów, autoryzacja, Chromium i wznowienia: instrukcja README.')
        if store is not None and st.button('Sprawdź start Chromium'):
            from playwright.sync_api import sync_playwright
            from wyscout.wyscout_adapter import connect_browser
            try:
                with sync_playwright() as playwright, connect_browser(playwright) as browser:
                    st.success(f'Chromium działa w trybie headless: {browser.version}. Nie łączono się z Wyscout.')
            except Exception:
                st.error('Chromium nie wystartowało. Sprawdź instalację i biblioteki zgodnie z README.')
    if store is None:
        st.info('Prywatna historia, ustawienia konta i logi zadań są dostępne po skonfigurowaniu dostępu.')
        return
    items = store.history()
    if not items:
        st.caption('Brak zapisanych zadań.')
        return
    import pandas as pd

    jobs_by_id = {j['id']: j for j in items}
    choices = {j['id']: f"{j['created'][:19].replace('T', ' ')} UTC · {j['mode']} · {j['status']}" for j in items}
    if not diagnostics:
        table = pd.DataFrame(items)[['created', 'updated', 'mode', 'status', 'downloaded']]
        st.dataframe(table.rename(columns={'created': 'Start (UTC)', 'updated': 'Aktualizacja (UTC)', 'mode': 'Tryb',
                                          'status': 'Status', 'downloaded': 'Sprawdzone wiersze'}), hide_index=True)
    selected = st.selectbox('Szczegóły zadania', list(choices), format_func=choices.get)
    job = jobs_by_id[selected]
    if job['message']:
        st.write(job['message'])
    table = pd.DataFrame(store.batches(selected))[['league', 'timeframe', 'season', 'position', 'layout', 'expected', 'downloaded', 'status', 'message', 'updated']]
    st.dataframe(table.rename(columns={'league': 'Liga', 'season': 'Sezon', 'position': 'Pozycja', 'layout': 'DISPLAY',
                                      'expected': 'Wyniki w Wyscout', 'downloaded': 'Sprawdzone wiersze', 'message': 'Uwagi'}), hide_index=True)
    if diagnostics:
        st.download_button('Pobierz log diagnostyczny', table.to_json(orient='records', force_ascii=False, indent=2),
                           'wyscout_diagnostics.json', mime='application/json', on_click='ignore')
    else:
        file_button(store, job, 'Pobierz wynik zadania')


st.title('Wyscout Data Collector')
st.caption('FK Žalgiris Vilnius · Narzędzie działu scoutingu')
authorized = panel_access(config_value('WYSCOUT_PANEL_PASSWORD'))
store = Store() if authorized else None
st.sidebar.title('Scouting · Model S')
page = st.sidebar.radio('Sekcje', ('Dashboard', 'Konfiguracja', 'Pobieranie', 'Historia eksportów', 'Diagnostyka', 'Import Excel'),
                        label_visibility='collapsed')
st.sidebar.caption('Timeframe · previous season / current season')
if page == 'Dashboard':
    dashboard(store)
elif page == 'Konfiguracja':
    configuration(store)
elif page == 'Pobieranie':
    manager(store)
elif page == 'Import Excel':
    from wyscout.import_panel import import_panel

    import_panel()
else:
    history_panel(store, diagnostics=(page == 'Diagnostyka'))
