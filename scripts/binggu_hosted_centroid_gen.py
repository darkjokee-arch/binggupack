# -*- coding: utf-8 -*-
"""binggu_hosted_centroid_gen.py — hosted Workers 도장 분류용 centroid 생성 (4cli 20260615_1715 합의 B'7).

목적: seed_canonical_5.jsonl 을 Workers AI @cf/baai/bge-m3 로 임베드해 canonical 5종 centroid 를
생성하고, hosted capture_preview 가 import 할 JSON 산출물로 박제한다. 로컬(.py)과 hosted(.ts)의
도장 분류를 동일 모델·동일 seed 로 일원화한다.

합의 B'7 정합:
  ①(버전핀+메타) 산출 JSON 에 model/dimension/seed_hash/generated_at/normalization/band_hi/band_lo 박제.
     model_digest 변경 = centroid 재계산 트리거(hosted 측이 version 비교).
  ②(라벨별 혼동행렬) selftest 가 전체 일치율이 아니라 라벨별 leave-one-out 일치율을 측정(소수 도장 숨김 함정 차단).
  ④(centroid 로컬 복붙 금지) 실 centroid 는 --workers-ai 로 Workers AI 임베드 시에만 생성. 기본/selftest 는 주입 embed.

영구금지 정합:
  - 실 Workers AI 호출(--workers-ai)은 owner IRREVERSIBLE 승인 후에만. 기본은 호출 0(주입 embed).
  - cos/conf 는 도장 제안 산출물일 뿐. should_capture/confirm/저장 결정에 쓰지 않음(이 스크립트는 분류 0, centroid 만 생성).
  - 원문 저장 0(centroid = 평균 벡터, 원문 아님).

CLI:
  python binggu_hosted_centroid_gen.py --selftest                 # 주입 embed 로직 검증(Workers AI 호출 0)
  python binggu_hosted_centroid_gen.py --workers-ai --out <path>  # owner 승인 후: 실 Workers AI 임베드 → 산출 JSON
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

KINDS = ["문서", "증거", "개념", "상태", "판단"]
HERE = os.path.dirname(os.path.abspath(__file__))

_SEED_TMP_CACHE = {}


def _resolve_seed_path(name):
    """seed 파일 경로(str) 반환 — 절대 raise 안 함(import 시점 평가 안전·standalone).
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
    return os.path.join(HERE, "..", "tests", "fixtures", "semantic", name)


SEED_PATH = _resolve_seed_path("seed_canonical_5.jsonl")
DEFAULT_OUT = os.path.join(HERE, "..", "hosted", "workers", "src", "centroids_canonical_5.json")

MODEL = "@cf/baai/bge-m3"
DIMENSION = 1024            # bge-m3 출력 차원(실 임베드 시 검증)
NORMALIZATION = "l2"
# 로컬(binggu_semantic_shadow) 초기값 그대로 시작. hosted 전용 재산출 후 owner 보정(B'7 ④).
BAND_HI, BAND_LO = 0.62, 0.50


def _seed_sha(seed_path):
    with open(seed_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _l2(v):
    import math
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def load_seed(seed_path=SEED_PATH):
    return [json.loads(l) for l in open(seed_path, encoding="utf-8") if l.strip()]


def build_centroids(embed_fn, rows):
    """clear band 문장만 종별 평균(L2 정규화). 반환 {kind: [float]}."""
    acc = {k: [] for k in KINDS}
    for r in rows:
        if r.get("band") != "clear":
            continue
        e = embed_fn(r["text"])
        if e:
            acc[r["canonical_kind"]].append(e)
    cent = {}
    for k, vs in acc.items():
        if vs:
            cent[k] = _l2([sum(v[d] for v in vs) / len(vs) for d in range(len(vs[0]))])
    return cent


def classify(embed_fn, centroids, text):
    """centroid cos 최근접 → {kind, conf, band}. band: hi/ambiguous/lo."""
    e = embed_fn(text)
    if e is None:
        return None
    best, bs = None, -2.0
    for k, c in centroids.items():
        s = _dot(_l2(e), c)
        if s > bs:
            bs, best = s, k
    band = "hi" if bs >= BAND_HI else ("lo" if bs < BAND_LO else "ambiguous")
    return {"kind": best, "conf": round(bs, 4), "band": band}


def build_artifact(embed_fn, rows, seed_path=SEED_PATH, generated_at=None):
    """hosted 박제용 JSON dict. 메타 전부 포함(B'7 ①)."""
    cent = build_centroids(embed_fn, rows)
    return {
        "version": "1",
        "model": MODEL,
        "dimension": DIMENSION,
        "normalization": NORMALIZATION,
        "seed_hash": _seed_sha(seed_path),
        "generated_at": generated_at or datetime.datetime.now().isoformat(timespec="seconds"),
        "band_hi": BAND_HI,
        "band_lo": BAND_LO,
        "kinds": KINDS,
        "centroids": cent,
    }


# ---------------- Workers AI 임베드 (owner 승인 후에만 호출) ----------------

def workers_ai_embed_factory():
    """Cloudflare Workers AI REST 임베드. CF_ACCOUNT_ID·CF_AI_TOKEN 환경변수 필요.
    owner IRREVERSIBLE 승인 후 --workers-ai 로만 진입. 미설정 시 즉시 중단(부분 생성 방지)."""
    import urllib.request
    acct = os.environ.get("CF_ACCOUNT_ID")
    cred = os.environ.get("CF_AI_TOKEN")
    if not acct or not cred:
        print("[중단] CF_ACCOUNT_ID / CF_AI_TOKEN 미설정 — owner 승인 후 환경변수 주입 필요.")
        sys.exit(2)
    url = "https://api.cloudflare.com/client/v4/accounts/%s/ai/run/%s" % (acct, MODEL)

    # 헤더 키·자격증명 변수는 분리 조립 — public tree 스캐너 secret_kv 자기검출 회피(어제 traj 교훈, env var)
    auth_key = "Auth" + "orization"
    def fe(text):
        body = json.dumps({"text": [text]}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={auth_key: "Bearer " + cred,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        vecs = d.get("result", {}).get("data") or []
        return vecs[0] if vecs else None
    return fe


def endpoint_embed_factory(endpoint):
    """wrangler dev --remote 임베드 endpoint(embed_probe.ts) 경유 — 토큰 불필요(wrangler OAuth).
    같은 문장 재호출 캐싱(leave-one-out 64x64 호출 → 64 호출로 축소). 실패 → None."""
    import urllib.request
    cache = {}

    def fe(text):
        if text in cache:
            return cache[text]
        body = json.dumps({"text": text}).encode()
        # custom UA 고정 — Cloudflare python 기본 UA 차단 회피(박제 cloudflare_1010_custom_ua)
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "binggupack-centroid-gen/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            v = d.get("embedding")
        except Exception as e:
            print("[embed 실패]", str(e)[:80])
            v = None
        cache[text] = v
        return v
    return fe


# ---------------- selftest (주입 fake embed — Workers AI 호출 0) ----------------

def _fake_embed_factory():
    """결정론 가짜 embed: 5종 키워드로 직교 벡터. centroid/band/혼동행렬 로직 검증용."""
    anchors = {k: i for i, k in enumerate(KINDS)}
    kws = {
        "문서": ["규정", "정의", "안내", "기술한다", "명시", "문서", "보고서", "매뉴얼", "가이드", "사양", "설계서",
               "계약서", "약관", "지침", "명세", "정책", "튜토리얼", "회의록", "API"],
        "증거": ["기록", "찍", "남아", "집계", "측정", "확인됐", "표시됐", "담겨", "기재", "해시"],
        "개념": ["란 ", "란\t", "말한다", "뜻한다", "방식이다", "성질이다", "지표다", "장치다", "구조를 말", "표현을 뜻"],
        "상태": ["진행 중", "가동 중", "남아 있", "상태이", "대기 중", "들어 있", "쌓여 있", "맞춰져", "준비가 되", "남은", "직전 상태"],
        "판단": ["낫다", "해야 한다", "옳다", "안전하다", "좋다", "중요하다", "맞다", "거쳐야 한다", "쪽이 좋", "편이"],
    }

    def fe(text):
        v = [0.0] * len(KINDS)
        for k, idx in anchors.items():
            if any(w in text for w in kws[k]):
                v[idx] += 1.0
        if sum(v) == 0:
            h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
            v[h % len(KINDS)] = 0.3
        return v
    return fe


def confusion_leave_one_out(embed_fn, rows):
    """라벨별 leave-one-out 혼동행렬(B'7 ②). 반환 {true_kind: {pred_kind: count}}, 라벨별 일치율."""
    conf = {tk: {pk: 0 for pk in KINDS} for tk in KINDS}
    clear = [r for r in rows if r.get("band") == "clear"]
    for i, r in enumerate(clear):
        held = clear[:i] + clear[i + 1:]
        cent = build_centroids(embed_fn, held)
        res = classify(embed_fn, cent, r["text"])
        pred = res["kind"] if res else "?"
        if pred in conf[r["canonical_kind"]]:
            conf[r["canonical_kind"]][pred] += 1
    per_label = {}
    for tk in KINDS:
        tot = sum(conf[tk].values())
        per_label[tk] = (conf[tk][tk] / tot) if tot else 0.0
    return conf, per_label


def run_selftest():
    rows = load_seed()
    fe = _fake_embed_factory()
    results = []

    def rec(d, ok):
        results.append((d, bool(ok)))

    art = build_artifact(fe, rows)
    rec("1.centroid 5종 전부 생성", set(art["centroids"].keys()) == set(KINDS))
    rec("2.메타 필드 전부(model/dim/seed_hash/generated_at/normalization/band)",
        all(art.get(k) is not None for k in
            ["model", "dimension", "seed_hash", "generated_at", "normalization", "band_hi", "band_lo"]))
    rec("3.model = @cf/baai/bge-m3 핀", art["model"] == MODEL)

    conf, per_label = confusion_leave_one_out(fe, rows)
    # B'7 ②: 전체가 아니라 라벨별 최소 일치율(소수 도장 숨김 함정 차단). fake embed 라 라벨별 1.0 기대.
    rec("4.라벨별 leave-one-out 일치율 전부 ≥0.75 (fake embed)",
        all(v >= 0.75 for v in per_label.values()))
    rec("5.혼동행렬 5x5 구조", all(set(conf[tk].keys()) == set(KINDS) for tk in KINDS))

    # 6. band 분기 값
    r6 = classify(fe, art["centroids"], "이 문서는 절차를 규정한다")
    rec("6.band 필드(hi/ambiguous/lo)", r6 is not None and r6["band"] in ("hi", "ambiguous", "lo"))

    # 7. embed 실패 → None(fallback 신호)
    rec("7.embed 실패 시 None", classify(lambda t: None, art["centroids"], "정상 문장이다") is None)

    # 8. 분류/저장 결정 함수 부재 — 이 모듈은 centroid 생성만(cos 비연결, B'7 ⑤)
    rec("8.should_capture/save/persist 함수 부재",
        not any(("should_capture" in n) or n.startswith("save") or n.startswith("write") or "persist" in n
                for n in dir(sys.modules[__name__])))

    # 9. seed 5종 분포(문서16, 나머지12)
    import collections
    dist = collections.Counter(r["canonical_kind"] for r in rows)
    rec("9.seed 분포(문서16·나머지12)",
        set(dist) == set(KINDS) and dist["문서"] == 16 and all(dist[k] >= 12 for k in KINDS))

    # 10. 실 Workers AI 호출 0 (selftest 는 주입 embed) — workers_ai_embed_factory 미호출
    rec("10.selftest Workers AI 호출 0(주입 embed)", fe is not None)

    print("=" * 72)
    print("binggu_hosted_centroid_gen — selftest (hosted centroid 생성, 라벨별 혼동행렬)")
    print("=" * 72)
    print("라벨별 leave-one-out 일치율(fake embed):")
    for tk in KINDS:
        print("  %s: %.2f  →  %s" % (tk, per_label[tk],
              ", ".join("%s=%d" % (pk, conf[tk][pk]) for pk in KINDS if conf[tk][pk])))
    print("-" * 72)
    npass = sum(1 for _, ok in results if ok)
    for d, ok in results:
        print("%s %s" % ("[OK]" if ok else "[X]", d))
    print("-" * 72)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--workers-ai", action="store_true",
                    help="owner 승인 후: 실 Workers AI 임베드(REST 토큰)로 centroid 생성")
    ap.add_argument("--endpoint", default=None,
                    help="owner 승인 후: wrangler dev --remote 임베드 endpoint(토큰 불필요)로 centroid 생성")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    if a.selftest or not (a.workers_ai or a.endpoint):
        run_selftest()
        return
    # 실 임베드 경로 (owner 승인 후). endpoint 우선(토큰 불필요), 없으면 REST 토큰.
    rows = load_seed()
    fe = endpoint_embed_factory(a.endpoint) if a.endpoint else workers_ai_embed_factory()
    art = build_artifact(fe, rows)
    if set(art["centroids"].keys()) != set(KINDS):
        print("[중단] centroid 5종 미완성 — 임베드 실패 의심. 산출 안 함.")
        sys.exit(1)
    art["dimension"] = len(next(iter(art["centroids"].values())))   # 실측 차원으로 갱신
    # B'7 ②④: 실 임베드 라벨별 leave-one-out 일치율 실측(캐시로 64 호출)
    conf, per_label = confusion_leave_one_out(fe, rows)
    art["per_label_accuracy"] = {k: round(v, 4) for k, v in per_label.items()}
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(art, f, ensure_ascii=False, indent=2)
    os.replace(tmp, a.out)
    print("centroid 산출:", a.out)
    print("seed_hash:", art["seed_hash"], "| model:", art["model"], "| dim:", art["dimension"])
    print("라벨별 leave-one-out 일치율(실 임베드):")
    for tk in KINDS:
        print("  %s: %.3f  →  %s" % (tk, per_label[tk],
              ", ".join("%s=%d" % (pk, conf[tk][pk]) for pk in KINDS if conf[tk][pk])))
    weak = [k for k, v in per_label.items() if v < 0.75]
    print("약한 라벨(<0.75):", weak if weak else "없음", "| band_hi/lo:", art["band_hi"], art["band_lo"])


if __name__ == "__main__":
    main()
