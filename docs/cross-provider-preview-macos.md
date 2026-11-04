# Apple-silicon cross-provider preview

This is an unofficial Codex CLI prerelease from `maxtheaxe/codex`, built for
Apple silicon and macOS 15 or newer. It is not a desktop-app installer. The
archive includes the CLI, code-mode host, executor, and ripgrep together; keep
the whole directory intact.

## Published preview.3

[Download preview.3 and its checksum file](https://github.com/maxtheaxe/codex/releases/tag/cross-provider-v0.0.0-preview.3).
It was built from source commit `3c764bdf6c963c63fc296f34f531b8767dcafd7c`.
The [successful Mac run](https://github.com/maxtheaxe/codex/actions/runs/41858118484)
passed the launcher isolation test, locked compilation of all three binaries,
ARM64 architecture and ad-hoc signature checks, and checks of the extracted
archive (`--version`, `--help`, `features list`, and bundled ripgrep startup).
Compilation took 52 minutes 41 seconds. GitHub's first queue attempt could not
acquire a Mac runner; the requeued job completed successfully within its one-hour
execution limit.

The published archive was independently downloaded and verified against the
published `SHA256SUMS`. Its SHA-256 is:

```text
b9b9227dad847e3ced58414ca087f634526b915b0a8af2d1e9c948725ddeaf09
```

Live OpenAI/OpenRouter inference, interactive login, and Gatekeeper approval on
a user's Mac remain unverified. These are not Apple-notarized binaries.

## Known model-access blocker

A user reported preview.3 rejecting `gpt-6.1-sol` with “not supported when using
Codex with a ChatGPT account.” This is an unresolved live-use blocker, not an
installation failure. The same model previously worked for that user in the TUI;
the failure alone does not establish that the account lacks access.

The bundled fallback catalog selects `gpt-6.1-sol` even with no imported `model`.
The binary also sends `0.0.0` in its HTTP client version, not just its version
banner. Both properties are reproduced by the executable smoke test; neither
has been established as the cause of the live backend rejection. Model catalog
refresh, current authentication, and backend compatibility still need live
verification. Reinstalling the same archive does not change these properties.

Before assigning repository work, verify a short model response with the preview's
login. Use `/status` to check the active account and `/model` to select a model;
a bundled fallback entry is not proof of backend access. If a model-upgrade
prompt appears, choose **Use existing model** to retain your explicit selection.
If requests still fail, inspect model-catalog warnings (F2 in the TUI) rather than
repeatedly reinstalling. Do not share authentication files or API keys.

The warning that command-line overrides require embedded mode is expected: the
preview launcher passes `-c cli_auth_credentials_store="file"` to isolate login
storage. That warning is not itself a model-access diagnosis.

The `cross-provider-preview-smoke.yml` workflow downloads the existing published
archive and tests a fresh native Mac installation in an isolated home, including
PATH setup, config import, synthetic login, and model responses from a loopback
server. It does not compile binaries or call billable model endpoints. Its
synthetic rejection/recovery checks are not a live ChatGPT entitlement test.
The fixture's accepted model names are test inputs, not recommendations about
which models a user's account supports. This checks installation and first-turn
startup; native cross-provider delegation remains covered by the separate Rust
integration tests, not by this smoke test.

## Build history (2026-11-04)

The
[preview.2 attempt](https://github.com/maxtheaxe/codex/actions/runs/41846917077),
at source commit `84e30d990b185f3154e39994cfb77c9499d21659`, passed Apple-silicon
runner/toolchain setup, V8 checksum verification, and the launcher isolation
test. GitHub canceled compilation at the configured one-hour limit while
`codex-tui` was compiling. The log has no Rust compiler diagnostic before that
cancellation. Packaging, extracted-archive startup checks, and publication were
skipped; no distribution artifact was produced. The earlier preview.1 tag was
rejected before execution because of a workflow expression error, corrected in
preview.2.

Starting with preview.3, the workflow uses a lighter preview build at
optimization level 1 with 64 codegen units, retaining the same two-job and
one-hour limits. An archive is published only after its build and startup checks
pass; follow the run link in the release notes or `BUILD-INFO.txt` for the exact
result. The installation instructions below apply to successfully published
archives.

## Install alongside your usual Codex

### Automatic installer

Download [install.sh](../scripts/cross_provider_preview/install.sh), review it,
then run it as your normal user:

```sh
curl -fL https://raw.githubusercontent.com/maxtheaxe/codex/release/cross-provider-apple-silicon/scripts/cross_provider_preview/install.sh \
  -o codex-preview-install.sh
bash codex-preview-install.sh
```

The installer requires Apple silicon and macOS 15 or newer. It downloads
preview.3, verifies its pinned SHA-256 before extraction, and installs the whole
package into `~/Applications/codex-cross-provider-preview`. It adds the package
root to PATH in `~/.zshrc` (respecting `ZDOTDIR`) or `~/.bash_profile`, backing up
an existing shell profile before changing it. Open a new terminal afterward, or
run the `export PATH=...` command it prints to activate the command immediately.

It preserves a matching existing installation and refuses to overwrite a
different package or unrelated files at the destination. It does not replace
Codex Desktop or your regular CLI, copy login credentials, sign in, or modify
Gatekeeper settings. Config import is optional, as described below. Other shells
need manual PATH configuration. No rebuild or package manager is involved.

If you already downloaded the archive, avoid downloading it again with:

```sh
bash codex-preview-install.sh --archive "$HOME/Downloads/codex-cross-provider-aarch64-apple-darwin.tar.gz"
```

Use `--no-path` to leave shell startup files untouched. Run `--help` to see the
options. Failed installs clean up their temporary downloads; the installer never
deletes an existing package to resolve a conflict. An interrupted installer that
cannot run its cleanup may leave an empty
`~/Applications/.codex-preview-install.lock` directory; remove that lock only
after confirming no installer is still running.

Installer verification covers checksum rejection, repeat runs, destination
preservation, shell profile backups, PATH lookup, and config import in an isolated
home. Import checks cover credential/storage exclusions, source preservation,
existing-config and symlink protection, custom source homes, private file
permissions, and errors that do not echo config values. The
published archive also passed two installer runs in a Linux fixture with macOS
platform commands simulated. The
[native Mac install smoke run](https://github.com/maxtheaxe/codex/actions/runs/41888558501)
then passed all 30 installer/launcher tests and exercised the actual released
archive on macOS 15.8.2 ARM64: fresh installation, PATH, config import with skipped
auth, isolated synthetic API-key login, default-model rejection, explicit-model
recovery, and a custom-provider request. Inference used a loopback server and
synthetic credentials; this does not validate a real ChatGPT account or OpenRouter
key. No binary rebuild was performed.

### Optional one-time config import

The current installer can import selected settings from
`${CODEX_HOME:-$HOME/.codex}/config.toml` into
`~/.codex-cross-provider-preview/config.toml`. This requires `python3` on PATH
running Python 3.11 or newer, for its standard-library TOML parser. The installer
does not install Python or dependencies. Installation without import still needs
no Python.

Download the current installer using the command above. To install and import:

```sh
bash codex-preview-install.sh --import-config
```

If the preview is already installed, import without downloading the archive or
changing PATH:

```sh
bash codex-preview-install.sh --import-config-only
codex-preview login
```

The import copies model/reasoning/context preferences, selected TUI settings,
multi-agent V2 toggles, and provider definitions using environment-based
authentication. For OpenRouter this includes its endpoint and
`env_key = "OPENROUTER_API_KEY"`, but **not the key's value**. An already exported
key is inherited when you launch the preview from that terminal. Configured
inline credentials, static headers, query parameters, command-based auth,
gateway OAuth, and other provider fields outside the import allowlist cause a
clear error before import; those provider setups need manual configuration.
Errors identify the provider and rejected field paths without printing values.
For example, `model_providers.openrouter.auth` is reported with the explanation
that credential-helper settings require manual setup. Accepted fields such as
`name`, `base_url`, and `wire_api` are not listed as rejected fields.
Unsupported `wire_api` values are rejected rather than rewritten.

To import the supported settings while skipping unsupported provider fields,
add `--skip-unsupported`:

```sh
bash codex-preview-install.sh --import-config --skip-unsupported
```

For an already installed preview, use
`--import-config-only --skip-unsupported`. The option reports only skipped field
paths and records the same notice as a comment in the imported config. For
example, it skips `model_providers.openrouter.auth` while preserving `name`,
`base_url`, and `wire_api`. It never copies or runs the omitted credential helper.

Skipping `auth` does not configure replacement authentication. Before using that
provider, either configure its credential helper manually in the preview or add
`env_key = "OPENROUTER_API_KEY"` to the **existing**
`[model_providers.openrouter]` table and export that key in the launching terminal.
Existing valid `env_key` references are retained. Invalid supported field values
(including URLs, protocols, types, and environment-variable references), malformed
TOML, symlinked preview homes, and existing preview configs still stop the import.
Without this option, unsupported provider fields continue to stop the import.

This copies only the selected entries in the base config file. It does not follow
profiles or copy credential files, logs, databases, storage paths, permissions,
project trust, hooks, MCP servers, plugins, or integrations. Existing environment
and project-level overrides still apply at runtime. The original config is only
read; comments and formatting are not copied. Output permissions are `0600`, and
a new preview home is `0700`.

An existing preview config, including a symlink, is always preserved and causes
the import to stop. There is no merge or overwrite option. If you already have a
preview config, review and merge the desired settings manually. To undo a fresh
import before making further changes, remove only the new preview `config.toml`;
regular Codex settings and login are unaffected.

### Manual installation

Download the `.tar.gz` archive and `SHA256SUMS` from the same GitHub prerelease.
From the download directory, verify the archive before extracting it:

```sh
shasum -a 256 -c SHA256SUMS
mkdir -p "$HOME/Applications/codex-cross-provider-preview"
tar -xzf codex-cross-provider-aarch64-apple-darwin.tar.gz \
  -C "$HOME/Applications/codex-cross-provider-preview"
"$HOME/Applications/codex-cross-provider-preview/codex-preview" --version
```

### Add codex-preview to PATH

Add the package directory to your `PATH` so you can run `codex-preview` from
any directory. For the default macOS shell, zsh, these commands save the setting
in `~/.zshrc` and enable it in your current terminal:

```sh
preview_path_line='export PATH="$HOME/Applications/codex-cross-provider-preview:$PATH"'
grep -Fqx "$preview_path_line" "$HOME/.zshrc" 2>/dev/null ||
  printf '\n%s\n' "$preview_path_line" >> "$HOME/.zshrc"
export PATH="$HOME/Applications/codex-cross-provider-preview:$PATH"
command -v codex-preview
codex-preview --version
```

`command -v` should print
`/Users/<your-name>/Applications/codex-cross-provider-preview/codex-preview`.
Future interactive zsh terminals will pick up the saved setting automatically.
If you use Bash, use `~/.bash_profile` in place of `~/.zshrc` above. Keep the
launcher in the extracted package directory: it finds the bundled binaries
relative to its own location. Add the package root to `PATH`, not its `bin`
directory, so your usual `codex` command keeps resolving to your normal install.

Use an empty destination for each version. Run the included `codex-preview`
launcher rather than `bin/codex`. It sets `CODEX_HOME` to
`~/.codex-cross-provider-preview`, creates it with private permissions, and uses
file-backed login credentials there. It deliberately overrides an inherited
`CODEX_HOME`; the launcher does not copy or migrate `~/.codex`. Do not pass
configuration overrides that redirect storage to your normal installation. Authentication
files contain secrets; do not commit or share them.

Your usual `codex` command and desktop app remain installed in their existing
locations. No administrator privileges, Homebrew/npm replacement, or background
service are required. The preview still has access to the project you run it in
under its normal approval and sandbox policy. Start in a disposable
checkout and avoid running two agents against the same working tree.

The binaries have local ad-hoc signatures, not an Apple Developer ID signature
or notarization. If macOS blocks a verified download, use its Privacy & Security
approval flow. Do not disable Gatekeeper globally. CI cannot test the interactive
Gatekeeper prompt or login flow on your Mac.

## Configure and try it

Sign in independently for the preview:

```sh
codex-preview login
```

Import existing provider settings as described above, or create
`~/.codex-cross-provider-preview/config.toml` using the provider examples
in the included `cross-provider-subagents.md`. Add your OpenRouter provider and
export `OPENROUTER_API_KEY` privately in your shell; never put credentials in
the release or a repository. Ask a parent to spawn a child with an explicit
`model_provider`, a model supported by that provider, and fresh context
(`fork_turns: "none"` for V2 or `fork_context: false` for V1).

The separate installation does **not** create a separate usage allowance. Chats
authenticated with your ChatGPT account consume that account's allowance;
OpenRouter API requests use your OpenRouter account's balance and limits.
Provider-specific billing and available models still apply.

Both provider directions and lifecycle behavior passed mock-server tests in the
source implementation. No live cross-provider inference is claimed. See the
included `cross-provider-validation.md` for the existing test evidence and
`BUILD-INFO.txt` for the exact source commit and build profile. The compiled
version banner remains the source workspace version `0.0.0`; the archive's
package version and GitHub tag identify this preview.

## Remove or rebuild

Stop preview sessions and remove the extracted preview directory. Remove the
preview `export PATH=...` line from `~/.zshrc` (or `~/.bash_profile` for Bash),
then open a new terminal. Your usual installation remains available. The separate
`~/.codex-cross-provider-preview` directory holds preview sessions and credentials;
retain it if desired or delete it separately. No system-wide rollback is needed.

The dedicated `cross-provider-preview-macos.yml` workflow builds only
`aarch64-apple-darwin` on the standard public `macos-15` runner. It runs for
`cross-provider-v0.0.0-preview.N` tags in `maxtheaxe/codex`, independently of the
upstream release workflow. It uses the repository's package builder, pinned
Rust/actions, verified V8/ripgrep downloads, locked Cargo dependencies, the release
profile at optimization level 1 with 64 codegen units and LTO/debug symbols
disabled, and at most two compiler jobs. This functional preview is not optimized
to the same degree as a production release.
It performs startup checks after extracting the final archive and publishes a
GitHub prerelease only on success. The job stops after 60 minutes and has no
automatic retries. Failed runs retain any completed distribution files as an
Actions artifact for seven days. No release replaces GitHub's latest stable
release.

To rebuild after an update, reapply the source patches using
`cross-provider-subagents.md`, bring these packaging commits onto a dedicated
branch, review compatibility, and use a new preview tag. Do not move published
tags or overwrite existing release assets.
