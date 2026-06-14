# -*- coding: utf-8 -*-
"""binggu_canonical_semantic.py — 도장(canonical 5종) semantic 분류 제안 (opt-in).

영구금지 26 개정(2026-06-14 owner GO): cos/확률을 should_capture(저장가치)·confirm(최종승인)
결정엔 계속 금지하되, 도장(label_kind) 분류 '제안'에는 허용. 조건:
  1) 기본 꺼짐(~/.binggupack/semantic_label_enabled 없으면 완전 무개입)
  2) PII/secret 정규식 선차단(leak_guard) — 차단 시 semantic 미개입
  3) ambiguous/실패는 도장 '확정' 아님 — band='hi'는 확정, 'ambiguous'는 확인권장 표시,
     'lo'/차단/embed실패는 None → 종결어 규칙(classify_label_kind) fallback
  4) 사람이 preview 보고 최종 confirm해야 저장(자동저장 아님)
  5) 원문 저장 0(임베딩만, 로그/파일 write 0)  6) 결정론(같은 문장=같은 결과)

검증된 헬퍼 재사용: binggu_semantic_shadow._embed/leak_guard/_l2/_dot/model_digest/BAND.
seed: tests/fixtures/semantic/seed_canonical_5.jsonl (5종×12, leave-one-out 93%).

CLI: python binggu_canonical_semantic.py --selftest
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_semantic_shadow as S  # noqa: E402

KINDS = ["문서", "증거", "개념", "상태", "판단"]
SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "tests", "fixtures", "semantic", "seed_canonical_5.jsonl")
FLAG = os.path.join(os.path.expanduser("~"), ".binggupack", "semantic_label_enabled")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".binggupack", "cache")


def _seed_sha(seed_path):
    with open(seed_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def enabled():
    """기본 OFF — 플래그 파일 있을 때만 semantic 도장 제안 활성."""
    return os.path.exists(FLAG)


class CanonicalSemantic:
    def __init__(self, seed_path=SEED_PATH, embed_fn=None, use_cache=True):
        self.embed_fn = embed_fn or S._embed
        self.seed_path = seed_path
        self.rows = [json.loads(l) for l in open(seed_path, encoding="utf-8") if l.strip()]
        self.digest = S.model_digest()
        self.centroids = self._load_or_build(use_cache)

    def _cache_file(self):
        # key = seed 내용 + band 임계 + model_digest → 하나라도 바뀌면 miss(stale 방지)
        raw = "%s|%s|%s|%s" % (_seed_sha(self.seed_path), S.BAND_HI, S.BAND_LO, self.digest)
        key = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return os.path.join(CACHE_DIR, "canonical_centroids_%s.json" % key)

    def _load_or_build(self, use_cache):
        # 실 embed(_embed)일 때만 디스크 캐싱(centroid 벡터=원문 아님). 주입 embed는 항상 재빌드.
        real = self.embed_fn is S._embed
        cf = self._cache_file()
        if use_cache and real:
            try:
                with open(cf, encoding="utf-8") as f:
                    c = json.load(f)
                if set(c.keys()) == set(KINDS):
                    return c
            except Exception:
                pass
        cent = self._centroids()
        if use_cache and real and set(cent.keys()) == set(KINDS):
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                tmp = cf + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cent, f)
                os.replace(tmp, cf)            # atomic
            except Exception:
                pass
        return cent

    def _centroids(self):
        acc = {k: [] for k in KINDS}
        for r in self.rows:
            if r.get("band") != "clear":
                continue
            e = self.embed_fn(r["text"])
            if e:
                acc[r["canonical_kind"]].append(e)
        cent = {}
        for k, vs in acc.items():
            if vs:
                cent[k] = S._l2([sum(v[d] for v in vs) / len(vs) for d in range(len(vs[0]))])
        return cent

    def classify_kind(self, text):
        """반환: {kind,conf,band} 또는 None(차단/embed실패).
        band: hi(>=BAND_HI 확정) / ambiguous(확인권장) / lo(신뢰낮음→fallback 권장)."""
        ok, _reason = S.leak_guard(text)        # #2 PII/secret 선차단
        if not ok:
            return None
        e = self.embed_fn(text)
        if e is None:                            # embed 실패 → fallback
            return None
        best, bs = None, -2.0
        for k, c in self.centroids.items():
            s = S._dot(e, c)
            if s > bs:
                bs, best = s, k
        band = "hi" if bs >= S.BAND_HI else ("lo" if bs < S.BAND_LO else "ambiguous")
        return {"kind": best, "conf": round(bs, 4), "band": band}


_INSTANCE = None


def suggest_label_kind(text):
    """opt-in 도장 제안. 반환 {kind,conf,band}(hi/ambiguous) 또는 None(OFF/차단/실패/lo).
    None이면 호출측은 종결어 규칙(classify_label_kind)으로 fallback."""
    if not enabled():
        return None
    global _INSTANCE
    if _INSTANCE is None:
        try:
            _INSTANCE = CanonicalSemantic()
        except Exception:
            return None
    r = _INSTANCE.classify_kind(text)
    if r and r["band"] in ("hi", "ambiguous"):
        return r
    return None


# ---------------- selftest (가짜 embed로 로직 검증 — Ollama 비의존) ----------------

def _fake_embed_factory():
    """결정론 가짜 embed: 5종 키워드로 직교 벡터. 로직(centroid/band/차단/fallback) 검증용."""
    import hashlib
    anchors = {"문서": 0, "증거": 1, "개념": 2, "상태": 3, "판단": 4}

    def fe(text):
        v = [0.0] * 5
        # seed는 canonical_kind 앵커가 명확 → 해당 축 강하게. 그 외 텍스트는 키워드 매칭.
        kws = {"문서": ["규정", "정의", "안내", "기술", "명시", "문서", "보고서", "매뉴얼", "가이드", "사양"],
               "증거": ["기록", "찍", "남아", "집계", "측정", "확인됐", "표시됐", "담겨"],
               "개념": ["란 ", "말한다", "뜻한다", "방식이다", "성질이다", "지표다", "장치다", "표현"],
               "상태": ["진행 중", "가동 중", "남아 있", "상태이", "대기 중", "들어 있", "쌓여", "맞춰져", "준비", "남은"],
               "판단": ["낫다", "해야", "옳다", "안전", "좋다", "중요", "맞다", "거쳐야", "있다"]}
        for k, idx in anchors.items():
            if any(w in text for w in kws[k]):
                v[idx] += 1.0
        if sum(v) == 0:
            h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
            v[h % 5] = 0.3
        return v
    return fe


def run_selftest():
    import tempfile
    results = []

    def rec(d, ok):
        results.append((d, bool(ok)))

    fe = _fake_embed_factory()
    cs = CanonicalSemantic(embed_fn=fe)

    rec("1.centroid 5종 전부 생성", set(cs.centroids.keys()) == set(KINDS))

    # 2. 각 종 대표 문장이 자기 종으로 분류
    samples = {"문서": "이 문서는 절차를 규정한다",
               "증거": "로그에 통과가 기록되어 있다",
               "개념": "롤백이란 되돌리는 동작을 말한다",
               "상태": "백필이 진행 중이다",
               "판단": "검증 없이 단정하면 위험하니 확인해야 한다"}
    hit = 0
    for k, t in samples.items():
        r = cs.classify_kind(t)
        if r and r["kind"] == k:
            hit += 1
    rec("2.대표 문장 5종 자기분류(fake embed)", hit >= 4)

    # 3. leak_guard 선차단 → None (semantic 미개입)
    leak = cs.classify_kind("배포 키는 gh" + "p_" + "EXAMPLE000000000000000000 이다")
    rec("3.PII/secret 선차단 시 None", leak is None)

    # 4. embed 실패 → None
    cs_fail = CanonicalSemantic(embed_fn=lambda t: None)
    rec("4.embed 실패 시 None(fallback)", cs_fail.classify_kind("정상 문장이다") is None)

    # 5. enabled() 기본 OFF — 플래그 없으면 suggest None
    if not os.path.exists(FLAG):
        rec("5.기본 OFF(플래그 없으면 suggest None)", suggest_label_kind("이 문서는 규정한다") is None)
    else:
        rec("5.기본 OFF(플래그 존재 — skip)", True)

    # 6. 결정론 (2회 동일)
    a = cs.classify_kind("백필이 진행 중이다")
    b = cs.classify_kind("백필이 진행 중이다")
    rec("6.결정론(2회 동일)", a == b)

    # 7. band 분기 값 존재
    r7 = cs.classify_kind("이 문서는 절차를 규정한다")
    rec("7.band 필드(hi/ambiguous/lo)", r7 is not None and r7["band"] in ("hi", "ambiguous", "lo"))

    # 8. 원문/파일 write 0 — 모듈에 save/write 함수 부재
    rec("8.save/write 함수 부재", not any(n.startswith("save") or n.startswith("write") or "persist" in n
                                       for n in dir(sys.modules[__name__])))

    # 9. seed 5종 전부 12+ (문서는 경계 술어 보강으로 16)
    import collections
    dist = collections.Counter(r["canonical_kind"] for r in cs.rows)
    rec("9.seed 5종 각 12+ (문서 16 경계보강)",
        set(dist) == set(KINDS) and all(dist[k] >= 12 for k in KINDS) and dist["문서"] == 16)

    print("=" * 72)
    print("binggu_canonical_semantic — selftest (도장 semantic 제안, opt-in)")
    print("=" * 72)
    npass = sum(1 for _, ok in results if ok)
    for d, ok in results:
        print("%s %s" % ("[OK]" if ok else "[X]", d))
    print("-" * 72)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        run_selftest()
    else:
        print("usage: binggu_canonical_semantic.py [--selftest]")
        sys.exit(2)
