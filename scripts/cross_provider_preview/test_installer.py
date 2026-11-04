"""Exercise installer filesystem behavior using a tiny, pinned release fixture.

Only platform commands and the download transport are stubbed. The installer
copy pins the fixture's digest, so checksum checks use the real shasum and tar.
No production checksum bypass or network access is needed.
"""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest


SOURCE = Path(__file__).with_name("install.sh")
TAG = "cross-provider-v0.0.0-preview.3"
ASSET = "codex-cross-provider-aarch64-apple-darwin.tar.gz"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-preview-installer-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user_home = self.root / "user with spaces"
        self.user_home.mkdir()
        self.target = self.user_home / "Applications/codex-cross-provider-preview"
        self.profile = self.user_home / ".zshrc"
        self.profile.write_text("# existing shell settings\n")
        self.normal_state = self.user_home / ".codex/config.toml"
        self.normal_state.parent.mkdir()
        self.normal_state.write_text("existing Codex config\n")
        self.desktop = self.user_home / "Applications/Codex.app/keep"
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text("existing Desktop\n")
        self.stub_bin = self.root / "stub-bin"
        self.stub_bin.mkdir()
        self.script = self.root / "install.sh"
        self.archive = self.root / "release archive.tar.gz"
        self.package = self.root / "package"
        self.package.mkdir()
        for name in (
            "codex-preview",
            "bin/codex",
            "bin/codex-code-mode-host",
            "bin/codex-exec-server",
            "codex-path/rg",
        ):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        shutil.copyfile(
            SOURCE.with_name("codex-preview"), self.package / "codex-preview"
        )
        (self.package / "codex-package.json").write_text(
            '{"version":"0.0.0-preview.3"}\n'
        )
        (self.package / "BUILD-INFO.txt").write_text(f"Tag: {TAG}\n")
        self.repack()
        self.stub(
            "uname",
            'case "$1" in -s) echo "${PREVIEW_TEST_OS:-Darwin}";; -m) echo "${PREVIEW_TEST_ARCH:-arm64}";; esac\n',
        )
        self.stub("sw_vers", 'echo "${PREVIEW_TEST_VERSION:-15.7}"\n')
        self.stub(
            "sysctl",
            'case "$2" in hw.optional.arm64) echo "${PREVIEW_TEST_ARM64:-0}";; '
            'sysctl.proc_translated) echo "${PREVIEW_TEST_TRANSLATED:-0}";; esac\n',
        )
        self.stub("id", 'echo "${PREVIEW_TEST_UID:-1000}"\n')
        self.stub("codex", "exit 0\n")
        self.stub(
            "curl",
            "import os, pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            f"assert args[-1] == 'https://github.com/maxtheaxe/codex/releases/download/{TAG}/{ASSET}'\n"
            "pathlib.Path(os.environ['PREVIEW_TEST_CURL_LOG']).write_text(args[-1])\n"
            "shutil.copyfile(os.environ['PREVIEW_TEST_ARCHIVE'], args[args.index('--output') + 1])\n",
            python=True,
        )
        self.env = {
            **os.environ,
            "HOME": str(self.user_home),
            "SHELL": "/bin/zsh",
            "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
            "PREVIEW_TEST_ARCHIVE": str(self.archive),
            "PREVIEW_TEST_CURL_LOG": str(self.root / "curl.log"),
        }
        self.env.pop("ZDOTDIR", None)

    def stub(self, name, content, *, python=False):
        path = self.stub_bin / name
        path.write_text(
            ("#!/usr/bin/env python3\n" if python else "#!/bin/sh\n") + content
        )
        path.chmod(0o755)

    def repack(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(self.package, arcname=".")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        source = re.sub(
            r"(?m)^preview_sha256='[a-f0-9]{64}'$",
            f"preview_sha256='{digest}'",
            SOURCE.read_text(),
            count=1,
        )
        self.script.write_text(source)

    def install(self, *args, env=None, success=True):
        result = subprocess.run(
            ["bash", str(self.script), *args],
            cwd=self.root,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.normal_state.read_text(), "existing Codex config\n")
        self.assertEqual(self.desktop.read_text(), "existing Desktop\n")
        self.assertEqual(
            list(self.target.parent.glob(".codex-preview-install.??????")), []
        )
        return result

    def test_download_install_and_repeat_preserve_profile_and_normal_codex(self):
        self.install()
        self.assertTrue((self.root / "curl.log").is_file())
        self.assertTrue((self.target / "bin/codex-exec-server").is_file())
        self.assertEqual(self.profile.read_text().count("export PATH="), 1)
        backups = list(self.user_home.glob(".zshrc.codex-preview-backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "# existing shell settings\n")
        self.install("--archive", str(self.archive))
        self.assertEqual(self.profile.read_text().count("export PATH="), 1)
        self.assertEqual(
            list(self.user_home.glob(".zshrc.codex-preview-backup.*")), backups
        )
        if shutil.which("zsh"):
            result = subprocess.run(
                ["zsh", "-i", "-c", "cd /; command -v codex-preview; command -v codex"],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                result.stdout.splitlines(),
                [str(self.target / "codex-preview"), str(self.stub_bin / "codex")],
            )

    def test_bad_checksum_changes_neither_installation_nor_profile(self):
        self.archive.write_bytes(b"invalid download")
        result = self.install("--archive", str(self.archive), success=False)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_existing_different_destination_is_preserved(self):
        self.target.mkdir()
        sentinel = self.target / "personal-file"
        sentinel.write_text("keep me\n")
        result = self.install("--archive", str(self.archive), success=False)
        self.assertIn("destination differs", result.stderr)
        self.assertEqual(sentinel.read_text(), "keep me\n")
        self.assertEqual(list(self.target.iterdir()), [sentinel])
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_destination_symlink_is_rejected_before_download(self):
        self.target.symlink_to(self.normal_state.parent, target_is_directory=True)
        result = self.install(success=False)
        self.assertIn("destination is a symlink", result.stderr)
        self.assertFalse((self.root / "curl.log").exists())

    def test_existing_installer_lock_is_preserved(self):
        lock = self.target.parent / ".codex-preview-install.lock"
        lock.mkdir()
        result = self.install(success=False)
        self.assertIn("another installer may be running", result.stderr)
        self.assertTrue(lock.is_dir())
        self.assertFalse((self.root / "curl.log").exists())
        self.assertFalse(self.target.exists())

    def test_incomplete_package_is_not_installed(self):
        (self.package / "bin/codex-exec-server").unlink()
        self.repack()
        result = self.install("--archive", str(self.archive), success=False)
        self.assertIn("missing an executable", result.stderr)
        self.assertFalse(self.target.exists())

    def test_platform_and_root_checks_happen_before_download(self):
        for env in (
            {"PREVIEW_TEST_OS": "Linux"},
            {"PREVIEW_TEST_ARCH": "x86_64"},
            {"PREVIEW_TEST_VERSION": "14.7"},
            {"PREVIEW_TEST_UID": "0"},
        ):
            with self.subTest(env=env):
                self.install(env=env, success=False)
                self.assertFalse(self.target.exists())
                self.assertFalse((self.root / "curl.log").exists())

    def test_rosetta_shell_on_apple_silicon_is_supported(self):
        for arm64_evidence in ("PREVIEW_TEST_ARM64", "PREVIEW_TEST_TRANSLATED"):
            with self.subTest(arm64_evidence=arm64_evidence):
                self.install(
                    "--archive",
                    str(self.archive),
                    "--no-path",
                    env={"PREVIEW_TEST_ARCH": "x86_64", arm64_evidence: "1"},
                )
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_bash_profile_and_no_path(self):
        self.install(
            "--archive", str(self.archive), "--no-path", env={"SHELL": "/bin/bash"}
        )
        bash_profile = self.user_home / ".bash_profile"
        self.assertFalse(bash_profile.exists())
        self.install("--archive", str(self.archive), env={"SHELL": "/bin/bash"})
        self.assertIn("export PATH=", bash_profile.read_text())
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_custom_zdotdir_is_used(self):
        custom = self.user_home / "custom zsh config"
        custom.mkdir()
        self.install("--archive", str(self.archive), env={"ZDOTDIR": str(custom)})
        self.assertIn("export PATH=", (custom / ".zshrc").read_text())
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_invalid_arguments_fail_without_changes(self):
        for arguments in (("--archive",), ("--unknown",)):
            with self.subTest(arguments=arguments):
                self.install(*arguments, success=False)
                self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
