"""A failed frontend update must not leave placeholders, stale files or lost edits."""

import hashlib
import io
import json
import shutil

# Fixed argv only, used to prepare disposable Git fixtures.
import subprocess  # nosec B404
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from import_vue_release import ASSETS, LAYOUTS, REPOSITORY, import_release

SOURCE = "a" * 40
TAG = "v2.2.8-rc.1"


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        data = b"<html>Published fixture</html>"
        build = {
            "package": "inverter-dashboard-vue",
            "version": TAG[1:],
            "files": [{"path": "index.html", "sha256": hashlib.sha256(data).hexdigest()}],
        }
        with tarfile.open(self.artifacts / ASSETS[0], "w:gz") as tar:
            for name, value in [
                ("index.html", data),
                ("build-info.json", json.dumps(build).encode()),
            ]:
                item = tarfile.TarInfo(name)
                item.size = len(value)
                tar.addfile(item, io.BytesIO(value))
        receipt = {
            "source_sha": SOURCE,
            "artifacts": [self.asset(ASSETS[0])],
            "files": [{"path": "package-lock.json", "after_sha256": "b" * 64}],
        }
        (self.artifacts / ASSETS[1]).write_text(json.dumps(receipt))
        self.manifest = {
            "repository": REPOSITORY,
            "tag": TAG,
            "source_sha": SOURCE,
            "run_id": 123,
            "assets": [self.asset(name) for name in ASSETS[:2]],
        }
        self.write_manifest()

    def asset(self, name):
        data = (self.artifacts / name).read_bytes()
        return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def write_manifest(self):
        (self.artifacts / ASSETS[2]).write_text(json.dumps(self.manifest))

    def repository(self, kind):
        root = self.base / kind
        root.mkdir()
        distribution, metadata = LAYOUTS[kind]
        destination = root / distribution
        destination.mkdir(parents=True)
        (destination / "old.js").write_text("old tracked bytes")
        (root / metadata).write_text("{}")
        (root / ".github").mkdir()
        (root / ".github/browser-application.json").write_text(
            json.dumps({"manifestKind": kind, "distribution": distribution, "manifest": metadata})
        )
        for args in [
            ("init", "-q", "-b", "main"),
            ("add", "."),
            (
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-qm",
                "fixture",
            ),
        ]:
            executable = shutil.which("git")
            self.assertIsNotNone(executable)
            subprocess.run([executable, *args], cwd=root, check=True, capture_output=True)  # nosec B603
        return root, destination, root / metadata

    def test_replaces_complete_tree_and_records_verified_provenance(self):
        for kind in LAYOUTS:
            with self.subTest(kind=kind):
                root, destination, metadata = self.repository(kind)
                result = import_release(root, self.artifacts, TAG, SOURCE)
                self.assertFalse((destination / "old.js").exists())
                self.assertEqual(
                    (destination / "index.html").read_bytes(), b"<html>Published fixture</html>"
                )
                self.assertEqual(json.loads(metadata.read_text()), result)
                self.assertEqual(result["package_lock_sha256"], "b" * 64)
                self.assertEqual(result["release_receipt_sha256"], self.asset(ASSETS[1])["sha256"])
                self.assertFalse(list(destination.parent.glob(".frontend-import-*")))

    def test_bad_published_digest_preserves_old_tree(self):
        root, destination, metadata = self.repository("go")
        self.manifest["assets"][0]["sha256"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "release manifest"):
            import_release(root, self.artifacts, TAG, SOURCE)
        self.assertEqual((destination / "old.js").read_text(), "old tracked bytes")
        self.assertEqual(metadata.read_text(), "{}")

    def test_wrong_source_or_tag_never_changes_destination(self):
        root, destination, _ = self.repository("python")
        for tag, sha in [("v2.2.8-rc.2", SOURCE), (TAG, "c" * 40)]:
            with (
                self.subTest(tag=tag, source=sha),
                self.assertRaisesRegex(ValueError, "selected repository/tag/source"),
            ):
                import_release(root, self.artifacts, tag, sha)
            self.assertEqual((destination / "old.js").read_text(), "old tracked bytes")

    def test_manifest_write_failure_restores_previous_tree(self):
        root, destination, metadata = self.repository("go")
        with (
            mock.patch.object(Path, "replace", side_effect=OSError("Synthetic write failure")),
            self.assertRaisesRegex(OSError, "Synthetic write failure"),
        ):
            import_release(root, self.artifacts, TAG, SOURCE)
        self.assertEqual((destination / "old.js").read_text(), "old tracked bytes")
        self.assertFalse((destination / "index.html").exists())
        self.assertEqual(metadata.read_text(), "{}")
        self.assertFalse(list(destination.parent.glob(".frontend-import-*")))

    def test_in_checkout_symlink_ancestor_is_rejected(self):
        root, destination, _ = self.repository("go")
        parent = destination.parent
        moved = root / "shared-html"
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            import_release(root, self.artifacts, TAG, SOURCE)
        self.assertEqual((moved / "vue-ui/old.js").read_text(), "old tracked bytes")

    def test_dirty_or_ignored_files_are_preserved(self):
        root, destination, _ = self.repository("go")
        (destination / "old.js").write_text("unfinished edit")
        with self.assertRaisesRegex(ValueError, "existing frontend changes"):
            import_release(root, self.artifacts, TAG, SOURCE)
        (destination / "old.js").write_text("old tracked bytes")
        (root / ".git/info/exclude").write_text("local-only\n")
        (destination / "local-only").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "ignored local files"):
            import_release(root, self.artifacts, TAG, SOURCE)
        self.assertEqual((destination / "local-only").read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
