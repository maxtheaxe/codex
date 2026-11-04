# Apple-silicon cross-provider preview

This is an unofficial Codex CLI prerelease from `maxtheaxe/codex`, built for
Apple silicon and macOS 15 or newer. It is not a desktop-app installer. The
archive includes the CLI, code-mode host, executor, and ripgrep together; keep
the whole directory intact.

## Install alongside your usual Codex

Download the `.tar.gz` archive and `SHA256SUMS` from the same GitHub prerelease.
From the download directory, verify the archive before extracting it:

```sh
shasum -a 256 -c SHA256SUMS
mkdir -p "$HOME/Applications/codex-cross-provider-preview"
tar -xzf codex-cross-provider-aarch64-apple-darwin.tar.gz \
  -C "$HOME/Applications/codex-cross-provider-preview"
"$HOME/Applications/codex-cross-provider-preview/codex-preview" --version
```

Use an empty destination for each version. Run the included `codex-preview`
launcher rather than `bin/codex`. It sets `CODEX_HOME` to
`~/.codex-cross-provider-preview`, creates it with private permissions, and uses
file-backed login credentials there. It deliberately overrides an inherited
`CODEX_HOME`; it does not copy or migrate `~/.codex`. Do not pass configuration
overrides that redirect storage to your normal installation. Authentication
files contain secrets; do not commit or share them.

Your usual `codex` command and desktop app remain installed in their existing
locations. No administrator privileges, Homebrew/npm replacement, PATH changes,
or background service are required. The preview still has access to the project
you run it in under its normal approval and sandbox policy. Start in a disposable
checkout and avoid running two agents against the same working tree.

The binaries have local ad-hoc signatures, not an Apple Developer ID signature
or notarization. If macOS blocks a verified download, use its Privacy & Security
approval flow. Do not disable Gatekeeper globally. CI cannot test the interactive
Gatekeeper prompt or login flow on your Mac.

## Configure and try it

Sign in independently for the preview:

```sh
"$HOME/Applications/codex-cross-provider-preview/codex-preview" login
```

Create `~/.codex-cross-provider-preview/config.toml` using the provider examples
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

Stop preview sessions and remove the extracted preview directory. Your usual
installation remains available. The separate `~/.codex-cross-provider-preview`
directory holds preview sessions and credentials; retain it if desired or delete
it separately. No system-wide rollback is needed.

The dedicated `cross-provider-preview-macos.yml` workflow builds only
`aarch64-apple-darwin` on the standard public `macos-15` runner. It runs for
`cross-provider-v0.0.0-preview.N` tags in `maxtheaxe/codex`, independently of the
upstream release workflow. It uses the repository's package builder, pinned
Rust/actions, verified V8/ripgrep downloads, locked Cargo dependencies, release
optimization with LTO/debug symbols disabled, and at most two compiler jobs.
It performs startup checks after extracting the final archive and publishes a
GitHub prerelease only on success. The job stops after 60 minutes and has no
automatic retries. Failed runs retain any completed distribution files as an
Actions artifact for seven days. No release replaces GitHub's latest stable
release.

To rebuild after an update, reapply the source patches using
`cross-provider-subagents.md`, bring this packaging commit onto a dedicated
branch, review compatibility, and use a new preview tag. Do not move published
tags or overwrite existing release assets.
