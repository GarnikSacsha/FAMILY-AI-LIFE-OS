"""Print a local inventory without changing files, Git refs, or services.

Only allowlisted source/build files are hashed. Credentials, environment files,
diagnostic contents, caches, and database files are never read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ROOTS = ("app", "alembic")
ROOT_FILES = ("Dockerfile", "main.py", "pyproject.toml", "railway.toml", "alembic.ini")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed git executable; no shell
        ["git", "--no-optional-locks", "-c", "core.quotePath=false", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def source_hashes(directory: Path) -> dict[str, str]:
    files = [directory / name for name in ROOT_FILES]
    for name in SOURCE_ROOTS:
        source = directory / name
        if source.is_dir():
            files.extend(
                path
                for path in source.rglob("*")
                if path.suffix in {".py", ".mako"}
                and "__pycache__" not in path.parts
                and not path.is_symlink()
                and path.resolve().is_relative_to(directory.resolve())
            )
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in sorted(set(files))
        if path.is_file() and not path.is_symlink()
    }


def worktrees(repo: Path) -> list[dict[str, str]]:
    result = git(repo, "worktree", "list", "--porcelain", "-z")
    if result.returncode:
        raise RuntimeError("Unable to list worktrees; no changes made.")
    records = []
    record: dict[str, str] = {}
    for entry in result.stdout.split("\0"):
        if not entry:
            if record:
                records.append(record)
                record = {}
        else:
            key, _, value = entry.partition(" ")
            record[key] = value
    if record:
        records.append(record)
    return records


def status_entries(repo: Path) -> tuple[list[dict[str, str]], bool]:
    result = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode:
        raise RuntimeError("Unable to read Git status; no changes made.")
    entries = []
    parts = iter(result.stdout.split("\0"))
    for part in parts:
        if not part:
            continue
        entry = {"status": part[:2], "path": part[3:]}
        if "R" in part[:2] or "C" in part[:2]:
            entry["original_path"] = next(parts, "")
        entries.append(entry)
    return entries, bool(result.stderr.strip())


def inventory(repo: Path) -> dict:
    top = git(repo, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != repo:
        raise RuntimeError("Pass the actual repository root with --repository.")
    registered = worktrees(repo)
    by_path = {Path(row["worktree"]).resolve(): row for row in registered}
    main = next((Path(row["worktree"]).resolve() for row in registered if row.get("branch") == "refs/heads/main"), None)
    candidates = {path.resolve() for path in repo.parent.glob("FAMILY*") if path.is_dir()}
    candidates.update(by_path)
    baseline = source_hashes(main) if main is not None and main.is_dir() else None
    directories = []
    for path in sorted(candidates, key=str):
        hashes = source_hashes(path)
        item: dict = {"path": str(path), "runtime_file_count": len(hashes), "source_sha256_lf": hashes}
        metadata = by_path.get(path)
        if metadata is not None:
            entries, warnings = status_entries(path)
            item.update(
                kind="git_worktree",
                head=metadata.get("HEAD"),
                branch=metadata.get("branch"),
                detached="detached" in metadata,
                changes=entries,
                status_has_warnings=warnings,
            )
        elif (path / ".git").exists():
            item["kind"] = "other_git_directory_unclassified"
        else:
            item["kind"] = "source_snapshot" if hashes else "non_runtime_directory"
        if baseline is not None:
            item["compared_to_local_main"] = {
                "added": sorted(hashes.keys() - baseline.keys()),
                "missing": sorted(baseline.keys() - hashes.keys()),
                "different": sorted(name for name in hashes.keys() & baseline.keys() if hashes[name] != baseline[name]),
            }
        directories.append(item)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(repo),
        "local_main_worktree": str(main) if main is not None else None,
        "scope": "Local source/build files and Git metadata only; no fetch or live service access.",
        "limitations": [
            "Local main and origin refs do not establish the deployed version.",
            "Hashes normalize CRLF to LF and exclude tests, documents, secrets, and outputs.",
            "Git warnings can indicate inaccessible untracked directories; those are not safe to delete.",
        ],
        "directories": directories,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(inventory(args.repository.resolve()), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
