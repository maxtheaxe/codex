"""Exercise install -> import -> login -> model requests with synthetic credentials.

On macOS, --archive tests the actual published package without platform stubs.
--binary is a Linux development check of an already built CLI, not a release test.
Inference is served by a loopback fixture; this does not establish live entitlement.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import tomllib


HERE = Path(__file__).resolve().parent
REJECTED_MODEL = "gpt-6.1-sol"
ERROR = f"The '{REJECTED_MODEL}' model is not supported when using Codex with a ChatGPT account."


class Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_bytes(404, b"{}", "application/json")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = data.get("model")
        self.server.requests.append(
            {
                "path": self.path,
                "model": model,
                "user_agent": self.headers.get("User-Agent"),
                "originator": self.headers.get("originator"),
            }
        )
        if model == REJECTED_MODEL:
            self.send_bytes(
                400, json.dumps({"detail": ERROR}).encode(), "application/json"
            )
            return
        expected = (
            "Bearer fixture-router-key"
            if self.path.startswith("/router/")
            else "Bearer fixture-openai-key"
        )
        if self.headers.get("Authorization") != expected:
            self.send_bytes(
                401,
                b'{"error":{"message":"fixture auth mismatch"}}',
                "application/json",
            )
            return
        events = [
            {"type": "response.created", "response": {"id": "fixture-response"}},
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": "fixture-message",
                    "content": [{"type": "output_text", "text": "PREVIEW_SMOKE_OK"}],
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "fixture-response",
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            },
        ]
        body = "".join(
            "data: " + json.dumps(event) + "\n\n" for event in events
        ).encode()
        self.send_bytes(200, body, "text/event-stream")


def run(args, env, cwd, *, input=None, success=True, timeout=60):
    process = subprocess.Popen(
        args,
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if success and result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {args}\n{stdout[-6000:]}\n{stderr[-6000:]}"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--binary", type=Path)
    options = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    server.requests = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="codex-preview-release-smoke-") as temp:
            root = Path(temp)
            user_home = root / "fresh home"
            user_home.mkdir()
            regular = user_home / ".codex"
            regular.mkdir()
            (regular / "auth.json").write_text(
                '{"sentinel":"regular-login-not-copied"}\n'
            )
            config = f"""# Deliberately no model: exercise this build's fresh default.
[model_providers.openrouter]
name = "OpenRouter fixture"
base_url = "http://127.0.0.1:{server.server_port}/router/v1"
wire_api = "responses"
auth = {{ command = "must-not-execute", args = ["fixture-secret-not-imported"] }}
"""
            (regular / "config.toml").write_text(config)
            env = {
                "HOME": str(user_home),
                "PATH": os.environ["PATH"],
                "SHELL": "/bin/zsh",
                "TERM": "xterm-256color",
                "NO_PROXY": "127.0.0.1,localhost",
                "PREVIEW_SMOKE_ROUTER_KEY": "fixture-router-key",
            }
            if os.environ.get("TMPDIR"):
                env["TMPDIR"] = os.environ["TMPDIR"]
            package = user_home / "Applications/codex-cross-provider-preview"
            if options.binary:
                # This path only develops the request fixture on a Linux build.
                # CI uses --archive with the native, unmodified macOS installer.
                stubs = root / "platform-stubs"
                stubs.mkdir()
                for name, output in {
                    "uname": 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
                    "sw_vers": "echo 15.0",
                    "id": "echo 1000",
                }.items():
                    path = stubs / name
                    path.write_text("#!/bin/sh\n" + output + "\n")
                    path.chmod(0o755)
                env["PATH"] = str(stubs) + ":" + env["PATH"]
                (package / "bin").mkdir(parents=True)
                (package / "bin/codex").symlink_to(options.binary.resolve())
                shutil.copy2(HERE / "codex-preview", package / "codex-preview")
                install_args = ["--import-config-only", "--skip-unsupported"]
            else:
                install_args = [
                    "--archive",
                    str(options.archive.resolve()),
                    "--import-config",
                    "--skip-unsupported",
                ]
            run(
                ["/bin/bash", str(HERE / "install.sh"), *install_args],
                env,
                root,
                timeout=120,
            )
            preview_home = user_home / ".codex-cross-provider-preview"
            imported = tomllib.loads((preview_home / "config.toml").read_text())
            assert "auth" not in imported["model_providers"]["openrouter"]
            assert "model" not in imported
            assert not (preview_home / "auth.json").exists()
            launcher = str(package / "codex-preview")
            if options.archive:
                path = run(["/bin/zsh", "-ic", "command -v codex-preview"], env, root)
                assert path.stdout.strip() == launcher, path.stdout
            version = run([launcher, "--version"], env, root).stdout.strip()
            run(
                [launcher, "login", "--with-api-key"],
                env,
                root,
                input="fixture-openai-key\n",
            )
            run([launcher, "login", "status"], env, root)
            base = [
                launcher,
                "-c",
                f'openai_base_url="http://127.0.0.1:{server.server_port}/openai/v1"',
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                "features.code_mode.enabled=false",
            ]
            rejected = run(
                [*base, "Return PREVIEW_SMOKE_OK; do not use tools."],
                env,
                root,
                success=False,
            )
            assert rejected.returncode != 0, rejected.stdout
            assert ERROR in rejected.stdout + rejected.stderr, (
                rejected.stdout + rejected.stderr
            )
            accepted = run(
                [*base, "-m", "gpt-5.5", "Return PREVIEW_SMOKE_OK; do not use tools."],
                env,
                root,
            )
            assert "PREVIEW_SMOKE_OK" in accepted.stdout, accepted.stdout
            routed = run(
                [
                    *base,
                    "-c",
                    'model_provider="openrouter"',
                    "-c",
                    'model_providers.openrouter.env_key="PREVIEW_SMOKE_ROUTER_KEY"',
                    "-m",
                    "fixture/router-model",
                    "Return PREVIEW_SMOKE_OK; do not use tools.",
                ],
                env,
                root,
            )
            assert "PREVIEW_SMOKE_OK" in routed.stdout, routed.stdout
            assert (regular / "config.toml").read_text() == config
            assert (
                regular / "auth.json"
            ).read_text() == '{"sentinel":"regular-login-not-copied"}\n'
            assert server.requests[0]["model"] == REJECTED_MODEL, server.requests
            assert any(item["path"].startswith("/router/") for item in server.requests)
            print(
                json.dumps(
                    {
                        "mode": "native macOS released archive"
                        if options.archive
                        else "Linux development binary; installer platform simulated",
                        "version": version,
                        "fresh_default": server.requests[0]["model"],
                        "checks": [
                            "config import",
                            "login isolation",
                            "model rejection reproduced",
                            "explicit model request",
                            "custom provider request",
                            "regular state unchanged",
                        ],
                        "requests": server.requests,
                        "live_account_entitlement_tested": False,
                    },
                    indent=2,
                )
            )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
