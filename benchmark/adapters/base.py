# -*- coding: utf-8 -*-
"""MGB adapter Protocol (작은 구조적 타입 · ABC/플러그인 프레임워크 아님).

adapter 는 격리 홈을 만들고, 공개 인터페이스로 op 을 실행해 Observation 을 돌려주며,
운영 정본의 fingerprint(사후 오염 감지용)를 제공한다. verdict 계산은 하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from benchmark.contracts import Observation


@dataclass
class HomeHandle:
    """격리 홈 핸들. root 는 runner 가 realpath 로 검증한 허용 임시 경로 하위여야 한다."""
    root: str
    adapter_name: str
    meta: dict


@runtime_checkable
class Adapter(Protocol):
    name: str

    def capabilities(self) -> set[str]:
        """이 adapter 가 공개 인터페이스로 지원하는 Cap 집합. 여기 없는 op → UNSUPPORTED."""
        ...

    def new_home(self, root: str) -> HomeHandle:
        """root(허용 임시 디렉터리) 아래에 격리 홈을 만들고 핸들 반환. 운영 정본 미접촉."""
        ...

    def cleanup(self, home: HomeHandle) -> None:
        """격리 홈 정리(합성 장부·임시 파일 제거)."""
        ...

    def operating_fingerprint(self) -> dict | None:
        """운영 정본(예: 운영 ledger)의 사후 오염 감지 fingerprint.
        {path, exists, size, mtime_ns, digest} 형태. 운영 정본이 없는 adapter 는 None."""
        ...

    def observe(self, home: HomeHandle, op: str, **kwargs) -> Observation:
        """op 을 공개 인터페이스로 실행하고 관찰 자료를 반환. verdict 는 계산하지 않는다."""
        ...
