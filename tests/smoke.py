#!/usr/bin/env python3
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "scripts" / "query.py"

def run(*args: str) -> str:
    return subprocess.check_output([sys.executable, str(Q), *args], text=True, encoding="utf-8")


doctor = json.loads(run("doctor"))
assert doctor["facts"] >= 2000, doctor
assert doctor["docs"] >= 100, doctor

ctx = run("context", "перехватить исходящее сообщение и сохранить account", "--limit", "8")
assert "ExteraContext task packet" in ctx
assert "account" in ctx.lower() or "аккаунт" in ctx.lower()
assert "hook" in ctx.lower() or "перехват" in ctx.lower()

api = run("api", "send_request")
assert "send_request" in api

recipe = run("recipe", "reload hooks cleanup", "--limit", "2")
assert "reload" in recipe.lower() or "hook" in recipe.lower() or "выгруз" in recipe.lower()

print("smoke: ok")
