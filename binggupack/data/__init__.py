# -*- coding: utf-8 -*-
"""binggupack.data — 패키지 내장 데이터 자산(semantic seed 등).

빈 패키지: seed jsonl 을 wheel/clone 에 동봉해 설치본에서도 도장 semantic 분류가
동작하도록 한다(silent fallback 제거). 경로 해석은 각 스크립트의 _resolve_seed_path 가
importlib.resources 로 이 패키지를 우선 조회한다.
"""
