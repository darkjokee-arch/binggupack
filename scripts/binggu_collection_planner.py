"""binggu_collection_planner — 주제 → LLM 동적 분류설계(aspect) (backward-compatible thin wrapper).

strangler: 순수 정본(plan · default_ollama_transport · _parse_aspects · _normalize_aspects ·
_fallback_aspects · _build_prompt · _extract_json_block · _infer_lang · _selftest)은
binggupack.pack.collection_planner 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_collection_planner as PLAN — binggu_local_collect 등)는
그대로 동작한다.

정본은 순수 stdlib(re·json·urllib) + __file__ 미사용이라 cross-dep 0. 이 wrapper 는 scripts/ 를
sys.path 에 얹어 형제 import 경로만 보존한다.

CLI: python scripts/binggu_collection_planner.py --selftest
     python scripts/binggu_collection_planner.py --topic '<주제>' [--max N] [--live]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.collection_planner import *  # noqa: E402,F401,F403
from binggupack.pack.collection_planner import (  # noqa: E402,F401  (전체 명시 re-export)
    plan,
    default_ollama_transport,
    _parse_aspects,
    _normalize_aspects,
    _fallback_aspects,
    _build_prompt,
    _extract_json_block,
    _infer_lang,
    _selftest,
    _DEFAULT_MAX_ASPECTS,
)
import json  # noqa: E402


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # CLI: --topic '<주제>' [--max N] [--live]  (--live = 실 ollama 사용·네트워크)
    topic = None
    max_n = _DEFAULT_MAX_ASPECTS
    live = "--live" in sys.argv
    if "--topic" in sys.argv:
        i = sys.argv.index("--topic")
        if i + 1 < len(sys.argv):
            topic = sys.argv[i + 1]
    if "--max" in sys.argv:
        i = sys.argv.index("--max")
        if i + 1 < len(sys.argv):
            try:
                max_n = int(sys.argv[i + 1])
            except ValueError:
                pass
    if topic:
        tr = default_ollama_transport() if live else None
        print(json.dumps(plan(topic, llm_transport=tr, max_aspects=max_n),
                         ensure_ascii=False, indent=2))
    else:
        print("binggu_collection_planner — use --selftest, "
              "or --topic '<주제>' [--max N] [--live]")
        print("import: plan(topic, llm_transport=None, aspects_hint=None) -> {topic, aspects:[...]}")
