"""Git must preserve published frontend bytes even with Windows line endings."""

import hashlib
import json
import os
import shutil

# Fixed Git commands operate only on a disposable local fixture.
import subprocess  # nosec B404
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VueCheckoutTests(unittest.TestCase):
    """Exercise the real release inventory through Git's conversion boundary."""

    def test_published_bytes_survive_git_add_and_checkout(self):
        application = json.loads((ROOT / ".github/browser-application.json").read_text())
        distribution = Path(application["distribution"])
        manifest_path = Path(application["manifest"])
        manifest_bytes = (ROOT / manifest_path).read_bytes()
        manifest = json.loads(manifest_bytes)
        inventory = (
            {entry["path"]: entry["sha256"] for entry in manifest["files"]}
            if application["manifestKind"] == "python"
            else manifest["sha256"]
        )
        self.assertTrue(inventory)
        git = shutil.which("git")
        self.assertIsNotNone(git, "Git is required for the checkout regression")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            shutil.copyfile(ROOT / ".gitattributes", repository / ".gitattributes")
            shutil.copytree(ROOT / distribution, repository / distribution)
            (repository / manifest_path).write_bytes(manifest_bytes)
            # Exercise mixed endings as well as the current release's LF files.
            mixed_path = distribution / "assets" / "checkout-fixture.txt"
            mixed_bytes = b"first\r\nsecond\nthird\r\n"
            (repository / mixed_path).write_bytes(mixed_bytes)
            (repository / "control.txt").write_bytes(b"outside frontend\n")
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("GIT_")
            }
            environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

            def run_git(*arguments):
                subprocess.run(  # nosec B603
                    [git, "-c", "core.autocrlf=true", "-C", str(repository), *arguments],
                    env=environment,
                    check=True,
                    capture_output=True,
                )

            run_git("init", "-q")
            run_git("add", ".")
            for autocrlf in ("false", "true"):
                with self.subTest(autocrlf=autocrlf):
                    output = base / autocrlf
                    run_git(
                        "-c",
                        f"core.autocrlf={autocrlf}",
                        "checkout-index",
                        "--all",
                        f"--prefix={output.as_posix()}/",
                    )
                    for name, expected in inventory.items():
                        actual = hashlib.sha256((output / distribution / name).read_bytes())
                        self.assertEqual(actual.hexdigest(), expected, name)
                    self.assertEqual((output / manifest_path).read_bytes(), manifest_bytes)
                    self.assertEqual((output / mixed_path).read_bytes(), mixed_bytes)
                    expected_control = b"outside frontend" + (
                        b"\r\n" if autocrlf == "true" else b"\n"
                    )
                    self.assertEqual((output / "control.txt").read_bytes(), expected_control)


if __name__ == "__main__":
    unittest.main()
