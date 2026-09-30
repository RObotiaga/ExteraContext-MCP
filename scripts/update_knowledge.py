#!/usr/bin/env python3
"""Install the immutable, hash-pinned Knowledge release named by KNOWLEDGE_LOCK.

This updater downloads only data assets from this repository's GitHub release API.
It never clones a repository or executes downloaded code. The tracked lock file is
reviewed through a PR and pins both the manifest and SQLite bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_knowledge  # noqa: E402

REPOSITORY = "RObotiaga/ExteraContext-MCP"
SOURCE_REPOSITORY = "RObotiaga/ExteraContext-Knowledge"
LOCK_DEFAULT = ROOT / "KNOWLEDGE_LOCK"
DB_DEFAULT = ROOT / "data" / "exteracontext.sqlite"
FORMAT = "exteracontext-knowledge-release-v1"
MAX_DB_BYTES = 2 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def schema_digest(schema: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(schema)).hexdigest()


def create_manifest(db: Path, source_commit: str) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source commit must be a full 40-character lowercase Git SHA")
    # Reject unsafe or expensive inputs before SQLite opens or validates them.
    source_stat = db.lstat()
    if stat.S_ISLNK(source_stat.st_mode):
        raise ValueError("candidate database must not be a symlink")
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("candidate database must be a regular file")
    if source_stat.st_size <= 0 or source_stat.st_size > MAX_DB_BYTES:
        raise ValueError("candidate database is empty or exceeds the configured size limit")
    sync_knowledge.require_quiescent(db)
    details = sync_knowledge.validate(db)
    return {
        "format": FORMAT,
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": source_commit,
        "database_asset": "exteracontext.sqlite",
        "database_sha256": sha256_file(db),
        "size_bytes": db.stat().st_size,
        "schema": details,
        "schema_sha256": schema_digest(details),
    }


def verify_manifest(db: Path, manifest_path: Path, expected_commit: str | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("unsupported Knowledge release manifest format")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit) or (expected_commit and commit != expected_commit):
        raise ValueError("manifest source commit does not match locked Knowledge commit")
    if manifest.get("source_repository") != SOURCE_REPOSITORY or manifest.get("database_asset") != "exteracontext.sqlite":
        raise ValueError("manifest source repository or asset name is invalid")
    digest = manifest.get("database_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or sha256_file(db) != digest:
        raise ValueError("database bytes do not match release manifest SHA-256")
    if manifest.get("size_bytes") != db.stat().st_size or db.stat().st_size > MAX_DB_BYTES:
        raise ValueError("database size does not match manifest or exceeds the configured limit")
    details = sync_knowledge.validate(db)
    if manifest.get("schema") != details or manifest.get("schema_sha256") != schema_digest(details):
        raise ValueError("database schema/counts do not match release manifest")
    return manifest


def load_lock(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8").strip()
    # A legacy bare commit pins source only, not a published artifact. Fail closed.
    if COMMIT_RE.fullmatch(raw):
        raise ValueError("KNOWLEDGE_LOCK has no verified artifact hashes; wait for the protected publication PR")
    lock = json.loads(raw)
    required = {
        "format", "source_repository", "source_commit", "artifact_repository", "release_tag",
        "database_asset", "database_sha256", "manifest_asset", "manifest_sha256", "size_bytes",
        "schema_sha256", "counts",
    }
    if not isinstance(lock, dict) or set(lock) != required or lock.get("format") != FORMAT:
        raise ValueError("KNOWLEDGE_LOCK must be a complete version-1 Knowledge artifact lock")
    if lock["source_repository"] != SOURCE_REPOSITORY or lock["artifact_repository"] != REPOSITORY:
        raise ValueError("KNOWLEDGE_LOCK repository identity is invalid")
    commit = lock["source_commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise ValueError("KNOWLEDGE_LOCK source_commit is not a full Git SHA")
    if lock["release_tag"] != f"knowledge-{commit}":
        raise ValueError("Knowledge release tag is not content-addressed by source commit")
    for key in ("database_sha256", "manifest_sha256", "schema_sha256"):
        if not isinstance(lock[key], str) or not SHA256_RE.fullmatch(lock[key]):
            raise ValueError(f"KNOWLEDGE_LOCK {key} is invalid")
    if lock["database_asset"] != "exteracontext.sqlite" or lock["manifest_asset"] != "manifest.json":
        raise ValueError("KNOWLEDGE_LOCK asset names are invalid")
    if not isinstance(lock["size_bytes"], int) or not 0 < lock["size_bytes"] <= MAX_DB_BYTES:
        raise ValueError("KNOWLEDGE_LOCK size_bytes is invalid")
    counts = lock["counts"]
    if not isinstance(counts, dict) or set(counts) != {"docs", "facts", "source_runs", "source_provenance"}:
        raise ValueError("KNOWLEDGE_LOCK counts are invalid")
    return lock


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    _hosts = {"github.com", "api.github.com", "release-assets.githubusercontent.com"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in self._hosts:
            raise urllib.error.HTTPError(newurl, code, "redirect to untrusted host refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener():
    return urllib.request.build_opener(RestrictedRedirect())


CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def content_length(response, limit: int, expected_size: int | None = None) -> int | None:
    raw = response.headers.get("Content-Length")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise ValueError("download has an invalid Content-Length")
    length = int(raw)
    if length > limit:
        raise ValueError("download exceeds configured size limit")
    if expected_size is not None and length != expected_size:
        raise ValueError("download Content-Length does not match tracked KNOWLEDGE_LOCK asset size")
    return length


def read_limited(response, limit: int) -> bytes:
    declared = content_length(response, limit)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(CHUNK_BYTES, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("download exceeds configured size limit")
        chunks.append(chunk)
    if declared is not None and total != declared:
        raise ValueError("download size does not match Content-Length")
    return b"".join(chunks)


def stream_limited(response, destination: Path, *, limit: int, expected_size: int,
                   expected_sha256: str) -> int:
    declared = content_length(response, limit, expected_size)
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        while True:
            # Read at most one MiB, except one extra byte to detect overflow.
            chunk = response.read(min(CHUNK_BYTES, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit or total > expected_size:
                raise ValueError("download exceeds configured size limit or locked asset size")
            digest.update(chunk)
            output.write(chunk)
        if declared is not None and total != declared:
            raise ValueError("download size does not match Content-Length")
        if total != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError("downloaded database asset does not match tracked KNOWLEDGE_LOCK size/hash")
        output.flush()
        os.fsync(output.fileno())
    return total


def validate_asset_metadata(asset: dict[str, Any], *, limit: int,
                            expected_size: int | None = None) -> None:
    size = asset.get("size")
    if size is not None:
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > limit:
            raise ValueError("GitHub release asset size is invalid or exceeds the configured limit")
        if expected_size is not None and size != expected_size:
            raise ValueError("GitHub release asset size does not match KNOWLEDGE_LOCK")


def fetch_release_assets(lock: dict[str, Any], staged_db: Path) -> bytes:
    tag = lock["release_tag"]
    api = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}"
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "ExteraContext-Knowledge-Updater/1"})
    with opener().open(request, timeout=30) as response:
        release = json.loads(read_limited(response, 2 * 1024 * 1024).decode("utf-8"))
    if release.get("tag_name") != tag or release.get("draft") or release.get("prerelease"):
        raise ValueError("published Knowledge release is absent, draft, or prerelease")
    assets = {asset.get("name"): asset for asset in release.get("assets", []) if isinstance(asset, dict)}
    db_asset, manifest_asset = assets.get(lock["database_asset"]), assets.get(lock["manifest_asset"])
    if not db_asset or not manifest_asset:
        raise ValueError("published Knowledge release is missing required assets")
    validate_asset_metadata(db_asset, limit=MAX_DB_BYTES, expected_size=lock["size_bytes"])
    validate_asset_metadata(manifest_asset, limit=MAX_MANIFEST_BYTES)
    for asset, expected in ((db_asset, lock["database_sha256"]), (manifest_asset, lock["manifest_sha256"])):
        api_digest = asset.get("digest")
        if api_digest and api_digest != f"sha256:{expected}":
            raise ValueError("GitHub release asset digest conflicts with KNOWLEDGE_LOCK")
        url = asset.get("browser_download_url", "")
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or f"/{REPOSITORY}/releases/download/{tag}/" not in parsed.path:
            raise ValueError("release asset URL is outside the pinned repository/tag")

    request = urllib.request.Request(db_asset["browser_download_url"], headers={"User-Agent": "ExteraContext-Knowledge-Updater/1"})
    with opener().open(request, timeout=60) as response:
        stream_limited(response, staged_db, limit=MAX_DB_BYTES,
                       expected_size=lock["size_bytes"], expected_sha256=lock["database_sha256"])

    request = urllib.request.Request(manifest_asset["browser_download_url"], headers={"User-Agent": "ExteraContext-Knowledge-Updater/1"})
    with opener().open(request, timeout=60) as response:
        manifest_bytes = read_limited(response, MAX_MANIFEST_BYTES)
    if hashlib.sha256(manifest_bytes).hexdigest() != lock["manifest_sha256"]:
        raise ValueError("downloaded manifest bytes do not match tracked KNOWLEDGE_LOCK hash")
    return manifest_bytes


def _existing_regular_file(path: Path) -> bool:
    """Return whether path exists as a regular file; reject links and special files."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError(f"deployment destination must be a regular file: {path}")
    return True


def _copy_metadata(source: Path, destination: Path, *, source_stat: os.stat_result | None = None) -> None:
    """Copy supported file metadata, failing closed if POSIX ownership is lost.

    shutil.copystat copies mode, timestamps, flags and supported extended
    attributes (including ACL xattrs on platforms that expose them). It does
    not copy Windows per-file ACLs; staged files inherit from the staging
    directory, itself created under the destination parent.
    """
    source_stat = source_stat or source.lstat()
    shutil.copystat(source, destination, follow_symlinks=False)
    if os.name == "posix":
        destination_stat = destination.lstat()
        if (destination_stat.st_uid, destination_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
            try:
                os.chown(destination, source_stat.st_uid, source_stat.st_gid, follow_symlinks=False)
            except (AttributeError, NotImplementedError, OSError) as exc:
                destination_stat = destination.lstat()
                if (destination_stat.st_uid, destination_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
                    raise PermissionError(
                        f"cannot preserve owner/group for staged file before replacement: {source} -> {destination}"
                    ) from exc
            destination_stat = destination.lstat()
            if (destination_stat.st_uid, destination_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
                raise PermissionError(f"owner/group mismatch after metadata copy: {source} -> {destination}")
            # chown can clear setuid/setgid bits and ACL details; recopy supported
            # stat metadata, then restore the original timestamps captured before
            # reading the source bytes.
            shutil.copystat(source, destination, follow_symlinks=False)
    # destination is a regular staging file, so the default follow-symlinks
    # behavior is safe and supported by Windows as well. Callers fsync bytes
    # before reaching this helper, as required for the copy-then-metadata order.
    os.utime(destination, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))


def _copy_fsync(source: Path, destination: Path) -> None:
    """Stream bytes, flush them, then preserve source mode/timestamps/xattrs."""
    source_stat = source.lstat()
    with source.open("rb") as src, destination.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    _copy_metadata(source, destination, source_stat=source_stat)


def _inherit_destination_metadata(staged: Path, destination: Path) -> None:
    """Make an incoming staged file inherit a regular live destination's metadata."""
    if _existing_regular_file(destination):
        _copy_metadata(destination, staged)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory entry durability where the platform supports it."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        # Directory fsync is unavailable on some supported filesystems.
        pass


def deploy(db: Path, lock_path: Path) -> bool:
    lock = load_lock(lock_path)
    db = db.expanduser().absolute()
    manifest_path = db.parent / ".knowledge-manifest.json"
    if db.is_symlink() or manifest_path.is_symlink():
        raise ValueError("database and manifest destinations cannot be symlinks")
    # Refuse to replace a live SQLite database. Immutable validation ignores WAL;
    # deleting/replacing a database with pending transactions would lose data.
    sync_knowledge.require_quiescent(db)
    if db.is_file() and manifest_path.is_file():
        try:
            current = verify_manifest(db, manifest_path, lock["source_commit"])
            if current["database_sha256"] == lock["database_sha256"] and sha256_file(manifest_path) == lock["manifest_sha256"]:
                return False
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
            pass
    db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".knowledge-release-", dir=db.parent) as td:
        stage = Path(td)
        staged_db = stage / lock["database_asset"]
        staged_manifest = stage / lock["manifest_asset"]
        manifest_bytes = fetch_release_assets(lock, staged_db)
        with staged_manifest.open("wb") as output:
            output.write(manifest_bytes)
            output.flush()
            os.fsync(output.fileno())
        manifest = verify_manifest(staged_db, staged_manifest, lock["source_commit"])
        if (manifest["database_sha256"] != lock["database_sha256"] or manifest["size_bytes"] != lock["size_bytes"]
                or manifest["schema_sha256"] != lock["schema_sha256"] or manifest["schema"]["counts"] != lock["counts"]):
            raise ValueError("release manifest conflicts with tracked KNOWLEDGE_LOCK")
        # Validation uses SQLite's immutable read-only mode. Assert that it did
        # not leave a journal/WAL behind before this artifact becomes live.
        sync_knowledge.require_quiescent(staged_db)
        # Recheck immediately before replacement in case a writer opened the
        # deployed database while release assets were downloading/validating.
        sync_knowledge.require_quiescent(db)
        # Back up the currently installed regular files in the same staging directory
        # before the first replace. A rollback copy remains available even if the
        # second replacement fails after the database has already been installed.
        had_db = _existing_regular_file(db)
        had_manifest = _existing_regular_file(manifest_path)
        backup_db = stage / "previous-database"
        backup_manifest = stage / "previous-manifest"
        if had_db:
            _copy_fsync(db, backup_db)
        if had_manifest:
            _copy_fsync(manifest_path, backup_manifest)
        # Set metadata on staged replacements before touching either live file.
        # On POSIX, inability to preserve destination owner/group is therefore
        # reported while the installed database/manifest pair is still intact.
        if had_db:
            _inherit_destination_metadata(staged_db, db)
        if had_manifest:
            _inherit_destination_metadata(staged_manifest, manifest_path)

        # Each replacement is atomic individually, but the database/manifest pair
        # is not atomic. A power loss between replacements can still leave a mismatch;
        # a later AUTO_SYNC run detects it and repairs it from the locked release, or
        # fails closed if the locked release cannot be fetched/verified.
        try:
            staged_db.replace(db)
            staged_manifest.replace(manifest_path)
            _fsync_directory(db.parent)
        except BaseException as replace_error:
            rollback_errors: list[BaseException] = []
            for target, backup, existed in (
                (db, backup_db, had_db),
                (manifest_path, backup_manifest, had_manifest),
            ):
                try:
                    if existed:
                        restore = stage / f"restore-{target.name}"
                        _copy_fsync(backup, restore)
                        restore.replace(target)
                    else:
                        target.unlink(missing_ok=True)
                except BaseException as rollback_error:
                    rollback_errors.append(rollback_error)
            _fsync_directory(db.parent)
            if rollback_errors:
                raise OSError(
                    f"release replacement failed and rollback was incomplete: {rollback_errors[0]}"
                ) from replace_error
            raise
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("EXTERACONTEXT_DB", DB_DEFAULT)))
    parser.add_argument("--lock", type=Path, default=Path(os.environ.get("EXTERACONTEXT_KNOWLEDGE_LOCK", LOCK_DEFAULT)))
    args = parser.parse_args(argv)
    try:
        changed = deploy(args.db, args.lock)
    except (OSError, sqlite3.Error, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        parser.exit(1, f"trusted Knowledge update refused: {exc}\n")
    print(f"Knowledge database {'updated' if changed else 'already current'}: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
