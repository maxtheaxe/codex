#!/usr/bin/env python3
"""Offline safety and compatibility tests for cross_provider_patchset.py."""

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).with_name("cross_provider_patchset.py").resolve()


class PatchsetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git(self.source, "init", "--initial-branch=main")
        self.configure(self.source)
        (self.source / "feature.txt").write_text("base\n")
        self.commit(self.source, "base")
        self.base = self.git(self.source, "rev-parse", "HEAD").strip()
        (self.source / "feature.txt").write_text("first feature\n")
        self.commit(self.source, "first feature")
        (self.source / "binary.dat").write_bytes(b"\x00\x01\x02\xff")
        self.commit(self.source, "binary feature")
        self.bundle = self.root / "bundle"
        self.target = self.root / "target"
        self.git(self.root, "clone", "--no-hardlinks", str(self.source), str(self.target))
        self.configure(self.target)
        self.git(self.target, "checkout", "-b", "test-application", self.base)

    def git(self, repo, *args):
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if result.returncode:
            self.fail(result.stderr)
        return result.stdout

    def configure(self, repo):
        self.git(repo, "config", "user.name", "Patchset Test")
        self.git(repo, "config", "user.email", "patchset@example.invalid")
        self.git(repo, "config", "commit.gpgsign", "false")

    def commit(self, repo, message):
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", message)

    def run_helper(self, command, *extra, repo=None, success=True):
        args = [sys.executable, str(HELPER), command, "--repo", str(repo or self.target)]
        if command == "export":
            args += ["--base", self.base, "--output", str(self.bundle)]
        else:
            args += ["--bundle", str(self.bundle)]
        result = subprocess.run(args + list(extra), capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def export(self):
        self.run_helper("export", repo=self.source)

    def manifest(self):
        return json.loads((self.bundle / "manifest.json").read_text())

    def test_round_trip_including_binary_and_history_preserving_rollback(self):
        self.export()
        self.run_helper("apply")
        self.run_helper("verify")
        self.assertEqual((self.target / "binary.dat").read_bytes(), b"\x00\x01\x02\xff")
        applied = self.git(self.target, "rev-parse", "HEAD").strip()
        self.run_helper("rollback")
        self.assertEqual((self.target / "feature.txt").read_text(), "base\n")
        self.assertFalse((self.target / "binary.dat").exists())
        self.git(self.target, "merge-base", "--is-ancestor", applied, "HEAD")
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")

    def test_export_is_reproducible_and_refuses_overwrite(self):
        self.export()
        original = {path.name: path.read_bytes() for path in self.bundle.iterdir()}
        self.run_helper("export", repo=self.source, success=False)
        saved_bundle = self.bundle
        self.bundle = self.root / "bundle-two"
        self.export()
        self.assertEqual(original, {path.name: path.read_bytes() for path in self.bundle.iterdir()})
        self.assertTrue(saved_bundle.exists())

    def test_export_refuses_inside_source_or_another_worktree(self):
        self.bundle = self.source / "generated-patches"
        self.run_helper("export", repo=self.source, success=False)
        self.assertFalse(self.bundle.exists())
        linked = self.root / "linked-worktree"
        self.git(self.source, "worktree", "add", "--detach", str(linked), self.base)
        self.bundle = self.source / "generated-from-linked"
        result = self.run_helper("export", "--head", "main", repo=linked, success=False)
        self.assertIn("outside all repository worktrees", result.stderr)
        self.assertFalse(self.bundle.exists())

    def test_wrong_base_is_refused_without_changes(self):
        self.export()
        (self.target / "unrelated.txt").write_text("unrelated\n")
        self.commit(self.target, "unrelated")
        before = self.git(self.target, "rev-parse", "HEAD")
        result = self.run_helper("apply", success=False)
        self.assertIn("incompatible target HEAD", result.stderr)
        self.assertEqual(before, self.git(self.target, "rev-parse", "HEAD"))

    def test_dirty_and_untracked_work_are_refused(self):
        self.export()
        (self.target / "untracked.txt").write_text("valuable\n")
        self.run_helper("apply", success=False)
        self.assertEqual((self.target / "untracked.txt").read_text(), "valuable\n")
        (self.target / "untracked.txt").unlink()
        (self.target / "feature.txt").write_text("valuable edits\n")
        self.run_helper("apply", success=False)
        self.assertEqual((self.target / "feature.txt").read_text(), "valuable edits\n")

    def test_tampered_patch_is_refused_before_mutation(self):
        self.export()
        patch = self.bundle / self.manifest()["patches"][0]["file"]
        patch.write_bytes(patch.read_bytes() + b"tampered\n")
        result = self.run_helper("apply", success=False)
        self.assertIn("integrity check failed", result.stderr)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD").strip(), self.base)

    def test_manifest_path_traversal_is_refused(self):
        self.export()
        manifest = self.manifest()
        manifest["patches"][0]["file"] = "../outside.patch"
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        result = self.run_helper("apply", success=False)
        self.assertIn("filename/order", result.stderr)

    def test_later_unrelated_commit_prevents_rollback(self):
        self.export()
        self.run_helper("apply")
        (self.target / "valuable.txt").write_text("keep this\n")
        self.commit(self.target, "valuable followup")
        before = self.git(self.target, "rev-parse", "HEAD")
        self.run_helper("rollback", success=False)
        self.assertEqual(before, self.git(self.target, "rev-parse", "HEAD"))
        self.assertEqual((self.target / "valuable.txt").read_text(), "keep this\n")

    def test_failed_am_aborts_all_already_applied_patches(self):
        self.export()
        manifest = self.manifest()
        entry = manifest["patches"][1]
        patch = self.bundle / entry["file"]
        # A self-consistent but invalid bundle must still recover from Git failure.
        patch.write_bytes(b"This is not a patch\n")
        entry["sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        result = self.run_helper("apply", success=False)
        self.assertIn("original HEAD restored", result.stderr)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD").strip(), self.base)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self.assertEqual((self.target / "feature.txt").read_text(), "base\n")
        self.run_helper("apply", success=False)  # No stranded am state.

    def test_ignored_conflicting_file_is_preserved_on_abort(self):
        self.export()
        exclude_path = self.target / ".git" / "info" / "exclude"
        exclude_path.write_text("binary.dat\n")
        (self.target / "binary.dat").write_bytes(b"precious ignored data")
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self.run_helper("apply", success=False)
        self.assertEqual((self.target / "binary.dat").read_bytes(), b"precious ignored data")
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD").strip(), self.base)

    def test_repository_hooks_are_not_executed(self):
        self.export()
        hook = self.target / ".git" / "hooks" / "applypatch-msg"
        hook.write_text("#!/bin/sh\nexit 42\n")
        hook.chmod(0o755)
        self.run_helper("apply")
        self.run_helper("verify")

    def test_merge_and_empty_commit_ranges_are_refused(self):
        self.git(self.source, "checkout", "-b", "side", self.base)
        (self.source / "side.txt").write_text("side\n")
        self.commit(self.source, "side")
        self.git(self.source, "checkout", "main")
        self.git(self.source, "merge", "--no-ff", "side", "-m", "merge side")
        result = self.run_helper("export", repo=self.source, success=False)
        self.assertIn("linear", result.stderr)
        self.git(self.source, "checkout", "-b", "empty-only", self.base)
        self.git(self.source, "commit", "--allow-empty", "-m", "empty")
        result = self.run_helper("export", repo=self.source, success=False)
        self.assertIn("empty commit", result.stderr)
        self.assertFalse(self.bundle.exists())

    def test_unknown_schema_and_extra_files_are_refused(self):
        self.export()
        manifest = self.manifest()
        manifest["schema"] = 999
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        self.assertIn("schema", self.run_helper("apply", success=False).stderr)
        manifest["schema"] = 1
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        (self.bundle / "unexpected.txt").write_text("unexpected\n")
        self.assertIn("unexpected files", self.run_helper("apply", success=False).stderr)


if __name__ == "__main__":
    unittest.main()
