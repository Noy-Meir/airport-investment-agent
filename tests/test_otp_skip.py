import os

from src.clients import otp


def _write_fake_zip(path, size=2_000_000):
    with open(path, "wb") as f:
        f.write(b"PK" + b"\0" * (size - 2))


def test_download_month_skips_valid_existing_zip(monkeypatch, tmp_path):
    dest = tmp_path / "otp_2026_01.zip"
    _write_fake_zip(dest)

    def fail_if_called(*a, **k):
        raise AssertionError("should not attempt a download when a valid ZIP already exists")

    monkeypatch.setattr(otp, "_attempt_download", fail_if_called)
    path = otp.download_month(2026, 1, raw_dir=str(tmp_path))
    assert path == str(dest)


def test_download_month_force_redownloads(monkeypatch, tmp_path):
    dest = tmp_path / "otp_2026_01.zip"
    _write_fake_zip(dest)

    calls = []

    def fake_download(year, month):
        calls.append((year, month))
        return b"PK" + b"\0" * 2_000_000

    monkeypatch.setattr(otp, "_attempt_download", fake_download)
    otp.download_month(2026, 1, raw_dir=str(tmp_path), force=True)
    assert calls == [(2026, 1)]


def test_download_month_redownloads_when_existing_zip_too_small(monkeypatch, tmp_path):
    dest = tmp_path / "otp_2026_01.zip"
    _write_fake_zip(dest, size=100)  # below MIN_VALID_ZIP_BYTES -- looks truncated/corrupt

    calls = []

    def fake_download(year, month):
        calls.append((year, month))
        return b"PK" + b"\0" * 2_000_000

    monkeypatch.setattr(otp, "_attempt_download", fake_download)
    otp.download_month(2026, 1, raw_dir=str(tmp_path))
    assert calls == [(2026, 1)]
