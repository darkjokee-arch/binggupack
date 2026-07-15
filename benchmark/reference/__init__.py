# -*- coding: utf-8 -*-
"""Memory PR 로컬 참조구현(reference) — cross-adapter E2E 오케스트레이터와 reader-only adapter.

이 패키지는 Memory PR Spec(docs/memory-pr)의 **실행 가능한 로컬 참조구현**이다:
'모델 A 저장 → 모델 B recall/explain' 을 하나의 공유 격리 홈(ledger.sqlite 단일 버스) 위에서
로컬 프로세스만으로 재현한다. 외부 미접촉(네트워크 egress 0).

기존 benchmark 모듈(adapters/runner/contracts/scenarios/result)을 import 재사용만 한다(편집 0).
신규 코드는 이 패키지 3파일 + benchmark/tests/test_cross_adapter_e2e.py 에 국한된다.

공개 진입점(re-export):
  · run_e2e(root=None) -> dict — cross-adapter E2E 오케스트레이터. receipt(dict) 반환.
  · main(argv=None) -> int — CLI 진입점(사람 판독 요약 출력, GO=0).
  · ReaderOnlyAdapter — 쓰기 cap 미선언 '모델 B'(recall/explain 만).

canonicalization digest 결정성은 core 심볼을 재-import 하지 않고
docs/memory-pr/tools/check_vectors.py 를 subprocess 로 위임한다(상세: README.md).
"""
from benchmark.reference.e2e_cross_adapter import main, run_e2e
from benchmark.reference.reader_adapter import ReaderOnlyAdapter

__all__ = ["run_e2e", "main", "ReaderOnlyAdapter"]
