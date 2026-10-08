"""
Verifies each client converts a socket.timeout (as opposed to a bare
TimeoutError or urllib.error.URLError -- see CLAUDE.md note in each client's
module docstring) into its own error type, without ever touching the
network. socket.timeout is TimeoutError itself on Python 3.10+, but is a
distinct exception class pre-3.10, which is why it must be caught alongside
TimeoutError explicitly.
"""

import socket

import pytest

from src.clients import ourairports, source_a, t100_routes


def test_source_a_converts_socket_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(source_a.urllib.request, "urlopen", raise_timeout)
    with pytest.raises(source_a.SourceAError):
        source_a._get_page(2025, 0)


def test_ourairports_converts_socket_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(ourairports.urllib.request, "urlopen", raise_timeout)
    with pytest.raises(ourairports.OurAirportsError):
        ourairports._fetch_csv_text()


def test_t100_routes_converts_socket_timeout(monkeypatch, tmp_path):
    class FakeOpener:
        def open(self, *a, **k):
            raise socket.timeout("timed out")

    monkeypatch.setattr(t100_routes.urllib.request, "build_opener", lambda *a, **k: FakeOpener())
    monkeypatch.setattr(t100_routes, "RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(t100_routes.T100DownloadError):
        t100_routes.download_year(2025, raw_dir=str(tmp_path))
