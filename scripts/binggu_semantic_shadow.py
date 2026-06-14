# -*- coding: utf-8 -*-
"""binggu_semantic_shadow.py — L2-only semantic_subtype shadow 분류 (PoC).

정본 무변경 · 추천만 · 저장 0 · 로깅만. 4cli R2 16게이트 + centroid(설치0·결정적·실측 38→77%).
파이프라인: [L1 정규식 hard gate(bge 입력 전 재적용)] → [bge-m3 embed(CPU·concurrency=1)]
           → [seed centroid cos → semantic_subtype·band] → [shadow log(salt+HMAC·원문0)]

게이트 매핑(§9):
  #1 L1 정규식을 bge 호출 직전 재적용(leak_guard) · #2 shadow log 전문저장 금지(해시/메타만)
  #4 salt+HMAC 역추적 방어 · #5 Ollama L2=CPU·순차·timeout→None · #6 bge deterministic(temp 무관)
  #7 L1 런타임 우회 방지(leak_guard가 embed 전 항상) · #8 재현성 스냅샷(model_digest+band+build_hash)
  #10 모든 embed는 _embed wrapper 경유 · #13 default hard-disable(DEFAULT_ENABLED=False)
  #16 저장0·ledger0·preview0·shadow report only (shadow.jsonl 만 write, ledger 미접촉)
  L3(qwen)·reason_codes enum·prompt_hash = L2-only 범위 밖(연기).

정본 무변경(import 호출만): classify_label_kind · scan_residual_pii · SECRET_PATTERNS · _PREVIEW_PII_EXTRA.
"""
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# --- L1 rule layer 정본 (무변경·호출만) ---
from watcher_batch_m1 import scan_residual_pii                         # noqa: E402
from openbinggu_incoming_to_staging import SECRET_PATTERNS            # noqa: E402
import openbinggu_conversation_capture_preview as _cap               # _PREVIEW_PII_EXTRA  # noqa: E402
from openbinggu_label_kind_map import classify_label_kind            # canonical 도장  # noqa: E402

HOME = os.path.join(os.path.expanduser("~"), ".binggupack")
SEM_DIR = os.path.join(HOME, "semantic")
SEED_PATH = os.path.join(HERE, "..", "tests", "fixtures", "semantic", "seed_candidates.jsonl")
OLLAMA = "http://127.0.0.1:11434"
MODEL = "bge-m3"
SUBTYPES = ["교훈", "결정", "선호", "설계결정", "버그패턴", "사실"]
BAND_HI, BAND_LO = 0.62, 0.50            # centroid cos 임계(실측 초기값, owner 보정)
DEFAULT_ENABLED = False                  # #13 default hard-disable — 절대 자동 활성 0


# ---------------- #1·#7 L1 정규식 hard gate (bge 입력 전 재적용) ----------------
def leak_guard(text):
    """secret/PII hit → (False, reason). 통과 → (True, None). 정규식 모듈 호출만(정본 무변경)."""
    pii = scan_residual_pii(text) + [k for k, rx in _cap._PREVIEW_PII_EXTRA if rx.search(text)]
    if pii:
        return False, "pii:" + pii[0]
    if any(p.search(text) for p in SECRET_PATTERNS):
        return False, "secret"
    return True, None


# ---------------- #4 salt+HMAC (역추적 방어) ----------------
def _salt():
    os.makedirs(SEM_DIR, exist_ok=True)
    p = os.path.join(SEM_DIR, "salt")
    if not os.path.exists(p):
        with open(p, "wb") as f:
            f.write(os.urandom(32))
    with open(p, "rb") as f:
        return f.read()


def hmac_id(text, salt=None):
    s = salt if salt is not None else _salt()
    norm = re.sub(r"\s+", " ", text).strip().encode("utf-8")
    return hmac.new(s, norm, hashlib.sha256).hexdigest()[:16]


# ---------------- #5·#10 embed wrapper (L2=CPU·순차·timeout→None) ----------------
def _embed(text, timeout=10):
    """모든 임베딩은 이 wrapper 경유(#10). 실패/타임아웃 → None(호출측 rule fallback)."""
    body = json.dumps({"model": MODEL, "input": text}).encode()
    req = urllib.request.Request(OLLAMA + "/api/embed", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        v = d["embeddings"][0] if "embeddings" in d else d.get("embedding")
        return _l2(v) if v else None
    except Exception:
        return None


def _l2(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def model_digest():
    """#6·#8 모델 식별 — digest 변경 시 centroid 캐시 무효(drift regression 트리거)."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10) as r:
            d = json.loads(r.read())
        for m in d.get("models", []):
            if m.get("name", "").startswith(MODEL):
                dig = (m.get("digest") or "")[:16]
                if dig:
                    return dig
    except Exception:
        pass
    return hashlib.sha256(MODEL.encode()).hexdigest()[:16]


# ---------------- centroid 분류기 (결정적·#6) ----------------
class SemanticShadow:
    def __init__(self, seed_path=SEED_PATH, embed_fn=None):
        self.embed_fn = embed_fn or _embed
        self.rows = [json.loads(l) for l in open(seed_path, encoding="utf-8") if l.strip()]
        self.digest = model_digest()
        self.build_hash = self._build_hash(seed_path)
        self.centroids = self._centroids()

    def _build_hash(self, seed_path):
        # #8 재현성 — seed 내용 + 임계 + model_digest
        h = hashlib.sha256()
        with open(seed_path, "rb") as f:
            h.update(f.read())
        h.update(("%s|%s|%s" % (BAND_HI, BAND_LO, self.digest)).encode())
        return h.hexdigest()[:16]

    def _centroids(self):
        # subtype별 clear seed 평균(정규화). embed_fn 호출(wrapper #10)
        acc = {st: [] for st in SUBTYPES}
        for r in self.rows:
            if r["band"] != "clear":
                continue
            e = self.embed_fn(r["text"])
            if e:
                acc[r["semantic_subtype"]].append(e)
        cent = {}
        for st, vs in acc.items():
            if vs:
                cent[st] = _l2([sum(v[d] for v in vs) / len(vs) for d in range(len(vs[0]))])
        return cent

    def classify(self, text):
        """반환: dict(hmac_id·len·rule_label·sem_subtype·sem_conf·band·blocked·latency_ms·model_digest).
        저장 0 — 분류·로깅만. blocked=True 면 L1 차단(추천 없음)."""
        t0 = time.time()
        # #1·#7 L1 먼저 — 차단되면 embed 호출 0
        ok, reason = leak_guard(text)
        if not ok:
            return {"hmac_id": hmac_id(text), "text_len": len(text), "blocked": True,
                    "reason": reason, "sem_subtype": None, "sem_conf": None, "band": "blocked",
                    "rule_label": None, "latency_ms": int((time.time() - t0) * 1000),
                    "model_digest": self.digest}
        rule_label = classify_label_kind(text)[0]    # canonical(정본·표시용)
        e = self.embed_fn(text)
        if e is None:                                 # #5 fail → rule fallback(추천 없음)
            return {"hmac_id": hmac_id(text), "text_len": len(text), "blocked": False,
                    "sem_subtype": None, "sem_conf": None, "band": "no_embed",
                    "rule_label": rule_label, "latency_ms": int((time.time() - t0) * 1000),
                    "model_digest": self.digest}
        best, bs = None, -2.0
        for st, c in self.centroids.items():
            s = _dot(e, c)
            if s > bs:
                bs, best = s, st
        band = "hi" if bs >= BAND_HI else ("lo" if bs < BAND_LO else "ambiguous")
        return {"hmac_id": hmac_id(text), "text_len": len(text), "blocked": False,
                "sem_subtype": best, "sem_conf": round(bs, 4), "band": band,
                "rule_label": rule_label, "latency_ms": int((time.time() - t0) * 1000),
                "model_digest": self.digest}

    def subtype_suggestion(self, text):
        """preview 전용 read-only subtype 추천. capture 결정 미개입 · 파일/로그/salt/hmac write 0.
        반환: None(L1 차단 or embed 실패) 또는 {sem_subtype, sem_conf, band}.
        cos = subtype 추천/설명 전용 — should_capture 판단에 절대 쓰지 않음(규칙 게이트 책임)."""
        ok, _ = leak_guard(text)
        if not ok:
            return None
        e = self.embed_fn(text)
        if e is None:
            return None
        best, bs = None, -2.0
        for st, c in self.centroids.items():
            s = _dot(e, c)
            if s > bs:
                bs, best = s, st
        band = "hi" if bs >= BAND_HI else ("lo" if bs < BAND_LO else "ambiguous")
        return {"sem_subtype": best, "sem_conf": round(bs, 4), "band": band}


# ---------------- #2·#16 shadow logger (원문 0 · 별도 파일 · ledger 미접촉) ----------------
SHADOW_LOG = os.path.join(SEM_DIR, "shadow.jsonl")
_ALLOWED = {"hmac_id", "text_len", "rule_label", "sem_subtype", "sem_conf",
            "band", "blocked", "reason", "latency_ms", "model_digest", "build_hash"}


def shadow_log(rec, path=SHADOW_LOG):
    """화이트리스트 필드만 append. 원문/prompt/embedding 전문 금지(#2). ledger 미접촉(#16)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    safe = {k: v for k, v in rec.items() if k in _ALLOWED}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    return safe


# ---------------- selftest (temp·mock·정본 무변경·저장0) ----------------
def _selftest():
    import tempfile
    import shutil
    from openbinggu_save_intent_outbox_runner import OPERATING_PATHS
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}

    # mock embed — 결정적(텍스트 해시 기반 의사 벡터). 실제 Ollama 미접촉(selftest 격리)
    def mock_embed(text, timeout=10):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return _l2([h[i % len(h)] / 255.0 for i in range(64)])

    # 호출 카운트용 wrapper (#7 호출스택 동등성 검증)
    calls = {"n": 0}

    def counted_embed(text, timeout=10):
        calls["n"] += 1
        return mock_embed(text)

    sh = SemanticShadow(embed_fn=counted_embed)
    ck(len(sh.centroids) == 6, "centroid 6 subtype 생성")
    ck(len(sh.digest) >= 8 and len(sh.build_hash) == 16, "#8 model_digest + build_hash 기록")

    # #1·#7 L1 차단 → embed 호출 0
    PII = "담당자 연락처는 010-" + "1234-5678 이고 마진이 낮아 보류한다."
    before = calls["n"]
    r_pii = sh.classify(PII)
    ck(r_pii["blocked"] and calls["n"] == before,
       "#1·#7 PII 차단 → embed 미호출(L1이 bge 입력 전·호출스택 동등성)")

    SEC = "발급된 값은 sk-live-" + "abcdef0123456789 형식이다."  # SECRET_PATTERNS의 sk-live- prefix 검출 / scanner secret_kv(키워드 필요)는 회피 — 합성값
    r_sec = sh.classify(SEC)
    ck(r_sec["blocked"], "#1 secret 차단")

    # 정상 분류
    r = sh.classify("이 기능은 위험이 커서 기본 비활성으로 잠가 둔다.")
    ck((not r["blocked"]) and r["sem_subtype"] in SUBTYPES and r["band"] in ("hi", "lo", "ambiguous"),
       "정상 분류 → subtype + band")
    ck(r["rule_label"] in ("문서", "증거", "개념", "상태", "판단"),
       "rule_label(canonical 5종) 병기 — 정규식 무변경 호출")

    # #5 embed 실패 → rule fallback(추천 없음·crash 0)
    sh_fail = SemanticShadow(embed_fn=lambda t, timeout=10: None)
    rf = sh_fail.classify("백업 없이 큰 변경을 하면 되돌리기 어렵다.")
    ck(rf["band"] == "no_embed" and rf["sem_subtype"] is None and rf["rule_label"],
       "#5 embed 실패 → rule fallback(추천 없음·crash 0)")

    # #2·#4·#16 shadow log — salt+HMAC·원문 0·저장0
    tmp = tempfile.mkdtemp(prefix="sem_shadow_")
    logp = os.path.join(tmp, "shadow.jsonl")
    TXT = "마감 직전에 화면이 바뀌면 자동화가 깨지니 미리 점검한다."
    rec = sh.classify(TXT)
    safe = shadow_log(rec, path=logp)
    blob = open(logp, encoding="utf-8").read()
    ck(TXT not in blob and TXT[:12] not in blob, "#2 shadow.jsonl 원문 전문 미저장")
    ck(set(safe.keys()) <= _ALLOWED and len(safe["hmac_id"]) == 16,
       "#2·#4 화이트리스트 필드 + HMAC id(평문 해시 아님)")
    ck(rec["hmac_id"] == hmac_id(TXT), "#4 HMAC 재계산 일치(salt 기반)")
    shutil.rmtree(tmp, ignore_errors=True)

    # #13 default hard-disable
    ck(DEFAULT_ENABLED is False, "#13 DEFAULT_ENABLED=False (자동 활성 0)")

    # #16 저장 0 — 운영 store 불변(ledger 미접촉)
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "#16 운영 store 불변(ledger write 0)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    # --eval: 실제 Ollama로 seed LOO 정확도(설치0 centroid 실측 재현)
    if sys.argv[1] == "--eval":
        sh = SemanticShadow()
        rows = sh.rows
        clear = [r for r in rows if r["band"] == "clear"]
        print("centroid 분류기 로드 — digest=%s build=%s, centroid %d subtype"
              % (sh.digest, sh.build_hash, len(sh.centroids)))
        print("(정확도 실측은 tmp/semantic_l1_improve.py 참조 — 본 모듈은 분류 API)")
        sys.exit(0)
    print("usage: binggu_semantic_shadow.py [--selftest | --eval]")
    sys.exit(2)
