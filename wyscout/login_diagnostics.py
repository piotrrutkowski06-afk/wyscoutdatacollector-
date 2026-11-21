"""Ephemeral, redacted observations for the Wyscout connection test."""
from collections import Counter
from contextlib import contextmanager

from playwright.sync_api import Error as BrowserError


DIAGNOSTICS_VERSION = 'SC-W6-3'
_RESOURCE = {'script': 'skrypty', 'xhr': 'API', 'fetch': 'API'}


class PostPasswordDiagnostics:
    def __init__(self, page, is_official):
        self.page = page
        self.is_official = is_official
        self.document_status = None
        self.javascript_errors = 0
        self.http_errors = Counter()
        self.interrupted = Counter()

    def _group(self, url):
        # Keep only the category, never the actual URL.
        try:
            return 'Hudl/Wyscout' if self.is_official(url) else 'pozostałe'
        except ValueError:
            return 'pozostałe'

    def _response(self, response):
        resource = response.request.resource_type
        status = response.status
        if resource == 'document':
            try:
                if response.frame == self.page.main_frame:
                    self.document_status = status
            except BrowserError:
                pass
        elif resource in _RESOURCE and 400 <= status < 600:
            # Use a fixed set of status categories to bound what can be reported.
            code = str(status) if status in (401, 403, 404, 429) else f'{status // 100}xx'
            self.http_errors[(_RESOURCE[resource], self._group(response.url), code)] += 1

    def _request_failed(self, request):
        resource = request.resource_type
        if resource in _RESOURCE:
            self.interrupted[(_RESOURCE[resource], self._group(request.url))] += 1

    def _page_error(self, _error):
        self.javascript_errors += 1

    @contextmanager
    def observe(self):
        handlers = (
            ('response', self._response),
            ('requestfailed', self._request_failed),
            ('pageerror', self._page_error),
        )
        for event, callback in handlers:
            self.page.on(event, callback)
        try:
            yield
        finally:
            for event, callback in handlers:
                self.page.remove_listener(event, callback)

    def summary(self):
        http = ', '.join(f'{kind}/{source} HTTP {code} ×{count}'
                         for (kind, source, code), count in sorted(self.http_errors.items())) or 'brak'
        interrupted = ', '.join(f'{kind}/{source} ×{count}'
                                for (kind, source), count in sorted(self.interrupted.items())) or 'brak'
        return (f'Błędy JavaScript: {self.javascript_errors}. '
                f'Błędy HTTP zasobów: {http}. Przerwane żądania: {interrupted}.')
