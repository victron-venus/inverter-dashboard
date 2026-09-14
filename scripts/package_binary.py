"""Archive a verified executable and produce its portable SHA256 checksum."""

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from version_receipt import confined_cli_path


def checked_binary(root: Path, requested: Path) -> Path:
    """Accept only a regular executable produced in this checkout's dist directory."""
    requested = confined_cli_path(root, requested, "file")
    for name in ("inverter-dashboard", "inverter-dashboard.exe"):
        expected = root / "dist" / name
        if requested == expected:
            return confined_cli_path(root, expected, "file")
    raise ValueError("Release binary must be dist/inverter-dashboard[.exe]")


def main() -> None:
    """Package the requested platform's executable without changing its mode."""
    root = Path(__file__).resolve().parents[1]
    binary = checked_binary(root, Path(sys.argv[1]))
    platform = sys.argv[2]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", platform, flags=re.ASCII):
        raise ValueError("Release platform must be a simple filename component")
    output = root / "release"
    if output.exists():
        output = confined_cli_path(root, output, "directory")
    else:
        output = confined_cli_path(root, output, "new")
        output.mkdir()
    archive = confined_cli_path(root, output / f"inverter-dashboard-{platform}.zip", "new")
    checksum = confined_cli_path(root, archive.with_suffix(".zip.sha256"), "new")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(binary, arcname=binary.name)
        metadata = {
            "version": (root / "VERSION").read_text(encoding="utf-8").strip(),
            "platform": platform,
            "binary": binary.name,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
        bundle.writestr("build-info.json", json.dumps(metadata, indent=2) + "\n")
        for name in (".release-plan.json", ".release-inputs.json"):
            evidence = root / name
            if evidence.is_file() and not evidence.is_symlink():
                bundle.write(evidence, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {archive.name}\n")


if __name__ == "__main__":
    main()
