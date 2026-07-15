# -*- coding: utf-8 -*-
"""BingguPack adapter — 공개 CLI(`python binggu.py <cmd>`)만으로 op 을 실행/관찰한다.

실측 기반(2026-07-15):
  · write op(save/deprecate/pair) 은 `CLAUDECODE` env 가 set 이면 actor=reader 로 BLOCK 된다.
    → benchmark 는 CLAUDECODE 를 제거한 subprocess 로 실행해야 사람 게이트가 통과한다.
  · 격리는 `BINGGU_HOME` env 로만(--home 은 demo 전용).
  · `--json` 은 home/inbox/index status 3개뿐 → 카운트·audit 는 `home --json` 으로 얻는다.
  · save 흐름: `preview <text>` → preview_id 파싱 → `save <text> --preview-id <id> --pick n --confirm "SAVE n"`.
  · 전체 node_id 는 save 의 OK dict(node_ids)에서만 얻는다(list=id8·recall=claim).
  · save/deprecate/replace 의 성공 라인 "OK: {...}" 는 Python dict repr(비 JSON) 이라 ast 로 판다.
adapter 는 verdict 를 계산하지 않는다 — 관찰 자료(exit·구조화 state·stdout)만 반환한다.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from benchmark.adapters.base import HomeHandle
from benchmark.contracts import Cap, Observation, parse_block_code

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BINGGU = os.path.join(_ROOT, "binggu.py")
_PREVIEW_ID_RE = re.compile(r"preview_id[:\s]+([0-9a-f]{8})", re.IGNORECASE)


def _fingerprint(path: str) -> dict:
    """한 경로의 사후 오염 감지 fingerprint — 존재·symlink·realpath·type·size·mtime_ns·sha256.
    before 에 없던 파일이 after 에 생기거나(예: WAL/SHM) 사라져도 dict 비교에서 변경으로 드러난다."""
    is_link = os.path.islink(path)
    if not (os.path.exists(path) or is_link):
        return {"path": path, "exists": False}
    real = os.path.realpath(path)
    if not os.path.isfile(path):
        return {"path": path, "exists": True, "is_symlink": is_link,
                "is_dir": os.path.isdir(path), "realpath": real}
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return {"path": path, "exists": True, "is_symlink": is_link, "realpath": real,
            "size": st.st_size, "mtime_ns": st.st_mtime_ns, "digest": h.hexdigest()}


def _parse_preview_id(stdout: str) -> str | None:
    m = _PREVIEW_ID_RE.search(stdout or "")
    return m.group(1) if m else None


def _parse_ok_dict(stdout: str) -> dict:
    """'OK: {...}' 형태의 Python dict repr 를 안전하게 파싱(ast.literal_eval, JSON 아님)."""
    for line in (stdout or "").splitlines():
        s = line.strip()
        if s.startswith("OK:"):
            frag = s[3:].strip()
            if frag.startswith("{"):
                try:
                    v = ast.literal_eval(frag)
                    if isinstance(v, dict):
                        return v
                except (ValueError, SyntaxError):
                    return {}
    return {}


_LIST_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*([0-9a-f]{8})\s*\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|", re.MULTILINE)


def _parse_list_row(stdout: str, n: int) -> dict | None:
    """`binggu list` 마크다운 표에서 순번 n 의 행(id8·kind·state) 파싱.
    deprecate/replace 가 요구하는 id8 은 node_id 끝 세그먼트가 아니라 node_id8() 표시해시라
    공개 CLI(list) 로만 얻는다(black-box 원칙 — 내부 함수 직접호출 금지)."""
    for m in _LIST_ROW_RE.finditer(stdout or ""):
        if int(m.group(1)) == n:
            return {"id8": m.group(2), "kind": m.group(3).strip(), "state": m.group(4)}
    return None


class BingguPackAdapter:
    name = "binggupack"

    # 관찰 대상 운영 sentinel 집합 — HOME 전체 불변 주장이 아니라 'observed operational sentinel set'.
    # BingguPack 은 WAL 모드라 rollback journal(-journal)이 평시 부재(before/after 둘 다 exists:False →
    # 오탐 0)지만, journal 모드로 전환·복구되며 생기는 write 를 놓치지 않도록 집합에 포함한다(issue #54.2).
    _SENTINEL_NAMES = ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm",
                       "ledger.sqlite-journal", "approvals.jsonl")

    def capabilities(self) -> set[str]:
        # INTEGRITY_PUBLIC·STALE_FRESHNESS 는 의도적 미포함 → MGB-10·MGB-03 은 공개 CLI 로 독립 검증
        # 불가(UNSUPPORTED). save preview_id 는 텍스트 해시 결속(내용)만 검증하고, 시간·상태 신선도
        # 만료는 공개 CLI 로 결정적 재현할 수 없다(sleep 기반 flaky 금지).
        return {
            Cap.INIT, Cap.PREVIEW, Cap.SAVE, Cap.LIST_ACTIVE, Cap.RECALL, Cap.RECALL_FRESH,
            Cap.EXPLAIN, Cap.SUPERSEDE, Cap.PAIR, Cap.REMOTE_INTENT, Cap.CAPTURE_CANDIDATE,
            Cap.UNAUTHORIZED_WRITE, Cap.EXACT_BINDING, Cap.REPLAY_APPROVAL,
        }

    # ── 격리 홈 · 운영 정본 fingerprint ──
    def new_home(self, root: str) -> HomeHandle:
        home = tempfile.mkdtemp(prefix="mgb_bgp_", dir=root)
        return HomeHandle(root=os.path.realpath(home), adapter_name=self.name, meta={})

    def cleanup(self, home: HomeHandle) -> None:
        shutil.rmtree(home.root, ignore_errors=True)

    def operating_home(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".binggupack")

    def operating_ledger_path(self) -> str:
        # BINGGU_HOME 를 지정하지 않은 기본 운영 경로(실측: ~/.binggupack/ledger.sqlite).
        return os.path.join(self.operating_home(), "ledger.sqlite")

    def operating_fingerprint(self) -> dict | None:
        # ledger 단일 파일이 아니라 sentinel 집합(WAL/SHM/approvals 포함) — WAL 모드에서 main 파일만
        # 불변인 오염을 놓치지 않는다. 각 파일 before/after dict 비교로 신규 생성·삭제·변경을 감지.
        home = self.operating_home()
        return {name: _fingerprint(os.path.join(home, name)) for name in self._SENTINEL_NAMES}

    # ── 공개 CLI 실행 ──
    def _run(self, home: HomeHandle, args: list[str], *, keep_claudecode: bool = False,
             timeout: int = 120):
        env = dict(os.environ)
        if keep_claudecode:
            env["CLAUDECODE"] = "1"     # 비승인 경로 재현(agent 컨텍스트) — actor=reader
        else:
            env.pop("CLAUDECODE", None)  # 사람 게이트 통과(순수 터미널 컨텍스트)
        env["BINGGU_HOME"] = home.root
        env["PYTHONUTF8"] = "1"
        cmd = [sys.executable, _BINGGU] + args
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=_ROOT, timeout=timeout)
        return cmd, p

    def _home_json(self, home: HomeHandle) -> dict:
        _, p = self._run(home, ["home", "--json"])
        try:
            return json.loads(p.stdout)
        except (ValueError, json.JSONDecodeError):
            return {}

    def _ledger_state(self, home: HomeHandle) -> dict:
        hj = self._home_json(home)
        led = hj.get("ledger", {}) if isinstance(hj, dict) else {}
        return {
            "active_count": led.get("active"),
            "deprecated_count": led.get("deprecated"),
            "audit": led.get("audit"),
        }

    def _do_save(self, home: HomeHandle, text: str, pick: int = 1, *, keep_claudecode=False,
                 override_preview_id: str | None = None):
        _, pv = self._run(home, ["preview", text])
        pid = override_preview_id or _parse_preview_id(pv.stdout)
        cmd, sv = self._run(
            home, ["save", text, "--preview-id", pid or "", "--pick", str(pick),
                   "--confirm", "SAVE %d" % pick], keep_claudecode=keep_claudecode)
        return cmd, sv, pid

    # ── op 관찰 ──
    def observe(self, home: HomeHandle, op: str, **kw) -> Observation:
        if op == Cap.INIT:
            cmd, p = self._run(home, ["init"])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state=self._ledger_state(home))

        if op == Cap.PREVIEW:
            cmd, p = self._run(home, ["preview", kw["text"]])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state={"preview_id": _parse_preview_id(p.stdout)})

        if op in (Cap.SAVE,):
            cmd, sv, pid = self._do_save(home, kw["text"], kw.get("pick", 1))
            ok = _parse_ok_dict(sv.stdout)
            st = self._ledger_state(home)
            st.update({"preview_id": pid, "node_ids": ok.get("node_ids", []),
                       "saved": ok.get("saved")})
            return Observation(op, cmd, sv.returncode, sv.stdout, sv.stderr,
                               artifacts_created=int(ok.get("saved") or 0), state=st)

        if op == Cap.LIST_ACTIVE:
            cmd, p = self._run(home, ["list"])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state=self._ledger_state(home))

        if op in (Cap.RECALL, Cap.RECALL_FRESH):
            # recall CLI 는 항상 새 프로세스(subprocess)로 실행되므로 fresh-process 회상 자체다.
            cmd, p = self._run(home, ["recall", kw["query"]])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state={"stdout_has_query_hits": bool(p.stdout.strip())})

        if op == Cap.EXPLAIN:
            cmd, p = self._run(home, ["explain", kw["node_id"]])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state={"node_id": kw["node_id"]})

        if op == Cap.SUPERSEDE:
            n = kw.get("n", 1)
            _, lp = self._run(home, ["list", "--status", "all"])
            row = _parse_list_row(lp.stdout, n)
            id8 = row["id8"] if row else ""
            cmd, p = self._run(home, ["deprecate", str(n), id8, "--reason", "mgb",
                                      "--confirm", "DEPRECATE %d %s" % (n, id8)])
            _, lp2 = self._run(home, ["list", "--status", "all"])
            row2 = _parse_list_row(lp2.stdout, n)
            st = self._ledger_state(home)
            st.update({"target_id8": id8,
                       "target_state_after": row2["state"] if row2 else None,
                       "target_present_after": row2 is not None})  # 물리삭제 0(이력 보존) 검증용
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr, state=st)

        if op == Cap.PAIR:
            cmd, p = self._run(home, ["pair", kw["owner_text"], kw["ai_text"],
                                      "--relation", "accepts", "--by", "ai",
                                      "--confirm", "PAIR ai_accepts owner:1 ai:1"])
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state=self._ledger_state(home))

        if op == Cap.REMOTE_INTENT:
            before = self._ledger_state(home).get("active_count")
            cmd, p = self._run(home, ["hosted", "inbox", "--no-fetch"])
            after = self._ledger_state(home).get("active_count")
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state={"active_before": before, "active_after": after})

        if op == Cap.CAPTURE_CANDIDATE:
            active = self._ledger_state(home).get("active_count")
            cmd, p = self._run(home, ["capture", "status"])
            m = re.search(r"버퍼 후보[:\s]+(\d+)", p.stdout or "")
            return Observation(op, cmd, p.returncode, p.stdout, p.stderr,
                               state={"active_count": active,
                                      "candidate_count": int(m.group(1)) if m else None})

        if op == Cap.UNAUTHORIZED_WRITE:
            before = self._ledger_state(home).get("active_count")
            cmd, sv, pid = self._do_save(home, kw["text"], kw.get("pick", 1),
                                         keep_claudecode=True)  # agent 컨텍스트 재현
            after = self._ledger_state(home).get("active_count")
            return Observation(op, cmd, sv.returncode, sv.stdout, sv.stderr,
                               state={"active_before": before, "active_after": after,
                                      "preview_id": pid})

        if op == Cap.EXACT_BINDING:
            # baseline(제어군): 유효 preview 로 정상 저장이 성공함을 먼저 확인 → 빈 토큰·인자오류로 인한
            # exit1 을 binding 거부로 오인하는 우연통과를 배제한다.
            text_a, text_b = kw["text_a"], kw["text_b"]
            _, pva = self._run(home, ["preview", text_a])
            pid_a = _parse_preview_id(pva.stdout)
            active_before = self._ledger_state(home).get("active_count")
            _, base = self._run(home, ["save", text_a, "--preview-id", pid_a or "",
                                       "--pick", "1", "--confirm", "SAVE 1"])
            active_after_base = self._ledger_state(home).get("active_count")
            # mutation: 동일 preview_id 유지 + 내용만 text_b 로 변조 → 내용 결속 불일치로 거부돼야.
            cmd, mut = self._run(home, ["save", text_b, "--preview-id", pid_a or "",
                                        "--pick", "1", "--confirm", "SAVE 1"])
            active_after_mut = self._ledger_state(home).get("active_count")
            _, lp = self._run(home, ["list", "--status", "all"])
            b_present = text_b[:20] in (lp.stdout or "")  # 변조 내용이 장부에 생성됐는지(digest 유무)
            return Observation(op, cmd, mut.returncode, mut.stdout, mut.stderr, state={
                "preview_id_valid": bool(pid_a),
                "baseline_exit": base.returncode,
                "active_before": active_before,
                "active_after_baseline": active_after_base,
                "mutation_exit": mut.returncode,
                "mutation_error_code": parse_block_code(mut.stdout),
                "active_after_mutation": active_after_mut,
                "mutation_digest_present": b_present})

        if op == Cap.REPLAY_APPROVAL:
            # 동일 preview_id+pick 을 2회 저장 시도 — 2회차의 결과를 관찰.
            _, pv = self._run(home, ["preview", kw["text"]])
            pid = _parse_preview_id(pv.stdout)
            _, first = self._run(home, ["save", kw["text"], "--preview-id", pid or "",
                                        "--pick", "1", "--confirm", "SAVE 1"])
            active_after_first = self._ledger_state(home).get("active_count")
            cmd, second = self._run(home, ["save", kw["text"], "--preview-id", pid or "",
                                           "--pick", "1", "--confirm", "SAVE 1"])
            active_after_second = self._ledger_state(home).get("active_count")
            return Observation(op, cmd, second.returncode, second.stdout, second.stderr,
                               state={"first_exit": first.returncode,
                                      "active_after_first": active_after_first,
                                      "active_after_second": active_after_second,
                                      "preview_id": pid})

        raise ValueError("BingguPackAdapter 가 지원하지 않는 op: %s" % op)
