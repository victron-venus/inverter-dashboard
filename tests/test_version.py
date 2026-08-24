"""Tests for version module."""

import pytest

from inverter_dashboard.version import SelfUpdateDisabled, download_and_update, get_version


class TestGetVersion:
    """Tests for get_version."""

    def test_returns_string(self):
        result = get_version()
        assert isinstance(result, str)

    def test_returns_non_empty(self):
        result = get_version()
        assert len(result) > 0

    def test_falls_back_to_dev(self, tmp_path, monkeypatch):
        """When VERSION file is missing, returns 'dev'."""
        # Point __file__ to a non-existent directory
        monkeypatch.setattr(
            "inverter_dashboard.version.__file__",
            str(tmp_path / "nonexistent_version.py"),
        )
        # Also patch sys.frozen if present
        monkeypatch.delattr("sys.frozen", raising=False)
        assert get_version() == "dev"


class TestSelfUpdateDisabled:
    """Tests for SelfUpdateDisabled behavior."""

    def test_is_exception(self):
        with pytest.raises(SelfUpdateDisabled):
            raise SelfUpdateDisabled("test")


class TestDownloadAndUpdate:
    """Tests for download_and_update."""

    def _enable(self, monkeypatch, pin=""):
        monkeypatch.setattr("inverter_dashboard.version.SELF_UPDATE_ENABLED", True)
        monkeypatch.setattr("inverter_dashboard.version.UPDATE_PIN", pin)

    def test_raises_when_disabled(self, monkeypatch):
        monkeypatch.setattr("inverter_dashboard.version.SELF_UPDATE_ENABLED", False)
        with pytest.raises(SelfUpdateDisabled):
            download_and_update()

    def test_success_tracks_main(self, monkeypatch):
        self._enable(monkeypatch)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

        monkeypatch.setattr("subprocess.run", fake_run)
        ok, msg = download_and_update()
        assert ok is True
        assert isinstance(msg, str) and msg
        assert len(calls) == 2
        assert calls[0][:3] == ["git", "fetch", "origin"]
        assert calls[0][-1] == "main"
        assert calls[1] == ["git", "reset", "--hard", "origin/main"]

    def test_pin_uses_fetch_head(self, monkeypatch):
        self._enable(monkeypatch, pin="v9.9.9")
        cmds = []
        monkeypatch.setattr("subprocess.run", lambda cmd, **k: cmds.append(cmd))
        ok, _ = download_and_update()
        assert ok is True
        assert cmds[0][-1] == "v9.9.9"
        assert cmds[1] == ["git", "reset", "--hard", "FETCH_HEAD"]

    def test_git_failure_returns_false(self, monkeypatch):
        import subprocess

        self._enable(monkeypatch)

        def boom(cmd, **kwargs):
            raise subprocess.CalledProcessError(128, cmd)

        monkeypatch.setattr("subprocess.run", boom)
        ok, msg = download_and_update()
        assert ok is False
        assert msg.startswith("git update failed:")

    def test_os_error_returns_false(self, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **k: (_ for _ in ()).throw(OSError("no repo")),
        )
        ok, msg = download_and_update()
        assert ok is False
        assert "git update failed" in msg
