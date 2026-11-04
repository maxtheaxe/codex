# Cross-provider native subagents

This feature lets a native Codex parent create a native Codex child using a
different configured model provider. Both OpenAI-parent → OpenRouter-child and
OpenRouter-parent → OpenAI-child use the existing spawn, message, wait, and agent
lifecycle. The goal is to keep that behavior reproducible as a small feature
patch series that can be reviewed, reapplied, tested, and removed.

This is a source change, not a configuration-only feature in the installed CLI.
The development baseline is the `maxtheaxe/codex` fork at
`3e743d15787bd9091f674f0fbc27010f0cbcec62` ("Add cloud thread activation with
resolved settings (#59343)"). That checkout declares Cargo workspace version
`0.0.0` and Rust toolchain `1.95.0`. The existing installed CLI reports
`0.166.0-alpha.1`; its version does not establish source compatibility and it is
not replaced by exporting or applying this patchset.

## Architecture and API

`spawn_agent` gains an optional `model_provider` selector. Its value is a key in
the child's already resolved `model_providers` registry, such as `openai` or
`openrouter`. It is not an endpoint URL or a credential. Provider resolution uses
the existing `ModelProviderInfo`, authentication, and Responses API client.
Managed provider requirements remain enforced.

When switching providers, supply a nonempty explicit `model` valid at the chosen
provider. A model name alone does not select its provider: an OpenRouter ID such
as `vendor/model-id` still needs `model_provider: "openrouter"` when the parent
uses OpenAI. Omitting `model_provider` keeps the existing inheritance behavior.
The provider switch does not mutate the parent's provider or credentials.

Cross-provider children start with a fresh context. For multi-agent V2, pass
`fork_turns: "none"`; for V1, use `fork_context: false`. Full-history provider
switches are rejected because provider-specific response history, encrypted
reasoning, and continuation identifiers cannot be assumed portable. Put the
necessary task context in the child's `message`.

The child remains an ordinary Codex thread with the inherited working directory,
permissions, approval policy, cancellation, message delivery, and completion
reporting. No independent subprocess, second agent framework, or chat-completions
bridge is involved. The reusable implementation points are:

| Layer | Responsibility |
| --- | --- |
| `core/src/tools/handlers/multi_agents_spec.rs` | Advertise `model_provider` consistently with model overrides. |
| `core/src/tools/handlers/multi_agents{,_v2}/spawn.rs` | Parse the selector and pass it into shared child configuration. |
| `core/src/agent/child_config.rs` | Resolve and validate the child's provider/model before native thread creation. |
| Existing provider/auth/client code | Send child inference using that provider's configured endpoint and credentials. |

Paths in this table are relative to `codex-rs/`. Provider selection is not a
transport adapter: the selected endpoint must accept Codex's Responses API
requests, streaming events, and tools. This baseline supports `wire_api =
"responses"`; `wire_api = "chat"` is explicitly rejected by the existing provider
configuration parser.

## Configure both providers

Merge this example into the configuration read by the **patched** executable.
Replace the model placeholders with IDs available to your account. Keep API keys
in the environment or an existing supported credential mechanism.

```toml
# Existing OpenAI login or API-key authentication remains in use for this provider.
model_provider = "openai"
model = "REPLACE_WITH_YOUR_OPENAI_MODEL"

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false

[features.multi_agent_v2]
enabled = true
expose_spawn_agent_model_overrides = true
```

Set `OPENROUTER_API_KEY` in the environment that launches Codex through your
existing secret mechanism. Do not put the key in this file, a patch, a command
history example, or a bug report. Both providers' existing credentials must be
available to the process before a child can use them. Selecting the OpenRouter
provider must not send the parent's OpenAI login token to that endpoint.

The OpenRouter configuration reuses Codex's existing Responses transport. The
[OpenRouter Codex CLI guide](https://openrouter.ai/docs/cookbook/coding-agents/codex-cli)
documents that base URL and provider setup. Its command-auth option may be useful
when provider-specific model catalog discovery is needed. `env_key` configures
inference authentication; it does not guarantee a refreshed Codex model catalog.
This feature therefore accepts an explicit model for a provider switch instead
of requiring that model to appear in the parent's picker.

The
[OpenRouter OpenAPI specification](https://github.com/OpenRouterTeam/docs/blob/main/openapi/openapi.yaml)
also describes namespace tools, custom tools, and namespace-qualified calls.
There is no additional tool-flattening adapter in this patch. Those published
schemas do not establish that every routed model supports the entire Codex tool
surface. Check the chosen model and route with the smoke test below.

## Use either parent direction

The examples below are JSON **tool arguments generated by the parent agent**;
they are not terminal commands. Replace the model placeholders and ask the
parent to call `spawn_agent` with those values. The prompt should explicitly
authorize a provider/model override and include the task information needed by a
fresh child.

With an OpenAI parent, request an OpenRouter child:

```json
{
  "task_name": "router_check",
  "message": "Report the current working directory using the native terminal tool, then return a short result.",
  "fork_turns": "none",
  "model_provider": "openrouter",
  "model": "REPLACE_WITH_YOUR_OPENROUTER_MODEL_ID"
}
```

For the reverse direction, launch the patched executable with the configured
OpenRouter parent and its model, then request an OpenAI child:

```bash
./codex-rs/target/debug/codex \
  -c 'model_provider="openrouter"' \
  -m 'REPLACE_WITH_YOUR_OPENROUTER_MODEL_ID'
```

```json
{
  "task_name": "openai_check",
  "message": "Report the current working directory using the native terminal tool, then return a short result.",
  "fork_turns": "none",
  "model_provider": "openai",
  "model": "REPLACE_WITH_YOUR_OPENAI_MODEL"
}
```

For V1, remove `task_name`, replace `fork_turns` with `fork_context: false`, and
call the existing `multi_agent_v1.spawn_agent` tool. V2's provider selector follows
the existing model-override visibility settings; if model overrides are hidden by
the active host or configuration, that surface is not a way to request this
override.

After spawning, use the returned native child ID or task path with the existing
message/wait tools. Confirm the requested child actually calls a tool and that
its result reaches the parent. Merely receiving a child ID, or observing text
that names another provider, does not prove inference used that provider.

## Export and apply a reusable patchset

Use [the patchset helper](../scripts/cross_provider_patchset.py) with Python 3.10+
and a current Git supporting `--path-format=absolute` and `worktree list -z`.
It exports committed changes as an ordered `git format-patch` series;
uncommitted work is not included. Export from the final feature branch after
implementation, tests, and documentation have all been committed.

```bash
python3 scripts/cross_provider_patchset.py export \
  --repo . \
  --base 3e743d15787bd9091f674f0fbc27010f0cbcec62 \
  --head HEAD \
  --output /tmp/codex-cross-provider-patchset
```

The output directory must be new and outside every worktree and Git directory
belonging to the source repository. The helper never commits generated patches
into their own feature range. Keep the generated bundle as a release artifact
alongside the source branch; do not add it to Codex's existing dependency
`patches/` directory.

`manifest.json` records the exact base commit/tree, source head/tree, each ordered
source commit/tree, and SHA-256 of every numbered patch. Export refuses merge
commits and empty commits. Re-exporting the same range produces the same bundle
under the same Git version/configuration. Applying through `git am` preserves
authors and commit messages; applied commit hashes may differ because committer
metadata can differ. Verification compares every resulting tree and the commit
order/count rather than requiring the original commit hashes.

Review the feature diff, manifest, and patch files from a trusted source. The
manifest detects accidental tampering and incompatible bases; it is not a
publisher signature. Exporting and applying do not build or install Codex.

To apply into a separate clone or disposable worktree, first create a clean
branch at the exact manifest base. Use the helper from the feature checkout
because it is not present in the unpatched base:

```bash
git -C /path/to/target-codex switch -c cross-provider-native \
  3e743d15787bd9091f674f0fbc27010f0cbcec62

python3 /path/to/feature-codex/scripts/cross_provider_patchset.py apply \
  --repo /path/to/target-codex \
  --bundle /tmp/codex-cross-provider-patchset

python3 /path/to/feature-codex/scripts/cross_provider_patchset.py verify \
  --repo /path/to/target-codex \
  --bundle /tmp/codex-cross-provider-patchset
```

Application refuses a dirty target, untracked files, an existing Git operation,
unexpected bundle files, changed patch hashes, or a different `HEAD`. It uses
`git am --no-3way`, with repository hooks and automatic signing disabled for the
helper's operations. If a patch fails during application, the helper runs `git
am --abort` to restore the starting state. An abort failure is reported and
requires inspection before continuing. A verification failure after Git has
accepted the series is reported explicitly; inspect the target rather than
continuing to build an unverified tree.

## Build and verify

Run from the feature checkout or the successfully verified target. These commands
describe the required checks, not a claim that a local machine has completed all
of them:

```bash
python3 -m unittest discover -s scripts -p test_cross_provider_patchset.py -v

cd codex-rs
cargo fmt --all -- --check
cargo test -p codex-core child_config
cargo test -p codex-core multi_agents
cargo build -p codex-cli --bin codex
```

Invoke the resulting `codex-rs/target/debug/codex` explicitly from the repository
root. Do not infer behavior of that binary from `codex --version` on `PATH`.
Building this source tree does not modify the existing installed executable.

For a live smoke test with preexisting credentials and permitted endpoints:

1. Run the OpenAI parent → OpenRouter child example and verify a native child tool
   call and its returned result.
2. Run the OpenRouter parent → OpenAI child example and verify the same lifecycle.
3. Omit `model_provider` in an ordinary spawn to confirm inheritance still works.
4. Try an unknown provider, an empty/missing model for a provider switch, and a
   cross-provider full-history fork. Each should produce a clear validation error
   without an inference request to the unintended provider.
5. Confirm a managed provider lock refuses a disallowed selection. Inspect only
   non-secret endpoint/provider metadata; do not record authorization headers.

Offline unit/integration mocks can verify routing, authorization selection,
errors, and native lifecycle without spending provider credits. They cannot prove
that a live account has model entitlement, balance, network access, or complete
Responses tool compatibility. No live provider inference was performed in the
development environment because provider credentials and permitted provider
network access were unavailable. See the final validation report for the actual
build/test outcomes, including any dependency-download blockers.

## Roll back without discarding history

The helper removes the applied feature with ordinary `git revert` commits:

```bash
python3 /path/to/feature-codex/scripts/cross_provider_patchset.py rollback \
  --repo /path/to/target-codex \
  --bundle /tmp/codex-cross-provider-patchset
```

Rollback first requires a clean target whose history is exactly the manifest
base followed by the bundle's ordered commit trees. It refuses later commits or
different content, preserves the applied commits in history, and checks that the
final tree equals the base tree. It does not reset a branch, delete unrelated
work, uninstall an executable, or change user configuration. If follow-up work
exists, preserve it and review selective reverts on a separate branch yourself.

## Reapply after Codex updates

The patchset supports the exact full base ID in its manifest. A matching release
number, nearby upstream commit, identical subset of files, or a clean-looking
patch context is insufficient. The helper deliberately fails on a different
base instead of promising compatibility with future Codex releases.

For a new upstream/fork revision, preserve the working feature branch and make a
new branch for the port. Fetch updates through the verified fork's normal
workflow. Rebase or cherry-pick the feature commits onto the chosen new base,
resolve conflicts, and repeat the source and both-direction live checks. Pay
particular attention to native spawn schemas, child configuration precedence,
provider authentication, managed requirements, role overrides, and cold resume.
Then export a **new** bundle using the new exact base and rerun its apply/verify/
rollback tests on a separate clean worktree. Keep the previous bundle for
installations on the previous base; do not edit its manifest to bypass the base
check.

Do not add dependencies or a provider translation layer merely to silence a
compatibility failure. Determine whether the updated Codex or provider already
supports the required Responses behavior, then port the smallest necessary
change and report any remaining limitation.
