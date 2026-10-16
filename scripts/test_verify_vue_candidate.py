"""The browser release gate must reject stale, altered and unsafe candidates."""

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from verify_vue_candidate import prepare

SOURCE = "a" * 40


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()

    def candidate(
        self,
        *,
        content=b"<html>Real build fixture</html>",
        unsafe=None,
        link=False,
        metadata_hash=None,
        source=SOURCE,
    ):
        metadata = {
            "package": "inverter-dashboard-vue",
            "version": "2.2.8-rc.1",
            "files": [
                {
                    "path": "index.html",
                    "sha256": metadata_hash or hashlib.sha256(content).hexdigest(),
                },
            ],
        }
        archive = self.artifacts / "vue-spa-dist.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name, data in [
                ("./index.html", content),
                ("./build-info.json", json.dumps(metadata).encode()),
            ]:
                item = tarfile.TarInfo(name)
                item.size = len(data)
                bundle.addfile(item, io.BytesIO(data))
            if unsafe:
                item = tarfile.TarInfo(unsafe)
                if link:
                    item.type = tarfile.SYMTYPE
                    item.linkname = "../../outside"
                bundle.addfile(item, io.BytesIO())
        receipt = {
            "source_sha": source,
            "artifacts": [
                {
                    "name": archive.name,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "size": archive.stat().st_size,
                }
            ],
        }
        (self.artifacts / "release-inputs-web.json").write_text(json.dumps(receipt))

    def run_candidate(self):
        return prepare(self.artifacts, self.root / "dist", self.root / "diagnostics", SOURCE)

    def test_preserves_exact_packaged_bytes_and_identity(self):
        self.candidate()
        identity = self.run_candidate()
        self.assertEqual(
            (self.root / "dist/index.html").read_bytes(), b"<html>Real build fixture</html>"
        )
        self.assertEqual(identity["version"], "2.2.8-rc.1")
        self.assertEqual(identity["filesVerified"], 1)
        self.assertEqual(
            json.loads((self.root / "diagnostics/candidate.json").read_text()), identity
        )

    def test_rejects_source_mismatch(self):
        self.candidate(source="b" * 40)
        with self.assertRaisesRegex(ValueError, "another source"):
            self.run_candidate()

    def test_rejects_changed_archive_even_when_still_readable(self):
        self.candidate()
        with (self.artifacts / "vue-spa-dist.tar.gz").open("ab") as archive:
            archive.write(b"extra")
        with self.assertRaisesRegex(ValueError, "build receipt"):
            self.run_candidate()

    def test_rejects_changed_file_inside_valid_archive(self):
        self.candidate(metadata_hash="0" * 64)
        with self.assertRaisesRegex(ValueError, "file hash"):
            self.run_candidate()

    def test_rejects_traversal_links_and_case_collisions_before_writing(self):
        for name, link in [
            ("../outside", False),
            ("assets/link", True),
            ("INDEX.html", False),
            ("C:/outside", False),
        ]:
            with self.subTest(name=name):
                self.candidate(unsafe=name, link=link)
                with self.assertRaises(ValueError):
                    self.run_candidate()
                self.assertFalse((self.root / "dist").exists())

    def test_does_not_replace_an_existing_destination(self):
        self.candidate()
        (self.root / "dist").mkdir()
        with self.assertRaisesRegex(ValueError, "must not exist"):
            self.run_candidate()


if __name__ == "__main__":
    unittest.main()
