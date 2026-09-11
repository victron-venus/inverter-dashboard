"""Archive a verified executable and produce its portable SHA256 checksum."""

import hashlib
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
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii", newline="\n"
    )


if __name__ == "__main__":
    main()
