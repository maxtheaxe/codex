# Native cross-provider subagents: review bundle

This branch distributes a numbered patch series for the existing native Codex
subagent lifecycle. It is a validated review bundle, not a release or an installation.
Apply it to a separate worktree at the exact base below; the distribution branch
itself keeps the baseline source and stores the feature as patches.

| Item | Exact value |
| --- | --- |
| Repository | [`maxtheaxe/codex`](https://github.com/maxtheaxe/codex) |
| Source branch | [`feat/cross-provider-subagents`](https://github.com/maxtheaxe/codex/tree/feat/cross-provider-subagents) |
| Source checkpoint | [`562c52388265bfd8e7b747173ed2efaa4d063057`](https://github.com/maxtheaxe/codex/commit/562c52388265bfd8e7b747173ed2efaa4d063057) |
| Exact application base | `3e743d15787bd9091f674f0fbc27010f0cbcec62` |
| Expected applied tree | `1d73007681ffcdbd30b665028649b727c4937ce8` |
| Ordered patches | 23 |
| Rust verification | **124 distinct tests passed; 1 existing Linux ignore** |

The source includes provider selection on native spawn, provider-aware model
metadata, cold-resume preservation, provider-qualified service tiers, focused
tests, documentation, and the packaging helper. Read the
[source documentation at this checkpoint](https://github.com/maxtheaxe/codex/blob/562c52388265bfd8e7b747173ed2efaa4d063057/docs/cross-provider-subagents.md)
for OpenRouter Responses configuration, both parent directions, build/test
commands, role-model constraints, and live compatibility limitations.

## Validation status

The complete final 23-patch bundle was exported from committed source, applied to a
clean worktree at the exact base, and separately verified against every ordered
commit tree and patch SHA-256. Its final tree matched the source tree above.
History-preserving rollback then restored the exact base tree
`b9431040f6554743a1f1dcd3c1dfe3c71e1330c6`; the worktree was clean and the applied
commits remained in history.

The selected Rust suites passed **102 unit tests and 22 integration tests**, with
one preexisting Linux sandbox-test ignore and no remaining failures. The
packaging helper's 13 offline safety tests passed. Rust formatting, changed
Python formatting/lint, and documentation formatting passed. The standalone CLI
built successfully, reported development version `codex-cli 0.0.0`, and passed
its `--help` smoke test. The installed `codex-cli 0.166.0-alpha.1` was untouched.
These are focused checks rather than a full repository test run. The
[pinned validation report](https://github.com/maxtheaxe/codex/blob/562c52388265bfd8e7b747173ed2efaa4d063057/docs/cross-provider-validation.md)
records exact suites, environment, existing warning/ignore, and test-fixture
qualification. Its packaging entry describes the earlier 17-patch checkpoint;
the full final 23-patch roundtrip was additionally completed for this bundle.

No live provider inference was performed: provider credentials and
permitted provider network access were unavailable in the development
environment. Published OpenRouter schema support does not prove compatibility of
every routed model, particularly vendor-specific reasoning representations.

Final local roundtrip evidence:

| Step | Commit/tree |
| --- | --- |
| Applied HEAD | `81919d465e9e7b4c66d1861fbc8d5ceca50dfcdf` |
| Applied tree, equal to pinned source tree | `1d73007681ffcdbd30b665028649b727c4937ce8` |
| Revert HEAD | `c5acd92fc09756e93a7ef8fbdca7ba22a0fe73b8` |
| Reverted tree, equal to exact base tree | `b9431040f6554743a1f1dcd3c1dfe3c71e1330c6` |

The verification branch remained local, its final worktree was clean, and the
applied HEAD remained an ancestor after rollback. Commit IDs produced by another
application may differ because committer metadata can differ; verified trees
must match.

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
single-space blank context lines are part of the patch format. Review the source
and patches before application. The
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
