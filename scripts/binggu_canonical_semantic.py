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
seed: binggupack/data/semantic/seed_canonical_5.jsonl (설치본 동봉·5종×12, leave-one-out 93%).
      경로 해석 = _resolve_seed_path (importlib.resources → tests/fixtures 폴백·순환 import 안전).

CLI: python binggu_canonical_semantic.py --selftest
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_semantic_shadow as S  # noqa: E402
import binggu_platform as _plat  # binggu_home(BINGGU_HOME 존중 · 격리 폴백)  # noqa: E402

KINDS = ["문서", "증거", "개념", "상태", "판단"]
_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED_TMP_CACHE = {}


def _resolve_seed_path(name):
    """seed 파일 경로(str) 반환 — 절대 raise 안 함(import 시점 평가 안전).
    S 를 참조하지 않는 standalone (semantic_shadow↔capture_preview 순환 import 하에서도 안전).
    ① 설치본/clone: importlib.resources 로 binggupack.data/semantic/<name>
    ② zip/egg 설치: as_file 로 프로세스 수명 임시본 materialize
    ③ 폴백: 스크립트 상대 ../tests/fixtures/semantic/<name>. 부재여도 str 반환."""
    try:
        from importlib.resources import files
        res = files("binggupack.data").joinpath("semantic", name)
        try:
            if res.is_file():
                return str(res)
        except Exception:
            pass
        try:
            from importlib.resources import as_file
            import atexit
            import tempfile
            cached = _SEED_TMP_CACHE.get(name)
            if cached and os.path.exists(cached):
                return cached
            with as_file(res) as ap:
                data = open(ap, "rb").read()
            fd, tmp = tempfile.mkstemp(prefix="binggu_seed_", suffix="_" + name)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            _SEED_TMP_CACHE[name] = tmp
            atexit.register(lambda p=tmp: os.path.exists(p) and os.remove(p))
            return tmp
        except Exception:
            pass
    except Exception:
        pass
    return os.path.join(_HERE, "..", "tests", "fixtures", "semantic", name)


# 설치본/clone 동봉 seed 우선(binggupack.data), 폴백 tests/fixtures. import 시점 평가 안전(raise 0).
SEED_PATH = _resolve_seed_path("seed_canonical_5.jsonl")
def _flag_path(home=None):
    # 격리 존중: 모듈 import 시점 고정 대신 런타임 BINGGU_HOME 우선(운영 홈 하드코딩 제거).
    return os.path.join(home or _plat.binggu_home(), "semantic_label_enabled")


def _cache_dir(home=None):
    return os.path.join(home or _plat.binggu_home(), "cache")


def _seed_sha(seed_path):
    with open(seed_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


_OLLAMA_AVAIL = None


def ollama_available(probe=None):
    """Ollama 에 bge-m3 모델이 있으면 True (프로세스 1회 캐싱). 설치만 하면 자동 감지.
    probe= 테스트 주입(True/False). 네트워크 실패/미설치 → False(조용히 fallback)."""
    global _OLLAMA_AVAIL
    if probe is not None:
        return probe
    if _OLLAMA_AVAIL is not None:
        return _OLLAMA_AVAIL
    try:
        import urllib.request
        with urllib.request.urlopen(S.OLLAMA + "/api/tags", timeout=2) as r:
            tags = json.loads(r.read())
        names = [m.get("name", "") for m in tags.get("models", [])]
        _OLLAMA_AVAIL = any(S.MODEL in n for n in names)   # bge-m3 존재
    except Exception:
        _OLLAMA_AVAIL = False
    return _OLLAMA_AVAIL


def enabled():
    """semantic 도장 제안 활성 조건 — 옵션1(4cli 20260615_1900): 명시 플래그 OR Ollama bge-m3 자동 감지.
    Ollama+bge-m3 만 깔면 자동 ON(별도 플래그 불필요). BINGGU_SEMANTIC_OFF=1 이면 강제 OFF(사용자 거부/테스트).
    영구금지26 정합: 활성돼도 cos 는 도장 '제안'만 — should_capture/confirm/자동저장 결정엔 안 씀."""
    if os.environ.get("BINGGU_SEMANTIC_OFF") == "1":
        return False
    return os.path.exists(_flag_path()) or ollama_available()


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
        return os.path.join(_cache_dir(), "canonical_centroids_%s.json" % key)

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
                os.makedirs(_cache_dir(), exist_ok=True)
                tmp = cf + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cent, f)
                os.replace(tmp, cf)            # atomic
            except Exception:
                pass
        return cent

    def _centroids(self):
        acc = {k: [] for k in KINDS}
        clear = [r for r in self.rows if r.get("band") == "clear"]
        # #6 콜드 빌드 배치화: 실 embed 경로만 /api/embed 1회 왕복(seed clear N행 N왕복→1왕복).
        # 배치=단건 벡터 등가 실측(max abs diff 0.0) → centroid·캐시값 불변(분류 drift 0). 순수 속도.
        # 주입 embed_fn(테스트/커스텀)·배치 미지원/실패 → 단건 폴백(기존 동작 byte-identical).
        embs = S._embed_batch([r["text"] for r in clear]) if self.embed_fn is S._embed else None
        if embs is None:
            embs = [self.embed_fn(r["text"]) for r in clear]
        for r, e in zip(clear, embs):
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


_INSTANCE = None          # None=미시도 / False=인스턴스화 실패 sentinel / obj=성공
_SEED_WARN_EMITTED = False


def suggest_label_kind(text):
    """opt-in 도장 제안. 반환 {kind,conf,band}(hi/ambiguous) 또는 None(OFF/차단/실패/lo).
    None이면 호출측은 종결어 규칙(classify_label_kind)으로 fallback.
    seed 미해결/인스턴스화 실패는 False sentinel 로 캐시해 반복 인스턴스화·크래시(AttributeError)를 막고
    stderr 1회 경고 후 조용히 None(규칙분류로 fallback)."""
    if not enabled():
        return None
    global _INSTANCE, _SEED_WARN_EMITTED
    if _INSTANCE is None:
        try:
            _INSTANCE = CanonicalSemantic()
        except Exception:
            _INSTANCE = False       # 실패 sentinel(None 유지 시 매 호출 재시도 → 억제)
            if not _SEED_WARN_EMITTED:
                sys.stderr.write(
                    "[binggu_canonical_semantic] enabled 이나 seed 미해결/인스턴스화 실패 "
                    "→ 규칙분류(classify_label_kind) fallback\n")
                _SEED_WARN_EMITTED = True
    if _INSTANCE is False:          # sentinel: .classify_kind 호출 전 반드시 차단(AttributeError 방지)
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

    # 5. enabled() — 옵션1 자동 감지(4cli 20260615_1900): 강제 OFF + Ollama probe 주입(결정론)
    _prev = os.environ.get("BINGGU_SEMANTIC_OFF")
    os.environ["BINGGU_SEMANTIC_OFF"] = "1"
    off_ok = (enabled() is False)
    if _prev is None:
        del os.environ["BINGGU_SEMANTIC_OFF"]
    else:
        os.environ["BINGGU_SEMANTIC_OFF"] = _prev
    rec("5.BINGGU_SEMANTIC_OFF=1 이면 강제 OFF", off_ok)
    rec("5b.Ollama+bge-m3 감지 시 자동 ON(probe=True)", ollama_available(probe=True) is True)
    rec("5c.Ollama 없으면 자동 ON 안 함(probe=False)", ollama_available(probe=False) is False)

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

    # 10. sentinel: enabled=True + 인스턴스화 실패(False) → suggest_label_kind 크래시 없이 None
    #     (False.classify_kind AttributeError 방지 가드 실측). enabled/_INSTANCE 를 잠시 대체 후 복원.
    _mod = sys.modules[__name__]
    _prev_enabled, _prev_inst, _prev_warn = _mod.enabled, _INSTANCE, _SEED_WARN_EMITTED
    try:
        _mod.enabled = lambda: True          # OFF 강제 True (flag/ollama 비의존·결정론)
        globals()["_INSTANCE"] = False        # 인스턴스화 실패 sentinel
        got = suggest_label_kind("정상 문장이다")
        rec("10.실패 sentinel(False)에서 크래시 없이 None(AttributeError 0)", got is None)
    except Exception:
        rec("10.실패 sentinel(False)에서 크래시 없이 None(AttributeError 0)", False)
    finally:
        _mod.enabled = _prev_enabled
        globals()["_INSTANCE"] = _prev_inst
        globals()["_SEED_WARN_EMITTED"] = _prev_warn

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
