from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from typing import BinaryIO
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .settings import Batch, CollectorError

META = ('Kolektor: Liga', 'Kolektor: Sezon', 'Kolektor: Timeframe',
        'Kolektor: Pozycja główna', 'Kolektor: Układ DISPLAY')
LOG = ('Liga', 'Sezon', 'Timeframe', 'Pozycja', 'Układ DISPLAY', 'Wyniki w źródle',
       'Wiersze', 'Status', 'Źródło', 'Data eksportu (UTC)', 'Uwagi')
ALIASES = {
    'player': ('player', 'player name', 'name', 'zawodnik', 'player full name'),
    'team': ('team', 'club', 'drużyna'),
    'timeframe_team': ('team within selected timeframe',),
}


@dataclass(frozen=True)
class Column:
    label: str
    occurrence: int = 1


@dataclass(frozen=True)
class Player:
    cells: dict[Column, tuple[object, str]]
    name: str
    team: str
    timeframe_team: str | None

    def club_key(self, timeframe):
        team = self.timeframe_team
        if team is None and timeframe == 'current season':
            team = self.team
        return (self.name, team) if team else None


@dataclass(frozen=True)
class Report:
    batch: Batch
    season: str
    players: list[Player]
    columns: tuple[Column, ...]
    expected: int | None
    status: str
    source: str
    at: str
    message: str = ''


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return sha256(stream.read()).hexdigest()


def _name(value):
    return ' '.join(str(value).split()).casefold()


def _headers(cells):
    raw = [str(cell) if cell is not None else '' for cell in cells]
    while raw and not raw[-1].strip():
        raw.pop()
    if not raw or any(not col.strip() for col in raw) or {_name(col) for col in raw} & {_name(col) for col in META}:
        raise CollectorError('Nieprawidłowe nagłówki: puste lub zarezerwowane kolumny.')
    occurrences = Counter()
    result = []
    for label in raw:
        occurrences[label] += 1
        result.append(Column(label, occurrences[label]))
    return tuple(result)


def _match(headers, key):
    names = [_name(column.label) for column in headers]
    for alias in ALIASES[key]:
        matches = [i for i, name in enumerate(names) if name == alias]
        if len(matches) == 1:
            return matches[0]
    return None


def has_team(columns):
    return any(_match(columns, key) is not None for key in ('timeframe_team', 'team'))


def column_notes(columns):
    notes = []
    if columns and not has_team(columns):
        notes.append('Brak jednoznacznej kolumny drużyny w pliku źródłowym.')
    if any(col.occurrence > 1 for col in columns):
        notes.append('Powtórzone nagłówki zachowano jako osobne kolumny.')
    return ' '.join(notes)


def _club(value):
    name = _name(value) if isinstance(value, str) else ''
    return '' if name in {'-', '–', '—', 'n/a'} else name


def read_export(file: BinaryIO | Path):
    try:
        with ZipFile(file) as archive:
            if sum(item.file_size for item in archive.infolist()) > 75_000_000:
                raise CollectorError('Rozpakowany plik XLSX przekracza dopuszczalny rozmiar.')
        if hasattr(file, 'seek'):
            file.seek(0)
        book = load_workbook(file, data_only=False, keep_links=False)
    except CollectorError:
        raise
    except Exception:
        raise CollectorError('Pliku nie można odczytać jako poprawnego XLSX.') from None
    try:
        sheet = book.active
        if sheet is None or sheet.max_row * sheet.max_column > 1_500_000:
            raise CollectorError('Arkusz jest pusty lub przekracza obsługiwany rozmiar.')
        rows = sheet.iter_rows()
        first = next(rows, ())
        columns = _headers([cell.value for cell in first])
        player, team, timeframe_team = (_match(columns, key) for key in ('player', 'team', 'timeframe_team'))
        if player is None:
            raise CollectorError('Brakuje jednoznacznej kolumny zawodnika w pierwszym wierszu arkusza.')
        if timeframe_team is None and any(_name(col.label) in ALIASES['timeframe_team'] for col in columns):
            raise CollectorError('Znaleziono więcej niż jedną kolumnę „Team within selected timeframe”.')
        players = []
        for row in rows:
            values = [cell.value for cell in row]
            if all(value in (None, '') for value in values):
                continue
            if any(cell.data_type in ('f', 'e') for cell in row):
                raise CollectorError('Plik zawiera formuły lub błędy komórek. Wgraj eksport z wartościami.')
            if any(value not in (None, '') for value in values[len(columns):]):
                raise CollectorError('Znaleziono dane bez nagłówka kolumny.')
            if not isinstance(values[player], str) or not values[player].strip():
                raise CollectorError('Znaleziono wiersz bez nazwy zawodnika.')
            # Wyscout writes some missing metrics as zero-length inline strings.
            cells = {col: (None if cell.value == '' else cell.value, cell.number_format)
                     for col, cell in zip(columns, row)}
            players.append(Player(cells, _name(values[player]), _club(values[team]) if team is not None else '',
                                  _club(values[timeframe_team]) if timeframe_team is not None else None))
        return columns, players
    finally:
        book.close()


def check_players(players, expected, *, timeframe, strict):
    if expected is not None and len(players) != expected:
        raise CollectorError(f'W źródle: {expected} wyników, w pliku: {len(players)} wierszy.')
    missing = sum(player.club_key(timeframe) is None for player in players)
    if strict and missing:
        raise CollectorError(
            f'Nie można ustalić drużyny w wybranym sezonie dla {missing} wierszy. '
            'Sprawdź kolumnę „Team within selected timeframe”.'
        )
    return missing


def _sheet_name(league, taken):
    base = re.sub(r'[\\/\[\]:*?\x00-\x1f]', ' ', league).strip(" '") or 'Liga'
    if base.casefold() == 'history':
        base = 'Liga History'
    name = base[:31].rstrip(" '")
    index = 2
    while name.casefold() in taken:
        tail = f' ({index})'
        name = base[:31-len(tail)].rstrip(" '") + tail
        index += 1
    taken.add(name.casefold())
    return name


def _write(sheet, row_number, values, formats=None):
    for col, value in enumerate(values, 1):
        cell = sheet.cell(row_number, col, value)
        if isinstance(value, str):
            cell.data_type = 's'  # Never reinterpret a source string as a formula.
        if formats and formats[col-1] != 'General':
            cell.number_format = formats[col-1]


def _style(sheet):
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor='143A34')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[1].height = 32
    for col in sheet.iter_cols(max_row=min(sheet.max_row, 65)):
        width = max(len(str(cell.value or '')) for cell in col)
        sheet.column_dimensions[get_column_letter(col[0].column)].width = min(46, max(12, width + 3))


def _scoped_keys(report):
    scope = (_name(report.batch.league), _name(report.season))
    for player in report.players:
        if club_key := player.club_key(report.batch.timeframe):
            yield (*scope, *club_key)


def make_workbook(reports, *, extra_log=(), timeframe=None):
    reports = [r for r in reports if timeframe is None or r.batch.timeframe == timeframe]
    extra_log = [r for r in extra_log if timeframe is None or r.batch.timeframe == timeframe]
    counts = Counter(key for report in reports for key in _scoped_keys(report))
    book = Workbook()
    log = book.active
    log.title = 'EXPORT_LOG'
    _write(log, 1, LOG)
    leagues = {}
    for idx, report in enumerate((*reports, *extra_log), 2):
        b = report.batch
        repeated_rows = sum(counts[key] > 1 for key in _scoped_keys(report))
        notes = report.message
        if repeated_rows and idx <= len(reports) + 1:
            notes = (f'{notes} Powtórzenia zawodnik–klub–liga w tym sezonie: '
                     f'{repeated_rows} wierszy w tej partii. Zachowano wszystkie wiersze.').strip()
        _write(log, idx, (b.league, report.season, b.timeframe, b.position, b.layout,
                          report.expected, len(report.players), report.status, report.source, report.at, notes))
    for report in reports:
        if report.status in ('kompletny', 'import', 'pusty'):
            leagues.setdefault(report.batch.league, []).append(report)
    taken = {'export_log'}
    for league, pieces in leagues.items():
        sheet = book.create_sheet(_sheet_name(league, taken))
        columns = tuple(dict.fromkeys(col for p in pieces for col in p.columns))
        if len(columns) + len(META) > 16384 or sum(len(p.players) for p in pieces) >= 1048576:
            raise CollectorError('Dane jednej ligi przekraczają limit arkusza Excel.')
        _write(sheet, 1, (*META, *(column.label for column in columns)))
        for index, column in enumerate(columns, len(META) + 1):
            if column.occurrence > 1:
                sheet.cell(1, index).comment = Comment(
                    f'Wystąpienie nr {column.occurrence} tego nagłówka w pliku źródłowym.',
                    'Wyscout Data Collector',
                )
        row = 2
        for p in pieces:
            for player in p.players:
                entries = [player.cells.get(k, (None, 'General')) for k in columns]
                prefix = (league, p.season, p.batch.timeframe, p.batch.position, p.batch.layout)
                _write(sheet, row, (*prefix, *(item[0] for item in entries)),
                       (*('General',)*len(prefix), *(item[1] for item in entries)))
                row += 1
        _style(sheet)
    _style(log)
    result = BytesIO()
    book.save(result)
    return result.getvalue()
