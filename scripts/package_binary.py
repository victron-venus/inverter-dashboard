"""Archive a verified executable and produce its portable SHA256 checksum."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def main() -> None:
    """Package the requested platform's executable without changing its mode."""
    binary = Path(sys.argv[1])
    platform = sys.argv[2]
    output = Path("release")
    output.mkdir(exist_ok=True)
    archive = output / f"inverter-dashboard-{platform}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(binary, arcname=binary.name)
        metadata = {
            "version": Path("VERSION").read_text(encoding="utf-8").strip(),
            "platform": platform,
            "binary": binary.name,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
        bundle.writestr("build-info.json", json.dumps(metadata, indent=2) + "\n")
        for name in (".release-plan.json", ".release-inputs.json"):
            evidence = Path(name)
            if evidence.is_file() and not evidence.is_symlink():
                bundle.write(evidence, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii", newline="\n"
    )


if __name__ == "__main__":
    main()
