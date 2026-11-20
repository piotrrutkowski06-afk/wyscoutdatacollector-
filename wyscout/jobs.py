from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from itertools import groupby
import json
import logging
from pathlib import Path
import threading

from filelock import FileLock, Timeout

from .excel import Report, check_players, column_notes, digest, make_workbook, read_export
from .partition import LIMIT, partitions
from .settings import Batch, Cancelled, CollectorError, SignInRequired, VILNIUS
from .wyscout_adapter import load_profile, open_adapter

LOGGER = logging.getLogger('wyscout.jobs')
LIVE_OK = frozenset(('kompletny', 'pusty'))


def _file_lock(store):
    return FileLock(str(store.root / 'collector.lock'), timeout=0, thread_local=False)


def running(store):
    try:
        with _file_lock(store):
            return False
    except Timeout:
        return True


def probe(store, creds):
    if not all(creds):
        raise CollectorError('Dodaj WYSCOUT_USERNAME i WYSCOUT_PASSWORD w Secrets.')
    try:
        with _file_lock(store):
            store.remember('connection_ok', False)
            profile = load_profile(for_connection=True)
            with open_adapter(*creds, profile=profile):
                store.remember('connection_ok', True)
            return True
    except Timeout:
        raise CollectorError('Inny eksport lub test korzysta w tej chwili z konta Wyscout.') from None


def refresh_leagues(store, creds):
    if not all(creds):
        raise CollectorError('Dodaj WYSCOUT_USERNAME i WYSCOUT_PASSWORD w Secrets.')
    profile = load_profile()
    if profile.get('competition_list_complete') is not True:
        raise CollectorError('Profil nie potwierdza pełnej listy rozgrywek.')
    try:
        with _file_lock(store), open_adapter(*creds, profile=profile) as adapter:
            leagues = adapter.competitions()
            store.remember('available_leagues', leagues)
            return leagues
    except Timeout:
        raise CollectorError('Inny eksport lub test korzysta w tej chwili z konta Wyscout.') from None


def start(store, creds, *, settings=None, mode='pełny', resume=None):
    if not all(creds):
        raise CollectorError('Dodaj WYSCOUT_USERNAME i WYSCOUT_PASSWORD w Secrets.')
    lock = _file_lock(store)
    try:
        lock.acquire()
    except Timeout:
        raise CollectorError('Konto jest już używane przez inne zadanie.') from None
    try:
        base_job = store.preference('published') if mode == 'odświeżanie' else None
        if resume:
            row = store.get(resume)
            if not row or row['status'] == 'ukończono':
                raise CollectorError('Wybierz przerwane, niekompletne lub niedokończone zadanie.')
            job = resume
        else:
            if mode == 'odświeżanie' and not base_job:
                raise CollectorError('Pierwszą pełną bazę utwórz w trybie Full Download.')
            job = store.create(settings, mode, base_job)
        store.stop(job, False)
        store.job_update(job, 'w toku')
        threading.Thread(target=_run, args=(store, job, creds, lock), daemon=True, name='wyscout-job').start()
        return job
    except Exception:
        lock.release()
        raise


def _batch_from_row(row):
    return Batch(*(row[key] for key in ('league', 'timeframe', 'position', 'layout')))


def _collected_report(store, row):
    columns, players = {}, []
    for path, checksum in json.loads(row['paths']):
        full = store.path(path)
        if not full.is_file() or digest(full) != checksum:
            raise CollectorError('Brak zapisanego pliku partii lub plik uległ zmianie.')
        names, entries = read_export(full)
        columns.update(dict.fromkeys(names))
        players.extend(entries)
    check_players(players, row['expected'], timeframe=row['timeframe'], strict=True)
    message = ' '.join(filter(None, (row['message'], column_notes(columns))))
    return Report(_batch_from_row(row), row['season'], players, tuple(columns), row['expected'], row['status'],
                  'Hudl Wyscout / Advanced Search', row['updated'], message)


def _export_report(row):
    return Report(_batch_from_row(row), row['season'] or 'Niepotwierdzony', [], (), row['expected'], row['status'],
                  'Hudl Wyscout / Advanced Search', row['updated'], row['message'])


def _extract(store, adapter, job, index, expected, timeframe, checkpoint):
    if not expected:
        return (), []
    slices = [(None, None, expected)]
    if expected >= LIMIT:
        bounds = adapter.partition_bounds()
        slices = list(partitions(expected, *bounds, adapter.set_interval, checkpoint=checkpoint))
    folder = Path('jobs') / job / 'parts'
    store.path(folder).mkdir(parents=True, exist_ok=True)
    files, players = [], []
    for i, (start_day, end_day, count) in enumerate(slices):
        checkpoint()
        if start_day is not None and adapter.set_interval(start_day, end_day) != count:
            raise CollectorError('Licznik przedziału zmienił się od momentu planowania podziału.')
        relative = folder / f'{index:05d}-{i:04d}.xlsx'
        path = store.path(relative)
        temporary = path.with_suffix('.tmp.xlsx')
        try:
            adapter.export(temporary, count)
            _, entries = read_export(temporary)
            check_players(entries, count, timeframe=timeframe, strict=True)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        files.append((str(relative), digest(path)))
        players.extend(entries)
    check_players(players, expected, timeframe=timeframe, strict=True)
    return files, players


def _collect_batch(store, job, row, adapter, settings, checkpoint):
    idx = row['n']
    season = row['season']
    expected = None
    try:
        resolved = adapter.configure(_batch_from_row(row), settings)
        if season and resolved != season:
            raise Cancelled('Timeframe wskazuje inny sezon niż przy rozpoczęciu. Utwórz nowe zadanie.')
        season = resolved
        if row['status'] in LIVE_OK:
            try:
                _collected_report(store, row)
                return
            except CollectorError:
                pass  # Repeat a batch whose saved file is no longer valid.
        store.batch_update(job, idx, 'pobieranie', season=season)
        expected = adapter.count()
        store.batch_update(job, idx, 'pobieranie', season=season, expected=expected)
        paths, players = _extract(store, adapter, job, idx, expected, row['timeframe'], checkpoint)
        store.batch_update(job, idx, 'kompletny' if expected else 'pusty', season=season,
                           expected=expected, downloaded=len(players), paths=paths)
    except (Cancelled, SignInRequired):
        raise
    except CollectorError as exc:
        store.batch_update(job, idx, 'błąd', season=season, expected=expected, message=str(exc))


def _check_league(store, job, league):
    rows = [row for row in store.batches(job) if row['status'] in LIVE_OK and row['league'] == league]
    seasons = {}
    for row in rows:
        seasons.setdefault(row['timeframe'], set()).add(row['season'])
    invalid = (any(len(options) != 1 for options in seasons.values())
               or len({s for x in seasons.values() for s in x}) != len(seasons))
    if invalid:
        for row in rows:
            store.batch_update(job, row['n'], 'błąd', season=row['season'], expected=row['expected'],
                               downloaded=row['downloaded'], paths=json.loads(row['paths']),
                               message='Nie można jednoznacznie przypisać Timeframe do różnych sezonów ligi.')


def _inherit(store, base_job, reports):
    scopes = {(r.batch.league, r.season) for r in reports}
    baseline = store.get(base_job)
    if not baseline or baseline['status'] != 'ukończono':
        raise CollectorError('Brak kompletnej bazy odniesienia do odświeżenia.')
    manifest = store.path(Path('jobs') / base_job / 'manifest.json')
    if not manifest.is_file():
        raise CollectorError('Nie znaleziono manifestu poprzedniej bazy.')
    previous_rows = json.loads(manifest.read_text(encoding='utf-8'))
    old = [row for row in previous_rows if (row['league'], row['season']) not in scopes]
    return old, [_collected_report(store, row) for row in old]


def _finish(store, job):
    rows = store.batches(job)
    good = [row for row in rows if row['status'] in LIVE_OK]
    reports = [_collected_report(store, row) for row in good]
    errors = [_export_report(row) for row in rows if row['status'] not in LIVE_OK]
    root = Path('jobs') / job
    stamp = datetime.now(VILNIUS).date().isoformat()
    record = store.get(job)
    suffix = '_NIEKOMPLETNY' if errors else '_TEST' if record['mode'] == 'test' else ''
    output = root / f'WYSCOUT_DATABASE_{stamp}{suffix}.xlsx'
    current_manifest = good
    if record['base_job'] and not errors:
        if store.preference('published') != record['base_job']:
            raise CollectorError('Baza zmieniła się od rozpoczęcia odświeżania. Uruchom nowe zadanie.')
        inherited, previous = _inherit(store, record['base_job'], reports)
        current_manifest = inherited + good
        reports = previous + reports
    if errors:
        failed_scopes = {(row.batch.league, row.batch.timeframe) for row in errors}
        errors.extend(replace(r, status='pominięty', message='Pominięto dane: co najmniej jedna partia ligi i sezonu ma błąd.')
                      for r in reports if (r.batch.league, r.batch.timeframe) in failed_scopes)
        reports = [r for r in reports if (r.batch.league, r.batch.timeframe) not in failed_scopes]
    store.path(root).mkdir(parents=True, exist_ok=True)
    temporary = store.path(output.with_suffix('.tmp'))
    temporary.write_bytes(make_workbook(reports, extra_log=errors))
    temporary.replace(store.path(output))
    if errors:
        store.job_update(job, 'niekompletny', 'Nie zastąpiono głównej bazy. Szczegóły znajdują się w EXPORT_LOG.', output=str(output))
    elif record['mode'] == 'test':
        store.job_update(job, 'ukończono', 'Test zakończony. Nie zastąpiono głównej bazy.', output=str(output))
    else:
        manifest = store.path(root / 'manifest.json')
        temp_manifest = manifest.with_suffix('.tmp')
        temp_manifest.write_text(json.dumps(current_manifest, ensure_ascii=False), encoding='utf-8')
        temp_manifest.replace(manifest)
        store.publish(job, str(output), 'Gotowy plik Excel dla wybranych lig i sezonów.')


def _run(store, job, creds, lock):
    def checkpoint():
        if store.get(job)['stop']:
            raise Cancelled('Zatrzymano na żądanie. Poprawnie sprawdzone partie pozostawiono do wznowienia.')

    try:
        settings = store.settings(job)
        renewed = False
        store.remember('connection_ok', False)
        with ExitStack() as browsers:
            adapter = browsers.enter_context(open_adapter(*creds, checkpoint=checkpoint))
            store.remember('connection_ok', True)
            for league, rows in groupby(store.batches(job), key=lambda row: row['league']):
                for row in rows:
                    checkpoint()
                    try:
                        _collect_batch(store, job, row, adapter, settings, checkpoint)
                    except SignInRequired:
                        if renewed:
                            raise CollectorError('Sesja Wyscout wygasła ponownie. Wznów zadanie po zalogowaniu.') from None
                        renewed = True
                        browsers.close()
                        adapter = browsers.enter_context(open_adapter(*creds, checkpoint=checkpoint))
                        _collect_batch(store, job, row, adapter, settings, checkpoint)
                _check_league(store, job, league)
        checkpoint()
        _finish(store, job)
    except Cancelled as exc:
        store.job_update(job, 'zatrzymano', str(exc))
    except CollectorError as exc:
        LOGGER.warning('Zadanie %s: %s', job, exc)
        store.job_update(job, 'błąd', str(exc))
    except Exception as exc:
        LOGGER.error('Zadanie %s: nieoczekiwany błąd (%s)', job, type(exc).__name__)
        store.job_update(job, 'błąd', 'Nieoczekiwany błąd aplikacji. Sprawdź konfigurację i zapis danych.')
    finally:
        lock.release()
