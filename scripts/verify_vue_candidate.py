"""Verify a Vue release payload; kept aligned with Vue prepare_browser_candidate.py."""

import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
BUILD_INFO = "build-info.json"


def member_path(name):
    """Use paths with the same meaning on every supported runner OS."""
    if name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/"):
        raise ValueError("Unsafe archive path")
    path = PurePosixPath(name)
    if any(part.endswith((".", " ")) for part in path.parts):
        raise ValueError("Nonportable archive path")
    return path.as_posix()


def archive_files(bundle):
    members = bundle.getmembers()
    if (
        not members
        or len(members) > 10_000
        or sum(item.size for item in members) > MAX_UNPACKED_BYTES
    ):
        raise ValueError("Candidate has an invalid unpacked size")
    files, seen = {}, set()
    for item in members:
        name = member_path(item.name)
        if not (item.isfile() or item.isdir()):
            raise ValueError("Candidate contains a link or special file")
        if name == "." and item.isdir():
            continue
        if name.casefold() in seen:
            raise ValueError("Candidate contains duplicate paths")
        seen.add(name.casefold())
        if item.isfile():
            files[name] = item
    return files


def verify_inventory(bundle, files):
    if "index.html" not in files or BUILD_INFO not in files:
        raise ValueError("Candidate is not a complete packaged SPA")
    if files[BUILD_INFO].size > 2_000_000:
        raise ValueError("Candidate metadata exceeds size limits")
    metadata = json.load(bundle.extractfile(files[BUILD_INFO]))
    if metadata.get("package") != "inverter-dashboard-vue" or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", metadata.get("version", "")
    ):
        raise ValueError("Candidate package identity is invalid")
    inventory = metadata.get("files", [])
    hashes = {member_path(item["path"]): item["sha256"] for item in inventory}
    if len(hashes) != len(inventory) or set(hashes) != set(files) - {BUILD_INFO}:
        raise ValueError("Candidate file inventory differs from metadata")
    for name, expected in hashes.items():
        actual = hashlib.sha256(bundle.extractfile(files[name]).read()).hexdigest()
        if actual != expected:
            raise ValueError("Candidate file hash differs from metadata")
    return metadata, hashes


def write_payload(bundle, files, destination):
    destination.mkdir(parents=True)
    try:
        # Never extract archive ownership, permissions, symlinks or hard links.
        for name, item in files.items():
            output = destination.joinpath(*PurePosixPath(name).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(item) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
    except BaseException:
        shutil.rmtree(destination)
        raise


def prepare(artifacts, destination, diagnostics, source_sha):
    """Bind the archive to its build receipt, then verify every packaged file."""
    artifacts, destination, diagnostics = map(Path, (artifacts, destination, diagnostics))
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("The candidate requires the current source SHA")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Candidate destination must not exist")
    archive = artifacts / "vue-spa-dist.tar.gz"
    receipt_path = artifacts / "release-inputs-web.json"
    if archive.is_symlink() or receipt_path.is_symlink():
        raise ValueError("Candidate files must not be links")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES or receipt_path.stat().st_size > 2_000_000:
        raise ValueError("Candidate exceeds size limits")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("source_sha") != source_sha:
        raise ValueError("Candidate belongs to another source commit")
    entries = [item for item in receipt.get("artifacts", []) if item.get("name") == archive.name]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if (
        len(entries) != 1
        or entries[0].get("sha256") != digest
        or entries[0].get("size") != archive.stat().st_size
    ):
        raise ValueError("Candidate archive differs from its build receipt")

    with tarfile.open(archive, "r:gz") as bundle:
        files = archive_files(bundle)
        metadata, hashes = verify_inventory(bundle, files)
        write_payload(bundle, files, destination)
    identity = {
        "archive": archive.name,
        "sha256": digest,
        "size": archive.stat().st_size,
        "sourceSha": source_sha,
        "package": metadata["package"],
        "version": metadata["version"],
        "filesVerified": len(hashes),
    }
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "candidate.json").write_text(
        json.dumps(identity, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Verified {identity['version']}: {len(hashes)} packaged SPA files, sha256 {digest}")
    return identity
