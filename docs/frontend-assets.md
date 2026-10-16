# Embedded frontend assets

The Python package serves the reviewed Vue SPA committed under
`src/inverter_dashboard/static`. Do not edit the minified JavaScript or rebuild a
released bundle locally. Its exact source and published version are recorded in
`static/source-info.json`; that file is the authoritative current identity.

Use `python3 scripts/import_vue_release.py v2.2.8-rc.1`, replacing the example
with an explicit published tag. The command resolves that tag through GitHub,
downloads only the SPA archive and its two release metadata files, and checks the
repository, source commit, tag, packaged version, archive digest and complete
file inventory before replacing anything. The frozen version plan and its digest
bind the tag to the package projection: an RC using `promote-bytes` already
contains the stable package version. A stable tag can retain that exact RC
manifest only when the base version and source commit match. This requires a
modern versioned release with a frozen plan; historical manifests without that
plan are rejected. Archive links, traversal paths and
case collisions are rejected. A failed or unavailable release never creates a
placeholder or reports success with old files.

The importer refuses modified, untracked or ignored files in the frontend
paths. It stages the verified bundle separately, removes obsolete output files
by replacing the complete tree and preserves the previous tree if rollback
cannot finish. Review the resulting diff and run the required browser and
package checks before committing. GitHub CLI authentication is required for
the release lookup/download; no token is embedded in the package.

`source-info.json` records the source commit, effective release lockfile hash,
requested release tag, original producing tag, version-plan digest, run identity,
receipt and manifest hashes, archive hash, and hashes/sizes
of every copied output file. The package and browser checks verify this inventory.
The importer validates published bytes and provenance; it does not replace the
release's own acceptance checks.
