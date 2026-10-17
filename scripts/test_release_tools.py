#!/usr/bin/env python3
"""Release-tool tests use fake files in TemporaryDirectory; no user installation."""
from __future__ import annotations

import contextlib
import argparse
import io
import json
from pathlib import Path
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import build_release as release
import install_codex as installer


class ReleaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repository"
        self.source = self.repo / "skills" / release.SKILL_NAME
        for name in release.PUBLIC_ROOT_FILES:
            self.write(self.repo / name, f"public root: {name}")
        self.write(self.repo / "plugin.json", json.dumps({"name": release.SKILL_NAME, "version": release.VERSION}))
        for name in release.PUBLIC_SKILL_FILES:
            self.write(self.source / name, f"public skill: {name}")

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def install(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return installer.install(self.source, self.base / "isolated" / "skills", **options)

    def test_two_zip_layouts_and_privacy_allowlist(self):
        private = ("author.local.md", "references/author-style.md", "draft.docx", "reviews/review.json", ".git/config", "__pycache__/secret.pyc")
        for name in private:
            self.write(self.source / name, "PRIVATE")
            self.write(self.repo / name, "PRIVATE")
        plugin, skill = release.build(self.repo, "2026-10-18")
        self.assertEqual(plugin.name, f"2026-10-18_{release.SKILL_NAME}-v{release.VERSION}-plugin.zip")
        self.assertEqual(skill.name, f"2026-10-18_{release.SKILL_NAME}-v{release.VERSION}-skill.zip")
        expected_skill = {f"{release.SKILL_NAME}/{name}" for name in release.PUBLIC_SKILL_FILES}
        with zipfile.ZipFile(skill) as archive:
            self.assertEqual(set(archive.namelist()), expected_skill)
            self.assertTrue(all(b"PRIVATE" not in archive.read(name) for name in archive.namelist()))
        with zipfile.ZipFile(plugin) as archive:
            expected = set(release.PUBLIC_ROOT_FILES) | {f"skills/{release.SKILL_NAME}/{name}" for name in release.PUBLIC_SKILL_FILES}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIn("plugin.json", archive.namelist())
        before = (plugin.read_bytes(), skill.read_bytes())
        release.build(self.repo, "2026-10-18")
        self.assertEqual(before, (plugin.read_bytes(), skill.read_bytes()))

    def test_second_zip_commit_failure_restores_both_originals(self):
        plugin, skill = release.build(self.repo, "2026-10-18")
        before = (plugin.read_bytes(), skill.read_bytes())
        self.write(self.source / "SKILL.md", "updated public skill")
        real_replace, calls = release.os.replace, 0
        def fail_fourth(source, destination):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("second ZIP commit failed")
            return real_replace(source, destination)
        with patch.object(release.os, "replace", side_effect=fail_fourth):
            with self.assertRaisesRegex(OSError, "second ZIP commit failed"):
                release.build(self.repo, "2026-10-18")
        self.assertEqual(before, (plugin.read_bytes(), skill.read_bytes()))
        self.assertFalse(list(plugin.parent.glob(".release-*")))

    def test_missing_file_and_invalid_version_fail_before_output(self):
        missing = self.source / release.PUBLIC_SKILL_FILES[0]
        missing.unlink()
        with self.assertRaisesRegex(ValueError, "Required public file"):
            release.build(self.repo, "2026-10-18")
        self.assertFalse((self.repo / "dist").exists())
        self.write(missing, "restored")
        self.write(self.repo / "plugin.json", '{"version":"2.0.0"}')
        with self.assertRaisesRegex(ValueError, f"version {release.VERSION}"):
            release.build(self.repo, "2026-10-18")

    def test_date_validation(self):
        for value in ("20261013", "2026-02-30", "1979-01-01", "2108-01-01"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                release.release_date(value)

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("This environment cannot create symlinks.")

    def test_public_source_link_outside_root_rejected(self):
        outside = self.base / "private.md"
        self.write(outside, "PRIVATE")
        candidate = self.source / "SKILL.md"
        candidate.unlink()
        self.symlink(candidate, outside)
        with self.assertRaisesRegex(ValueError, "Links/reparse points"):
            release.build(self.repo, "2026-10-18")
        with self.assertRaisesRegex(ValueError, "Links/reparse points"):
            self.install()

    def test_install_backup_and_private_extension_preservation(self):
        target = self.base / "isolated" / "skills" / release.SKILL_NAME
        self.write(target / "SKILL.md", "old public")
        self.write(target / "references" / "author.local.md", "private author notes")
        self.write(target / "custom.py", "private extension")
        actual, backup = self.install(backup_dir=self.base / "explicit-backups")
        self.assertEqual(actual, target)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_relative_to(self.base / "explicit-backups"))
        self.assertEqual((backup / "SKILL.md").read_text(), "old public")
        self.assertEqual((backup / "custom.py").read_text(), "private extension")
        self.assertEqual((target / "references" / "author.local.md").read_text(), "private author notes")
        self.assertEqual((target / "custom.py").read_text(), "private extension")
        self.assertEqual((target / "SKILL.md").read_text(), "public skill: SKILL.md")
        self.assertFalse((self.base / "isolated" / "AGENTS.md").exists())

    def test_fresh_install_and_codex_home_default(self):
        target, backup = self.install()
        self.assertIsNone(backup)
        self.assertTrue((target / "SKILL.md").is_file())
        with patch.dict("os.environ", {"CODEX_HOME": str(self.base / "configured-home")}):
            self.assertEqual(installer.default_dest(), self.base / "configured-home" / "skills")

    def test_destination_link_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        skills = self.base / "isolated" / "skills"
        skills.parent.mkdir(parents=True)
        self.symlink(skills, outside, directory=True)
        with self.assertRaisesRegex(ValueError, "Links/reparse points"):
            self.install()

    def test_inside_backup_is_rejected_before_changes(self):
        skills = self.base / "isolated" / "skills"
        target = skills / release.SKILL_NAME
        self.write(target / "SKILL.md", "old")
        for backup in (target / "backups", skills):
            with self.subTest(backup=backup), self.assertRaisesRegex(ValueError, "outside the installation target"):
                self.install(backup_dir=backup)
        self.assertEqual((target / "SKILL.md").read_text(), "old")

    def test_simulated_symlink_and_windows_junction_rejected(self):
        real_lstat = Path.lstat
        suspect = self.source / "SKILL.md"
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFREG, 0x400)):
            def altered_lstat(path, *args, **kwargs):
                if path == suspect:
                    return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                return real_lstat(path, *args, **kwargs)
            with self.subTest(mode=mode, attributes=attributes), patch.object(Path, "lstat", altered_lstat):
                with self.assertRaisesRegex(ValueError, "Links/reparse points"):
                    release.build(self.repo, "2026-10-18")
                with self.assertRaisesRegex(ValueError, "Links/reparse points"):
                    self.install()

    def test_conflicting_directory_fails_without_changing_files(self):
        target = self.base / "isolated" / "skills" / release.SKILL_NAME
        (target / "SKILL.md").mkdir(parents=True)
        self.write(target / "custom.py", "private")
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.install()
        self.assertEqual((target / "custom.py").read_text(), "private")

    def test_copy_failure_restores_existing_files_from_backup(self):
        target = self.base / "isolated" / "skills" / release.SKILL_NAME
        self.write(target / "SKILL.md", "old public")
        self.write(target / "private.local.md", "keep")
        real_copy = installer.atomic_copy
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            return real_copy(source, destination)

        with patch.object(installer, "atomic_copy", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "Restore files from backup"):
                self.install()
        self.assertEqual((target / "SKILL.md").read_text(), "old public")
        self.assertEqual((target / "private.local.md").read_text(), "keep")
        backups = list((self.base / "isolated" / "backups" / release.SKILL_NAME).iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SKILL.md").read_text(), "old public")


if __name__ == "__main__":
    unittest.main()
