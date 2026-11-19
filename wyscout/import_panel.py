from datetime import datetime
from hashlib import sha256
from io import BytesIO
import re

import pandas as pd
import streamlit as st

from .excel import Report, check_players, column_notes, make_workbook, read_export
from .settings import Batch, CollectorError, FIELD_LAYOUT, GK_LAYOUT, POSITIONS, TIMEFRAMES, VILNIUS
from .store import now


def _clean(value):
    return str(value).strip() if pd.notna(value) else ''


def import_panel():
    st.header('Scal pliki Excel')
    st.write('Połącz istniejące eksporty Wyscout. Każda liga otrzyma własny arkusz, a sezon będzie zapisany przy każdym zawodniku.')
    st.caption('Import nie loguje się do Wyscout. Wynik jest dostępny w bieżącej sesji, bez zapisu w historii eksportów automatycznych.')
    files = st.file_uploader('Dodaj oryginalne eksporty Wyscout (.xlsx)', type='xlsx', accept_multiple_files=True)
    if not files:
        st.session_state.pop('import_result', None)
        return
    total_bytes = sum(file.size for file in files)
    if len(files) > 40 or total_bytes > 50_000_000:
        st.error('W jednej sesji dodaj maksymalnie 40 plików o łącznym rozmiarze do 50 MB.')
        return
    digests = [sha256(file.getbuffer()).hexdigest() for file in files]
    sig = sha256(''.join(digests).encode()).hexdigest()
    if len(set(digests)) != len(digests):
        st.error('Co najmniej dwa pliki mają identyczną zawartość. Usuń duplikat.')
        return
    metadata = pd.DataFrame([{'Plik': f.name, 'Liga': '', 'Sezon': '', 'Timeframe': '', 'Pozycja': '',
                              'Układ DISPLAY': ''} for f in files])
    metadata['Wyniki w Wyscout'] = pd.array([None] * len(files), dtype='Int64')
    st.caption('Dla każdego pliku podaj faktyczny sezon ligi, np. 2025/26 albo 2026. Licznik Wyscout podaj, jeśli go znasz.')
    config = st.data_editor(metadata, key=f'meta-{sig}', hide_index=True, disabled=['Plik'],
                            width='stretch', num_rows='fixed', column_config={
        'Timeframe': st.column_config.SelectboxColumn(options=TIMEFRAMES, required=True),
        'Pozycja': st.column_config.SelectboxColumn(options=POSITIONS, required=True),
        'Wyniki w Wyscout': st.column_config.NumberColumn(min_value=0, step=1, format='%d'),
    })
    fingerprint = (sig, config.to_json())
    if st.button('Scal i sprawdź pliki', type='primary'):
        st.session_state.pop('import_result', None)
        reports, stamp = [], now()
        try:
            categories, league_labels, seen_batches = {}, {}, set()
            for idx, row in config.iterrows():
                league, season, timeframe, position, layout = map(_clean, (row[k] for k in ('Liga', 'Sezon', 'Timeframe', 'Pozycja', 'Układ DISPLAY')))
                if not league or not re.search(r'\b(?:19|20)\d{2}\b', season) or timeframe not in TIMEFRAMES or position not in POSITIONS:
                    raise CollectorError(f'{files[idx].name}: uzupełnij ligę, rzeczywisty sezon, Timeframe i pozycję.')
                league = league_labels.setdefault(league.casefold(), league)
                ts = categories.setdefault(league.casefold(), {})
                if (timeframe in ts and ts[timeframe] != season) or any(s == season and t != timeframe for t, s in ts.items()):
                    raise CollectorError(f'{league}: przypisz Timeframe do różnych, jednoznacznych sezonów.')
                ts[timeframe] = season
                key = (league.casefold(), season, position)
                if key in seen_batches:
                    raise CollectorError(f'{league}, {season}, {position}: dodaj tylko jeden plik dla tej pozycji i sezonu.')
                seen_batches.add(key)
                expected = int(row['Wyniki w Wyscout']) if pd.notna(row['Wyniki w Wyscout']) else None
                layout = layout or (GK_LAYOUT if position == 'GK' else FIELD_LAYOUT)
                try:
                    columns, players = read_export(BytesIO(files[idx].getvalue()))
                    missing = check_players(players, expected, timeframe=timeframe, strict=False)
                except CollectorError as exc:
                    raise CollectorError(f'{files[idx].name}: {exc}') from None
                message = 'Import ręczny; ustawień źródłowych nie zweryfikowano automatycznie.'
                if missing:
                    message += f' Brak drużyny w wybranym sezonie: {missing} wierszy.'
                if notes := column_notes(columns):
                    message += f' {notes}'
                reports.append(Report(Batch(league, timeframe, position, layout), season, players, columns,
                                      expected, 'import', files[idx].name, stamp, message))
            st.session_state['import_result'] = (fingerprint, reports, {})
        except CollectorError as exc:
            st.error(str(exc))
    done = st.session_state.get('import_result')
    if done and done[0] == fingerprint:
        _, reports, cache = done
        timeframes = [t for t in TIMEFRAMES if any(r.batch.timeframe == t for r in reports)]
        options = [None, *timeframes] if len(timeframes) > 1 else timeframes
        timeframe = st.selectbox('Sezon w pliku Excel', options, format_func=lambda t: t or 'Oba sezony')
        selected = [r for r in reports if timeframe is None or r.batch.timeframe == timeframe]
        if timeframe not in cache:
            try:
                content = make_workbook(reports, timeframe=timeframe)
            except CollectorError as exc:
                st.error(str(exc))
                return
            cache.clear()
            cache[timeframe] = content
        rows = sum(len(r.players) for r in selected)
        leagues = len({r.batch.league for r in selected})
        missing = sum(check_players(r.players, None, timeframe=r.batch.timeframe, strict=False) for r in selected)
        st.success(f'Gotowe: {rows} wierszy w {leagues} ligach. Uwagi i liczniki są w arkuszu EXPORT_LOG.')
        if missing:
            st.warning(f'{missing} wierszy bez drużyny w wybranym sezonie. Zachowano je bez dopisywania danych.')
        suffix = timeframe.split()[0].upper() if timeframe else 'BOTH'
        st.download_button('Pobierz Excel', cache[timeframe], f'WYSCOUT_DATABASE_{datetime.now(VILNIUS).date()}_IMPORT_{suffix}.xlsx',
                           mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore')
