# -*- coding: utf-8 -*-
"""binggupack.config — 영속 config JSON 단일 로더(레지스트리 + 캐시).

목적: <home>/<name>.json 형태로 흩어진 사용자 설정 파일(binggu_config / capture_scope /
close_phrases / harvest_sources …)을 한 진입점으로 로드한다. 각 소비처가 json.loads +
부재/손상 방어 + 기본값 병합을 개별 재구현하던 중복을 제거한다.

정본 재사용(홈 재해석 금지):
  경로는 binggupack.paths.state_path(== scripts/binggu_paths.state_path) 를 그대로 쓴다.
  state_path(name) == <home>/<name> (home() = BINGGU_HOME 우선 · 없으면 OS별 홈/.binggupack).
  명시 home 인자를 준 경우만 <home>/<name>.json 을 직접 조립(테스트/격리용).

설계 원칙:
  - 파일 부재/손상/타입불일치 = 등록 기본값 사본(예외 0). p1_config._scope() 방어 스타일.
  - binggu_config 는 p1_config.load_user_config 로 "위임"(검증/coerce 재사용, 중복 0).
    이 경우 로더는 파일을 직접 읽지 않고 위임 함수의 결과를 그대로(캐시만) 반환한다.
  - 그 외(flat shape)는 기본값 위에 파일 dict 를 shallow 병합 + 선택 coerce 적용.
  - 캐시 키 = (name, abspath(config_path)). writer 는 write 직후 invalidate 로 무효화.
    hot-reload 가 필요한 소비처는 use_cache=False 로 항상 fresh 로드.

확장: register(name, default_factory, coerce=None, delegate=None) 로 신규 config 등록.

CLI: python -m binggupack.config  (또는 이 파일 직접 실행) → --selftest
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # 직접 실행(python binggupack/config.py) 시 repo 루트 부트스트랩
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binggupack.paths import state_path  # 경로 정본 재사용(홈 재해석 금지). scripts/ 도 sys.path 에 얹힘.


# ─────────────────────────────────────────────────────────────────────────────
# 레지스트리 — name -> (default_factory, coerce, delegate)
#   default_factory(home) -> dict   : 파일 부재/손상 시 반환할 기본값 사본
#   coerce(merged) -> dict | None    : 병합 결과 정규화(선택)
#   delegate(home) -> dict | None    : 지정 시 파일 직접읽기 대신 이 함수 결과를 그대로 반환(위임)
# ─────────────────────────────────────────────────────────────────────────────
class _Entry:
    __slots__ = ("default_factory", "coerce", "delegate")

    def __init__(self, default_factory, coerce=None, delegate=None):
        self.default_factory = default_factory
        self.coerce = coerce
        self.delegate = delegate


_REGISTRY: dict[str, _Entry] = {}
_CACHE: dict[tuple, dict] = {}


def register(name, default_factory, coerce=None, delegate=None):
    """신규(또는 기존 재정의) config 등록. default_factory(home)->dict 필수."""
    _REGISTRY[name] = _Entry(default_factory, coerce, delegate)


def config_path(name, home=None):
    """설정파일 경로 = <home>/<name>.json (정본 state_path 재사용)."""
    if home:
        return Path(home) / (name + ".json")
    return Path(state_path(name + ".json"))


def _cache_key(name, home):
    return (name, os.path.normcase(os.path.abspath(str(config_path(name, home)))))


def _resolve(name, home, entry):
    # 위임 등록 — 파일 직접 읽기 없이 위임 함수 결과 그대로(검증/coerce 는 위임 함수 책임).
    if entry.delegate is not None:
        return entry.delegate(home)
    base = entry.default_factory(home)
    p = config_path(name, home)
    if not p.exists():
        return base
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return base  # 손상 = 기본값(예외 0)
    if not isinstance(d, dict):
        return base  # 타입 불일치 = 기본값(fail-closed)
    base.update(d)  # shallow 병합(파일 값 우선)
    return entry.coerce(base) if entry.coerce else base


def load_config(name, home=None, *, use_cache=True):
    """등록된 config 로드. 부재/손상 = 기본값 사본(예외 0).

    use_cache=True 면 (name, abspath) 키로 캐시(모듈 dict). writer 는 invalidate 필요.
    반환은 매 호출 독립 dict(캐시 대상은 caller 가 mutate 하지 않는 read 용도 가정)."""
    if name not in _REGISTRY:
        raise KeyError("unregistered config: %r (registered: %s)"
                       % (name, sorted(_REGISTRY)))
    if not use_cache:
        return _resolve(name, home, _REGISTRY[name])
    key = _cache_key(name, home)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = _resolve(name, home, _REGISTRY[name])
    _CACHE[key] = result
    return result


def invalidate(name=None, home=None):
    """캐시 무효화. name=None → 전체. home=None(name 지정) → 해당 name 전 홈 키 제거."""
    if name is None:
        _CACHE.clear()
        return
    if home is None:
        for k in [k for k in _CACHE if k[0] == name]:
            del _CACHE[k]
    else:
        _CACHE.pop(_cache_key(name, home), None)


# ─────────────────────────────────────────────────────────────────────────────
# 기본 등록 (import 시 1회)
# ─────────────────────────────────────────────────────────────────────────────
def _load_user_config_delegate(home):
    """binggu_config 위임 — p1_config.load_user_config 재사용(검증/coerce 중복 0)."""
    from binggupack.safety.p1_config import load_user_config  # lazy: import-time 결합 회피
    return load_user_config(home)


def _default_capture_scope(home=None):
    # fail-closed 기본(전역 off · 허용 prefix 없음 · 거부 substring 없음).
    return {"global": False, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}


def _default_close_phrases(home=None):
    # phrases=세션 마무리 표현 · suffixes=표현 뒤 짧은 종결/조사 접미(N3 유한폐포 opt-in).
    return {"phrases": [], "suffixes": []}


def _default_harvest_sources(home=None):
    return {"sources": []}


def _default_fresh_index(home=None):
    # Local Fresh Index(LFI) 설정. hot_weights=Hot 랭킹 가중, semantic_timeout=조회 embed
    # 짧은 timeout(초), allowed_paths=2단계 로컬 파일 인덱싱 허용목록(기본 빈=owner 옵트인).
    return {
        "hot_weights": {"freshness": 1.0, "trust": 1.2, "utility": 0.8, "pin_boost": 5.0},
        "semantic_timeout": 1.0,
        "allowed_paths": [],  # phase2: owner 가 명시한 로컬 md/traj 경로만
    }


# binggu_config 는 위임 전용 — default_factory 는 delegate 실패시 폴백용 빈 dict(정상경로 미사용).
register("binggu_config", lambda home=None: {}, delegate=_load_user_config_delegate)
register("capture_scope", _default_capture_scope)
register("close_phrases", _default_close_phrases)
register("harvest_sources", _default_harvest_sources)
register("fresh_index", _default_fresh_index)


# ─────────────────────────────────────────────────────────────────────────────
# --selftest
# ─────────────────────────────────────────────────────────────────────────────
def _selftest():
    import tempfile

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print(("PASS" if c else "FAIL"), m)

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        invalidate()

        # 1) 부재 → 기본값 사본
        cp = load_config("close_phrases", home)
        ck(cp == {"phrases": [], "suffixes": []}, "close_phrases 부재 → 기본값")
        hs = load_config("harvest_sources", home)
        ck(hs == {"sources": []}, "harvest_sources 부재 → 기본값")
        cs = load_config("capture_scope", home)
        ck(cs["global"] is False and cs["allowed_cwd_prefixes"] == []
           and cs["denied_cwd_substrings"] == [], "capture_scope 부재 → fail-closed 기본값")

        # 2) 존재 → 병합
        (home / "close_phrases.json").write_text(
            json.dumps({"phrases": ["오늘 여기까지", "마무리하자"]}, ensure_ascii=False),
            encoding="utf-8")
        invalidate("close_phrases", home)
        cp2 = load_config("close_phrases", home)
        ck(cp2["phrases"] == ["오늘 여기까지", "마무리하자"], "close_phrases 존재 → 병합")

        (home / "harvest_sources.json").write_text(
            json.dumps({"sources": [{"url": "https://ex.com", "kind": "rss"}], "ts": 1}),
            encoding="utf-8")
        invalidate("harvest_sources", home)
        hs2 = load_config("harvest_sources", home)
        ck(hs2["sources"] == [{"url": "https://ex.com", "kind": "rss"}], "harvest_sources 존재 → 병합")

        # 3) 손상 JSON → 기본값(예외 0)
        (home / "close_phrases.json").write_text("{not valid json", encoding="utf-8")
        invalidate("close_phrases", home)
        cp3 = load_config("close_phrases", home)
        ck(cp3 == {"phrases": [], "suffixes": []}, "close_phrases 손상 → 기본값(예외 0)")

        # 3b) 비-dict JSON(리스트) → 기본값
        (home / "harvest_sources.json").write_text("[1,2,3]", encoding="utf-8")
        invalidate("harvest_sources", home)
        ck(load_config("harvest_sources", home) == {"sources": []},
           "harvest_sources 비-dict → 기본값")

        # 4) 캐시 히트 · invalidate 라운드트립
        (home / "close_phrases.json").write_text(
            json.dumps({"phrases": ["A"]}, ensure_ascii=False), encoding="utf-8")
        invalidate("close_phrases", home)
        first = load_config("close_phrases", home)          # 캐시 채움
        (home / "close_phrases.json").write_text(
            json.dumps({"phrases": ["A", "B"]}, ensure_ascii=False), encoding="utf-8")
        cached = load_config("close_phrases", home)         # 캐시 히트 → 이전 값
        ck(cached is first and cached["phrases"] == ["A"], "캐시 히트(파일 변경 무시)")
        invalidate("close_phrases", home)
        fresh = load_config("close_phrases", home)          # 무효화 후 재로드
        ck(fresh["phrases"] == ["A", "B"], "invalidate 후 재로드(fresh)")
        nocache = load_config("close_phrases", home, use_cache=False)
        ck(nocache["phrases"] == ["A", "B"] and nocache is not fresh,
           "use_cache=False → 항상 fresh 신규 객체")

        # 5) binggu_config 위임 — p1_config.load_user_config 결과 그대로(검증 재사용)
        try:
            bc = load_config("binggu_config", home)
            from binggupack.safety.p1_config import load_user_config as _luc
            ck(bc == _luc(home) and "ranking_weights" in bc and "recall_config" in bc,
               "binggu_config 위임 → load_user_config 동치")
        except Exception as e:
            ck(False, "binggu_config 위임 로드 실패: %r" % e)

        # 6) 미등록 name → KeyError
        try:
            load_config("nope_%s" % os.getpid(), home)
            ck(False, "미등록 name → KeyError 안 남")
        except KeyError:
            ck(True, "미등록 name → KeyError")

    print("GATE=GO" if ok else "GATE=NO-GO")
    return ok


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        sys.exit(0 if _selftest() else 1)
