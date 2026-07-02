# -*- coding: utf-8 -*-
"""comp5 — 3단 A 분리(진화 추출기): 적중률 raw 단방향 export.

빙구팩은 적중률 raw(JSONL) + Merkle root + audit anchor 를 외부검증 가능 형식으로
**export 만** 하고, 거버넌스 자산(박제/CLAUDE.md/정책파일/ledger)은 절대 read-only.
빙구팩은 거버넌스 진화 루프(제안·diff·요약·렌더링·규칙 write)에서 구조적으로 빠진다.
사람이 git diff·외부 뷰어로 raw 를 직접 보고 판단하며, 규칙 변경은 빙구팩 무관 독립 도구로 수행.

self-modifying 회피 (4겹):
  1. write 표적 부재 — 박제/CLAUDE.md/정책파일/ledger 로의 write 0(_assert_export_target 물리 차단).
  2. 단방향(루프 차단) — hit_events→raw→파일에서 종료. raw 를 다시 읽어 규칙 생성/적용 0.
  3. 렌더링/제안 부재 — 제안 텍스트·diff·요약·UI 생성 함수 0(빙구팩=raw만, 해석=외부 도구).
  4. read-only 산정·외부 검증가능 — Merkle root 로 봉인, 제3자가 빙구팩 신뢰 없이 재계산·대조.

★ 가드1 fix (검증서·최중요): deny-list 가 아니라 capability-removal.
  ① 정책파일(binggu_policy.json/.sha256)·박제·CLAUDE.md·ledger(nodes/edges) 를
     GOVERNANCE_FORBIDDEN 에 명시 + export 경로에 _assert 물리가드.
  ② 경로 비교를 os.path.realpath(symlink/junction/.. 해석)로 — normcase+startswith 우회 차단.
  ③ out_dir 외부주입(파라미터)도 realpath 검증.
  ④ PII 제외 — binggu_cloud_pack_export 의 _LEAK 스캔 재사용(export 직전 게이트).

comp3(binggu_merkle_anchor)의 merkle_root/_canon_event/_leaf_hash 를 import(중복정의 금지·단일 진실).
빙구팩은 적중률 raw 만 export, 규칙 write 0.

stdlib only: hashlib/json/os/sys/datetime. 외부 바이너리 0.
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# comp3 단일 진실 — Merkle leaf/_canon/root 를 여기서 import(중복정의 금지).
from binggu_merkle_anchor import merkle_root, _leaf_hash, _LEAF_EVENT_COLS  # noqa: E402
import binggu_hit_stats as hs  # noqa: E402

RAW_SCHEMA_VER = "binggu-hit-raw-v1"

# ---------------- ① 거버넌스 자산 capability-removal (deny-list 아님) ----------------
# export out_dir 가 이 경로 '하위'면 물리적으로 write 거부(PermissionError).
# 정책파일·박제·CLAUDE.md·ledger 디렉터리를 명시 — 적중률 raw 가 거버넌스 자산을 덮어쓸 수 없게 한다.
_CLAUDE_HOME = os.environ.get("BINGGU_CLAUDE_HOME", os.path.expanduser("~/.claude"))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _governance_forbidden():
    """capability-removal 대상 거버넌스 자산 경로 목록(파일·디렉터리 혼합).
    env override 가능(자산 이동 시 등록). 기본 = ~/.claude/CLAUDE.md·박제·정책파일·ledger."""
    paths = [
        # CLAUDE.md (헌법급) + 박제 디렉터리
        os.environ.get("BINGGU_CLAUDE_MD", os.path.join(_CLAUDE_HOME, "CLAUDE.md")),
        os.environ.get("BINGGU_PAJAE_DIR", os.path.join(_CLAUDE_HOME, "memory", "박제")),
        os.environ.get("BINGGU_MEMORY_DIR", os.path.join(_CLAUDE_HOME, "memory")),
        # 선언형 정책 파일 + pin (comp2)
        os.environ.get("BINGGU_POLICY_JSON", os.path.join(_REPO_ROOT, "policies", "binggu_policy.json")),
        os.environ.get("BINGGU_POLICY_PIN", os.path.join(_REPO_ROOT, "policies", "binggu_policy.sha256")),
        os.environ.get("BINGGU_POLICY_DIR", os.path.join(_REPO_ROOT, "policies")),
    ]
    # ledger(nodes/edges) 디렉터리 — binggu_platform.binggu_home() 하위(read-only 보호).
    try:
        from binggu_platform import binggu_home
        paths.append(binggu_home())
    except Exception:
        paths.append(os.environ.get("BINGGU_HOME", os.path.expanduser("~/.binggupack")))
    return [p for p in paths if p]


def _real(p):
    """② symlink/junction/.. 해석 — normcase+startswith 우회를 realpath 로 차단."""
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _is_within(child, parent):
    """child 가 parent(파일이면 그 자신, 디렉터리면 하위 포함)에 속하는지 realpath 기반 판정."""
    c = _real(child)
    pa = _real(parent)
    if c == pa:
        return True
    return c.startswith(pa + os.sep)


def _assert_export_target(out_dir):
    """③ out_dir(외부주입 포함)이 거버넌스 자산이거나 그 하위면 PermissionError(물리 가드).
    realpath 로 symlink/junction/.. 우회 차단. 정책파일·박제·CLAUDE.md·ledger 경로 export 거부."""
    for g in _governance_forbidden():
        if _is_within(out_dir, g):
            raise PermissionError("governance_write_forbidden: %s" % g)
    return True


# ---------------- ④ PII/secret leak 스캔 (cloud_pack_export 재사용) ----------------

def _leak_count(text):
    """binggu_cloud_pack_export._LEAK 스캔 재사용(import 실패 시 graceful 0 — 단 export 차단 안 함은
    위험하므로, import 실패는 보수적으로 자체 최소 패턴으로 폴백)."""
    try:
        from binggu_cloud_pack_export import _leak_count as _lc
        return _lc(text)
    except Exception:
        import re
        fallback = [re.compile(r"sk-live-[A-Za-z0-9]"), re.compile(r"\b\d{3}-\d{4}-\d{4}\b"),
                    re.compile(r"password\s*[:=]", re.I), re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")]
        return sum(1 for rx in fallback if rx.search(text or ""))


# ---------------- collect (read-only SELECT, PII 제외) ----------------

def collect_hit_raw(db, now_ts=None):
    """hit_events append-only 로그 전체를 결정적 순서로 추출(read-only).
    binggu_hit_stats.get_hit_rate/both_sides 를 그대로 호출해 산정값 동봉.
    반환 {schema, generated_at, events[], rates{owner,ai,both}, counts, merkle_root}.

    PII 제외(헌법): sentence/원문 0 — event_id·node_id·speaker·kind·outcome·subtype·ts 만.
    domain/context_hash/decision_id 같은 라벨 메타도 raw 에 포함하지 않는다(_LEAF_EVENT_COLS 한정).
    """
    cols = ",".join(_LEAF_EVENT_COLS)  # event_id,node_id,speaker,kind,outcome,subtype,ts
    rows = db.con.execute(
        "SELECT %s FROM hit_events ORDER BY event_id" % cols).fetchall()
    events = [dict(zip(_LEAF_EVENT_COLS, r)) for r in rows]
    leaves = [_leaf_hash(e) for e in events]  # comp3 _leaf_hash(단일 진실)
    rates = {
        "owner": hs.get_hit_rate(db, "owner", now_ts=now_ts),
        "ai": hs.get_hit_rate(db, "ai", now_ts=now_ts),
        "both": hs.both_sides(db, now_ts=now_ts),
    }
    gen = now_ts if now_ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": RAW_SCHEMA_VER,
        "generated_at": gen,
        "events": events,
        "rates": rates,
        "counts": {"event_count": len(events)},
        "merkle_root": merkle_root(leaves),  # comp3 merkle_root(import·중복정의 금지)
    }


def _audit_anchor(db):
    """운영 ledger 의 audit_meta(head_entry_hash/entry_count) read-only 복사 — 교차검증용.
    write/checkpoint 0. 메타 없으면 빈 dict(graceful)."""
    try:
        meta = {k: v for k, v in db.con.execute("SELECT key,value FROM audit_meta")}
    except Exception:
        meta = {}
    return {"head_entry_hash": meta.get("head_entry_hash"),
            "entry_count": meta.get("entry_count")}


# ---------------- export (단방향 write — out_dir 데이터파일 2개만) ----------------

def export_hit_raw(db, out_dir, ts=None):
    """raw JSONL + manifest(merkle_root + audit anchor) 를 out_dir 에 write.
    out_dir 는 거버넌스 자산(정책파일/박제/CLAUDE.md/ledger) 경로일 수 없음(_assert_export_target).
    반환 {written:[paths], merkle_root, event_count, audit_anchor}.

    빙구팩이 안 하는 것: 적중률→규칙 매핑 제안 0, diff 생성 0, 요약 0, 규칙 파일 write 0.
    write 표면 = out_dir 의 데이터파일(hit_raw.jsonl/hit_manifest.json) 2개뿐.
    """
    _assert_export_target(out_dir)            # ① 거버넌스 경로 export 물리 차단(realpath)
    raw = collect_hit_raw(db, now_ts=ts)

    # ④ PII/secret 누출 게이트 — export 직전 raw 전체 텍스트 스캔(방어적).
    jsonl_lines = [json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                   for e in raw["events"]]
    leak = _leak_count("\n".join(jsonl_lines))
    if leak:
        raise PermissionError("pii_leak_blocked: %d hit(s)" % leak)

    os.makedirs(out_dir, exist_ok=True)
    jsonl_path = os.path.join(out_dir, "hit_raw.jsonl")
    manifest_path = os.path.join(out_dir, "hit_manifest.json")

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for line in jsonl_lines:
            f.write(line + "\n")

    anchor = _audit_anchor(db)
    manifest = {
        "schema": RAW_SCHEMA_VER,
        "generated_at": raw["generated_at"],
        "event_count": raw["counts"]["event_count"],
        "merkle_root": raw["merkle_root"],
        "rates": raw["rates"],
        "audit_anchor": anchor,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, sort_keys=True, indent=2)

    return {
        "written": [jsonl_path, manifest_path],
        "merkle_root": raw["merkle_root"],
        "event_count": raw["counts"]["event_count"],
        "audit_anchor": anchor,
    }


# ---------------- 외부 독립 재계산(검증 독립성 — 빙구팩 무관) ----------------

def recompute_root_from_jsonl(jsonl_path):
    """export 된 hit_raw.jsonl 만으로 merkle_root 재계산(빙구팩 DB·코드 상태 불요).
    누구든 stdlib 한 줄로 manifest.merkle_root 와 대조 가능 → 검증 독립성."""
    leaves = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            leaves.append(_leaf_hash({k: e.get(k) for k in _LEAF_EVENT_COLS}))
    return merkle_root(leaves)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import binggu_hit_export_selftest as st
        sys.exit(st.run())
    print("binggu_hit_export: --selftest 로 검증 실행 (binggu_hit_export_selftest.run)")
