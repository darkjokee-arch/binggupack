"""semantic 콜드스타트 배치 선채움 — 2026-07-13 Codex 감사 잔여(콜드 35s) 수리.

콜드 35s = why_search 가 미캐시 노드마다 1회씩 순차 HTTP embed(N왕복) 하던 비용.
수리 = _embed_batch(1왕복 다건) + _prefill_cache(미캐시분 일괄 선적재) + scorer.prefill
배선. 전부 Ollama 없이 fake batch_fn 주입으로 검증(결정적·네트워크 0).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import recall as R                                # noqa: E402

_PASS_GUARD = lambda t: (True, None)                                   # noqa: E731


def _vec(i, dim=8):
    v = [0.0] * dim
    v[i % dim] = 1.0
    return v


def _cache(tmp_path):
    return R._open_embed_cache(str(tmp_path))


def _rows(cache):
    return cache.execute("SELECT count(*) FROM embed_cache").fetchone()[0]


def test_prefill_fills_once_and_idempotent(tmp_path):
    """N문장 → 배치 1왕복·캐시 N행. 재호출 → 왕복 0(멱등·전량 캐시 hit)."""
    cache = _cache(tmp_path)
    calls = []

    def batch(texts):
        calls.append(len(texts))
        return [_vec(i) for i in range(len(texts))]

    sents = ["문장 %d 판단" % i for i in range(10)]
    filled = R._prefill_cache(cache, "m1", sents, batch, _PASS_GUARD)
    assert filled == 10 and calls == [10] and _rows(cache) == 10
    filled2 = R._prefill_cache(cache, "m1", sents, batch, _PASS_GUARD)
    assert filled2 == 0 and calls == [10]        # 왕복 추가 0
    cache.close()


def test_prefill_chunks_by_limit(tmp_path):
    """_PREFILL_CHUNK(64) 초과 → ceil(N/64) 왕복으로 분할."""
    cache = _cache(tmp_path)
    calls = []

    def batch(texts):
        calls.append(len(texts))
        return [_vec(i) for i in range(len(texts))]

    sents = ["긴 목록 문장 %d" % i for i in range(130)]
    filled = R._prefill_cache(cache, "m1", sents, batch, _PASS_GUARD)
    assert filled == 130 and calls == [64, 64, 2]
    cache.close()


def test_prefill_batch_failure_graceful(tmp_path):
    """배치 실패(None) → 채움 중단·예외 0(단건 경로가 이어받음)."""
    cache = _cache(tmp_path)
    filled = R._prefill_cache(cache, "m1", ["a 판단", "b 판단"], lambda t: None, _PASS_GUARD)
    assert filled == 0 and _rows(cache) == 0
    cache.close()


def test_prefill_leak_guard_parity(tmp_path):
    """leak_guard 거부 문장은 임베드 요청 자체에서 제외(단건 경로와 동일 패리티)."""
    cache = _cache(tmp_path)
    sent_bad = "secret 포함 문장"
    seen = []

    def batch(texts):
        seen.extend(texts)
        return [_vec(i) for i in range(len(texts))]

    guard = lambda t: (t != sent_bad, None)                            # noqa: E731
    filled = R._prefill_cache(cache, "m1", ["안전 문장", sent_bad], batch, guard)
    assert filled == 1 and sent_bad not in seen and _rows(cache) == 1
    cache.close()


def test_embed_batch_empty_no_network():
    """빈 입력 → [] 즉시 반환(네트워크 0)."""
    import binggu_semantic_shadow as SH
    assert SH._embed_batch([]) == []


def test_scorer_prefill_attr_and_fake_embed_noop(tmp_path):
    """embed_fn 주입 scorer(테스트 경로) — prefill 존재하되 no-op(캐시 우회 유지)."""
    scorer = R._semantic_scorer(home=str(tmp_path), embed_fn=lambda t: _vec(1))
    assert scorer is not None and hasattr(scorer, "prefill")
    assert scorer.prefill(["아무 문장"]) == 0
