"""binggu_parser_adapter — 전방위 파싱 어댑터(HTML/PDF/HWP/HWPX/XLSX/DOCX/PPTX...).

raw bytes + 힌트(content-type/파일명) → 파서 backend 라우팅 → derived_text(가공본).
harvest 본체와 **분리된 별도 모듈**(harvest 의 '서드파티 import 0' T12 불변식을 안 건드림).

owner 조건부 GO 반영:
  - 조건1: raw 원문은 그대로(이 모듈은 변형 안 함) + raw_sha256 fingerprint 동봉.
           파싱 결과는 'derived_text'(가공본)로 분리 — 원문 ≠ 파생.
  - 조건3: parse_document() 는 **절대 raise 하지 않음**. 모든 실패를 typed error 로 반환 →
           상위(harvest)는 ok=False 면 그 문서만 skip 하고 다음 문서로. 전체 수집이 죽지 않음.

backend 가용성은 런타임 탐지. MarkItDown/KorDoc 미설치면 PARSER_MISSING typed error +
텍스트류는 plain 폴백. 바이너리(PDF/HWP 등)는 plain 폴백 금지(깨진 텍스트 방지).
"""
import io
import os
import re
import shutil
import hashlib
import tempfile
import subprocess

# typed error 코드(조건3) — 자유 문자열 금지, 분류 가능한 enum.
ERR_UNSUPPORTED = "UNSUPPORTED_FORMAT"
ERR_NOT_WIRED = "BACKEND_NOT_WIRED"      # backend CLI/런타임 미가용(설치 여부와 별개로 호출 불가)
ERR_CALL_FAILED = "BACKEND_CALL_FAILED"  # backend 호출은 됐으나 exit≠0/timeout/예외
ERR_MISSING = "PARSER_MISSING"           # (deprecated 호환 — NOT_WIRED 로 대체)
ERR_FAILED = "PARSER_FAILED"
ERR_CORRUPT = "CORRUPT_DOCUMENT"
ERR_EMPTY = "EMPTY_RESULT"


def _which(name):
    """Windows .cmd/.exe 포함 실행 경로 탐색."""
    return shutil.which(name) or shutil.which(name + ".cmd")


def _run_cli(cmd, timeout=300):
    """CLI subprocess 실행 → (returncode, stdout_bytes, stderr_text). 예외는 호출부가 분류."""
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")

# 확장자/콘텐츠타입 → 논리 포맷
_EXT_FMT = {
    "txt": "text", "md": "text", "log": "text", "csv": "text",
    "json": "text", "xml": "text", "rss": "text", "atom": "text",
    "html": "html", "htm": "html",
    "pdf": "pdf", "hwp": "hwp", "hwpx": "hwpx",
    "xlsx": "xlsx", "xls": "xlsx", "docx": "docx", "doc": "docx",
    "pptx": "pptx", "ppt": "pptx",
}
_CT_FMT = [
    ("text/html", "html"), ("application/pdf", "pdf"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml", "docx"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml", "xlsx"),
    ("application/vnd.openxmlformats-officedocument.presentationml", "pptx"),
    ("application/json", "text"), ("application/xml", "text"), ("text/", "text"),
]
_TEXT_FMTS = {"text", "html"}           # plain 폴백 허용
_BINARY_FMTS = {"pdf", "hwp", "hwpx", "xlsx", "docx", "pptx"}


def detect_format(content_type=None, filename=None):
    """content-type 우선, 없으면 확장자. 미상이면 'unknown'."""
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in _EXT_FMT:
            return _EXT_FMT[ext]
    ct = (content_type or "").lower()
    for prefix, fmt in _CT_FMT:
        if ct.startswith(prefix) or prefix in ct:
            return fmt
    return "unknown"


# ── backend 인터페이스 ────────────────────────────────────────────────
class ParserBackend:
    name = "base"

    def available(self):
        return False

    def parse(self, raw_bytes, fmt):  # -> str (raise 가능, 상위가 typed error 로 감쌈)
        raise NotImplementedError


class PlainTextBackend(ParserBackend):
    """텍스트류 폴백 — 항상 가용. text/html 은 간이 태그 제거. 바이너리엔 안 씀."""
    name = "plain"
    HANDLES = _TEXT_FMTS

    def available(self):
        return True

    def parse(self, raw_bytes, fmt):
        text = raw_bytes.decode("utf-8", errors="replace") if isinstance(raw_bytes, bytes) else str(raw_bytes)
        if fmt == "html":
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
        return text


class MarkItDownBackend(ParserBackend):
    """MS MarkItDown — CLI 로 호출(html/docx/pptx/xlsx/pdf). markitdown CLI 또는 uvx 경유.
    실행 경로 미결선이면 available()=False(→BACKEND_NOT_WIRED)."""
    name = "markitdown"
    HANDLES = {"html", "docx", "pptx", "xlsx", "pdf"}
    _EXT = {"html": "html", "docx": "docx", "pptx": "pptx", "xlsx": "xlsx", "pdf": "pdf"}

    def available(self):
        if os.environ.get("BINGGU_PARSER_CLI_OFF") == "1":
            return False  # 상위 selftest 결정성 보장(실 CLI 0)
        return (_which("markitdown") or _which("uvx")) is not None

    def parse(self, raw_bytes, fmt):
        td = tempfile.mkdtemp(prefix="markitdown_")
        try:
            infile = os.path.join(td, "in." + self._EXT.get(fmt, "bin"))
            with open(infile, "wb") as f:
                f.write(raw_bytes)
            exe = _which("markitdown")
            # uvx 경유 시 PDF/office 풀파서를 위해 markitdown[all] extra 사용.
            cmd = [exe, infile] if exe else [_which("uvx"), "--from", "markitdown[all]", "markitdown", infile]
            rc, out, err = _run_cli(cmd)
            if rc != 0:
                raise RuntimeError("markitdown exit %d: %s" % (rc, err[:200]))
            return out.decode("utf-8", "replace")
        finally:
            shutil.rmtree(td, ignore_errors=True)  # temp 디렉토리 누수 방지(성공/실패 무관)


class KorDocBackend(ParserBackend):
    """KorDoc — npx CLI 로 호출(HWP/HWPX/PDF/XLSX/DOCX → Markdown). 한국 문서 강점.
    npx 미결선이면 available()=False(→BACKEND_NOT_WIRED).

    pdfjs-dist DEFERRED(보류·2026-06-29 재확인): kordoc 의 PDF 는 pdfjs-dist(optional peerDep·~35MB)
    미설치 시 빈 출력(soft fail) → _ROUTING['pdf'] 가 markitdown[all] 으로 폴백한다(실 PDF 추출 검증 완료).
    markitdown 이 PDF 를 충분히 커버하므로 pdfjs-dist 번들 추가는 보류. HWP/HWPX/XLSX/DOCX 등
    한국문서는 kordoc 단독 경로 유지(npx kordoc v3.5.1 실행 확인)."""
    name = "kordoc"
    HANDLES = {"hwp", "hwpx", "pdf", "xlsx", "docx"}
    _EXT = {"hwp": "hwp", "hwpx": "hwpx", "pdf": "pdf", "xlsx": "xlsx", "docx": "docx"}

    def available(self):
        if os.environ.get("BINGGU_PARSER_CLI_OFF") == "1":
            return False  # 상위 selftest 결정성 보장(실 CLI 0)
        return _which("npx") is not None

    def parse(self, raw_bytes, fmt):
        td = tempfile.mkdtemp(prefix="kordoc_")
        try:
            infile = os.path.join(td, "in." + self._EXT.get(fmt, "bin"))
            outfile = os.path.join(td, "out.md")
            with open(infile, "wb") as f:
                f.write(raw_bytes)
            cmd = [_which("npx"), "--no-install", "kordoc", infile,
                   "--format", "markdown", "--silent", "-o", outfile]
            rc, out, err = _run_cli(cmd)
            if rc != 0:
                raise RuntimeError("kordoc exit %d: %s" % (rc, err[:200]))
            if os.path.exists(outfile):
                with open(outfile, encoding="utf-8") as f:
                    txt = f.read()
                if txt.strip():
                    return txt
            # rc=0 이어도 출력 없음 = soft fail(예: PDF→pdfjs-dist 의존성 부족) → 호출부가 CALL_FAILED 분류
            raise RuntimeError("kordoc no output(의존성 부족 가능): %s"
                               % ((err or out.decode("utf-8", "replace"))[:150]))
        finally:
            shutil.rmtree(td, ignore_errors=True)  # temp 디렉토리 누수 방지(성공/실패 무관)


# 라우팅 우선순위(포맷별 backend 선호 순). 미가용은 자동 skip → 폴백 체인.
_ROUTING = {
    "text": ["plain"],
    "html": ["markitdown", "plain"],
    "pdf": ["kordoc", "markitdown"],
    "hwp": ["kordoc"],
    "hwpx": ["kordoc"],
    "xlsx": ["markitdown", "kordoc"],
    "docx": ["markitdown", "kordoc"],
    "pptx": ["markitdown"],
}


def _default_backends():
    return {b.name: b for b in (PlainTextBackend(), MarkItDownBackend(), KorDocBackend())}


def parse_document(raw_bytes, content_type=None, filename=None, backends=None):
    """전방위 파싱 진입점. **절대 raise 안 함**(조건3) — 결과는 항상 ParseResult dict.

    반환: {
      ok: bool,
      derived_text: str|None,        # 가공본(조건1 — 원문 아님)
      parser: str|None,              # 실제 쓴 backend
      fmt: str,
      raw_sha256: str,               # 원문 fingerprint(조건1)
      raw_len: int,
      error: {"type": <ERR_*>, "detail": str} | None
    }
    """
    backends = backends or _default_backends()
    raw = raw_bytes if isinstance(raw_bytes, (bytes, bytearray)) else str(raw_bytes).encode("utf-8")
    raw = bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    base = {"ok": False, "derived_text": None, "parser": None,
            "raw_sha256": sha, "raw_len": len(raw)}

    fmt = detect_format(content_type, filename)
    base["fmt"] = fmt

    if fmt == "unknown":
        base["error"] = {"type": ERR_UNSUPPORTED, "detail": "fmt=unknown ct=%r fn=%r" % (content_type, filename)}
        return base

    chain = _ROUTING.get(fmt, [])
    if not chain:
        base["error"] = {"type": ERR_UNSUPPORTED, "detail": "no route for fmt=%s" % fmt}
        return base

    last_err = None
    tried_available = False
    for bname in chain:
        b = backends.get(bname)
        if not b or not b.available():
            last_err = {"type": ERR_NOT_WIRED, "detail": "%s 미결선(CLI/런타임 호출 불가)" % bname}
            continue
        tried_available = True
        try:
            text = b.parse(raw, fmt)
        except subprocess.TimeoutExpired:                # backend 호출 timeout
            last_err = {"type": ERR_CALL_FAILED, "detail": "%s: timeout" % bname}
            continue
        except Exception as e:  # 조건3 — backend 호출 실패(exit≠0/예외)도 typed error 로 흡수
            last_err = {"type": ERR_CALL_FAILED, "detail": ("%s: %s" % (bname, e))[:200]}
            continue
        if not text or not str(text).strip():
            last_err = {"type": ERR_EMPTY, "detail": "%s produced empty" % bname}
            continue
        base.update({"ok": True, "derived_text": str(text), "parser": bname, "error": None})
        return base

    # 폴백: 텍스트류인데 backend 가 다 죽었으면 plain 으로 최후 시도(바이너리는 금지)
    if fmt in _TEXT_FMTS and (not tried_available or last_err):
        try:
            text = PlainTextBackend().parse(raw, fmt)
            if text and text.strip():
                base.update({"ok": True, "derived_text": text, "parser": "plain(fallback)", "error": None})
                return base
        except Exception as e:
            last_err = {"type": ERR_FAILED, "detail": "plain fallback: %s" % e}

    base["error"] = last_err or {"type": ERR_FAILED, "detail": "no backend succeeded"}
    return base


def _looks_corrupt(exc):
    s = str(exc).lower()
    return any(k in s for k in ("corrupt", "not a", "invalid", "bad", "cannot read", "damaged"))


# ── selftest (backend mock — 실 파서/네트워크 0) ──────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(cond)
        print(("  PASS " if cond else "  FAIL ") + name)

    # 1) 텍스트/HTML — selftest 는 실 CLI 안 타게 plain 만 주입(결정적·실네트워크 0). 실 markitdown 은 별도 샘플 검증.
    r = parse_document(b"<html><body><p>\xec\x95\x88\xeb\x85\x95 hello</p><script>x=1</script></body></html>",
                       content_type="text/html", filename="a.html",
                       backends={"plain": PlainTextBackend()})
    chk("P1 HTML 파싱 ok", r["ok"] and "hello" in r["derived_text"])
    chk("P1b script 제거", "x=1" not in r["derived_text"])
    chk("P1c raw_sha256 동봉(fingerprint)", len(r["raw_sha256"]) == 64)
    chk("P1d derived_text 분리(가공본)", r["derived_text"] is not None and r["parser"].startswith("plain"))

    # 2) 미지원 확장자 → typed error, raise 안 함
    r = parse_document(b"\x00\x01", filename="a.bin")
    chk("P2 미지원 → ok=False", r["ok"] is False)
    chk("P2b typed error UNSUPPORTED", r["error"]["type"] == ERR_UNSUPPORTED)

    # 3) 바이너리(PDF)인데 backend 미설치 → PARSER_MISSING, plain 폴백 안 함(깨진 텍스트 방지)
    no_backends = {"plain": PlainTextBackend()}  # markitdown/kordoc 미결선 가정
    r = parse_document(b"%PDF-1.4 ...", content_type="application/pdf", filename="a.pdf",
                       backends=no_backends)
    chk("P3 PDF backend 부재 → ok=False", r["ok"] is False)
    chk("P3b BACKEND_NOT_WIRED(미결선)", r["error"]["type"] == ERR_NOT_WIRED)
    chk("P3c 바이너리 plain 폴백 안 함", r["derived_text"] is None)

    # 4) backend 가 raise(호출 실패) → BACKEND_CALL_FAILED 로 흡수(전체 안 죽음)
    class BoomBackend(ParserBackend):
        name = "markitdown"
        def available(self): return True
        def parse(self, raw, fmt): raise RuntimeError("markitdown exit 1: bad file")
    r = parse_document(b"xxxx", content_type="application/pdf", filename="a.pdf",
                       backends={"markitdown": BoomBackend()})
    chk("P4 backend raise → typed error(raise 전파 0)", r["ok"] is False and r["error"] is not None)
    chk("P4b BACKEND_CALL_FAILED 분류", r["error"]["type"] == ERR_CALL_FAILED)

    # 5) mock 성공 backend → derived_text + parser 기록
    class OkBackend(ParserBackend):
        name = "markitdown"
        def available(self): return True
        def parse(self, raw, fmt): return "# 표\n매출 100"
    r = parse_document(b"\x50\x4b", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml",
                       filename="x.xlsx", backends={"markitdown": OkBackend()})
    chk("P5 xlsx mock 파싱 ok", r["ok"] and "매출" in r["derived_text"])
    chk("P5b parser=markitdown 기록", r["parser"] == "markitdown")

    # 6) detect_format
    chk("P6 hwp 감지", detect_format(filename="문서.hwp") == "hwp")
    chk("P6b pdf content-type 감지", detect_format(content_type="application/pdf") == "pdf")

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_parser_adapter — use --selftest, or import parse_document()")
