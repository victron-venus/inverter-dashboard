"""Confine qualification CLI files to their trusted checkout, without symlinks."""

from __future__ import annotations

import os
import stat
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]


def _location(value: str | Path, root: Path | None) -> tuple[Path, Path]:
    supplied = Path(value)
    if not str(value) or ".." in supplied.parts:
        raise ValueError("Qualification paths cannot traverse parent directories")
    anchor = Path(os.path.abspath(root if root is not None else CHECKOUT))
    trusted = anchor.resolve(strict=True)
    candidate = supplied if supplied.is_absolute() else anchor / supplied
    try:
        relative = candidate.relative_to(anchor)
    except ValueError:
        try:
            relative = candidate.relative_to(trusted)
        except ValueError as error:
            raise ValueError("Qualification path escapes the trusted checkout") from error
    if relative == Path("."):
        raise ValueError("The checkout itself is not a qualification path")
    return trusted, relative


def _regular_ancestors(trusted: Path, relative: Path) -> None:
    cursor = trusted
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            entry = cursor.lstat()
        except FileNotFoundError:
            break
        mode = entry.st_mode
        reparse = getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if stat.S_ISLNK(mode) or reparse:
            raise ValueError("Qualification paths cannot contain links or reparse points")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError("Qualification path parent is not a directory")
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("Qualification paths must be regular files or directories")


def _validate_leaf(result: Path, kind: str | None, must_exist: bool, new: bool) -> None:
    if kind not in {None, "file", "directory"}:
        raise ValueError("Unsupported qualification path kind")
    if must_exist and not result.exists():
        raise ValueError("Required qualification path is missing")
    if new and result.exists():
        raise ValueError("Qualification output already exists")
    if result.exists() and kind == "file" and not result.is_file():
        raise ValueError("Qualification path must be a file")
    if result.exists() and kind == "directory" and not result.is_dir():
        raise ValueError("Qualification path must be a directory")


def workspace_path(
    value: str | Path,
    *,
    root: Path | None = None,
    kind: str | None = None,
    must_exist: bool = False,
    new: bool = False,
) -> Path:
    """Validate paths before I/O; root overrides belong only to isolated tests.

    CLI callers cannot select the trusted root. Missing final components are
    permitted for new outputs, but every existing ancestor must be a real
    directory. Consumers still use exclusive creation and validate contents.
    """
    trusted, relative = _location(value, root)
    _regular_ancestors(trusted, relative)
    result = trusted / relative
    if not result.resolve(strict=False).is_relative_to(trusted):
        raise ValueError("Resolved qualification path escapes the trusted checkout")
    _validate_leaf(result, kind, must_exist, new)
    return result
