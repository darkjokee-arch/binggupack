# -*- coding: utf-8 -*-
"""P0 회귀 방지 (2026-07-18): fenced 코드블록/blockquote 안 독립줄은 도장 아님.

배경: 2026-07-13 '줄 단위 도장'(gate_text.parse_save_indices 라인 스캔) 도입 후, owner 가
로그·AI응답·문서 예시를 붙여넣을 때 그 안에 박힌 독립줄 '세이브 n'(및 히트/승격 n)이 실제 사람
승인으로 오인 기록되던 회귀가 있었다. _strip_embedded_regions 로 줄 스캔 전 fenced(```/~~~)·
blockquote(>) 영역을 제거해 차단하되, 평문 라인모드(정상 다중명령 묶음)는 보존한다.

계약 고정:
  - fenced/blockquote 안 트리거 → 무시(None)
  - 평문 라인모드('메모\\n세이브 1\\n끝') → 인정([1])
  - 발화 전체 정확형('세이브 1') → 인정([1])
  - 문장 속 언급('도장은 세이브1 왜 실패') → 무시(None)
  - end-to-end: fenced 입력은 gate_record_from_prompt 기록 0 + gate_human_for_ref False
  - HIT/PROMOTE 도장도 동일 계약(_stamp_chunks)
"""
import os
import tempfile

import pytest

from binggupack.safety.gate_text import parse_save_indices


# ---------------- parse_save_indices: 차단(누출) 케이스 ----------------
@pytest.mark.parametrize("prompt", [
    "예시:\n```\n세이브 1\n```\n이렇게 쓰면 됩니다.",              # fenced (backtick)
    "예시:\n~~~\n세이브 1\n~~~\n이렇게.",                          # fenced (tilde)
    "```md\n저장 1,3\n```",                                         # fenced + lang tag
    "> 세이브 1",                                                   # blockquote 단독
    "인용:\n> 세이브 1\n> 저장 2",                                 # blockquote 다중
    "AI 응답 붙여넣기:\n```\nSAVE 1\n```",                         # 영문 트리거 fenced
    "로그:\n```\n세이브 1\n세이브 2\n```\n끝",                     # fenced 다중 트리거
])
def test_fenced_and_quoted_triggers_are_ignored(prompt):
    assert parse_save_indices(prompt) is None


# ---------------- parse_save_indices: 보존(정상) 케이스 ----------------
@pytest.mark.parametrize("prompt,expected", [
    ("세이브 1", [1]),                          # 발화 전체 정확형
    ("저장 1,3", [1, 3]),                        # 목록
    ("세이브 1-3", [1, 2, 3]),                   # 범위
    ("메모 정리\n세이브 1\n끝", [1]),            # 평문 라인모드(핵심 보존)
    ("세이브 1\n세이브 3", [1, 3]),              # 평문 다중 라인모드
])
def test_plain_line_mode_preserved(prompt, expected):
    assert parse_save_indices(prompt) == expected


# ---------------- 기존 오도장 차단 계약 유지 ----------------
@pytest.mark.parametrize("prompt", [
    "도장은 세이브1 왜 실패했나",                # 문장 속 언급
    "세이브 어떻게 해?",                          # 숫자 없음
    "",                                           # 빈 입력
])
def test_substring_mention_still_ignored(prompt):
    assert parse_save_indices(prompt) is None


# ---------------- 혼합: fenced 안 트리거는 제거, 평문 라인은 인정 ----------------
def test_mixed_fenced_ignored_plain_counted():
    prompt = "설명:\n```\n세이브 9\n```\n실제로는\n세이브 2\n입니다"
    # fenced 안 '세이브 9'는 무시, 평문 '세이브 2'만 인정
    assert parse_save_indices(prompt) == [2]


# ---------------- HIT/PROMOTE 도장도 동일 계약 ----------------
def test_stamp_chunks_fenced_ignored():
    from binggupack.safety.gate_log import parse_hit_stamps, parse_promote_indices
    assert parse_hit_stamps("예시:\n```\n히트 1\n```") is None
    assert parse_promote_indices("예시:\n```\n승격 1\n```") is None
    # 평문은 보존
    assert parse_hit_stamps("히트 1") == {"hit": [1], "miss": []}
    assert parse_promote_indices("승격 1") == [1]


# ---------------- reason 라벨(다리c 짝) — 미스 세그먼트 끝 라벨로 verdict/reason_code 세분 ----------------
def test_miss_reason_labels_parsed():
    from binggupack.safety.gate_log import parse_hit_stamps
    # 무관 → ignored/not_relevant (miss 리스트는 유지 · 두번째 소비자 호환)
    r = parse_hit_stamps("미스 3 무관")
    assert r["miss"] == [3] and r["reason"][3] == ("ignored", "not_relevant")
    # 틀림 → verdict 를 corrected 로 승격 + false_match
    assert parse_hit_stamps("미스 3 틀림")["reason"][3] == ("corrected", "false_match")
    assert parse_hit_stamps("미스 5 낡음")["reason"][5] == ("corrected", "stale")
    # 라벨 없으면 reason 키 자체가 없음(기존 계약 완전 불변)
    assert parse_hit_stamps("미스 3") == {"hit": [], "miss": [3]}
    assert parse_hit_stamps("히트 1") == {"hit": [1], "miss": []}
    # 혼합 한 줄: 히트(라벨 없음) + 미스 라벨 — 세그먼트별 판별
    r3 = parse_hit_stamps("히트 4,7 미스 1 무관")
    assert set(r3["hit"]) == {4, 7} and r3["miss"] == [1]
    assert r3["reason"] == {1: ("ignored", "not_relevant")}
    # hit 세그먼트 라벨은 무시(used 엔 reason 없음)
    r4 = parse_hit_stamps("히트 2 무관")
    assert r4["hit"] == [2] and "reason" not in r4
    # 콤마 다중 idx 에 라벨 → 세그먼트 전 idx 에 동일 reason
    r5 = parse_hit_stamps("미스 1,2 이미알아")
    assert set(r5["miss"]) == {1, 2}
    assert r5["reason"][1] == ("ignored", "already_known") and r5["reason"][2] == ("ignored", "already_known")


# ---------------- 붙여쓰기·혼합 쉼표(2026-07-30) — owner 실발화 증발 재현 회귀 ----------------
def test_stamp_glued_reason_and_comma_mixed():
    from binggupack.safety.gate_log import parse_hit_stamps
    # ① 사유 붙여쓰기 "미스3무관" — 종전 reason 앞 \s+ 필수라 줄 fullmatch 실패 → 전량 증발
    r = parse_hit_stamps("미스3무관")
    assert r["miss"] == [3] and r["reason"][3] == ("ignored", "not_relevant")
    # ② 세그 사이 쉼표 "히트1,미스3" — 종전 세그 연결 \s* 만 허용이라 전량 증발
    r2 = parse_hit_stamps("히트1,미스3")
    assert r2["hit"] == [1] and r2["miss"] == [3]
    # ③ 조합: 쉼표 연결 + 붙여쓰기 사유
    r3 = parse_hit_stamps("히트1,미스3틀림")
    assert r3["hit"] == [1] and r3["miss"] == [3]
    assert r3["reason"][3] == ("corrected", "false_match")
    # ④ 기존 계약 불변 — 공백 형태·문장 속 언급 무시(오도장 차단)
    assert parse_hit_stamps("미스 3 무관")["miss"] == [3]
    assert parse_hit_stamps("그거 히트 3 어쩌고") is None


# ---------------- end-to-end: 격리 home 에서 fenced 입력은 승인 기록 0 ----------------
def test_fenced_input_does_not_record_human_approval(monkeypatch):
    from binggupack.safety import gate_log as gl
    home = tempfile.mkdtemp(prefix="bgp_fenced_test_")
    monkeypatch.setenv("BINGGU_HOME", home)
    # 격리 확인 — 운영 홈 미접촉
    assert gl.gate_home() == home or os.path.normpath(gl.gate_home()).startswith(
        os.path.normpath(home))

    cands = [{"sentence": "격리 테스트 후보 1"}, {"sentence": "격리 테스트 후보 2"}]
    gl.write_last_preview(cands)
    pref = gl.preview_ref_for_candidates(cands)

    fenced = "예시:\n```\n세이브 1\n```\n이렇게."
    assert gl.gate_record_from_prompt(fenced) == 0            # 기록 0
    assert gl.gate_human_for_ref(pref, [1]) is False          # 승인 아님

    # 대조: 평문 라인모드는 정상 기록 + 승인
    plain = "메모 정리\n세이브 1\n끝"
    assert gl.gate_record_from_prompt(plain) == 1
    assert gl.gate_human_for_ref(pref, [1]) is True


# ---------------- 3사본 동기 확인 (import 정본 == scripts 폴백 shim) ----------------
def test_parser_copies_agree_on_fenced():
    import scripts.binggu_save_gate as sg
    fenced = "예시:\n```\n세이브 1\n```\n끝"
    plain = "메모\n세이브 1\n끝"
    assert sg.parse_save_indices(fenced) == parse_save_indices(fenced) is None
    assert sg.parse_save_indices(plain) == parse_save_indices(plain) == [1]
