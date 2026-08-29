# -*- coding: utf-8 -*-
"""OpenBinggu Watcher — Incoming Folder Adapter transform (정본 impl · dry-run only).

strangler 이관(candidate_mvp2 전례): 순수 파이프라인 로직(scan → parse → chunk →
redact → adapt) + 상수/정규식을 scripts/watcher_incoming_folder_adapter.py 에서
byte-identical 이관. scripts/watcher_incoming_folder_adapter.py 는 이 모듈을 re-export
하는 backward-compatible thin wrapper(__file__ 경로상수 + fixtures/CLI/selftest
오케스트레이션 잔류)다.

기존 기록(박제·traj·md/txt)을 읽어 MVP2 노드 파이프라인이 바로 물 수 있는
evidence_chunk[] 로 정규화한다. 이 모듈은 **입력 어댑터**일 뿐 — node→node 강한관계
(edge) 0, 운영 store(~/.binggupack) 절대 미접촉(read-only stat만), candidate=true/
promotion_allowed=false/temp only.

재사용(무수정, in-package):
  - binggupack.pack.batch_m1.batch_redact(text, field_name="") -> (redacted, hits, review)
  - binggupack.pack.batch_m1.scan_residual_pii(text) -> [kind...]  (검증자≠피검증자)
  - binggupack.pack.candidate_mvp2.to_nodes(chunks) -> (nodes, ev_index, stops)

강제: candidate=true / promotion_allowed=false / origin=watcher / node→node edge 0 /
redaction_required. STOP: PII/secret 잔존(FN) = 전체 STOP.

MCP 상한(MF3): adapt_incoming_folder(input_dirs, *, max_files, max_total_bytes,
max_file_bytes). 기본 None = 무제한 = 기존 동작 100% 보존. 상한 초과 시 REJECT
(운영 store 미접촉·chunks=[]).
"""
import hashlib
import re
from pathlib import Path

# batchm1.batch_redact/scan_residual_pii 재사용. mvp2(to_nodes)는 transform 본문엔 unused 로
# 보이나 scripts wrapper 의 run_selftest 가 re-export 로 사용 → impl 보존 필수(F401 의도적, candidate_mvp2 전례).
from binggupack.pack import batch_m1 as batchm1
from binggupack.pack import candidate_mvp2 as mvp2  # noqa: F401

__all__ = [
    "SCOPE", "ALLOWED_SUFFIXES", "OPERATING_STORE_FILES", "make_evc_id", "_text_hash",
    "_is_excluded", "scan_markdown_files", "_classify_block_type",
    "parse_markdown_preserve_blocks", "make_evidence_chunks", "redact_and_validate",
    "_store_snapshot", "adapt_incoming_folder", "batchm1", "mvp2",
]

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
    return sorted(found, key=str)


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


def adapt_incoming_folder(input_dirs, *, max_files=None, max_total_bytes=None, max_file_bytes=None,
                          reject_symlinks=False):
    """scan → parse → chunk → redact → MVP2 입력(evidence_chunk[]) 반환.
    residual PII/secret 발견 시 전체 STOP (chunks 미반환).

    MCP 상한(선택): max_files / max_total_bytes / max_file_bytes. 전부 None(기본)=무제한=
    기존 동작 100% 보존(회귀 0). 상한 초과 시 gate="REJECT" (chunks=[], 운영 store 미접촉).
      - 파일 수 > max_files            → reason="too_many_files"
      - 파일 크기(stat) > max_file_bytes → reason="file_too_large"
      - 누적 바이트 > max_total_bytes    → reason="total_too_large"

    reject_symlinks(선택, 기본 False=기존 동작): True 면 스캔 결과에 심링크 파일이 1건이라도
    있으면 gate="REJECT"(reason="symlink_forbidden"). MCP 결선 경로가 자식 심링크로 게이트를
    우회해 외부 파일을 read 하는 것을 차단한다(CS-3). 기본 False 로 기존 호출·selftest 회귀 0.
    """
    store_before = _store_snapshot()
    files = scan_markdown_files(input_dirs)

    # CS-3: 심링크 파일 자식으로 path-gate 우회(외부 파일 read) 차단 — MCP 결선 전용(기본 off).
    if reject_symlinks:
        n_sym = sum(1 for f in files if f.is_symlink())
        if n_sym:
            return {
                "gate": "REJECT",
                "reason": "symlink_forbidden",
                "n_files": len(files),
                "n_symlinks": n_sym,
                "chunks": [],
                "stops": [],
                "operating_store_unchanged": (_store_snapshot() == store_before),
            }

    # MCP 상한 1: 파일 수 (read 전 REJECT)
    if max_files is not None and len(files) > max_files:
        return {
            "gate": "REJECT",
            "reason": "too_many_files",
            "n_files": len(files),
            "limit": max_files,
            "chunks": [],
            "stops": [],
            "operating_store_unchanged": (_store_snapshot() == store_before),
        }

    all_chunks = []
    per_file = []
    total_bytes = 0
    for fp in files:
        # MCP 상한 2·3: 파일 크기·누적 바이트 (각 파일 read 전 stat). 상한 미지정 시 stat 생략(회귀 0).
        if max_file_bytes is not None or max_total_bytes is not None:
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if max_file_bytes is not None and fsize > max_file_bytes:
                return {
                    "gate": "REJECT",
                    "reason": "file_too_large",
                    "n_files": len(files),
                    "source_path": str(fp),
                    "file_bytes": fsize,
                    "limit": max_file_bytes,
                    "chunks": [],
                    "stops": [],
                    "operating_store_unchanged": (_store_snapshot() == store_before),
                }
            total_bytes += fsize
            if max_total_bytes is not None and total_bytes > max_total_bytes:
                return {
                    "gate": "REJECT",
                    "reason": "total_too_large",
                    "n_files": len(files),
                    "total_bytes": total_bytes,
                    "limit": max_total_bytes,
                    "chunks": [],
                    "stops": [],
                    "operating_store_unchanged": (_store_snapshot() == store_before),
                }
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
