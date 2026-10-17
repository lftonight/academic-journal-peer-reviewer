#!/usr/bin/env python3
"""Install public skill files locally, with a complete backup of an existing skill."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from build_release import PUBLIC_SKILL_FILES, SKILL_NAME, checked_file, plain_path


def default_dest() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured).expanduser() if configured else Path.home() / ".codex") / "skills"


def validate_tree(root: Path) -> None:
    root = plain_path(root)
    if root.exists() and not root.is_dir():
        raise ValueError(f"Expected a directory: {root}")
    if root.exists():
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in folders + files:
                path = plain_path(Path(directory) / name)
                if not path.is_dir() and not path.is_file():
                    raise ValueError(f"Unsupported item in destination: {path}")


def atomic_copy(source: Path, target: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix=".install-", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path, dest: Path, backup_dir: Path | None = None) -> tuple[Path, Path | None]:
    source, dest = plain_path(source), plain_path(dest)
    public = [(name, checked_file(source, name)) for name in PUBLIC_SKILL_FILES]
    target = dest / SKILL_NAME
    validate_tree(target)
    if source.resolve().is_relative_to(target.resolve()) or target.resolve().is_relative_to(source.resolve()):
        raise ValueError("Source and installation target must not overlap.")
    for name, _ in public:
        candidate = plain_path(target / name)
        if candidate.exists() and not candidate.is_file():
            raise ValueError(f"Public-file target is not a regular file: {candidate}")
        for parent in candidate.parents:
            if parent == target.parent:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"Public-file parent is not a directory: {parent}")
    backup_root = plain_path(backup_dir if backup_dir is not None else dest.parent / "backups")
    if backup_root.resolve().is_relative_to(target.resolve()):
        raise ValueError("Backup directory must be outside the installation target.")
    backup: Path | None = None
    if target.exists():
        backup = backup_root / SKILL_NAME / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
        plain_path(backup)
        if backup.resolve().is_relative_to(target.resolve()):
            raise ValueError("Backup directory must be outside the installation target.")
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(target, backup)
        except OSError as exc:
            raise OSError(f"Backup failed; installation was not changed. Partial backup: {backup}. {exc}") from exc
        print(f"Complete backup: {backup}")
    changed: list[tuple[Path, bool]] = []
    created: list[Path] = []
    try:
        for name, original in public:
            candidate = target / name
            missing = []
            parent = candidate.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created.append(directory)
            existed = candidate.exists()
            atomic_copy(original, candidate)
            changed.append((candidate, existed))
    except (OSError, ValueError) as exc:
        rollback_errors = []
        for candidate, existed in reversed(changed):
            try:
                if existed and backup is not None:
                    atomic_copy(backup / candidate.relative_to(target), candidate)
                else:
                    candidate.unlink(missing_ok=True)
            except OSError as failure:
                rollback_errors.append(str(failure))
        for directory in reversed(created):
            try:
                directory.rmdir()  # Empty directories only; never recursively delete.
            except OSError:
                pass
        detail = f" Restore files from backup: {backup}." if backup is not None else ""
        if rollback_errors:
            detail += " Rollback errors: " + "; ".join(rollback_errors)
        raise OSError(f"Installation failed: {exc}.{detail}") from exc
    return target, backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).absolute().parents[1] / "skills" / SKILL_NAME,
                        help="Canonical public skill directory")
    parser.add_argument("--dest", type=Path, default=default_dest(), help="Skills root (not the individual skill directory)")
    parser.add_argument("--backup-dir", type=Path, help="Backup root; default: <dest parent>/backups")
    args = parser.parse_args()
    try:
        target, _ = install(args.source, args.dest, args.backup_dir)
        print(f"Installed public files: {target}")
        print(f"Private extensions were retained. Reload Codex, then invoke ${SKILL_NAME}.")
        print("See docs/codex.md for usage, confidentiality, and the packaged writing gate.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
