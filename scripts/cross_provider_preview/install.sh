#!/bin/bash
# Install the published preview alongside Codex Desktop and the normal CLI.
# Bash 3.2 compatible; only the optional config import requires Python 3.11+.
set -euo pipefail

preview_tag='cross-provider-v0.0.0-preview.3'
preview_asset='codex-cross-provider-aarch64-apple-darwin.tar.gz'
preview_sha256='b9b9227dad847e3ced58414ca087f634526b915b0a8af2d1e9c948725ddeaf09'
preview_url="https://github.com/maxtheaxe/codex/releases/download/$preview_tag/$preview_asset"
preview_archive_input=''
preview_update_path=true
preview_import_config=false
preview_import_only=false
preview_stage_dir=''
preview_lock_dir=''
preview_lock_owned=false

fail() {
  printf 'codex-preview installer: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bash install.sh [--archive FILE] [--no-path] [--import-config]
       bash install.sh --import-config-only

Install the verified Apple-silicon preview.3 for macOS 15 or newer.
  --archive FILE  Use an already downloaded archive instead of downloading it.
                  The same pinned SHA-256 check still applies.
  --no-path       Leave shell startup files unchanged.
  --import-config Copy selected settings from the usual Codex config once.
                  Requires python3 with Python 3.11+. Never overwrites config.
  --import-config-only
                  Import settings without downloading/installing the package
                  or changing PATH. Use this if preview is already installed.
  -h, --help      Show this help.

Installs into ~/Applications/codex-cross-provider-preview.
Adds that directory to ~/.zshrc (or $ZDOTDIR/.zshrc) for zsh, or
~/.bash_profile for Bash. Other shells require manual PATH configuration.
Existing matching packages are reused; different installations are preserved
and rejected. The installer does not replace Codex Desktop/the regular CLI,
copy login credentials, sign in, or disable macOS security checks.
Settings are imported only when requested, from ${CODEX_HOME:-$HOME/.codex}/config.toml
into ~/.codex-cross-provider-preview/config.toml. Storage paths, profiles,
permissions, hooks and integrations are excluded. Providers using inline
credentials or auth helpers require manual setup; env_key providers are supported.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      [[ $# -ge 2 && -n "$2" ]] || fail '--archive requires a file path'
      preview_archive_input="$2"
      shift 2
      ;;
    --no-path) preview_update_path=false; shift ;;
    --import-config) preview_import_config=true; shift ;;
    --import-config-only) preview_import_config=true; preview_import_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1 (use --help)" ;;
  esac
done
if "$preview_import_only" && [[ -n "$preview_archive_input" ]]; then
  fail '--import-config-only cannot be combined with --archive'
fi

[[ "$(uname -s)" == Darwin ]] || fail 'this package requires macOS'
if [[ "$(uname -m)" != arm64 &&
      "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" != 1 &&
      "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" != 1 ]]; then
  fail 'this package requires an Apple-silicon Mac'
fi
preview_os_version=$(sw_vers -productVersion)
preview_os_major=${preview_os_version%%.*}
case "$preview_os_major" in
  ''|*[!0-9]*) fail "cannot identify macOS version: $preview_os_version" ;;
esac
[[ "$preview_os_major" -ge 15 ]] || fail 'this package requires macOS 15 or newer'
[[ "$(id -u)" != 0 ]] || fail 'run as your normal user, without sudo'
[[ -n "${HOME:-}" && "$HOME" == /* && "$HOME" != / ]] || fail 'HOME must be your absolute home directory'

import_preview_config() {
  command -v python3 >/dev/null || fail 'config import requires python3 (Python 3.11 or newer) on PATH'
  python3 -I - "$1" "${CODEX_HOME:-$HOME/.codex}/config.toml" "$HOME/.codex-cross-provider-preview" <<'PYTHON_IMPORT'
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:
    sys.exit(
        "codex-preview import: Python 3.11 or newer is required; no settings were imported"
    )


class ImportFailure(Exception):
    pass


def reject(message):
    raise ImportFailure(message)


def select(table, schema):
    if not isinstance(table, dict):
        reject("a selected config section is not a TOML table")
    selected = {}
    for key, expected in schema.items():
        if key in table:
            value = table[key]
            if type(value) is not expected:
                reject("a selected setting has an unsupported value type")
            if expected is list and any(type(item) is not str for item in value):
                reject("a selected setting requires an array of strings")
            selected[key] = value
    return selected


def quote(value):
    # JSON basic strings also work in TOML, except for literal DEL. Preserve
    # non-ASCII characters rather than emitting JSON surrogate-pair escapes.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def literal(value):
    if type(value) is str:
        return quote(value)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is list:
        return "[" + ", ".join(quote(item) for item in value) + "]"
    reject("unsupported value in selected settings")


def serialize(table, path=()):
    lines = ["[" + ".".join(quote(part) for part in path) + "]"] if path else []
    for key, value in table.items():
        if not isinstance(value, dict):
            lines.append(quote(key) + " = " + literal(value))
    for key, value in table.items():
        if isinstance(value, dict):
            lines.extend(["", *serialize(value, (*path, key))])
    return lines


def main():
    mode, source_name, preview_name = sys.argv[1:]
    source = Path(source_name)
    preview = Path(preview_name)
    destination = preview / "config.toml"
    if preview.is_symlink() or (preview.exists() and not preview.is_dir()):
        reject("preview home must be a real directory, not a symlink; preserved")
    if os.path.lexists(destination):
        reject(
            "preview config already exists; preserved. One-time import never overwrites it"
        )
    if source.resolve().parent == preview.resolve():
        reject("source must be a separate Codex home")
    with source.open("rb") as stream:
        original = tomllib.load(stream)

    selected = select(
        original,
        {
            "model": str,
            "model_provider": str,
            "model_reasoning_effort": str,
            "plan_mode_reasoning_effort": str,
            "model_reasoning_summary": str,
            "model_verbosity": str,
            "personality": str,
            "model_context_window": int,
            "model_auto_compact_token_limit": int,
            "tool_output_token_limit": int,
            "hide_agent_reasoning": bool,
            "show_raw_agent_reasoning": bool,
            "web_search": str,
        },
    )
    provider_schema = {
        "name": str,
        "base_url": str,
        "env_key": str,
        "env_key_instructions": str,
        "wire_api": str,
        "requires_openai_auth": bool,
        "supports_websockets": bool,
        "supports_standalone_web_search": bool,
        "request_max_retries": int,
        "stream_max_retries": int,
        "stream_idle_timeout_ms": int,
        "websocket_connect_timeout_ms": int,
        "env_http_headers": dict,
    }
    providers = original.get("model_providers", {})
    if not isinstance(providers, dict):
        reject("model_providers must be a TOML table")
    imported_providers = {}
    for name, provider in providers.items():
        if not isinstance(provider, dict) or set(provider) - provider_schema.keys():
            reject(
                "a provider uses fields outside the import allowlist (such as inline credentials or auth helpers); configure that provider manually"
            )
        imported = select(provider, provider_schema)
        if imported.get("wire_api", "responses") != "responses":
            reject(
                "a provider uses an unsupported wire_api; this preview requires responses"
            )
        if "base_url" in imported:
            url = urlsplit(imported["base_url"])
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                reject(
                    "provider URLs must be HTTP(S) endpoints without embedded credentials, query strings or fragments"
                )
        references = [imported["env_key"]] if "env_key" in imported else []
        references.extend(imported.get("env_http_headers", {}).values())
        if any(
            type(name) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            for name in references
        ):
            reject(
                "provider authentication must reference environment variable names, not credential values"
            )
        imported_providers[name] = imported
    if imported_providers:
        selected["model_providers"] = imported_providers
    tui = select(
        original.get("tui", {}),
        {
            "theme": str,
            "animations": bool,
            "show_tooltips": bool,
            "status_line": list,
            "status_line_use_colors": bool,
            "notification_method": str,
            "alternate_screen": str,
        },
    )
    if tui:
        selected["tui"] = tui
    features = original.get("features", {})
    if not isinstance(features, dict):
        reject("features must be a TOML table")
    multi_agent = select(
        features.get("multi_agent_v2", {}),
        {
            "enabled": bool,
            "expose_spawn_agent_model_overrides": bool,
        },
    )
    if multi_agent:
        selected["features"] = {"multi_agent_v2": multi_agent}
    if not selected:
        reject("no supported settings or providers found in the source config")
    rendered = (
        "# One-time import of selected Codex settings.\n"
        + "\n".join(serialize(selected))
        + "\n"
    )
    # Check serialization with the same real TOML parser before creating files.
    if tomllib.loads(rendered) != selected:
        reject("could not serialize selected settings without changing them")
    if mode == "check":
        return
    preview.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(preview, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        temporary = ".config-import-" + secrets.token_hex(8)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            # Publish complete content without overwriting even a concurrently
            # created config. Directory fd + NOFOLLOW avoid symlink redirects.
            os.link(
                temporary,
                "config.toml",
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        finally:
            os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)
    print("Imported selected settings into ~/.codex-cross-provider-preview/config.toml")
    print(
        "Source config was only read. Login, storage paths, profiles, permissions, hooks and integrations were not imported."
    )
    print(
        "Sign in separately with codex-preview login; keep provider API keys in your shell environment."
    )


try:
    main()
except ImportFailure as error:
    sys.exit("codex-preview import: " + str(error))
except tomllib.TOMLDecodeError:
    # Parser errors can include source fragments or keys containing secrets.
    sys.exit(
        "codex-preview import: source config is not valid supported TOML; settings were not imported"
    )
except FileExistsError:
    sys.exit("codex-preview import: preview config already exists; preserved")
except (OSError, ValueError, RuntimeError):
    sys.exit(
        "codex-preview import: could not read the source or create the preview config; check paths and permissions"
    )
PYTHON_IMPORT
}

if "$preview_import_only"; then
  import_preview_config apply
  exit 0
fi
if "$preview_import_config"; then
  import_preview_config check
fi

preview_parent="$HOME/Applications"
preview_install_dir="$preview_parent/codex-cross-provider-preview"
preview_path_line='export PATH="$HOME/Applications/codex-cross-provider-preview:$PATH"'
preview_profile=''
if "$preview_update_path"; then
  case "${SHELL:-/bin/zsh}" in
    */zsh) preview_profile="${ZDOTDIR:-$HOME}/.zshrc" ;;
    */bash) preview_profile="$HOME/.bash_profile" ;;
    *) printf 'Shell %s requires manual PATH setup.\n' "${SHELL:-unknown}" ;;
  esac
fi
if [[ -n "$preview_profile" ]]; then
  if [[ -e "$preview_profile" || -L "$preview_profile" ]]; then
    [[ -f "$preview_profile" && -w "$preview_profile" ]] ||
      fail "cannot update $preview_profile; use --no-path for manual setup"
  else
    [[ -d "$(dirname "$preview_profile")" && -w "$(dirname "$preview_profile")" ]] ||
      fail "cannot create $preview_profile; use --no-path for manual setup"
  fi
fi
[[ ! -L "$preview_install_dir" ]] || fail "destination is a symlink; preserved: $preview_install_dir"
if [[ -e "$preview_install_dir" && ! -d "$preview_install_dir" ]]; then
  fail "destination is not a directory; preserved: $preview_install_dir"
fi
if [[ -n "$preview_archive_input" ]]; then
  [[ -f "$preview_archive_input" ]] || fail "archive not found: $preview_archive_input"
fi

umask 077
mkdir -p "$preview_parent"
cleanup() {
  if [[ -n "$preview_stage_dir" ]]; then
    rm -rf "$preview_stage_dir" || printf 'Could not remove staging directory: %s\n' "$preview_stage_dir" >&2
  fi
  if "$preview_lock_owned"; then
    rmdir "$preview_lock_dir" || printf 'Could not remove installer lock: %s\n' "$preview_lock_dir" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
preview_lock_dir="$preview_parent/.codex-preview-install.lock"
mkdir "$preview_lock_dir" 2>/dev/null ||
  fail "another installer may be running. If none is running, remove the stale empty lock directory: $preview_lock_dir"
preview_lock_owned=true
preview_stage_dir=$(mktemp -d "$preview_parent/.codex-preview-install.XXXXXX")

if [[ -n "$preview_archive_input" ]]; then
  cp -- "$preview_archive_input" "$preview_stage_dir/$preview_asset"
else
  printf 'Downloading %s...\n' "$preview_tag"
  curl --fail --location --show-error --proto '=https' --tlsv1.2 \
    --retry 2 --connect-timeout 20 \
    --output "$preview_stage_dir/$preview_asset" "$preview_url"
fi
if ! (
  cd "$preview_stage_dir"
  printf '%s  %s\n' "$preview_sha256" "$preview_asset" | shasum -a 256 -c -
); then
  fail 'archive checksum mismatch; installation and shell profile were not changed'
fi

mkdir "$preview_stage_dir/package"
tar -xzf "$preview_stage_dir/$preview_asset" -C "$preview_stage_dir/package"
for preview_executable in codex-preview bin/codex bin/codex-code-mode-host bin/codex-exec-server codex-path/rg; do
  [[ -f "$preview_stage_dir/package/$preview_executable" && -x "$preview_stage_dir/package/$preview_executable" ]] ||
    fail "archive is missing an executable: $preview_executable"
done
[[ -f "$preview_stage_dir/package/codex-package.json" ]] || fail 'archive is missing its package manifest'
grep -Fqx "Tag: $preview_tag" "$preview_stage_dir/package/BUILD-INFO.txt" ||
  fail 'archive release metadata does not match this installer'

if [[ -e "$preview_install_dir" ]]; then
  diff -qr "$preview_stage_dir/package" "$preview_install_dir" >/dev/null ||
    fail "destination differs from this release; preserved: $preview_install_dir. Move it aside after closing preview sessions, then retry."
  printf 'Existing %s package matches; keeping it.\n' "$preview_tag"
else
  mv "$preview_stage_dir/package" "$preview_install_dir"
  printf 'Installed %s in %s\n' "$preview_tag" "$preview_install_dir"
fi

if "$preview_import_config"; then
  import_preview_config apply
fi

if [[ -n "$preview_profile" ]]; then
  if grep -Fqx "$preview_path_line" "$preview_profile" 2>/dev/null; then
    printf 'PATH is already configured in %s\n' "$preview_profile"
  else
    if [[ -e "$preview_profile" ]]; then
      preview_profile_backup=$(mktemp "${preview_profile}.codex-preview-backup.XXXXXX")
      cp -p "$preview_profile" "$preview_profile_backup"
      printf 'Shell profile backup: %s\n' "$preview_profile_backup"
    fi
    printf '\n# Codex cross-provider preview\n%s\n' "$preview_path_line" >> "$preview_profile"
    printf 'Added codex-preview to PATH in %s\n' "$preview_profile"
  fi
  printf '\nOpen a new terminal, or run this in your current terminal:\n  %s\n' "$preview_path_line"
else
  printf '\nFor PATH access, add this directory using your shell configuration:\n  %s\n' "$preview_install_dir"
fi
printf '\nLaunch: %s/codex-preview\n' "$preview_install_dir"
printf 'Preview settings and login remain separate in ~/.codex-cross-provider-preview.\n'
printf 'This is an unofficial CLI build with ad-hoc signatures, without Apple notarization.\n'
