# -*- coding: utf-8 -*-
"""OpenBinggu Watcher — Incoming Folder Adapter (기존 기록 폴더 → MVP2 evidence_chunk[], dry-run only).

P0 첫 삽: 빈 그래프엔 채울 게 없다 → 기존 기록(박제·traj·md/txt)을 읽어 MVP2 노드 파이프라인이
바로 물 수 있는 evidence_chunk[] 로 정규화한다. 이 모듈은 **입력 어댑터**일 뿐 —
node→node 강한관계(edge) 0, 운영 store(~/.binggupack) 절대 미접촉(read-only stat만), candidate=true/
promotion_allowed=false/temp only.

기존 git diff 어댑터(watcher_capture_mvp1/batch_m1)와 다른 점:
  - 입력 = 폴더 안의 .md/.txt 파일들(구조 있는 문서). git diff 아님.
  - 빈 줄 단락분할 우회: 표·코드블록(fenced)·중첩리스트·blockquote 를 쪼개지 않고 한 덩어리로 보존
    (markdown 구조 파서). 단락은 빈 줄로만 분리.
  - 각 block 에 line_start/line_end 부여 → chunk→원본 파일+라인 역추적 가능.
  - evc_id = sha256[:24] (기존 EVC-sha8 폐기 — 대량 박제 충돌 방지).

재사용(무수정):
  - watcher_batch_m1.batch_redact(text) -> (redacted, hits, review_flag)   # secret(mvp1)+PII 복합 마스킹
  - watcher_batch_m1.scan_residual_pii(text) -> [kind...]                  # 독립 잔존 스캐너(검증자≠피검증자)
  - watcher_candidate_mvp2.to_nodes(chunks) -> (nodes, ev_index, stops)    # 노드 변환(엣지 미생성)
  - watcher_candidate_mvp2._meaningful(text) -> bool                       # 짧은/redacted-only 문장 거부

강제: candidate=true / promotion_allowed=false / origin=watcher / node→node edge 0 / redaction_required.
STOP: PII/secret 잔존(FN) = 전체 STOP (chunk 미생성, 작업 중단).

CLI:
  python watcher_incoming_folder_adapter.py --selftest
  python watcher_incoming_folder_adapter.py <folder> [<folder> ...]   # dry-run (temp 산출)
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "incoming_folder"
TMP_OUT = BASE / "tmp" / "watcher_incoming_folder"
SELFTEST_REPORT = BASE / "reports" / "watcher_incoming_folder_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_batch_m1 as batchm1          # batch_redact / scan_residual_pii 재사용
import watcher_candidate_mvp2 as mvp2       # to_nodes / _meaningful 재사용

SCOPE = "project:openbinggu"
ALLOWED_SUFFIXES = (".md", ".txt")

# 운영 store (절대 write 금지 — read-only stat 으로 mtime/size 불변 검증). 헌법: ~/.binggupack 미접촉.
_BINGGU_HOME = Path.home() / ".binggupack"
_ONTOLOGY = Path.home() / ".claude" / "memory" / "ontology"
OPERATING_STORE_FILES = [
    _BINGGU_HOME / "ledger.sqlite",
    _BINGGU_HOME / "capture_buffer.sqlite",
    _ONTOLOGY / "user_graph.yaml",
    _ONTOLOGY / "_graph_merge.yaml",
]

# 숨김/백업/temp 제외 (파일명·경로 토큰 기준)
_EXCLUDE_NAME_TOKENS = ("_backup", ".bak", ".tmp", "~", ".swp")
_EXCLUDE_DIR_TOKENS = ("_backup", ".git", "__pycache__", "node_modules", ".venv", "tmp", "_archived")

# 코드펜스 시작/끝 (``` 또는 ~~~, 들여쓰기 3칸까지 허용)
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# 표 행 (| 로 시작하거나 셀 구분 | 포함). 보수적으로 '|' 가 줄에 존재.
_TABLE_ROW_RE = re.compile(r"^\s{0,3}\|.*\|?\s*$|^\s{0,3}[^\n|]*\|[^\n|]*$")
# 표 구분선 (---|--- 형태)
_TABLE_SEP_RE = re.compile(r"^\s{0,3}\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
# 리스트 아이템 (-, *, +, 또는 1. 형태, 들여쓰기 허용)
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\S")
# blockquote (> 시작, 들여쓰기 허용)
_QUOTE_RE = re.compile(r"^\s{0,3}>")
# 헤딩 (# 시작)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def make_evc_id(source_path, block_text, block_index):
    """대량 박제 충돌 방지용 안정 ID. sha256[:24] (기존 EVC-sha8 폐기).
    소스경로+블록순번+내용 기반 → 같은 파일 내 동일 텍스트 반복 블록도
    block_index가 달라 다른 id (provenance 라인 보존). cross-file 동일 블록도
    소스가 다르면 다른 id. 같은 파일 같은 위치 재실행은 동일 id (멱등)."""
    key = str(source_path) + "::" + str(block_index) + "::" + block_text
    return "EVC-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _is_excluded(path):
    name = path.name
    low = name.lower()
    if name.startswith("."):
        return True
    if any(tok in low for tok in _EXCLUDE_NAME_TOKENS):
        return True
    parts = [p.lower() for p in path.parts]
    if any(tok in parts for tok in _EXCLUDE_DIR_TOKENS):
        return True
    return False


def scan_markdown_files(input_dirs):
    """폴더(들)에서 .md/.txt 수집. 숨김/백업/temp 제외. 결정적 정렬."""
    found = []
    for d in input_dirs:
        d = Path(d)
        if not d.exists() or not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if _is_excluded(p):
                continue
            found.append(p)
    return sorted(found, key=lambda x: str(x))


def _classify_block_type(lines):
    """블록의 첫 의미줄 기준 type 판정 (heading/table/code_fence/list/blockquote/paragraph)."""
    first = next((ln for ln in lines if ln.strip()), "")
    if _FENCE_RE.match(first):
        return "code_fence"
    if _HEADING_RE.match(first):
        return "heading"
    if _QUOTE_RE.match(first):
        return "blockquote"
    if _LIST_ITEM_RE.match(first):
        return "list"
    # 표: 첫 줄에 | 있고 다음 줄이 구분선이거나, 연속 | 행
    if "|" in first and any(_TABLE_SEP_RE.match(ln) for ln in lines):
        return "table"
    return "paragraph"


def parse_markdown_preserve_blocks(markdown):
    """markdown → [block] (line_start/line_end 1-based, 끝 포함).
    빈줄 단락분할을 우회: 표·코드펜스·중첩리스트·blockquote 는 빈 줄이 끼어도 한 덩어리로 보존.
    각 block = {'type', 'text', 'line_start', 'line_end'}.
    CRLF 정규화: \r\n 및 단독 \r 을 \n 으로 통일 → text_hash/evc_id 멱등(Windows).
    라인 번호(line_start/line_end)는 정규화 후 라인 기준."""
    # \r\n → \n, 그다음 잔여 단독 \r → \n (Mac classic). 정규화 후 라인 분할 → 라인번호 일관.
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = markdown.split("\n")
    n = len(raw_lines)
    blocks = []
    i = 0
    while i < n:
        line = raw_lines[i]
        # 빈 줄 스킵 (블록 사이 구분)
        if not line.strip():
            i += 1
            continue
        start = i  # 0-based

        # 1) 코드펜스: 시작 펜스 → 동일/더 긴 닫는 펜스까지 통째 (내부 빈 줄·구조 무시)
        mfence = _FENCE_RE.match(line)
        if mfence:
            open_fence = mfence.group(1)        # 여는 펜스 전체 (예: ``` 또는 ````)
            fence_char = open_fence[0]           # ` 또는 ~
            open_len = len(open_fence)            # 여는 펜스 길이
            j = i + 1
            while j < n:
                m2 = _FENCE_RE.match(raw_lines[j])
                # CommonMark: 닫는 펜스 = 같은 문자 + 같거나 더 긴 길이.
                # (여는 ``` 는 ``` 이상으로만 닫힘; 더 긴 ```` 로 열면 ``` 로 안 닫힘 → 중첩 펜스 보존)
                if m2 and m2.group(1)[0] == fence_char and len(m2.group(1)) >= open_len:
                    j += 1  # 닫는 펜스 포함
                    break
                j += 1
            end = min(j, n) - 1
            i = end + 1
            blocks.append(("code_fence", raw_lines[start:end + 1], start, end))
            continue

        # 2) 표: 첫 줄에 | 포함 + 다음 줄 구분선 → 연속 표행(빈 줄 만나면 종료)
        if "|" in line:
            # 표 여부 확인: 다음 비어있지 않은 줄이 구분선
            look = i + 1
            is_table = False
            if look < n and _TABLE_SEP_RE.match(raw_lines[look]):
                is_table = True
            if is_table:
                j = i
                while j < n and raw_lines[j].strip() and ("|" in raw_lines[j] or _TABLE_SEP_RE.match(raw_lines[j])):
                    j += 1
                end = j - 1
                i = j
                blocks.append(("table", raw_lines[start:end + 1], start, end))
                continue

        # 3) blockquote: 연속 > 행 (인접한 비-빈 줄도 lazy continuation 으로 포함)
        if _QUOTE_RE.match(line):
            j = i
            while j < n and raw_lines[j].strip():
                # > 행이거나, 인용 내부의 lazy continuation(빈 줄 전까지)
                j += 1
            end = j - 1
            i = j
            blocks.append(("blockquote", raw_lines[start:end + 1], start, end))
            continue

        # 4) 리스트(중첩 포함): 리스트 아이템 시작 → 들여쓰기 연속/하위 단락/빈 줄 1개 끼움 허용
        if _LIST_ITEM_RE.match(line):
            j = i + 1
            while j < n:
                cur = raw_lines[j]
                if cur.strip() == "":
                    # 빈 줄 다음이 들여쓰기된 줄 또는 새 리스트 아이템이면 리스트 계속
                    k = j + 1
                    while k < n and raw_lines[k].strip() == "":
                        k += 1
                    if k < n and (raw_lines[k].startswith(("  ", "\t")) or _LIST_ITEM_RE.match(raw_lines[k])):
                        j = k
                        continue
                    break  # 빈 줄로 리스트 종료
                # 새 리스트 아이템이거나 들여쓰기된 연속 줄(중첩/이어쓰기)
                if _LIST_ITEM_RE.match(cur) or cur.startswith(("  ", "\t")):
                    j += 1
                    continue
                # 들여쓰기 없는 비-리스트 줄 → 리스트 종료
                break
            end = j - 1
            i = j
            blocks.append(("list", raw_lines[start:end + 1], start, end))
            continue

        # 5) 헤딩: 한 줄
        if _HEADING_RE.match(line):
            end = i
            i = i + 1
            blocks.append(("heading", raw_lines[start:end + 1], start, end))
            continue

        # 6) 일반 단락: 빈 줄 또는 구조 시작 줄 전까지
        j = i + 1
        while j < n:
            cur = raw_lines[j]
            if cur.strip() == "":
                break
            if (_FENCE_RE.match(cur) or _HEADING_RE.match(cur) or _QUOTE_RE.match(cur)
                    or _LIST_ITEM_RE.match(cur)):
                break
            j += 1
        end = j - 1
        i = j
        blocks.append(("paragraph", raw_lines[start:end + 1], start, end))

    # type 정밀 보정 + dict 화 (line 은 1-based, end 포함)
    out = []
    for btype, lines, s, e in blocks:
        if btype == "paragraph":
            refined = _classify_block_type(lines)
        else:
            refined = btype
        out.append({
            "type": refined,
            "text": "\n".join(lines).rstrip("\n"),
            "line_start": s + 1,
            "line_end": e + 1,
        })
    return out


def make_evidence_chunks(path, blocks):
    """[block] → [evidence_chunk] (redaction 전 raw 골격). block_index 부여."""
    chunks = []
    for idx, b in enumerate(blocks):
        text = b["text"]
        if not text.strip():
            continue
        chunks.append({
            "source_path": str(path),
            "block_index": idx,
            "block_type": b["type"],
            "text": text,
            "line_start": b["line_start"],
            "line_end": b["line_end"],
        })
    return chunks


def redact_and_validate(chunks):
    """기존 batch_redact + scan_residual_pii 재사용. residual PII/secret 발견 시 전체 STOP.
    반환 (clean_chunks, stops). stops 비어있지 않으면 호출자가 전체 STOP 처리."""
    out, stops = [], []
    for c in chunks:
        red, hits, review = batchm1.batch_redact(c["text"])
        residual = batchm1.scan_residual_pii(red)
        if residual:
            stops.append({
                "source_path": c["source_path"],
                "block_index": c["block_index"],
                "line_start": c["line_start"],
                "line_end": c["line_end"],
                "reason": "secret/PII residual",
                "kinds": residual,
            })
            continue
        # MVP2 to_nodes 호환: item_id + text + evidence_meta.raw_pointer 필수.
        evc_id = make_evc_id(c["source_path"], red, c["block_index"])
        out.append({
            "evc_id": evc_id,
            "item_id": evc_id,            # to_nodes 가 읽는 키 (호환)
            "source_path": c["source_path"],
            "block_index": c["block_index"],
            "block_type": c["block_type"],
            "text": red,
            "text_hash": _text_hash(red),
            "line_start": c["line_start"],
            "line_end": c["line_end"],
            "evidence_meta": {
                "confidence": 0.5,
                "source_kind": "incoming_folder",
                "timestamp": "(deterministic-incoming)",
                "scope": SCOPE,
                "raw_pointer": c["source_path"],     # to_nodes 가 읽는 evidence_meta.raw_pointer
                "redaction_applied": True,
                "redaction_hits": hits,
                "review_flag": review,
                "line_start": c["line_start"],
                "line_end": c["line_end"],
                "block_type": c["block_type"],
            },
        })
    return out, stops


def _store_snapshot():
    """운영 store mtime/size 스냅샷 (write 안 함 — read-only stat)."""
    snap = {}
    for p in OPERATING_STORE_FILES:
        try:
            if p.exists():
                st = p.stat()
                snap[str(p)] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "exists": True}
            else:
                snap[str(p)] = {"exists": False}
        except OSError:
            snap[str(p)] = {"exists": False, "stat_error": True}
    return snap


def adapt_incoming_folder(input_dirs):
    """scan → parse → chunk → redact → MVP2 입력(evidence_chunk[]) 반환.
    residual PII/secret 발견 시 전체 STOP (chunks 미반환)."""
    store_before = _store_snapshot()
    files = scan_markdown_files(input_dirs)

    all_chunks = []
    per_file = []
    for fp in files:
        md = fp.read_text(encoding="utf-8", errors="replace")
        blocks = parse_markdown_preserve_blocks(md)
        raw_chunks = make_evidence_chunks(fp, blocks)
        all_chunks.extend(raw_chunks)
        per_file.append({"source_path": str(fp), "n_blocks": len(blocks), "n_chunks": len(raw_chunks)})

    clean_chunks, stops = redact_and_validate(all_chunks)
    store_after = _store_snapshot()
    store_unchanged = (store_before == store_after)

    if stops:
        return {
            "gate": "STOP",
            "reason": "secret/PII residual — 전체 STOP",
            "n_files": len(files),
            "per_file": per_file,
            "stops": stops,
            "chunks": [],
            "operating_store_unchanged": store_unchanged,
        }

    return {
        "gate": "GO",
        "n_files": len(files),
        "per_file": per_file,
        "n_chunks": len(clean_chunks),
        "chunks": clean_chunks,           # MVP2 to_nodes 입력 그대로
        "stops": [],
        "operating_store_unchanged": store_unchanged,
        "production_write": 0, "store_write": 0, "db_write": 0,
        "opencrab_call": 0, "github_push": 0, "node_to_node_edges": 0,
    }


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
