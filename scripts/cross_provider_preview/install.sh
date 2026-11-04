#!/bin/bash
# Install the published preview alongside Codex Desktop and the normal CLI.
# Compatible with the Bash 3.2 shipped by macOS; no sudo or Python required.
set -euo pipefail

preview_tag='cross-provider-v0.0.0-preview.3'
preview_asset='codex-cross-provider-aarch64-apple-darwin.tar.gz'
preview_sha256='b9b9227dad847e3ced58414ca087f634526b915b0a8af2d1e9c948725ddeaf09'
preview_url="https://github.com/maxtheaxe/codex/releases/download/$preview_tag/$preview_asset"
preview_archive_input=''
preview_update_path=true
preview_stage_dir=''
preview_lock_dir=''
preview_lock_owned=false

fail() {
  printf 'codex-preview installer: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bash install.sh [--archive FILE] [--no-path]

Install the verified Apple-silicon preview.3 for macOS 15 or newer.
  --archive FILE  Use an already downloaded archive instead of downloading it.
                  The same pinned SHA-256 check still applies.
  --no-path       Leave shell startup files unchanged.
  -h, --help      Show this help.

Installs into ~/Applications/codex-cross-provider-preview.
Adds that directory to ~/.zshrc (or $ZDOTDIR/.zshrc) for zsh, or
~/.bash_profile for Bash. Other shells require manual PATH configuration.
Existing matching packages are reused; different installations are preserved
and rejected. The installer does not replace Codex Desktop/the regular CLI,
copy settings or credentials, sign in, or disable macOS security checks.
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
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1 (use --help)" ;;
  esac
done

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
