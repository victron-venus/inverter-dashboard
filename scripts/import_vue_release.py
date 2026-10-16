#!/usr/bin/env python3
"""Import one published Vue release after validating its tag, receipt and bytes."""

import argparse
import hashlib
import json
import re
import shutil

# Commands use fixed executables and argv arrays; shell execution is never enabled.
import subprocess  # nosec B404
import tempfile
from pathlib import Path

from verify_vue_candidate import MAX_ARCHIVE_BYTES, prepare
from version_plan import plan_digest, projections, validate_plan

REPOSITORY = "victron-venus/inverter-dashboard-vue"
RECEIPT = "release-inputs-web.json"
RELEASE_MANIFEST = "release-manifest.json"
ASSETS = ("vue-spa-dist.tar.gz", RECEIPT, RELEASE_MANIFEST)
LAYOUTS = {
    "python": ("src/inverter_dashboard/static", "src/inverter_dashboard/static/source-info.json"),
    "go": ("internal/html/vue-ui", "internal/html/vue-ui-source.json"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_metadata(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Release metadata must be a bounded regular file")
    return json.loads(path.read_text(encoding="utf-8"))


def verified_plan(manifest, tag, source_sha):
    """Stable promotions retain the original RC manifest and package bytes."""
    plan = validate_plan(manifest.get("version_plan"), source_sha=source_sha)
    exact_tag = tag == plan["tag"]
    promoted_tag = (
        tag == "v" + plan["base_version"]
        and plan["channel"] == "rc"
        and plan["promotion"] == "promote-bytes"
    )
    if (
        manifest.get("repository") != REPOSITORY
        or manifest.get("source_sha") != source_sha
        or manifest.get("tag") != plan["tag"]
        or manifest.get("channel") != plan["channel"]
        or manifest.get("version") != plan["base_version"]
        or manifest.get("plan_sha256") != plan_digest(plan)
        or not (exact_tag or promoted_tag)
    ):
        raise ValueError("Release manifest does not match selected repository/tag/source or plan")
    return plan


def verify_published_assets(artifacts, manifest):
    for name in ASSETS[:2]:
        entries = [entry for entry in manifest.get("assets", []) if entry.get("name") == name]
        file = artifacts / name
        if (
            file.is_symlink()
            or not file.is_file()
            or file.stat().st_size > MAX_ARCHIVE_BYTES
            or len(entries) != 1
        ):
            raise ValueError("Release asset inventory is missing or ambiguous")
        if file.stat().st_size != entries[0].get("size") or digest(file) != entries[0].get(
            "sha256"
        ):
            raise ValueError("Published asset differs from release manifest")


def effective_lock(receipt, plan):
    if receipt.get("plan_sha256") != plan_digest(plan):
        raise ValueError("Release receipt belongs to another version plan")
    locks = [file for file in receipt.get("files", []) if file.get("path") == "package-lock.json"]
    if len(locks) != 1 or not re.fullmatch(r"[0-9a-f]{64}", locks[0].get("after_sha256", "")):
        raise ValueError("Release receipt has no unique effective package lock hash")
    return locks[0]["after_sha256"]


def verify_release(artifacts, payload, diagnostics, tag, source_sha):
    """Bind published assets and internal inventory to the explicit source tag."""
    artifacts = Path(artifacts)
    manifest = read_metadata(artifacts / RELEASE_MANIFEST)
    plan = verified_plan(manifest, tag, source_sha)
    verify_published_assets(artifacts, manifest)
    receipt = read_metadata(artifacts / RECEIPT)
    lock_hash = effective_lock(receipt, plan)
    identity = prepare(artifacts, payload, diagnostics, source_sha)
    if identity["version"] != projections(plan)["package"]:
        raise ValueError("Packaged version does not match the frozen release projection")
    run_id = manifest.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("Release manifest has no valid producing run")
    return {
        "schema": 1,
        "repository": "https://github.com/" + REPOSITORY,
        "source_commit": source_sha,
        "package_version": identity["version"],
        "package_lock_sha256": lock_hash,
        "build_command": "Official GitHub Actions publication; imported without rebuilding",
        "release_tag": tag,
        "origin_tag": plan["tag"],
        "release_plan_sha256": plan_digest(plan),
        "release_run_id": run_id,
        "release_receipt_sha256": digest(artifacts / RECEIPT),
        "release_manifest_sha256": digest(artifacts / RELEASE_MANIFEST),
        "archive_sha256": identity["sha256"],
        "files": [
            {
                "path": file.relative_to(payload).as_posix(),
                "sha256": digest(file),
                "size": file.stat().st_size,
            }
            for file in sorted(payload.rglob("*"))
            if file.is_file()
        ],
    }


def command(program, *args, cwd=None):
    if program not in ("git", "gh"):
        raise ValueError("Unsupported executable")
    executable = shutil.which(program)
    if executable is None:
        raise RuntimeError(f"Required executable is unavailable: {program}")
    # Only fixed git/gh subcommands and validated tag/layout arguments reach this helper.
    argv = [executable, *args]
    options = {"cwd": cwd, "text": True, "timeout": 180}
    output = subprocess.check_output(argv, **options)  # nosec B603
    return output.strip()


def embedded_layout(root):
    application = read_metadata(root / ".github/browser-application.json")
    # Select literal paths; values read from JSON are never used as filesystem paths.
    if application.get("manifestKind") == "python":
        kind, layout = "python", LAYOUTS["python"]
    elif application.get("manifestKind") == "go":
        kind, layout = "go", LAYOUTS["go"]
    else:
        raise ValueError("Unsupported embedded frontend layout")
    if layout != (application.get("distribution"), application.get("manifest")):
        raise ValueError("Unsupported embedded frontend layout")
    return kind, layout


def check_layout_paths(root, layout):
    for relative in layout:
        current = root
        parts = Path(relative).parts
        for index, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                raise ValueError("Frontend destination cannot traverse any symbolic link")
            if index < len(parts) - 1 and current.exists() and not current.is_dir():
                raise ValueError("Frontend destination ancestor must be a directory")
    destination = root / layout[0]
    if destination.exists() and not destination.is_dir():
        raise ValueError("Frontend destination must be a directory")


def check_clean_frontend(root, layout):
    check_layout_paths(root, layout)
    if command("git", "status", "--porcelain=v1", "--untracked-files=all", "--", *layout, cwd=root):
        raise ValueError("Commit or preserve existing frontend changes before importing")
    if command(
        "git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", *layout, cwd=root
    ):
        raise ValueError("Frontend destination contains ignored local files")


def stage_metadata(kind, metadata, payload, work):
    if kind == "python":
        if (payload / "source-info.json").exists():
            raise ValueError("Release archive conflicts with local provenance filename")
        output = payload / "source-info.json"
    else:
        metadata["commit"] = metadata.pop("source_commit")
        metadata["sha256"] = {row["path"]: row["sha256"] for row in metadata.pop("files")}
        output = work / "manifest.json"
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def restore_previous(
    destination, backup, manifest_file, previous_manifest, promoted, external_manifest
):
    if promoted:
        shutil.rmtree(destination)
    if backup.exists():
        backup.rename(destination)
    if external_manifest:
        if previous_manifest is None:
            manifest_file.unlink(missing_ok=True)
        else:
            manifest_file.write_bytes(previous_manifest)


def promote_payload(kind, work, destination, manifest_file):
    payload, backup = work / "payload", work / "previous"
    previous_manifest = manifest_file.read_bytes() if manifest_file.exists() else None
    if destination.exists():
        destination.rename(backup)
    promoted = False
    try:
        payload.rename(destination)
        promoted = True
        if kind == "go":
            (work / "manifest.json").replace(manifest_file)
    except BaseException:
        restore_previous(
            destination, backup, manifest_file, previous_manifest, promoted, kind == "go"
        )
        raise
    if backup.exists():
        shutil.rmtree(backup)


def import_release(root, artifacts, tag, source_sha):
    root = Path(root).resolve()
    kind, layout = embedded_layout(root)
    check_clean_frontend(root, layout)
    destination, manifest_file = (root / name for name in layout)
    work = Path(tempfile.mkdtemp(prefix=".frontend-import-", dir=destination.parent))
    payload = work / "payload"
    try:
        metadata = verify_release(artifacts, payload, work / "diagnostics", tag, source_sha)
        stage_metadata(kind, metadata, payload, work)
        check_clean_frontend(root, layout)
        promote_payload(kind, work, destination, manifest_file)
        return metadata
    finally:
        # Keep a recoverable previous tree if an exceptional rollback itself failed.
        if not (work / "previous").exists():
            shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Explicit published version, such as v2.2.8-rc.1")
    args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", args.tag):
        parser.error("Select an explicit version tag; latest and placeholders are not accepted")
    root = Path(__file__).resolve().parents[1]
    source_sha = command("gh", "api", f"repos/{REPOSITORY}/commits/{args.tag}", "--jq", ".sha")
    with tempfile.TemporaryDirectory(prefix="vue-published-release-") as directory:
        command(
            "gh",
            "release",
            "download",
            args.tag,
            "--repo",
            REPOSITORY,
            "--dir",
            directory,
            "--pattern",
            ASSETS[0],
            "--pattern",
            ASSETS[1],
            "--pattern",
            ASSETS[2],
        )
        result = import_release(root, Path(directory), args.tag, source_sha)
    print(
        f"Imported {result['release_tag']} from {source_sha}; review the resulting diff before committing"
    )


if __name__ == "__main__":
    main()
