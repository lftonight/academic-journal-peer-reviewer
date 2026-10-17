#!/usr/bin/env python3
"""Build two offline release ZIPs from an explicit public-file allowlist."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import zipfile

SKILL_NAME = "academic-journal-peer-reviewer"
VERSION = "1.1.0"
PUBLIC_SKILL_FILES = (
    "SKILL.md",
    "README.md",
    "LICENSE",
    "ATTRIBUTION.md",
    "references/anti-defensive-gate.md",
    "references/gates.md",
    "references/language-modules.md",
    "references/reference-verification.md",
    "references/report-templates.md",
    "references/reporting-guidelines.md",
    "references/review-checklists.md",
    "scripts/writing_gate.py",
    "scripts/test_writing_gate.py",
    "scripts/batch_doi_verify.py",
    "scripts/test_batch_doi_verify.py",
    "third_party/anti-defensive-writing/LICENSE",
)
PUBLIC_ROOT_FILES = (
    "README.md", "README_EN.md", "LICENSE", "plugin.json", "ATTRIBUTION.md",
    "docs/chatgpt.md", "docs/codex.md",
    "scripts/build_release.py", "scripts/install_codex.py", "scripts/test_release_tools.py",
)


def plain_path(path: Path) -> Path:
    """Reject links and Windows junction/reparse points, including ancestors."""
    path = path.absolute()
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Links/reparse points are not allowed: {part}")
    return path


def checked_file(base: Path, relative: str) -> Path:
    """Resolve one approved regular file without leaving its declared root."""
    base = plain_path(base)
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Invalid allowlist path: {relative}")
    path = plain_path(base / rel)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"File leaves its source root: {path}")
    if not path.is_file():
        raise ValueError(f"Required public file is missing or not a regular file: {path}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Unsupported file type: {path}")
    return path


def release_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or not 1980 <= parsed.year <= 2107:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD with a year from 1980 to 2107.") from exc
    return value


def write_zip(path: Path, entries: list[tuple[str, Path]], day: str) -> None:
    stamp = date.fromisoformat(day)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(entries):
            info = zipfile.ZipInfo(name, (stamp.year, stamp.month, stamp.day, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def build(repo: Path, day: str, out_dir: Path | None = None) -> tuple[Path, Path]:
    day = release_date(day)
    repo = plain_path(repo)
    skill = repo / "skills" / SKILL_NAME
    root_entries = [(name, checked_file(repo, name)) for name in PUBLIC_ROOT_FILES]
    skill_entries = [(name, checked_file(skill, name)) for name in PUBLIC_SKILL_FILES]
    manifest = json.loads(checked_file(repo, "plugin.json").read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise ValueError(f"plugin.json must declare version {VERSION}.")
    out_dir = plain_path(out_dir if out_dir is not None else repo / "dist")
    if out_dir.exists() and not out_dir.is_dir():
        raise ValueError(f"Output directory is not a directory: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = tuple(out_dir / f"{day}_{SKILL_NAME}-v{VERSION}-{kind}.zip" for kind in ("plugin", "skill"))
    for output in outputs:
        plain_path(output)
        if output.exists() and not output.is_file():
            raise ValueError(f"Output path is not a regular file: {output}")
    groups = (
        root_entries + [(f"skills/{SKILL_NAME}/{name}", path) for name, path in skill_entries],
        [(f"{SKILL_NAME}/{name}", path) for name, path in skill_entries],
    )
    staged: list[Path] = []
    old: dict[Path, Path] = {}
    committed: list[Path] = []
    backups: list[Path] = []
    retained: set[Path] = set()
    try:
        for entries in groups:
            with tempfile.NamedTemporaryFile(prefix=".release-", suffix=".zip", dir=out_dir, delete=False) as handle:
                temporary = Path(handle.name)
            staged.append(temporary)
            write_zip(temporary, entries, day)
        for temporary, output in zip(staged, outputs):
            if output.exists():
                with tempfile.NamedTemporaryFile(prefix=".release-old-", dir=out_dir, delete=False) as handle:
                    saved = Path(handle.name)
                backups.append(saved)
                os.replace(output, saved)
                old[output] = saved
            os.replace(temporary, output)
            committed.append(output)
    except OSError as exc:
        errors = []
        for output in reversed(outputs):
            try:
                if output in old:
                    os.replace(old[output], output)
                elif output in committed:
                    output.unlink(missing_ok=True)
            except OSError as failure:
                if output in old:
                    retained.add(old[output])
                errors.append(f"{output}: {failure}")
        if errors:
            raise OSError(f"{exc}. Recovery errors: {'; '.join(errors)}. Retained backups: {sorted(map(str, retained))}") from exc
        raise
    finally:
        for temporary in staged + backups:
            if temporary not in retained:
                temporary.unlink(missing_ok=True)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).absolute().parents[1])
    parser.add_argument("--date", type=release_date, default=date.today().isoformat())
    parser.add_argument("--out-dir", type=Path, help="Default: <repo>/dist")
    args = parser.parse_args()
    try:
        for output in build(args.repo, args.date, args.out_dir):
            print(output)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
