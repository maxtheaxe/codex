#!/usr/bin/env python3
"""Export, apply, verify, or revert an exact-base Codex feature patch series.

Requires Python 3.10+ and Git. Generated bundles belong outside the source tree.
The manifest checks integrity and compatibility, not publisher authenticity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


SCHEMA = 1
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PatchsetError(Exception):
    """A compatibility or Git operation failed without silently forcing it."""


def git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    # Do not execute repository hooks or trigger signing dialogs while applying.
    command = [
        "git",
        "-C",
        str(repo),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "am.threeWay=false",
        "-c",
        "rerere.enabled=false",
        *args,
    ]
    result = subprocess.run(command, input=input_data, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise PatchsetError(f"git {' '.join(args[:2])} failed: {detail}")
    return result.stdout


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).decode().strip()


def resolve(repo: Path, revision: str, kind: str = "commit") -> str:
    # --end-of-options prevents a revision supplied by a manifest becoming an option.
    return git_text(
        repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{{kind}}}"
    )


def repository(path: Path) -> Path:
    return Path(git_text(path.resolve(), "rev-parse", "--show-toplevel")).resolve()


def require_clean(repo: Path) -> None:
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise PatchsetError(
            "target must be clean, including untracked files; save your work first"
        )
    for state in (
        "rebase-apply",
        "rebase-merge",
        "sequencer",
        "MERGE_HEAD",
        "REVERT_HEAD",
        "CHERRY_PICK_HEAD",
    ):
        state_path = Path(git_text(repo, "rev-parse", "--git-path", state))
        if not state_path.is_absolute():
            state_path = repo / state_path
        if state_path.exists():
            raise PatchsetError(
                f"finish or abort the existing Git operation ({state}) first"
            )


def linear_commits(repo: Path, base: str, head: str) -> list[str]:
    git(repo, "merge-base", "--is-ancestor", base, head)
    commits = git_text(repo, "rev-list", "--reverse", f"{base}..{head}").splitlines()
    previous = base
    for commit in commits:
        parents = git_text(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if parents != [commit, previous]:
            raise PatchsetError("patch series must be linear without merge commits")
        previous = commit
    return commits


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def outside_repo(path: Path, repo: Path) -> None:
    # Worktrees share object storage. Refuse output in any checkout or Git internals,
    # including when --repo points to a linked worktree rather than the main checkout.
    roots = [
        repo,
        Path(
            git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        ).resolve(),
    ]
    for field in git(repo, "worktree", "list", "--porcelain", "-z").split(b"\0"):
        if field.startswith(b"worktree "):
            roots.append(Path(field[len(b"worktree ") :].decode()).resolve())
    if any(path == root or root in path.parents for root in roots):
        raise PatchsetError(
            "bundle must be outside all repository worktrees and Git internals to avoid recursive patch exports"
        )


def export_bundle(
    repo: Path, base_revision: str, head_revision: str, output: Path
) -> None:
    base = resolve(repo, base_revision)
    head = resolve(repo, head_revision)
    commits = linear_commits(repo, base, head)
    if not commits:
        raise PatchsetError("base..head contains no feature commits")
    output = output.resolve()
    outside_repo(output, repo)
    if output.exists():
        raise PatchsetError("output already exists; choose a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".patchset-", dir=output.parent))
    try:
        entries = []
        previous_tree = resolve(repo, base, "tree")
        for index, commit in enumerate(commits, 1):
            tree = resolve(repo, commit, "tree")
            if tree == previous_tree:
                raise PatchsetError(
                    f"empty commit {commit} cannot be applied by this helper"
                )
            name = f"{index:04d}-{commit}.patch"
            patch = git(
                repo,
                "format-patch",
                "-1",
                "--stdout",
                "--binary",
                "--full-index",
                "--no-renames",
                "--no-stat",
                "--no-signature",
                "--no-cover-letter",
                "--no-notes",
                "--no-thread",
                "--subject-prefix=PATCH",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--diff-algorithm=myers",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                commit,
            )
            (staging / name).write_bytes(patch)
            entries.append(
                {"file": name, "commit": commit, "tree": tree, "sha256": sha256(patch)}
            )
            previous_tree = tree
        manifest = {
            "schema": SCHEMA,
            "project": "maxtheaxe/codex",
            "base": base,
            "base_tree": resolve(repo, base, "tree"),
            "source_head": head,
            "source_tree": resolve(repo, head, "tree"),
            "patches": entries,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Exported {len(commits)} patches; exact base {base}; bundle {output}")


def load_bundle(bundle: Path) -> tuple[dict, list[Path]]:
    bundle = bundle.resolve()
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink():
        raise PatchsetError("manifest must be a regular file, not a symlink")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        raise PatchsetError(f"cannot read manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise PatchsetError("unsupported patchset manifest schema")
    for key in ("base", "base_tree", "source_head", "source_tree"):
        if not isinstance(manifest.get(key), str) or not OBJECT_ID.fullmatch(
            manifest[key]
        ):
            raise PatchsetError(f"manifest {key} must be a full Git object ID")
    entries = manifest.get("patches")
    if not isinstance(entries, list) or not entries:
        raise PatchsetError("manifest must contain an ordered nonempty patches array")
    paths = []
    commits = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise PatchsetError("invalid patch entry")
        commit = entry.get("commit")
        tree = entry.get("tree")
        digest = entry.get("sha256")
        if (
            not isinstance(commit, str)
            or not OBJECT_ID.fullmatch(commit)
            or commit in commits
        ):
            raise PatchsetError("patch commit IDs must be unique full Git object IDs")
        if not isinstance(tree, str) or not OBJECT_ID.fullmatch(tree):
            raise PatchsetError("patch tree must be a full Git object ID")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise PatchsetError("patch digest must be SHA-256")
        expected_name = f"{index:04d}-{commit}.patch"
        if entry.get("file") != expected_name:
            raise PatchsetError("patch filename/order does not match manifest")
        path = bundle / expected_name
        if path.is_symlink() or not path.is_file():
            raise PatchsetError(
                f"patch is missing or is not a regular file: {expected_name}"
            )
        if sha256(path.read_bytes()) != digest:
            raise PatchsetError(f"patch integrity check failed: {expected_name}")
        paths.append(path)
        commits.add(commit)
    if (
        entries[-1]["commit"] != manifest["source_head"]
        or entries[-1]["tree"] != manifest["source_tree"]
    ):
        raise PatchsetError("source head/tree does not match final patch")
    expected_files = {"manifest.json", *(path.name for path in paths)}
    if {path.name for path in bundle.iterdir()} != expected_files:
        raise PatchsetError(
            "bundle contains unexpected files; keep receipts/build output elsewhere"
        )
    return manifest, paths


def check_base(repo: Path, manifest: dict) -> None:
    try:
        actual_tree = resolve(repo, manifest["base"], "tree")
    except PatchsetError as error:
        raise PatchsetError(
            f"exact base {manifest['base']} is not available in target"
        ) from error
    if actual_tree != manifest["base_tree"]:
        raise PatchsetError("base tree differs from manifest")


def verify_applied(repo: Path, manifest: dict) -> list[str]:
    check_base(repo, manifest)
    commits = linear_commits(repo, manifest["base"], resolve(repo, "HEAD"))
    entries = manifest["patches"]
    if len(commits) != len(entries):
        raise PatchsetError(
            "target must contain exactly the bundle commits after its base; later commits are refused"
        )
    for commit, entry in zip(commits, entries):
        if resolve(repo, commit, "tree") != entry["tree"]:
            raise PatchsetError(
                f"applied content differs from expected tree at {commit}"
            )
    return commits


def apply_bundle(repo: Path, manifest: dict, paths: list[Path]) -> None:
    require_clean(repo)
    check_base(repo, manifest)
    if resolve(repo, "HEAD") != manifest["base"]:
        raise PatchsetError(
            f"incompatible target HEAD; check out exact base {manifest['base']} on a new branch"
        )
    try:
        git(repo, "am", "--no-3way", "--no-rerere-autoupdate", *map(str, paths))
    except PatchsetError as error:
        try:
            git(repo, "am", "--abort")
        except PatchsetError as abort_error:
            raise PatchsetError(
                f"{error}\nAutomatic abort failed: {abort_error}; inspect target before continuing"
            ) from error
        raise PatchsetError(
            f"{error}\nApplication aborted; original HEAD restored"
        ) from error
    verify_applied(repo, manifest)
    print(f"Applied and verified {len(paths)} patches; HEAD {resolve(repo, 'HEAD')}")


def rollback_bundle(repo: Path, manifest: dict) -> None:
    require_clean(repo)
    commits = verify_applied(repo, manifest)
    try:
        git(repo, "revert", "--no-edit", *reversed(commits))
    except PatchsetError as error:
        try:
            git(repo, "revert", "--abort")
        except PatchsetError as abort_error:
            raise PatchsetError(
                f"{error}\nAutomatic revert abort failed: {abort_error}"
            ) from error
        raise PatchsetError(
            f"{error}\nRevert aborted; original HEAD restored"
        ) from error
    if resolve(repo, "HEAD", "tree") != manifest["base_tree"]:
        raise PatchsetError(
            "rollback completed but tree differs from base; inspect target"
        )
    print(
        f"Reverted {len(commits)} patches; original commits remain in history; HEAD {resolve(repo, 'HEAD')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser(
        "export", help="export a linear feature range outside the source repository"
    )
    export.add_argument("--repo", type=Path, default=Path.cwd())
    export.add_argument(
        "--base", required=True, help="full base commit or revision to resolve"
    )
    export.add_argument("--head", default="HEAD")
    export.add_argument("--output", type=Path, required=True)
    for command in ("apply", "verify", "rollback"):
        action = subparsers.add_parser(command)
        action.add_argument("--repo", type=Path, default=Path.cwd())
        action.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo = repository(args.repo)
        if args.command == "export":
            export_bundle(repo, args.base, args.head, args.output)
        else:
            outside_repo(args.bundle.resolve(), repo)
            manifest, paths = load_bundle(args.bundle)
            if args.command == "apply":
                apply_bundle(repo, manifest, paths)
            elif args.command == "verify":
                require_clean(repo)
                commits = verify_applied(repo, manifest)
                print(
                    f"Verified {len(commits)} ordered commit trees and patch SHA-256 digests"
                )
            else:
                rollback_bundle(repo, manifest)
    except (PatchsetError, OSError) as error:
        print(f"patchset: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
