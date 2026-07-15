# -*- coding: utf-8 -*-
"""MGB-02 exact-preview-binding — 승인이 사용자가 본 정확한 내용에 결속되는지.

★우연통과 방지(4cli 사후 반영): 빈 preview_id·인자오류로 생긴 exit1 을 binding 거부로 인정하지
않는다. (1)유효 preview 로 baseline 저장이 성공함을 제어군으로 확인하고, (2)동일 preview_id 를
유지한 채 내용만 변조한 저장이 거부되며, (3)거부 후 active count 불변 + 변조 내용의 digest 미생성
을 함께 요구한다.
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-02"
TITLE = "exact-preview-binding"
REQUIRES = {Cap.INIT, Cap.EXACT_BINDING}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.EXACT_BINDING,
                        text_a="배포 전에는 스테이징에서 먼저 검증하기로 했어요.",
                        text_b="완전히 다른 판단 문장을 하기로 정했어요.")
    st = o.state
    preview_valid = st.get("preview_id_valid") is True
    active_before = st.get("active_before")
    baseline_ok = (st.get("baseline_exit") == 0
                   and isinstance(active_before, int)
                   and st.get("active_after_baseline") == active_before + 1)
    mutation_rejected = st.get("mutation_exit") == 1  # 정책 거부(usage 오류 2 아님)
    active_unchanged = st.get("active_after_mutation") == st.get("active_after_baseline")
    no_digest = st.get("mutation_digest_present") is False
    ok = preview_valid and baseline_ok and mutation_rejected and active_unchanged and no_digest
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason=("preview유효=%s baseline성공=%s 변조거부(exit=%s,code=%s)=%s active불변=%s digest미생성=%s"
                % (preview_valid, baseline_ok, st.get("mutation_exit"),
                   st.get("mutation_error_code"), mutation_rejected, active_unchanged, no_digest)),
        evidence={"obs": o.to_dict()})
