"""Tests for version module."""

import pytest

from inverter_dashboard.version import get_version, SelfUpdateDisabled


class TestGetVersion:
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
    def test_is_exception(self):
        with pytest.raises(SelfUpdateDisabled):
            raise SelfUpdateDisabled("test")
