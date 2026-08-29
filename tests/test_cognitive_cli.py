from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "binggu.py"), *args], cwd=ROOT,
                          env=env, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, timeout=30, check=False)


def test_catchup_cli_is_usable_and_does_not_create_missing_ledger(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["BINGGU_HOME"] = str(tmp_path / "isolated-home")
    for args in (("init",), ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "Test")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, env=env, check=True, capture_output=True)
    ledger = tmp_path / "missing-ledger.sqlite"
    before = hashlib.sha256((repo / "a.txt").read_bytes()).hexdigest()
    proc = _run("--ledger", str(ledger), "catchup", "--repo", str(repo), "--query", "release", "--json",
                env=env)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["current_state"]["clean"] is True
    assert out["safety"]["writes"] == 0
    assert not ledger.exists()
    assert hashlib.sha256((repo / "a.txt").read_bytes()).hexdigest() == before
