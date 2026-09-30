"""Check Git objects before publishing; never inspect local data or print secrets."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys


PUBLIC_MARKDOWN = {
    "README.md", "ARCHITECTURE.md", "extension/README.md",
    "docs/development.md", "docs/manual-qa.md", "docs/privacy.md",
}
ROOT_FILES = {
    ".gitignore", ".gitattributes", "LICENSE", "pyproject.toml",
    "alembic.ini", "一键启动.bat", ".githooks/pre-push",
}
SOURCE_DIRS = {"app", "extension", "migrations", "scripts", "tests"}
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".css", ".html", ".ps1", ".mako"}
PUBLIC_ASSETS = {"extension/manifest.json", *(
    f"extension/icons/icon{size}.png" for size in (16, 48, 128)
)}
PRIVATE_PARTS = {"local", "private", "data", "prompts", "backups", "exports",
                 "logs", "output", ".tmp", ".venv", "node_modules", "__pycache__"}
SECRET_RULES = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "API key": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}"),
    "AWS access key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    "personal Windows path": re.compile(rb"[A-Za-z]:[/\\](?:Users|users)[/\\][^/\\\s]+"),
    "personal QQ address": re.compile(rb"\b[1-9][0-9]{5,11}@qq\.com\b", re.I),
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def public_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if any(part.lower() in PRIVATE_PARTS for part in parts):
        return False
    if path in ROOT_FILES | PUBLIC_MARKDOWN | PUBLIC_ASSETS:
        return True
    return bool(parts and parts[0] in SOURCE_DIRS and PurePosixPath(path).suffix in SOURCE_SUFFIXES)


def staged_objects() -> set[tuple[str, str, str]]:
    entries = set()
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        if stage != "0":
            raise ValueError("Resolve merge conflicts before publication")
        entries.add((name.decode("utf-8"), oid, mode))
    return entries


def history_objects(ref: str) -> set[tuple[str, str, str]]:
    entries = set()
    commits = git("rev-list", ref, "--").decode().splitlines()
    if not commits:
        raise ValueError("Publication ref must resolve to commit history")
    for commit in commits:
        for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            entries.add((name.decode("utf-8"), oid, mode))
    return entries


def check(entries: set[tuple[str, str, str]]) -> list[str]:
    problems = []
    checked = {}
    for path, oid, mode in sorted(entries):
        if not public_path(path):
            problems.append(f"{path}: outside public file allowlist")
            continue
        if mode not in {"100644", "100755"}:
            problems.append(f"{path}: symlinks and submodules require separate review")
            continue
        if oid not in checked:
            content = git("cat-file", "blob", oid)
            rules = [name for name, pattern in SECRET_RULES.items() if pattern.search(content)]
            if len(content) > 2_000_000:
                rules.append("oversized artifact")
            if content.startswith(b"SQLite format 3"):
                rules.append("database content")
            checked[oid] = rules
        problems.extend(f"{path}: {rule}" for rule in checked[oid])
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--ref", help="Check every file in the complete reachable history")
    group.add_argument("--pre-push", action="store_true")
    args = parser.parse_args()
    try:
        if args.staged:
            entries = staged_objects()
        elif args.ref:
            entries = history_objects(args.ref)
        else:
            entries = set()
            for line in sys.stdin:
                _local_ref, local_sha, _remote_ref, _remote_sha = line.split()
                if set(local_sha) != {"0"}:
                    entries.update(history_objects(local_sha))
        problems = check(entries)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Publication check could not complete: {type(error).__name__}", file=sys.stderr)
        return 2
    if problems:
        print("Publication blocked; review these paths (matched values are hidden):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Publication check passed: {len(entries)} Git file objects; no blocked paths or known secret patterns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
