from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from wyscout.excel import Column, META, Report, check_players, column_notes, make_workbook, read_export
from wyscout.settings import Batch, CollectorError, FIELD_LAYOUT, GK_LAYOUT, TIMEFRAMES, Settings


def source(rows, metric='Passing %', club_header='Team within selected timeframe'):
    input_file = BytesIO()
    book = Workbook()
    book.active.append(['Player', club_header, 'Age', metric])
    for row in rows:
        book.active.append(row)
        book.active.cell(book.active.max_row, 4).number_format = '0.0%'
    book.save(input_file)
    return input_file


def report(league, timeframe, season, position, rows, metric='Passing %'):
    columns, players = read_export(source(rows, metric))
    return Report(Batch(league, timeframe, position, GK_LAYOUT if position == 'GK' else FIELD_LAYOUT),
                  season, players, columns, len(rows), 'kompletny', 'synthetic test', '2026-01-15')


def test_default_two_seasons_and_exact_layouts():
    batches = list(Settings(('League',)).batches())
    assert len(batches) == 20
    assert tuple(dict.fromkeys(b.timeframe for b in batches)) == TIMEFRAMES
    assert {b.layout for b in batches if b.position == 'GK'} == {'Model S, appka - GK'}
    assert {b.layout for b in batches if b.position != 'GK'} == {'Model S, appka'}


def test_excel_preserves_two_seasons_and_column_union():
    a = report('Liga A', 'previous season', '2025', 'GK', [('A', 'Team', 1, .9)], 'Saves %')
    b = report('Liga A', 'current season', '2026', 'CB', [('A', 'Team', 1, .7)])
    c = report('Liga B', 'current season', '2025/26', 'CB', [('B', 'Team', 2, None)])
    workbook = load_workbook(BytesIO(make_workbook([a, b, c])))
    assert workbook.sheetnames == ['EXPORT_LOG', 'Liga A', 'Liga B']
    sheet = workbook['Liga A']
    assert tuple(c.value for c in sheet[1][:5]) == META
    assert [sheet.cell(r, 2).value for r in [2, 3]] == ['2025', '2026']
    assert sheet['I2'].value == .9 and sheet['I2'].number_format == '0.0%'
    assert sheet['I3'].value is None and sheet['J2'].value is None and sheet['J3'].value == .7
    assert sheet.freeze_panes == 'A2' and sheet.auto_filter.ref == 'A1:J3'
    assert workbook['EXPORT_LOG'].max_row == 4
    assert all(not r[10] for r in list(workbook['EXPORT_LOG'].values)[1:])
    for timeframe, seasons in (('previous season', ['2025']), ('current season', ['2026', '2025/26'])):
        filtered = load_workbook(BytesIO(make_workbook([a, b, c], timeframe=timeframe)))
        assert [r[1] for r in list(filtered['EXPORT_LOG'].values)[1:]] == seasons
        assert all(r[2] == timeframe for name in filtered.sheetnames[1:] for r in list(filtered[name].values)[1:])


def test_duplicate_detection_never_uses_name_alone():
    _, players = read_export(source([('A', 'T', None, 1), ('A', 'U', None, 1)]))
    assert check_players(players, 2, timeframe='previous season', strict=True) == 0
    assert players[0].club_key('previous season') != players[1].club_key('previous season')
    with pytest.raises(CollectorError, match='w pliku: 2'):
        check_players(players, 3, timeframe='previous season', strict=False)


def test_repeated_player_club_is_logged_not_dropped_even_across_positions():
    a = report('Liga A', 'current season', '2026', 'CB', [('A B', 'Team FC', 21, .5)])
    b = report('Liga A', 'current season', '2026', 'RB', [(' a  b ', 'team FC', 21, .5), ('A B', 'Other FC', 21, .5)])
    other_league = report('Liga B', 'current season', '2026', 'CB', [('A B', 'Team FC', 21, .5)])
    workbook = load_workbook(BytesIO(make_workbook([a, b, other_league])))
    log = workbook['EXPORT_LOG']
    assert log['K2'].value.startswith('Powtórzenia zawodnik–klub–liga w tym sezonie: 1')
    assert log['K3'].value.startswith('Powtórzenia zawodnik–klub–liga w tym sezonie: 1')
    assert not log['K4'].value
    assert workbook['Liga A'].max_row == 4


def test_season_team_takes_priority_and_no_current_team_substituted_for_previous():
    book, data = Workbook(), BytesIO()
    book.active.append(['Player', 'Team', 'Team within selected timeframe'])
    book.active.append(['A', 'Current FC', 'Previous FC'])
    book.active.append(['B', 'Current FC', '-'])
    book.save(data)
    _, players = read_export(data)
    assert players[0].club_key('previous season') == ('a', 'previous fc')
    assert players[1].club_key('previous season') is None
    assert check_players(players, 2, timeframe='previous season', strict=False) == 1
    _, only_current = read_export(source([('A', 'Current FC', 20, 1)], club_header='Team'))
    assert check_players(only_current, 1, timeframe='current season', strict=True) == 0
    with pytest.raises(CollectorError, match='Team within selected timeframe'):
        check_players(only_current, 1, timeframe='previous season', strict=True)


def test_bad_input_rejected_and_literal_not_reinterpreted():
    with pytest.raises(CollectorError, match='XLSX'):
        read_export(BytesIO(b'not an xlsx'))
    with pytest.raises(CollectorError, match='formuły'):
        read_export(source([('=1+1', 'T', 1, .5)]))
    a = report('=HYPERLINK', 'current season', '2026', 'CB', [('A', 'T', 1, .5)])
    workbook = load_workbook(BytesIO(make_workbook([a])))
    assert workbook['EXPORT_LOG']['A2'].value == '=HYPERLINK'
    assert workbook['EXPORT_LOG']['A2'].data_type == 's'


def test_sheet_names_unique_and_valid():
    base = 'A' * 36
    items = [report(league, 'current season', '2026', 'GK', [('A', 'Team', 1, .9)])
             for league in [base + '[x]', base + ':y', 'export_log', 'history']]
    workbook = load_workbook(BytesIO(make_workbook(items)))
    assert len(workbook.sheetnames) == len(set(s.casefold() for s in workbook.sheetnames)) == 5
    assert all(len(s) <= 31 for s in workbook.sheetnames)


def test_wyscout_source_with_repeated_metric_and_no_team():
    test_file = BytesIO()
    book = Workbook()
    metric = 'Aerial duels per 90'
    book.active.append(['Player', 'Position', metric, 'Save rate, %', metric])
    book.active.append(['Synthetic GK', 'GK', 1.75, 81.4, 2.5])
    book.save(test_file)

    columns, players = read_export(test_file)
    assert columns[-1] == Column(metric, 2)
    assert players[0].club_key('current season') is None
    assert check_players(players, 1, timeframe='current season', strict=False) == 1
    assert 'Brak jednoznacznej kolumny drużyny' in column_notes(columns)
    assert 'Powtórzone nagłówki' in column_notes(columns)
    with pytest.raises(CollectorError, match='drużyny'):
        check_players(players, 1, timeframe='current season', strict=True)

    gk = Report(Batch('Liga A', 'current season', 'GK', GK_LAYOUT), '2026',
                players, columns, 1, 'import', 'test', '2026-01-15')
    cb = report('Liga A', 'previous season', '2025', 'CB', [('Synthetic CB', 'Test FC', 1, 3.0)], metric)
    sheet = load_workbook(BytesIO(make_workbook([cb, gk])))['Liga A']
    repeated = [c for c in sheet[1] if c.value == metric]
    assert len(repeated) == 2
    first, second = (c.column for c in repeated)
    assert sheet.cell(2, first).value == 3.0 and sheet.cell(2, second).value is None
    assert sheet.cell(3, first).value == 1.75 and sheet.cell(3, second).value == 2.5
    assert sheet.cell(3, second).number_format == 'General'
    assert repeated[1].comment is not None
