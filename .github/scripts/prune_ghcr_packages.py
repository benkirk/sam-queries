#!/usr/bin/env python3
"""Prune old container versions from GitHub Container Registry (GHCR).

Used by `.github/workflows/clean-ghcr.yaml` to keep the GHCR namespace
small. Retention policy (per package):

    * Untagged versions               → DELETE
    * Tag matches PROTECTED_REGEX     → KEEP (never deleted)
        (default: ^(latest|main|staging|v[0-9].*)$)
    * Tag starts with `sha-`          → KEEP the N most-recent, DELETE rest
        (default N=10)
    * Anything else                   → KEEP (defensive — leave unknown
                                       conventions alone)

Owner / repo default to the GitHub Actions context env vars
(`GITHUB_REPOSITORY_OWNER`, `GITHUB_REPOSITORY`); both can be overridden
on the command line.

Examples
--------
Default (used by the workflow):

    python .github/scripts/prune_ghcr_packages.py

Override packages and retention:

    python .github/scripts/prune_ghcr_packages.py \\
        --package mysql --package webapp --keep 5

Preview without deleting anything:

    python .github/scripts/prune_ghcr_packages.py --dry-run

Authentication: relies on `gh api`, which reads `GH_TOKEN` from the
environment. The workflow sets `GH_TOKEN` to a token that has
`packages: write` scope.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse


PROTECTED_REGEX = re.compile(r"^(latest|main|staging|v[0-9].*)$")


def fetch_versions(pkg_encoded: str, owner: str) -> list[dict]:
    """Return all versions for a single GHCR package (paginated)."""
    cmd = [
        "gh", "api", "--paginate",
        f"users/{owner}/packages/container/{pkg_encoded}/versions",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Error fetching {pkg_encoded}: {exc.stderr}", file=sys.stderr)
        return []

    if not res.stdout.strip():
        return []
    return json.loads(res.stdout.strip())


def delete_version(pkg_encoded: str, owner: str, version_id: int,
                   dry_run: bool = False) -> bool:
    """Delete a single GHCR package version. Returns True on success."""
    if dry_run:
        return True
    cmd = [
        "gh", "api", "-X", "DELETE",
        f"users/{owner}/packages/container/{pkg_encoded}/versions/{version_id}",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Failed to delete {version_id}: {exc.stderr}", file=sys.stderr)
        return False


def prune_package(pkg: str, owner: str, keep_sha_count: int,
                  dry_run: bool) -> int:
    """Apply the retention policy to one package; return delete count."""
    print("\n" + "=" * 49)
    print(f"Processing package: {pkg}")
    pkg_encoded = urllib.parse.quote(pkg, safe="")

    versions = fetch_versions(pkg_encoded, owner)
    print(f"Found {len(versions)} versions.")

    deleted = 0
    sha_kept = 0
    for v in versions:
        v_id = v["id"]
        tags = v.get("metadata", {}).get("container", {}).get("tags", [])

        if not tags:
            print(f"Deleting {v_id} (untagged)")
            if delete_version(pkg_encoded, owner, v_id, dry_run):
                deleted += 1
            continue

        if any(PROTECTED_REGEX.match(t) for t in tags):
            print(f"Keeping  {v_id} (protected: {tags})")
            continue

        if any(t.startswith("sha-") for t in tags):
            if sha_kept < keep_sha_count:
                print(f"Keeping  {v_id} (recent SHA: {tags})")
                sha_kept += 1
            else:
                print(f"Deleting {v_id} (old SHA: {tags})")
                if delete_version(pkg_encoded, owner, v_id, dry_run):
                    deleted += 1
            continue

        # Defensive default: anything we don't explicitly recognise stays.
        print(f"Keeping  {v_id} (unknown tags: {tags})")

    return deleted


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="prune_ghcr_packages.py",
        description="Prune old GHCR package versions per the retention "
                    "policy documented in this script's docstring.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER"),
        help="GHCR namespace owner (default: $GITHUB_REPOSITORY_OWNER).",
    )
    repo_default = os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1] or None
    p.add_argument(
        "--repo", default=repo_default,
        help="Repository name (default: parsed from $GITHUB_REPOSITORY).",
    )
    p.add_argument(
        "--package", action="append", dest="packages", metavar="NAME",
        help="Package name relative to --repo (e.g. 'webapp'). Repeat to "
             "specify multiple. Defaults to: mysql, webapp, collectors.",
    )
    p.add_argument(
        "--keep", type=int, default=10, metavar="N",
        help="Number of recent sha-* tagged versions to retain per package "
             "(default: 10).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not args.owner:
        print("--owner not set and $GITHUB_REPOSITORY_OWNER is empty",
              file=sys.stderr)
        return 2
    if not args.repo:
        print("--repo not set and $GITHUB_REPOSITORY is empty",
              file=sys.stderr)
        return 2

    package_names = args.packages or ["mysql", "webapp", "collectors"]
    packages = [f"{args.repo}/{name}" for name in package_names]

    if args.dry_run:
        print("[DRY RUN] No deletions will be performed.")

    total = 0
    for pkg in packages:
        total += prune_package(pkg, args.owner, args.keep, args.dry_run)

    verb = "would have been deleted" if args.dry_run else "deleted"
    print(f"\nCleanup complete. Total versions {verb}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
