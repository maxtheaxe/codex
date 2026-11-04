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
import tomllib
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
        self.normal_auth = self.normal_state.with_name("auth.json")
        self.normal_auth.write_text('{"fake_login":"never-copy"}\n')
        self.preview_home = self.user_home / ".codex-cross-provider-preview"
        self.preview_config = self.preview_home / "config.toml"
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
        self.env.pop("CODEX_HOME", None)

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
        original_config = self.normal_state.read_bytes()
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
        self.assertEqual(self.normal_state.read_bytes(), original_config)
        self.assertEqual(self.normal_auth.read_text(), '{"fake_login":"never-copy"}\n')
        self.assertEqual(self.desktop.read_text(), "existing Desktop\n")
        self.assertEqual(
            list(self.target.parent.glob(".codex-preview-install.??????")), []
        )
        return result

    def test_import_only_copies_settings_and_env_providers_without_credentials(self):
        self.normal_state.write_text(r"""
model = "example-模型😀"
model_provider = "openrouter"
model_reasoning_effort = "high"
"sqlite_home" = "/usual/state"
log_dir = "/usual/logs"
cli_auth_credentials_store = "keyring"
approval_policy = "never"
sandbox_mode = "danger-full-access"
profile = "work"
notify = ["touch", "must-not-run"]
[shell_environment_policy.set]
OPENROUTER_API_KEY = "fake-secret-do-not-copy"
[profiles.work]
model = "profile-not-imported"
log_dir = "/usual/profile-logs"
[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
env_http_headers = { "X-Organization" = "PREVIEW_ORG" }
[tui]
theme = 'theme"with\punctuation'
status_line = ["model-name", "context-remaining"]
[features.multi_agent_v2]
enabled = true
expose_spawn_agent_model_overrides = true
""")
        self.normal_state.chmod(0o400)
        result = self.install(
            "--import-config-only",
            env={"OPENROUTER_API_KEY": "fixture-only-never-print"},
        )
        original = tomllib.loads(self.normal_state.read_text())
        imported = tomllib.loads(self.preview_config.read_text())
        self.assertEqual(imported["model"], "example-模型😀")
        self.assertEqual(imported["model_providers"], original["model_providers"])
        self.assertEqual(imported["tui"], original["tui"])
        self.assertEqual(imported["features"], original["features"])
        self.assertEqual(
            set(imported),
            {
                "model",
                "model_provider",
                "model_reasoning_effort",
                "model_providers",
                "tui",
                "features",
            },
        )
        self.assertNotIn("fake-secret", self.preview_config.read_text())
        self.assertNotIn("fake-secret", result.stdout + result.stderr)
        self.assertNotIn(
            "fixture-only-never-print",
            self.preview_config.read_text() + result.stdout + result.stderr,
        )
        self.assertEqual(self.preview_config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.preview_home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(self.preview_home.iterdir()), [self.preview_config])
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "curl.log").exists())
        self.assertEqual(self.profile.read_text(), "# existing shell settings\n")

    def test_import_can_accompany_install_but_never_replaces_preview_config(self):
        self.normal_state.write_text('model = "existing-model"\n')
        self.install("--import-config", "--archive", str(self.archive))
        self.assertEqual(
            tomllib.loads(self.preview_config.read_text())["model"], "existing-model"
        )
        self.assertTrue(self.target.exists())
        self.preview_config.write_text('model = "my-preview-setting"\n')
        result = self.install("--import-config", success=False)
        self.assertIn("already exists; preserved", result.stderr)
        self.assertEqual(
            self.preview_config.read_text(), 'model = "my-preview-setting"\n'
        )
        self.assertFalse((self.root / "curl.log").exists())

    def test_import_does_not_write_config_if_archive_verification_fails(self):
        self.normal_state.write_text('model = "existing-model"\n')
        self.archive.write_bytes(b"invalid download")
        self.install("--import-config", "--archive", str(self.archive), success=False)
        self.assertFalse(self.preview_home.exists())

    def test_import_preserves_preview_login_and_other_state(self):
        self.normal_state.write_text('model = "existing-model"\n')
        self.preview_home.mkdir()
        auth = self.preview_home / "auth.json"
        auth.write_text("existing preview login")
        sessions = self.preview_home / "sessions"
        sessions.mkdir()
        self.install("--import-config-only")
        self.assertEqual(auth.read_text(), "existing preview login")
        self.assertTrue(sessions.is_dir())

    def test_import_honors_custom_codex_home_and_reads_source_symlinks(self):
        custom = self.user_home / "custom Codex home"
        custom.mkdir()
        source = custom / "settings.toml"
        source.write_text('model = "custom-home-model"\n')
        source.chmod(0o400)
        (custom / "config.toml").symlink_to(source)
        self.install("--import-config-only", env={"CODEX_HOME": str(custom)})
        self.assertEqual(
            tomllib.loads(self.preview_config.read_text())["model"], "custom-home-model"
        )
        self.assertEqual(source.read_text(), 'model = "custom-home-model"\n')

    def test_import_refuses_symlinked_preview_home(self):
        self.normal_state.write_text('model = "existing-model"\n')
        self.preview_home.symlink_to(self.normal_state.parent, target_is_directory=True)
        result = self.install("--import-config-only", success=False)
        self.assertIn("not a symlink; preserved", result.stderr)

    def test_import_missing_source_fails_before_download(self):
        missing_home = self.user_home / "missing Codex home"
        result = self.install(
            "--import-config", env={"CODEX_HOME": str(missing_home)}, success=False
        )
        self.assertIn("could not read the source", result.stderr)
        self.assertFalse(self.preview_home.exists())
        self.assertFalse((self.root / "curl.log").exists())

    def test_import_missing_python_fails_before_download(self):
        (self.stub_bin / "bash").symlink_to(shutil.which("bash"))
        result = self.install(
            "--import-config", env={"PATH": str(self.stub_bin)}, success=False
        )
        self.assertIn("requires python3", result.stderr)
        self.assertFalse(self.preview_home.exists())
        self.assertFalse((self.root / "curl.log").exists())

    def test_regular_install_does_not_require_python(self):
        self.stub("python3", "echo 'unexpected Python invocation' >&2; exit 99\n")
        self.install("--archive", str(self.archive))
        self.assertTrue(self.target.exists())
        self.assertFalse(self.preview_home.exists())

    def test_import_refuses_existing_and_dangling_config_links(self):
        self.preview_home.mkdir()
        for target in (self.normal_state, self.user_home / "absent-config"):
            with self.subTest(target=target.name):
                self.preview_config.symlink_to(target)
                result = self.install("--import-config-only", success=False)
                self.assertIn("already exists; preserved", result.stderr)
                self.assertTrue(self.preview_config.is_symlink())
                self.preview_config.unlink()
        self.assertFalse((self.user_home / "absent-config").exists())

    def test_import_rejects_invalid_toml_without_echoing_source(self):
        self.normal_state.write_text('"fake-secret-do-not-log" = [ invalid value\n')
        result = self.install("--import-config", success=False)
        self.assertIn("not valid supported TOML", result.stderr)
        self.assertNotIn("fake-secret", result.stdout + result.stderr)
        self.assertFalse(self.preview_home.exists())
        self.assertFalse((self.root / "curl.log").exists())

    def test_import_rejects_unsupported_auth_or_provider_routes_before_download(self):
        for entry in (
            'experimental_bearer_token = "fake-secret-do-not-copy"',
            'auth = { command = "echo", args = ["fake-secret-do-not-copy"] }',
            'http_headers = { Authorization = "fake-secret-do-not-copy" }',
            'base_url = "https://fake-secret-do-not-copy@openrouter.ai/api/v1"',
            'base_url = "https://openrouter.ai/api/v1?key=fake-secret-do-not-copy"',
            'env_key = "fake-secret-do-not-copy"',
            'wire_api = "chat"',
        ):
            with self.subTest(entry=entry.split(" =")[0]):
                self.normal_state.write_text(
                    "[model_providers.router]\n" + entry + "\n"
                )
                result = self.install("--import-config", success=False)
                self.assertNotIn("fake-secret", result.stdout + result.stderr)
                self.assertFalse(self.preview_home.exists())
                self.assertFalse((self.root / "curl.log").exists())

    def test_import_requires_supported_settings_and_compatible_value_types(self):
        for source in (
            'log_dir = "/usual/logs"',
            'model = ["invalid"]',
            "model_context_window = true",
        ):
            with self.subTest(source=source):
                self.normal_state.write_text(source)
                self.install("--import-config-only", success=False)
                self.assertFalse(self.preview_home.exists())

    def test_download_install_and_repeat_preserve_profile_and_normal_codex(self):
        self.install()
        self.assertTrue((self.root / "curl.log").is_file())
        self.assertTrue((self.target / "bin/codex-exec-server").is_file())
        self.assertFalse(self.preview_home.exists())
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
        for arguments in (
            ("--archive",),
            ("--unknown",),
            ("--import-config-only", "--archive", str(self.archive)),
        ):
            with self.subTest(arguments=arguments):
                self.install(*arguments, success=False)
                self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
