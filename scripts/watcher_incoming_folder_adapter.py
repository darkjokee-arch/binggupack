# -*- coding: utf-8 -*-
"""OpenBinggu Watcher — Incoming Folder Adapter (backward-compatible thin wrapper).

strangler 이관(candidate_mvp2 전례): 순수 파이프라인 로직(scan → parse → chunk →
redact → adapt) + 상수/정규식 정본은 binggupack.pack.incoming_folder 로 byte-identical
이관됐고, 이 파일은 공개 심볼을 re-export 하는 thin wrapper 다. __file__ 경로상수
(BASE/SCRIPTS/FIXTURE_DIR/TMP_OUT/SELFTEST_REPORT) + fixtures/selftest/CLI 오케스트레이션
(경로 의존)은 이 wrapper 에 잔류한다. dry-run only(운영 store write 0).

MCP 상한(MF3): adapt_incoming_folder(input_dirs, *, max_files, max_total_bytes,
max_file_bytes). 기본 None = 무제한 = 기존 동작 100% 보존.

CLI:
  python watcher_incoming_folder_adapter.py --selftest
  python watcher_incoming_folder_adapter.py <folder> [<folder> ...]   # dry-run (temp 산출)
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # scripts 형제(importer 호환) 호환

from binggupack.pack.incoming_folder import *  # noqa: E402,F401,F403
from binggupack.pack.incoming_folder import (  # noqa: E402,F401  (전체 명시 re-export)
    SCOPE,
    ALLOWED_SUFFIXES,
    OPERATING_STORE_FILES,
    make_evc_id,
    _text_hash,
    _is_excluded,
    scan_markdown_files,
    _classify_block_type,
    parse_markdown_preserve_blocks,
    make_evidence_chunks,
    redact_and_validate,
    _store_snapshot,
    adapt_incoming_folder,
    batchm1,
    mvp2,
)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "incoming_folder"
TMP_OUT = BASE / "tmp" / "watcher_incoming_folder"
SELFTEST_REPORT = BASE / "reports" / "watcher_incoming_folder_selftest.json"


# ---------------------------------------------------------------------------
# fixtures (합성 — PII/secret 절대 미포함, 순수 기술 내용만)
# ---------------------------------------------------------------------------
def ensure_fixtures():
    base = FIXTURE_DIR / "clean"
    base.mkdir(parents=True, exist_ok=True)

    def wr(rel, content):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # 1) 표 포함 (빈 줄 없이 연속, 단일 chunk 유지 검증)
    wr("a_table.md",
       "# 파서 결과 비교\n\n"
       "다음 표는 블록 타입별 보존 결과를 정리한다.\n\n"
       "| 블록타입 | 보존여부 | 비고 |\n"
       "| --- | --- | --- |\n"
       "| 표 | 보존 | 한 덩어리 |\n"
       "| 코드펜스 | 보존 | fenced 통째 |\n"
       "| 단락 | 분할 | 빈 줄 기준 |\n\n"
       "표 이후의 일반 문장은 별도 블록으로 분리된다.\n")

    # 2) 코드펜스 (내부 빈 줄 포함 — 쪼개지면 안 됨)
    wr("b_code.md",
       "## 코드블록 보존\n\n"
       "아래 코드는 내부 빈 줄이 있어도 한 블록으로 보존되어야 한다.\n\n"
       "```python\n"
       "def adapt(input_dirs):\n"
       "    files = scan_markdown_files(input_dirs)\n"
       "\n"
       "    return redact_and_validate(files)\n"
       "```\n\n"
       "코드 다음 단락은 별개 블록이다.\n")

    # 3) 중첩 리스트 (들여쓰기/하위 항목 한 덩어리)
    wr("c_list.md",
       "### 중첩 리스트\n\n"
       "- 최상위 항목 하나\n"
       "  - 하위 항목 가\n"
       "  - 하위 항목 나\n"
       "    - 더 깊은 항목\n"
       "- 최상위 항목 둘\n\n"
       "리스트 종료 후 단락.\n")

    # 4) blockquote + 일반 단락 혼합
    wr("d_quote.md",
       "# 인용 보존\n\n"
       "> 이 문장은 인용 블록이다.\n"
       "> 두 번째 인용 줄도 같은 블록에 속한다.\n\n"
       "인용 바깥의 결론 문장은 별도 블록으로 캡처된다.\n")

    # 5) 박제 스타일(traj 유사) — 판단/상태/개념 문장 섞임
    wr("nested/e_handoff.md",
       "# 핸드오프 요약\n\n"
       "현재 어댑터 파이프라인은 정상 가동 중이다.\n\n"
       "redaction 이란 민감정보를 제거하는 절차이다.\n\n"
       "다음 세션에서는 edge 생성을 진행해야 한다.\n")

    # 6) 중첩 코드펜스 — 더 긴 ```` 로 열고 내부에 ``` (3칸) 포함.
    #    CommonMark: ```` 는 ``` 으로 안 닫힘 → 전체가 단일 코드블록으로 보존되어야 함(3덩어리로 쪼개지면 결함).
    wr("f_nested_fence.md",
       "## 중첩 펜스 보존\n\n"
       "아래는 코드블록을 보여주는 코드블록(중첩 펜스)이다.\n\n"
       "````markdown\n"
       "다음과 같이 코드를 감싼다:\n"
       "```python\n"
       "print('nested')\n"
       "```\n"
       "위 블록은 내부 펜스다.\n"
       "````\n\n"
       "중첩 펜스 다음 단락은 별개 블록이다.\n")


def _stop_fixture_dir():
    """STOP 검증용 — 가짜 PII 주입 fixture를 temp(git 미커밋)에 만든다.
    공개 repo tree scan 자기검출 방지: 런타임 조립, 디스크에 평문 PII를 repo 내부에 남기지 않음."""
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="incoming_stop_"))
    (d / "leak.md").write_text(
        "# 연락처 노출 테스트\n\n"
        "담당자 휴대폰 010-" + "1234-5678 로 연락 바람.\n\n"
        "이메일 " + "tester" + "@example.com 도 본문에 노출됨.\n",
        encoding="utf-8")
    return d


def run_selftest():
    ensure_fixtures()
    clean_dir = FIXTURE_DIR / "clean"

    # ① 정상 폴더 어댑팅
    res = adapt_incoming_folder([clean_dir])
    chunks = res["chunks"]

    # ② 구조 보존 검증 — type 별 블록이 단일 chunk 로 살아있는지
    by_type = {}
    for c in chunks:
        by_type.setdefault(c["block_type"], []).append(c)

    table_chunks = by_type.get("table", [])
    code_chunks = by_type.get("code_fence", [])
    list_chunks = by_type.get("list", [])
    quote_chunks = by_type.get("blockquote", [])

    # 표: 데이터 행 3개 + 헤더 + 구분선이 한 chunk 안에 (개행으로 다 들어있어야)
    table_single = bool(table_chunks) and all(
        c["text"].count("\n") >= 3 and "블록타입" in c["text"] and "단락" in c["text"]
        for c in table_chunks)
    # 코드펜스: 내부 빈 줄이 있는데도 def + return 이 한 chunk
    code_single = bool(code_chunks) and any(
        "def adapt" in c["text"] and "return redact_and_validate" in c["text"]
        for c in code_chunks)
    # 중첩 펜스: ```` 로 연 블록이 내부 ``` 에 안 닫히고 단일 chunk 로 보존 (3덩어리 X).
    #  - 내부 펜스(```python)와 닫는 ````, 마지막 줄("위 블록은 내부 펜스다.")이 한 chunk 안에 모두 존재.
    #  - 그리고 그 코드블록 chunk 가 정확히 1개 (쪼개지면 2개 이상).
    nested_fence_chunks = [c for c in code_chunks if "f_nested_fence.md" in c["source_path"]]
    nested_fence_single = (len(nested_fence_chunks) == 1
                           and "```python" in nested_fence_chunks[0]["text"]
                           and "print('nested')" in nested_fence_chunks[0]["text"]
                           and "위 블록은 내부 펜스다." in nested_fence_chunks[0]["text"]
                           and "중첩 펜스 다음 단락" not in nested_fence_chunks[0]["text"])
    # 중첩 리스트: 최상위 둘 + 더 깊은 항목이 한 chunk
    list_single = bool(list_chunks) and any(
        "최상위 항목 하나" in c["text"] and "더 깊은 항목" in c["text"]
        and "최상위 항목 둘" in c["text"] for c in list_chunks)
    # blockquote: 두 인용 줄이 한 chunk
    quote_single = bool(quote_chunks) and any(
        "인용 블록이다" in c["text"] and "두 번째 인용 줄" in c["text"] for c in quote_chunks)

    # ③ sha256[:24] 멱등 + 충돌 0
    res2 = adapt_incoming_folder([clean_dir])
    ids1 = [c["evc_id"] for c in chunks]
    ids2 = [c["evc_id"] for c in res2["chunks"]]
    idempotent = (ids1 == ids2)
    id_len_ok = all(c["evc_id"].startswith("EVC-") and len(c["evc_id"]) == 4 + 24 for c in chunks)
    no_collision = (len(ids1) == len(set(ids1)))

    # ③' CRLF 멱등: 동일 내용의 LF vs CRLF(+단독 CR) 입력이 동일 evc_id/text_hash 산출.
    #     (\r 잔류 시 text_hash/evc_id 가 어긋나 멱등이 깨짐 — Windows 회귀 케이스)
    import tempfile as _tf
    #  - LF 정본: 모든 개행이 \n. CRLF 변형: 같은 논리 라인을 \r\n / 단독 \r 로만 표기(라인 경계 동일).
    #    정규화가 \r\n·\r 을 \n 으로 통일하면 두 입력의 라인·내용이 완전히 일치해야 함.
    _lf_base = ("# CRLF 멱등 테스트\n\n"
                "첫 단락 문장이다.\n\n"
                "| 키 | 값 |\n| --- | --- |\n| a | 1 |\n\n"
                "```python\nprint('x')\n```\n\n"
                "마지막 단락.\n")
    _lf_dir = Path(_tf.mkdtemp(prefix="incoming_lf_"))
    _crlf_dir = Path(_tf.mkdtemp(prefix="incoming_crlf_"))
    # LF: 바이트 그대로 / CRLF: \n→\r\n 전체 변환 후 한 개행만 단독 \r 로 교체(라인 경계 보존).
    # write_bytes 로 Python 텍스트모드 개행 변환 우회.
    (_lf_dir / "g.md").write_bytes(_lf_base.encode("utf-8"))
    _crlf_text = _lf_base.replace("\n", "\r\n")
    _crlf_text = _crlf_text.replace("마지막 단락.\r\n", "마지막 단락.\r", 1)  # 단독 \r 회귀 케이스
    (_crlf_dir / "g.md").write_bytes(_crlf_text.encode("utf-8"))
    _lf_res = adapt_incoming_folder([_lf_dir])
    _crlf_res = adapt_incoming_folder([_crlf_dir])
    # source_path 가 달라 evc_id(경로포함)는 다를 수 있으므로 text_hash 로 내용 멱등 비교.
    _lf_hashes = [c["text_hash"] for c in _lf_res["chunks"]]
    _crlf_hashes = [c["text_hash"] for c in _crlf_res["chunks"]]
    _no_stray_cr = all("\r" not in c["text"] for c in _crlf_res["chunks"])
    crlf_idempotent = (bool(_lf_hashes) and _lf_hashes == _crlf_hashes and _no_stray_cr)
    import shutil as _sh
    _sh.rmtree(_lf_dir, ignore_errors=True)
    _sh.rmtree(_crlf_dir, ignore_errors=True)

    # ④ 근거 역추적: 모든 chunk 가 source_path + line range 보유 + 라인 유효
    traceable = all(
        c.get("source_path") and isinstance(c.get("line_start"), int)
        and isinstance(c.get("line_end"), int) and c["line_start"] <= c["line_end"]
        and Path(c["source_path"]).exists()
        for c in chunks)
    # 실제 라인 범위가 원문과 정합 (첫 chunk 한 건 정밀 검증)
    line_range_correct = True
    if chunks:
        c0 = sorted(chunks, key=lambda x: (x["source_path"], x["line_start"]))[0]
        src_lines = Path(c0["source_path"]).read_text(encoding="utf-8").split("\n")
        slice_txt = "\n".join(src_lines[c0["line_start"] - 1:c0["line_end"]]).rstrip("\n")
        # redaction 없는 clean fixture 이므로 원문 슬라이스 == chunk text
        line_range_correct = (slice_txt == c0["text"])

    # ⑤ MVP2 to_nodes 실제 물림 → 노드 생성
    nodes, ev_index, node_stops = mvp2.to_nodes(chunks)
    nodes_built = len(nodes) > 0
    candidate_all_true = all(n["properties"]["candidate"] is True for n in nodes)
    promotion_all_false = all(n["promotion_allowed"] is False for n in nodes)
    evidence_refs_matched = all(
        all(r in {e["evidence_id"] for e in ev_index} for r in n["evidence_refs"]) for n in nodes)
    # to_nodes 가 source_path 역추적 보존 (raw_pointer)
    raw_pointer_preserved = all(e["source_path"] for e in ev_index)

    # ⑥ 운영 store 미접촉
    store_unchanged = res["operating_store_unchanged"] and res2["operating_store_unchanged"]

    # ④' STOP 게이트 — 가짜 PII fixture 주입 시 전체 STOP.
    # 두 경로로 검증:
    #  (a) batch_redact 가 마스킹에 성공하면 정상적으로 STOP 이 아니라 GO (방어 정상 — 잔존 0).
    #  (b) 잔존 스캐너가 FN(redactor blind spot)을 잡는 경우 = 진짜 STOP 경로.
    #      redactor==scanner 가 잘 정렬돼 자연 잔존이 안 생기므로, scanner 의 FN 탐지를
    #      일시 stub 하여 STOP 메커니즘 자체를 결정적으로 검증(검증자≠피검증자 원칙의 STOP 분기).
    stop_dir = _stop_fixture_dir()
    # (a) 실제 fixture: redactor 가 잘 마스킹 → 잔존 0 → GO (방어 정상 확인)
    masked_ok = adapt_incoming_folder([stop_dir])
    masked_no_residual = (masked_ok["gate"] == "GO"
                          and all(not batchm1.scan_residual_pii(c["text"]) for c in masked_ok["chunks"]))
    # (b) 잔존 스캐너가 무언가 잡는 상황을 강제 → 전체 STOP 분기 검증
    _orig_scan = batchm1.scan_residual_pii
    try:
        # redaction 통과 텍스트에 잔존이 남은 것처럼(FN) 신호 → STOP 분기 강제
        batchm1.scan_residual_pii = lambda t: ["scan_simulated_fn"]
        stop_res = adapt_incoming_folder([stop_dir])
    finally:
        batchm1.scan_residual_pii = _orig_scan
    stop_gate = (masked_no_residual
                 and stop_res["gate"] == "STOP" and len(stop_res["stops"]) > 0
                 and len(stop_res["chunks"]) == 0)
    # STOP 시에도 운영 store 미접촉
    stop_store_unchanged = stop_res["operating_store_unchanged"]
    import shutil
    shutil.rmtree(stop_dir, ignore_errors=True)

    # node→node edge 0 (이 모듈은 edge 생성 안 함)
    no_node_to_node = (res.get("node_to_node_edges", 0) == 0)

    checks = {
        "normal_folder_GO": res["gate"] == "GO" and len(chunks) > 0,
        "table_single_block": table_single,
        "code_fence_single_block": code_single,
        "nested_fence_single_block": nested_fence_single,
        "crlf_idempotent": crlf_idempotent,
        "nested_list_single_block": list_single,
        "blockquote_single_block": quote_single,
        "sha256_24_idempotent": idempotent,
        "sha256_24_id_len": id_len_ok,
        "no_id_collision": no_collision,
        "traceable_source_line": traceable,
        "line_range_correct": line_range_correct,
        "mvp2_nodes_built": nodes_built,
        "candidate_all_true": candidate_all_true,
        "promotion_all_false": promotion_all_false,
        "evidence_refs_matched": evidence_refs_matched,
        "raw_pointer_preserved": raw_pointer_preserved,
        "redaction_stop_on_pii": stop_gate,
        "operating_store_unchanged": store_unchanged and stop_store_unchanged,
        "no_node_to_node_edge": no_node_to_node,
    }
    gate = "GO" if all(checks.values()) else "STOP"

    report = {
        "tool": "watcher_incoming_folder_adapter.py",
        "phase": "P0 — 기존 기록 폴더 → MVP2 evidence_chunk[] 어댑터",
        "mode": "dry-run / selftest",
        "n_files": res["n_files"], "n_chunks": len(chunks),
        "n_nodes": len(nodes),
        "block_type_counts": {k: len(v) for k, v in by_type.items()},
        "operating_store_write": 0, "production_write": 0, "db_write": 0,
        "opencrab_call": 0, "github_push": 0, "node_to_node_edges": 0,
        "checks": checks, "gate": gate,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 76)
    print("OpenBinggu Watcher — Incoming Folder Adapter (P0, dry-run / selftest)")
    print("=" * 76)
    print("  files=%d chunks=%d nodes=%d" % (res["n_files"], len(chunks), len(nodes)))
    print("  block_types:", {k: len(v) for k, v in by_type.items()})
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  report:", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(dirs):
    res = adapt_incoming_folder(dirs)
    TMP_OUT.mkdir(parents=True, exist_ok=True)
    out = TMP_OUT / "incoming_chunks.jsonl"
    out.write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in res["chunks"]),
                   encoding="utf-8")
    print(json.dumps({"gate": res["gate"], "n_files": res["n_files"],
                      "n_chunks": len(res["chunks"]), "stops": res["stops"],
                      "operating_store_unchanged": res["operating_store_unchanged"],
                      "out": str(out)}, ensure_ascii=False, indent=2))
    sys.exit(0 if res["gate"] == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args)


if __name__ == "__main__":
    main()
