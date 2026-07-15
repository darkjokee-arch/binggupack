# -*- coding: utf-8 -*-
"""MGB v0.1 adapter 관찰 계약.

원칙(owner 확정): adapter 는 verdict("PASS")를 반환하지 않는다. 관찰 가능한 자료만 반환하고,
최종 verdict 는 runner 의 시나리오 계약 코드가 계산한다. adapter 는 신뢰된 측정 코드이되,
그 반환값(exit code·stdout·구조화 상태·산출물 수)만 판정 입력으로 쓴다.

capability(Cap)로 "이 op 을 공개 인터페이스로 지원하는가"를 선언한다 — 미지원이면 시나리오는
UNSUPPORTED/UNSUPPORTED 로 남긴다(공개 CLI 로 검증 불가한 항목을 PASS 로 위장하지 않는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class Cap:
    """adapter 가 공개 인터페이스로 지원한다고 선언하는 능력(capability) 이름."""
    INIT = "init"
    PREVIEW = "preview"
    SAVE = "save"                     # 사람 승인 저장(공개 CLI)
    LIST_ACTIVE = "list_active"       # 활성 기억 목록/카운트
    RECALL = "recall"
    RECALL_FRESH = "recall_fresh_process"  # 새 프로세스 회상(동일 정본)
    EXPLAIN = "explain"               # 근거/provenance
    SUPERSEDE = "supersede"           # 교체/폐기(이력 보존)
    PAIR = "pair"                     # owner/AI 화자 페어
    REMOTE_INTENT = "remote_intent"   # 원격 저장 의도(로컬 write 없이)
    CAPTURE_CANDIDATE = "capture_candidate"  # 자동수집 후보(active 아님)
    UNAUTHORIZED_WRITE = "unauthorized_write_attempt"  # 비승인 경로 활성화 시도
    EXACT_BINDING = "exact_binding_attempt"       # MGB-02: 유효 preview baseline 성공 + 내용 변조 거부
    STALE_FRESHNESS = "stale_freshness_attempt"   # MGB-03: 시간·상태 신선도 만료(공개 CLI 결정적 재현 필요)
    REPLAY_APPROVAL = "replay_approval_attempt"
    INTEGRITY_PUBLIC = "integrity_verify_public"  # 공개 CLI 로 독립 무결성 검증(MGB-10 관건)


@dataclass
class Observation:
    """adapter 가 한 op 을 실행하고 관찰한 자료. verdict 는 없다 — 판정 재료만."""
    op: str
    command: list[str] | None = None       # 실행한 공개 명령(있으면)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts_created: int = 0             # 이 op 이 새로 만든 공개 산출물 수
    state: dict = field(default_factory=dict)  # 공개 인터페이스로 조회한 구조화 상태(active_count 등)
    note: str = ""

    def to_dict(self) -> dict:
        # stdout/stderr 는 evidence 로 앞부분만(장부/경로 대량 노출 방지·판정엔 state/exit 사용).
        return {
            "op": self.op,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_head": (self.stdout or "")[:800],
            "stderr_head": (self.stderr or "")[:400],
            "artifacts_created": self.artifacts_created,
            "state": self.state,
            "note": self.note,
        }


# ── 시나리오 판정 헬퍼 — 취약한 "문자열 하나 포함" assertion 을 대체하는 조합 판정 ──

def exit_ok(obs: Observation) -> bool:
    return obs.exit_code == 0


def exit_rejected(obs: Observation) -> bool:
    """도메인 거부(1) — usage/부재(2)가 아니라 정책 BLOCK 인지. 단독 판정 금지:
    시나리오는 error_code(거부 원인 분류) + 상태 불변(active·digest)을 함께 요구한다."""
    return obs.exit_code == 1


def state_int(obs: Observation, key: str, default: int = -1) -> int:
    v = obs.state.get(key, default)
    return v if isinstance(v, int) else default


_BLOCK_RE = re.compile(r"BLOCK:\s*([a-z0-9_]+)", re.IGNORECASE)


def parse_block_code(stdout: str) -> str | None:
    """'BLOCK: <code>' 형태의 정책 거부 코드 파싱. usage/인자오류(exit2)와 정책 BLOCK(exit1)을
    구분하고, 거부 원인(preview_required_mismatch·g4_no_auto 등)을 시나리오가 기대값과 대조하게 한다.
    안정된 공개 error code 가 없는 명령은 None — 시나리오가 텍스트 파싱 한계로 취급한다."""
    m = _BLOCK_RE.search(stdout or "")
    return m.group(1) if m else None


# ── 거부 코드 → 표준 거부 클래스 정규화(issue #54.1) ──
# adapter 마다 실제 코드 문자열은 다르다(BingguPack=preview_required_mismatch · 참조 adapter=
# content_binding_mismatch). 특정 코드 문자열을 계약에 하드코딩하지 않고(이식성) 같은 '클래스'로
# 정규화해, MGB-02 가 '내용 결속 불일치' 거부와 '엉뚱한 거부(빈입력·usage·confirm)'를 구분한다.
REJECTION_CONTENT_BINDING = "content_binding"   # 사용자가 본 내용과 저장 내용의 결속 불일치
REJECTION_OTHER = "other"                        # 내용 결속과 무관한 거부(우연통과 배제 대상)

_CONTENT_BINDING_CODES = frozenset({
    "preview_required_mismatch",   # BingguPack: preview 텍스트 해시 불일치
    "content_binding_mismatch",    # 참조/일반 adapter
})
_NONBINDING_REJECTION_CODES = frozenset({
    "no_candidates", "preview_unavailable", "confirm_mismatch",
    "confirm_required_mismatch", "confirm_phrase_mismatch", "owner_flat_save_forbidden",
    "unsafe_segmentation", "node_hash_mismatch", "proposal_not_found",
})


def classify_rejection(code: str | None) -> str | None:
    """정책 거부 코드를 표준 거부 클래스로 정규화.

    반환:
      · REJECTION_CONTENT_BINDING — 내용 결속 불일치 거부(MGB-02 가 기대하는 클래스)
      · REJECTION_OTHER — 내용 결속과 무관한 거부(빈입력·usage·confirm 등 · 우연통과로 배제)
      · None — 안정 공개 거부 코드 없음 또는 미등록 코드. 특정 코드 문자열을 강제하지 않고(이식성)
        시나리오의 조합 판정(baseline·active·digest)에 판정을 위임한다.
    """
    if not code:
        return None
    c = code.strip().lower()
    if c in _CONTENT_BINDING_CODES:
        return REJECTION_CONTENT_BINDING
    # 미등록 코드라도 명시적 내용 결속 키워드를 담으면 인정(preview/content/binding + mismatch).
    if "mismatch" in c and any(k in c for k in ("preview", "content", "binding")):
        return REJECTION_CONTENT_BINDING
    if c in _NONBINDING_REJECTION_CODES:
        return REJECTION_OTHER
    return None  # 미등록·불명 — 계약 강제 안 함(조합 판정에 위임)


def fp_content_equal(a: dict | None, b: dict | None) -> bool:
    """운영 sentinel fingerprint 오염 판정 — content(존재·size·digest·symlink·realpath) 기준.

    mtime_ns 는 외부 SQLite 활동(WAL/SHM 체크포인트·다른 프로세스의 read)만으로도 변동하므로 오염
    판정에서 제외한다(각 파일의 mtime_ns 는 evidence 로 기록됨). content(size·digest)가 바뀌면 —
    WAL 내용 증가·approvals 추가·ledger 본체 변경 등 실제 write — hard FAIL 로 잡는다.
    """
    def _content(fp):
        if not isinstance(fp, dict):
            return fp
        return {k: ({kk: vv for kk, vv in v.items() if kk != "mtime_ns"} if isinstance(v, dict) else v)
                for k, v in fp.items()}
    return _content(a) == _content(b)
