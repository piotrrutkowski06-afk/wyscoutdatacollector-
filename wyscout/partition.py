from datetime import date, timedelta
from .settings import CollectorError

LIMIT = 500


def partitions(total, first, last, count, *, checkpoint=lambda: None):
    """Plan inclusive, disjoint DOB queries; never assume missing DOBs do not exist."""
    if not isinstance(first, date) or not isinstance(last, date) or first > last:
        raise CollectorError('Nieprawidłowe granice dostępnego filtra daty urodzenia.')
    checkpoint()
    if count(first, last) != total:
        raise CollectorError('Filtr daty urodzenia nie pokrywa całej populacji wyszukiwania.')

    def split(left, right, expected):
        checkpoint()
        if not expected:
            return
        if expected < LIMIT:
            yield left, right, expected
            return
        if left == right:
            raise CollectorError('Nie udało się rozdzielić wyników poniżej limitu Wyscout.')
        middle = left + (right - left) // 2
        next_day = middle + timedelta(days=1)
        a = count(left, middle)
        checkpoint()
        b = count(next_day, right)
        if a + b != expected:
            raise CollectorError('Liczniki przedziałów nie zgadzają się z licznikiem grupy nadrzędnej.')
        yield from split(left, middle, a)
        yield from split(next_day, right, b)

    yield from split(first, last, total)
