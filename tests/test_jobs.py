from contextlib import contextmanager
from datetime import date, timedelta
from openpyxl import Workbook, load_workbook
import pytest

from wyscout import jobs
from wyscout.excel import digest
from wyscout.partition import partitions
from wyscout.settings import CollectorError, Settings
from wyscout.store import Store
from wyscout.wyscout_adapter import load_profile


def test_connection_uses_bundled_template_without_fetching_leagues(tmp_path, monkeypatch):
    monkeypatch.delenv('WYSCOUT_ADAPTER_CONFIG', raising=False)
    monkeypatch.setattr('wyscout.wyscout_adapter.adapter_file', lambda: tmp_path / 'wyscout.json')
    store = Store(tmp_path)
    store.remember('available_leagues', ['Test League'])
    calls = []

    @contextmanager
    def sign_in_only(*_, profile):
        calls.append(profile)
        yield object()  # The probe must not call competitions().

    monkeypatch.setattr(jobs, 'open_adapter', sign_in_only)
    assert jobs.probe(store, ('test-user', 'test-only')) is True
    assert calls[0]['selectors']['app_switcher']
    assert calls[0]['competition_list_complete'] is False
    assert store.preference('connection_ok') is True
    assert store.preference('available_leagues') == ['Test League']
    with pytest.raises(CollectorError, match='Brak profilu'):
        load_profile()
    monkeypatch.setenv('WYSCOUT_ADAPTER_CONFIG', str(tmp_path / 'wyscout.json'))
    with pytest.raises(CollectorError, match='Brak profilu'):
        jobs.probe(store, ('test-user', 'test-only'))
    assert store.preference('connection_ok') is False


def test_league_refresh_requires_a_confirmed_profile_and_preserves_saved_list_on_error(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.remember('available_leagues', ['Old League'])
    profile = {'competition_list_complete': False}
    monkeypatch.setattr(jobs, 'load_profile', lambda: profile)
    monkeypatch.setattr(jobs, 'open_adapter', lambda *_a, **_kw: pytest.fail('No browser for unverified catalogue'))
    with pytest.raises(CollectorError, match='nie potwierdza'):
        jobs.refresh_leagues(store, ('test-user', 'test-only'))
    assert store.preference('available_leagues') == ['Old League']

    profile['competition_list_complete'] = True

    class VerifiedCatalogue:
        def competitions(self):
            return ['New League']

    @contextmanager
    def open_catalogue(*_, profile):
        assert profile['competition_list_complete']
        yield VerifiedCatalogue()

    monkeypatch.setattr(jobs, 'open_adapter', open_catalogue)
    assert jobs.refresh_leagues(store, ('test-user', 'test-only')) == ['New League']
    assert store.preference('available_leagues') == ['New League']


def test_partition_at_boundary():
    left, right = date(1999, 10, 1), date(1999, 10, 2)
    count = lambda start, end: ((end - start).days + 1) * 250
    assert list(partitions(500, left, right, count)) == [(left, left, 250), (right, right, 250)]
    with pytest.raises(CollectorError, match='nie pokrywa'):
        list(partitions(501, left, right, count))
    with pytest.raises(CollectorError, match='Nie udało'):
        list(partitions(500, left, left, lambda a, b: 500))
    with pytest.raises(CollectorError, match='nie zgadzają'):
        list(partitions(500, left, right, lambda a, b: 500 if a != b else 249))


def save_part(store, job, n, season, age=1):
    rel = f'jobs/{job}/parts/{n}.xlsx'
    path = store.path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    book.active.append(['Player', 'Team within selected timeframe', 'Age'])
    book.active.append([f'Synthetic test {age}', 'Test FC', age])
    book.save(path)
    store.batch_update(job, n, 'kompletny', season=season, expected=1, downloaded=1, paths=[(rel, digest(path))])
    return path


def test_resume_keeps_checked_files_after_reopen(tmp_path):
    store = Store(tmp_path)
    job = store.create(Settings(('Test League',), positions=('GK',)), 'pełny')
    completed = save_part(store, job, 0, '2025')
    restarted = Store(tmp_path)
    r = restarted.batches(job)[0]
    assert r['status'] == 'kompletny'
    assert restarted.batches(job)[1]['status'] == 'oczekuje'

    class NoSecondDownload:
        def configure(self, batch, settings):
            return '2025'

        def count(self):
            raise AssertionError('A good completed batch should not be downloaded again')

    jobs._collect_batch(restarted, job, r, NoSecondDownload(), restarted.settings(job), lambda: None)
    completed.write_bytes(b'changed')
    with pytest.raises(CollectorError, match='plik uległ zmianie'):
        jobs._collected_report(restarted, r)


def test_refresh_replaces_only_selected_league_season(tmp_path):
    store = Store(tmp_path)
    base = store.create(Settings(('Liga A',), positions=('GK',)), 'pełny')
    save_part(store, base, 0, '2025', 1)
    save_part(store, base, 1, '2026', 2)
    jobs._finish(store, base)
    refreshed = store.create(Settings(('Liga A',), ('current season',), ('GK',)), 'odświeżanie', base)
    save_part(store, refreshed, 0, '2026', 3)
    jobs._finish(store, refreshed)
    assert store.preference('published') == refreshed
    rows = load_workbook(store.path(store.get(refreshed)['output']))['Liga A']
    assert [rows['H2'].value, rows['H3'].value] == [1, 3]
    assert store.path(store.get(base)['output']).is_file()


def test_incomplete_run_does_not_publish_or_include_partial_league(tmp_path):
    store = Store(tmp_path)
    job = store.create(Settings(('Liga A',), ('current season',), ('GK', 'CB')), 'pełny')
    save_part(store, job, 0, '2026')
    store.batch_update(job, 1, 'błąd', season='2026', expected=800, message='Brak podziału')
    jobs._finish(store, job)
    run = store.get(job)
    assert run['status'] == 'niekompletny' and store.preference('published') is None
    book = load_workbook(store.path(run['output']))
    assert book.sheetnames == ['EXPORT_LOG']
    assert book['EXPORT_LOG']['H2'].value == 'błąd' and book['EXPORT_LOG']['F2'].value == 800
    assert book['EXPORT_LOG']['H3'].value == 'pominięty'


@pytest.mark.parametrize('timeframes', [('previous season',), ('current season',), ('previous season', 'current season')])
def test_collect_and_export_chosen_seasons_without_player_id(tmp_path, timeframes):
    store = Store(tmp_path)
    settings = Settings(('Liga A',), timeframes, ('GK',))
    job = store.create(settings, 'test')
    seasons = {'previous season': '2025', 'current season': '2026'}

    class SampleAdapter:
        def configure(self, batch, settings):
            self.timeframe = batch.timeframe
            return seasons[self.timeframe]

        def count(self):
            return 2

        def export(self, path, count):
            book = Workbook()
            book.active.append(['Player', 'Team', 'Team within selected timeframe', 'Save rate, %'])
            row = ['Synthetic Player', 'Current FC', 'Season FC', .9 if self.timeframe == 'current season' else .8]
            book.active.append(row)
            book.active.append(row)
            book.save(path)

    for row in store.batches(job):
        jobs._collect_batch(store, job, row, SampleAdapter(), settings, lambda: None)
    jobs._check_league(store, job, 'Liga A')
    jobs._finish(store, job)
    run = store.get(job)
    assert run['status'] == 'ukończono'
    book = load_workbook(store.path(run['output']))
    logs = list(book['EXPORT_LOG'].values)[1:]
    assert [(r[1], r[2], r[5], r[6]) for r in logs] == [(seasons[t], t, 2, 2) for t in timeframes]
    assert all('2 wierszy w tej partii' in r[10] for r in logs)
    assert [r[2] for r in list(book['Liga A'].values)[1:]] == [t for t in timeframes for _ in range(2)]
