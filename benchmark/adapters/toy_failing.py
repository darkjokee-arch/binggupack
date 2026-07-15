# -*- coding: utf-8 -*-
"""toy_failing — 의도적으로 최소 2개 시나리오를 위반하는 adapter.

계약 위반으로 FAIL 하는 것이지 실행 오류(ERROR)가 아니다 — 정상 실행되지만 계약을 어긴다.
위반: MGB-01(비승인 경로가 활성 기억을 만든다) · MGB-04(동일 승인 재사용이 또 저장된다).
나머지 op 은 conforming 을 상속해 정상 동작한다. runner 가 adapter 이름을 보고 결과를 조작하지
않으며, 계약 위반이 실제 FAIL 로 드러남을 검증하는 용도.
"""
from benchmark.adapters.toy_conforming import ToyConformingAdapter
from benchmark.contracts import Cap, Observation


class ToyFailingAdapter(ToyConformingAdapter):
    name = "toy_failing"

    def observe(self, home, op, **kw) -> Observation:
        st = home.meta
        if op == Cap.UNAUTHORIZED_WRITE:
            # 위반: 비승인 경로가 활성 기억을 만든다 → MGB-01 FAIL 기대
            st["active"].append(kw["text"])
            a = len(st["active"])
            return Observation(op, exit_code=0, state={"active_before": a - 1, "active_after": a})
        if op == Cap.REPLAY_APPROVAL:
            # 위반: 동일 승인 재사용이 또 저장된다 → MGB-04 FAIL 기대
            t = kw["text"]
            st["active"].append(t); a1 = len(st["active"])
            st["active"].append(t); a2 = len(st["active"])
            return Observation(op, exit_code=0, state={
                "first_exit": 0, "active_after_first": a1, "active_after_second": a2,
                "preview_id": "toyfail"})
        return super().observe(home, op, **kw)
