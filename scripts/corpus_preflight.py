#!/usr/bin/env python3
"""Offline consistency checks for a static corpus and its deployment manifest.

A matching local manifest proves byte/schema consistency, not authentic git origin.
The corpus directory must be protected from untrusted writers and quiescent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from sync_knowledge import require_quiescent, validate

MAX_MANIFEST_BYTES = 1024 * 1024
SHA256 = re.compile(r'^[0-9a-f]{64}$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
RELEASE_FORMAT = 'exteracontext-knowledge-release-v1'
_CACHE: dict[tuple, dict[str, Any]] = {}


def identity(value: os.stat_result) -> tuple:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def stable_digest(path: Path, expected: tuple) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        opened = identity(os.fstat(stream.fileno()))
        # ctime has platform-dependent path/fstat semantics on Windows.
        if opened[:4] != expected[:4]:
            raise ValueError('corpus changed before manifest hashing')
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        if identity(os.fstat(stream.fileno())) != opened or identity(path.stat()) != expected:
            raise ValueError('corpus changed during manifest hashing')
    return digest.hexdigest()


def verify_base(path: Path, *, full: bool = False) -> dict[str, Any]:
    path = path.expanduser().absolute()
    manifest = path.parent / '.knowledge-manifest.json'
    required = os.environ.get('EXTERACONTEXT_REQUIRE_MANIFEST', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    require_quiescent(path)
    base_stat = path.stat()
    if not stat.S_ISREG(base_stat.st_mode):
        raise ValueError('corpus must be a regular SQLite file')
    base_id = identity(base_stat)
    if manifest.is_symlink():
        raise ValueError('corpus manifest must not be a symlink')
    manifest_stat = manifest.stat() if manifest.exists() else None
    if manifest_stat is not None and not stat.S_ISREG(manifest_stat.st_mode):
        raise ValueError('corpus manifest must be a regular file')
    manifest_id = identity(manifest_stat) if manifest_stat is not None else None
    if manifest_id is None and required:
        raise ValueError(f'corpus manifest required but absent: {manifest}')
    key = (str(path), base_id, manifest_id, required, full)
    if key in _CACHE:
        return dict(_CACHE[key])
    result: dict[str, Any] = {'manifest_status': 'absent-unverified', 'commit_verified': False}
    if manifest_id is None:
        if full:
            validate(path, require_provenance=False)
    else:
        if path.is_symlink():
            raise ValueError('managed corpus must not be a symlink')
        with manifest.open('rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('corpus manifest must be a regular file')
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('corpus manifest exceeds size limit')
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('corpus manifest is not valid UTF-8 JSON') from exc
        if not isinstance(data, dict):
            raise ValueError('corpus manifest must be an object')
        if data.get('format') == 1 and data.get('method') == 'offline-existing-db-copy':
            expected = data.get('destination_sha256')
            if data.get('source_sha256') != expected or data.get('commit_verified') is not False:
                raise ValueError('offline corpus manifest has inconsistent identity/provenance')
            result['method'] = data['method']
        elif data.get('format') == RELEASE_FORMAT:
            expected = data.get('database_sha256')
            commit = data.get('source_commit')
            if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
                raise ValueError('release corpus manifest has invalid source commit')
            if data.get('source_repository') != 'RObotiaga/ExteraContext-Knowledge' or data.get('database_asset') != 'exteracontext.sqlite':
                raise ValueError('release corpus manifest has invalid repository/asset')
            result.update(method=RELEASE_FORMAT, declared_source_commit=commit)
        else:
            raise ValueError('unsupported corpus manifest format')
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise ValueError('corpus manifest has invalid SHA-256')
        if type(data.get('size_bytes')) is not int or data['size_bytes'] != base_stat.st_size:
            raise ValueError('corpus size does not match manifest')
        if stable_digest(path, base_id) != expected:
            raise ValueError('corpus SHA-256 does not match manifest')
        details = validate(path)
        if data.get('schema') != details:
            raise ValueError('corpus schema/counts do not match manifest')
        if data['format'] == RELEASE_FORMAT:
            canonical = json.dumps(details, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            if data.get('schema_sha256') != hashlib.sha256(canonical).hexdigest():
                raise ValueError('corpus manifest schema digest mismatch')
        result['manifest_status'] = 'verified'
        result['manifest_path'] = str(manifest)
    require_quiescent(path)
    if identity(path.stat()) != base_id or (identity(manifest.stat()) if manifest.exists() else None) != manifest_id:
        raise ValueError('corpus or manifest changed during verification')
    # Bound memory if a long-lived CLI process observes successive deployments.
    if len(_CACHE) >= 8:
        _CACHE.clear()
    _CACHE[key] = result
    return dict(result)
