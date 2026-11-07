"""Regression controls for trusted qualification path boundaries."""

import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qualification_paths import workspace_path


class QualificationPathsTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        (self.root / "reports").mkdir()
        (self.root / "reports" / "source.json").write_text("{}")

    def test_relative_and_absolute_inputs_resolve_inside_root(self):
        for value in ("reports/source.json", self.root / "reports/source.json"):
            self.assertEqual(
                workspace_path(value, root=self.root, kind="file", must_exist=True),
                self.root.resolve() / "reports/source.json",
            )

    def test_new_nested_output_is_allowed_without_creating_it(self):
        path = workspace_path("reports/new/result.json", root=self.root, kind="file", new=True)
        self.assertFalse(path.exists())

    def test_escape_and_root_are_rejected(self):
        for value in ("../escape", "reports/../source", "/etc/passwd", "", ".", self.root):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                workspace_path(value, root=self.root)

    def test_symlink_file_parent_and_dangling_link_are_rejected(self):
        (self.root / "file-link").symlink_to(self.root / "reports/source.json")
        (self.root / "directory-link").symlink_to(self.root / "reports", target_is_directory=True)
        (self.root / "dangling").symlink_to(self.root / "missing")
        for value in ("file-link", "directory-link/source.json", "dangling/child"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workspace_path(value, root=self.root)

    def test_wrong_types_missing_and_overwrite_are_rejected(self):
        cases = [
            ("reports", {"kind": "file"}),
            ("reports/source.json", {"kind": "directory"}),
            ("missing", {"must_exist": True}),
            ("reports/source.json", {"new": True}),
            ("reports/source.json/child", {}),
            ("reports", {"kind": "socket"}),
        ]
        for value, options in cases:
            with self.subTest(value=value, options=options), self.assertRaises(ValueError):
                workspace_path(value, root=self.root, **options)

    def test_windows_directory_reparse_point_is_rejected(self):
        junction = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o755,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
        )
        with mock.patch.object(Path, "lstat", return_value=junction), self.assertRaises(ValueError):
            workspace_path("reports/source.json", root=self.root)


if __name__ == "__main__":
    unittest.main()
