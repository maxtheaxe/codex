# Native cross-provider subagents: review bundle

This branch distributes a numbered patch series for the existing native Codex
subagent lifecycle. It is a review checkpoint, not a release or an installation.
Apply it to a separate worktree at the exact base below; the distribution branch
itself keeps the baseline source and stores the feature as patches.

| Item | Exact value |
| --- | --- |
| Repository | [`maxtheaxe/codex`](https://github.com/maxtheaxe/codex) |
| Source branch | [`feat/cross-provider-subagents`](https://github.com/maxtheaxe/codex/tree/feat/cross-provider-subagents) |
| Source checkpoint | [`cbf93aab403d788eb9ce7905aabe18852376f288`](https://github.com/maxtheaxe/codex/commit/cbf93aab403d788eb9ce7905aabe18852376f288) |
| Exact application base | `3e743d15787bd9091f674f0fbc27010f0cbcec62` |
| Expected applied tree | `f903ce87cedd27f84775836011be60312f3fcf36` |
| Ordered patches | 17 |
| Rust verification | **Pending for this checkpoint** |

The source includes provider selection on native spawn, provider-aware model
metadata, cold-resume preservation, provider-qualified service tiers, focused
tests, documentation, and the packaging helper. Read the
[source documentation at this checkpoint](https://github.com/maxtheaxe/codex/blob/cbf93aab403d788eb9ce7905aabe18852376f288/docs/cross-provider-subagents.md)
for OpenRouter Responses configuration, both parent directions, build/test
commands, role-model constraints, and live compatibility limitations.

## Validation status

The complete 17-patch bundle was exported from committed source, applied to a
clean worktree at the exact base, and separately verified against every ordered
commit tree and patch SHA-256. Its final tree matched the source tree above.
History-preserving rollback then restored the exact base tree
`b9431040f6554743a1f1dcd3c1dfe3c71e1330c6`; the worktree was clean and the applied
commits remained in history.

The packaging helper has 13 passing offline safety tests in the source branch.
Rust build and targeted Rust test results are pending for this published
checkpoint. No live provider inference was performed: provider credentials and
permitted provider network access were unavailable in the development
environment. Published OpenRouter schema support does not prove compatibility of
every routed model, particularly vendor-specific reasoning representations.

## Apply to a new worktree

Requires Python 3.10+ and a current Git supporting `--path-format=absolute` and
`worktree list -z`. Start from this distribution checkout. Replace the absolute
paths and choose unused target/branch names. Keep the copied bundle outside all
worktrees; this is checked by the helper to prevent recursive exports.

```bash
PATCHSET_REPO=/absolute/path/to/codex-patchset
PATCHSET_DIR="$PATCHSET_REPO/patchsets/native-cross-provider"
PATCHSET_TARGET=/absolute/path/to/codex-cross-provider-applied
PATCHSET_BUNDLE=$(mktemp -d /tmp/codex-cross-provider-bundle.XXXXXX)
cp -R "$PATCHSET_DIR/bundle/." "$PATCHSET_BUNDLE/"

git -C "$PATCHSET_REPO" worktree add \
  -b apply/native-cross-provider "$PATCHSET_TARGET" \
  3e743d15787bd9091f674f0fbc27010f0cbcec62

python3 "$PATCHSET_DIR/cross_provider_patchset.py" apply \
  --repo "$PATCHSET_TARGET" --bundle "$PATCHSET_BUNDLE"

python3 "$PATCHSET_DIR/cross_provider_patchset.py" verify \
  --repo "$PATCHSET_TARGET" --bundle "$PATCHSET_BUNDLE"
```

The sibling helper is copied from the pinned source checkpoint. `bundle/`
contains only `manifest.json` and the numbered patch files; keep build output and
receipts elsewhere. The sibling `.gitattributes` preserves exact patch bytes;
single-space blank context lines are part of the patch format. Review the source and patches before application. The
manifest verifies integrity and the exact base, not publisher authenticity.
Application refuses dirty targets, existing Git operations, incompatible bases,
or modified bundles, and attempts `git am --abort` if a patch fails.

## Roll back

With the target still clean and containing exactly the applied series after the
base, run:

```bash
python3 "$PATCHSET_DIR/cross_provider_patchset.py" rollback \
  --repo "$PATCHSET_TARGET" --bundle "$PATCHSET_BUNDLE"
```

Rollback uses ordinary revert commits and preserves the applied history. It
refuses later commits or different content. It does not modify an installed CLI
or user provider configuration.

## Update this bundle

The source branch owns implementation history; this branch owns distribution
checkpoints. Export a new source range outside all worktrees and update the
manifest, numbered patches, sibling helper, and this README in a normal new
commit. Do not export this distribution branch as a feature range, embed its
patches recursively in source, rewrite the manifest base, or claim compatibility
with a different upstream revision. Port and retest against a new exact base
before publishing a bundle for that base.
