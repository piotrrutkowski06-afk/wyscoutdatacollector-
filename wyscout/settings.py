from dataclasses import dataclass
from pathlib import Path
import os
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ('GK', 'RB', 'CB', 'LB', 'DMF', 'CMF', 'AMF', 'RW', 'LW', 'CF')
TIMEFRAMES = ('previous season', 'current season')
FIELD_LAYOUT = 'Model S, appka'
GK_LAYOUT = 'Model S, appka - GK'
VILNIUS = ZoneInfo('Europe/Vilnius')


class CollectorError(Exception):
    """An error safe to show in the panel and export report."""


class SignInRequired(CollectorError):
    pass


class Cancelled(CollectorError):
    pass


def data_directory() -> Path:
    directory = Path(os.getenv('WYSCOUT_DATA_DIR', str(ROOT / 'data'))).expanduser().resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def adapter_file() -> Path:
    return Path(os.getenv('WYSCOUT_ADAPTER_CONFIG', str(ROOT / 'config' / 'wyscout.json')))


@dataclass(frozen=True)
class Batch:
    league: str
    timeframe: str
    position: str
    layout: str


@dataclass(frozen=True)
class Settings:
    leagues: tuple[str, ...]
    timeframes: tuple[str, ...] = TIMEFRAMES
    positions: tuple[str, ...] = POSITIONS
    field_layout: str = FIELD_LAYOUT
    gk_layout: str = GK_LAYOUT
    saved_filter: str = ''
    min_minutes: int | None = None

    def batches(self):
        if (not self.leagues or any(not x.strip() for x in self.leagues)
                or not self.timeframes or not set(self.timeframes) <= set(TIMEFRAMES)
                or not self.positions or not set(self.positions) <= set(POSITIONS)
                or self.min_minutes is not None and self.min_minutes < 0):
            raise CollectorError('Sprawdź ligi, sezony, pozycje i minimalną liczbę minut.')
        for league in dict.fromkeys(self.leagues):
            for timeframe in dict.fromkeys(self.timeframes):
                for position in dict.fromkeys(self.positions):
                    layout = (self.gk_layout if position == 'GK' else self.field_layout).strip()
                    if not layout:
                        raise CollectorError('Podaj dokładną nazwę każdego używanego układu DISPLAY.')
                    yield Batch(league, timeframe, position, layout)
