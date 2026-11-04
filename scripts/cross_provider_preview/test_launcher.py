"""Verify preview isolation without starting a model or changing real user state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class LauncherTests(unittest.TestCase):
    def test_separate_home_and_literal_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package with spaces"
            (package / "bin").mkdir(parents=True)
            launcher = package / "codex-preview"
            shutil.copyfile(Path(__file__).with_name("codex-preview"), launcher)
            launcher.chmod(0o755)
            executable = package / "bin/codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "print(json.dumps([os.environ['CODEX_HOME'], sys.argv[1:]]))\n"
            )
            executable.chmod(0o755)
            user_home = root / "user home"
            user_home.mkdir()
            usual_home = user_home / ".codex"
            usual_home.mkdir()
            sentinel = usual_home / "config.toml"
            sentinel.write_text("do not touch\n")
            result = subprocess.run(
                [str(launcher), "exec", "literal $(text) with spaces", "--json"],
                env={**os.environ, "HOME": str(user_home), "CODEX_HOME": str(usual_home)},
                check=True,
                capture_output=True,
                text=True,
            )
            preview_home = user_home / ".codex-cross-provider-preview"
            home, arguments = json.loads(result.stdout)
            self.assertEqual(home, str(preview_home))
            self.assertEqual(preview_home.stat().st_mode & 0o777, 0o700)
            self.assertEqual(sentinel.read_text(), "do not touch\n")
            self.assertEqual(
                arguments,
                [
                    "-c",
                    'cli_auth_credentials_store="file"',
                    "exec",
                    "literal $(text) with spaces",
                    "--json",
                ],
            )


if __name__ == "__main__":
    unittest.main()
