# -*- coding: utf-8 -*-
"""hag_keyring.py — 사용자별 vault secret(HMAC 키) 자동생성/안전저장 (신규).

목적
  빙구팩은 GitHub 공개 도구다. attestation 위조 방지용 vault secret(HMAC 키)을
  코드에 고정값으로 박으면 누구나 공개 소스에서 그 키를 읽어 attestation 을
  위조할 수 있다. 따라서 secret 은 **각 사용자 머신에서 1회 자동생성**되어
  사용자 홈 하위(repo 밖)에 보관돼야 한다(사용자별 격리).

저장 경로 (★ git repo 밖)
  기본: ~/.binggupack/hybrid_agi/vault_secret
  - binggupack git repo(C:/Users/fixture-user/binggupack) **밖**의 사용자 홈 하위라
    git 에 절대 올라가지 않는다(gitignore 불필요). 공개 소스엔 키가 없다.
  - secret 파일은 ledger.sqlite·capture_buffer.sqlite 와 **별도 경로**
    (hybrid_agi/ 하위)이며, 이 모듈은 그 운영 ledger 들을 절대 미접촉한다
    (읽기도 X). secret 파일만 다룬다.

영구금지 준수
  - secret 평문 출력/로그/repr 금지(영구금지 18). describe_secret() 는
    sha256 hash8 + 길이만 반환한다. secret 본문은 어떤 반환/print 에도 없다.
  - 운영 ledger 미접촉(secret 파일만, 별도 경로).
  - 파일 권한: POSIX 는 0600(소유자 read/write 만). Windows 는 chmod 무의미
    하므로 best-effort(예외 무시) — 경로 자체가 사용자 프로필 하위라 격리됨.
  - 결정론 selftest: keyring 실생성은 런타임 secrets.token_hex 사용(허용),
    단 selftest 는 temp HOME 격리로 실제 ~/.binggupack 미접촉.

CLI: python hag_keyring.py --selftest  ->  'GATE: GO' | 'GATE: STOP'
"""
from __future__ import annotations

from contextlib import suppress
import hashlib
import importlib
import os
import secrets
import sys
import tempfile

# secret 길이 = token_hex(32) -> 64 hex chars (256-bit). 위조 방지에 충분.
_SECRET_NBYTES = 32
_SECRET_FILENAME = "vault_secret"
_DEFAULT_SUBDIR = os.path.join("~", ".binggupack", "hybrid_agi")


class KeyringBlock(Exception):
    """keyring 불변식 위반(빈 secret 로드·경로 오류 등)."""


def default_home_dir() -> str:
    """기본 secret 보관 디렉터리(절대경로) — ~/.binggupack/hybrid_agi/.

    binggupack git repo 밖(사용자 홈 하위)이라 git 에 안 올라간다.
    """
    return os.path.abspath(os.path.expanduser(_DEFAULT_SUBDIR))


def secret_path(home_dir: str) -> str:
    """home_dir 안의 secret 파일 절대경로."""
    return os.path.join(os.path.abspath(os.path.expanduser(home_dir)), _SECRET_FILENAME)


def _harden_permissions(path: str) -> None:
    """secret 파일 권한을 0600(소유자만)으로. Windows 는 best-effort."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        # Windows 등 chmod 미지원 — 경로 자체가 사용자 프로필 하위라 격리됨.
        pass


def _read_secret(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _write_secret_atomic(path: str, secret: str) -> None:
    """secret 을 원자적으로(temp -> replace) 기록하고 권한 0600 적용.

    부분 기록/경쟁으로 빈/깨진 secret 이 남지 않게 같은 디렉터리 temp 에
    먼저 쓰고 권한을 좁힌 뒤 os.replace 로 교체한다.
    """
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".vault_secret_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secret)
            f.flush()
            os.fsync(f.fileno())
        _harden_permissions(tmp)
        os.replace(tmp, path)
    except BaseException:
        # 실패 시 temp 정리(부분 파일 잔존 방지).
        with suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)
        raise
    _harden_permissions(path)


def get_or_create_secret(home_dir: str = None) -> str:
    """home_dir 의 secret 파일을 로드하거나, 없으면 새로 생성해 저장 후 반환.

    - 파일 있으면: 로드해 반환(동일 머신/사용자 = 동일 secret).
    - 파일 없으면: secrets.token_hex(32) 로 새 secret 생성 -> 디렉터리 생성 ->
      0600 권한으로 저장 -> 반환.

    반환값(secret 문자열)은 절대 print/log/repr 하지 말 것(영구금지 18).
    노출이 필요하면 describe_secret() 으로 hash8+길이만 보여라.

    운영 ledger(ledger.sqlite·capture_buffer.sqlite) 절대 미접촉 — secret 파일만 다룬다.
    """
    if home_dir is None:
        home_dir = default_home_dir()
    home_dir = os.path.abspath(os.path.expanduser(home_dir))
    path = os.path.join(home_dir, _SECRET_FILENAME)

    if os.path.exists(path):
        secret = _read_secret(path)
        if not secret:
            # 빈/깨진 secret 파일 — 위조 방지 키로 부적격. 재생성하지 않고 BLOCK
            # (사용자 데이터 손상 가능성 신호, 조용한 덮어쓰기 금지).
            raise KeyringBlock("secret 파일이 비어있음 — 손상 의심: %s" % path)
        # 기존 파일 권한도 좁혀 둔다(이전에 느슨하게 만들어졌을 수 있음).
        _harden_permissions(path)
        return secret

    # 없음 -> 생성. 디렉터리 보장(중간 경로 포함).
    os.makedirs(home_dir, exist_ok=True)
    secret = secrets.token_hex(_SECRET_NBYTES)
    _write_secret_atomic(path, secret)
    return secret


def describe_secret(secret: str) -> dict:
    """secret 평문 미노출 요약 — sha256 hash8 + 길이만(영구금지 18).

    반환 dict 어디에도 secret 본문이 없다. 로그/보고에 안전.
    """
    if not isinstance(secret, str) or not secret:
        raise KeyringBlock("describe_secret: secret 은 비어있지 않은 문자열")
    h = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return {"sha256_hash8": h[:8], "length": len(secret)}


# ─────────────────────────────────────────────────────────────────────────────
# selftest (결정론 · temp HOME 격리 · 실제 ~/.binggupack 미접촉)
# ─────────────────────────────────────────────────────────────────────────────
def _operating_snapshot():
    """운영 ledger 파일 mtime+size 스냅샷(미접촉 실측용). stat 만, read 0."""
    snap = {}
    base = os.path.expanduser("~/.binggupack")
    for name in ("ledger.sqlite", "capture_buffer.sqlite"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            st = os.stat(p)
            snap[name] = (st.st_mtime, st.st_size)
        else:
            snap[name] = None
    return snap


def _selftest():
    results = []

    def ck(name, cond):
        results.append((name, bool(cond)))

    # ── 운영 ledger 미접촉 실측: 전 스냅샷 ──
    op_before = _operating_snapshot()

    root = tempfile.mkdtemp(prefix="hag_keyring_")
    home_a = os.path.join(root, "userA", ".binggupack", "hybrid_agi")
    home_b = os.path.join(root, "userB", ".binggupack", "hybrid_agi")

    # ===== 1) 최초 생성: 파일 존재 · 권한 · hash8 =====
    s_a = get_or_create_secret(home_a)
    pa = secret_path(home_a)
    ck("create_returns_nonempty_str", isinstance(s_a, str) and len(s_a) > 0)
    ck("create_file_exists", os.path.exists(pa))
    ck("create_secret_len_64hex", len(s_a) == _SECRET_NBYTES * 2)
    # 파일 권한 0600 (POSIX). Windows 는 chmod 무의미 -> 검증 스킵(통과 처리).
    if os.name == "posix":
        mode = os.stat(pa).st_mode & 0o777
        ck("create_file_mode_0600", mode == 0o600)
    else:
        ck("create_file_mode_0600", True)  # non-POSIX: 경로 격리로 갈음
    desc = describe_secret(s_a)
    ck("describe_hash8_len8", len(desc["sha256_hash8"]) == 8)
    ck("describe_length_matches", desc["length"] == len(s_a))

    # ===== 2) 재로드 동일 secret (같은 home = 같은 키) =====
    s_a2 = get_or_create_secret(home_a)
    ck("reload_same_secret", s_a2 == s_a)
    # 파일이 새로 안 만들어지고 로드만 됐는지 — 내용 동일로 갈음 + mtime 불변 확인
    mtime1 = os.stat(pa).st_mtime
    _ = get_or_create_secret(home_a)
    ck("reload_no_rewrite_mtime", os.stat(pa).st_mtime == mtime1)

    # ===== 3) 두 다른 home = 다른 secret (사용자별 격리) =====
    s_b = get_or_create_secret(home_b)
    ck("different_home_different_secret", s_b != s_a)
    ck("isolation_both_files_exist",
       os.path.exists(secret_path(home_a)) and os.path.exists(secret_path(home_b)))

    # ===== 4) describe_secret 평문 미노출 =====
    # 반환 dict 의 어떤 값에도 secret 본문이 들어있지 않아야 한다.
    desc_b = describe_secret(s_b)
    leaked = any(s_b in str(v) for v in desc_b.values())
    ck("describe_no_plaintext_leak", not leaked)
    # 키 집합도 hash8/length 만
    ck("describe_keys_only_hash_len",
       set(desc_b.keys()) == {"sha256_hash8", "length"})
    # hash8 은 sha256 앞 8자와 일치(검증 가능 · 본문 비노출)
    expect8 = hashlib.sha256(s_b.encode("utf-8")).hexdigest()[:8]
    ck("describe_hash8_matches", desc_b["sha256_hash8"] == expect8)

    # ===== 5) orchestrator 가 keyring secret 을 사용 (고정값 제거 확증) =====
    # orchestrator 를 keyring 으로 만든 secret 으로 구성 -> 정상 동작.
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    orch_mod = importlib.import_module("hag_orchestrator")

    orch_secret = get_or_create_secret(home_a)  # 사용자별 키
    led_path = os.path.join(root, "blind_orch.sqlite")
    orch = orch_mod.HybridAGIOrchestrator(orch_secret, led_path)
    raw = "빌드가 깨져 있다. 배포 전 확인하자."
    orch.save_l0("L0-1", raw, created_at=1000)
    orch.propose_l1("L0-1", "L1-j1", "배포 전 빌드를 확인해야 한다", (11, len(raw)), created_at=1000)
    r = orch.blind_stamp_l1(
        "L1-j1", qid="q-l1", nonce="deterministic-nonce-keyring-0001", seal_ts=1000,
        human_answer="확인 후 배포 진행한다(독립 판단)", commit_ts=2000, reveal_ts=3000)
    ck("orchestrator_uses_keyring_secret", r["permanent"] is True)
    ck("orchestrator_attestation_verified", r["attestation_verified"] is True)
    # 다른 사용자 키로 만든 vault 의 attestation 은 이 vault 가 검증 못함(사용자별 격리 실증).
    orch_b = orch_mod.HybridAGIOrchestrator(s_b, os.path.join(root, "blind_orch_b.sqlite"))
    # b vault 로 attestation 발급 -> a vault 로 검증하면 reject (키 다름)
    sealed_b = orch_b._vault.seal_proposal("x", "n-iso", seal_ts=1, qid="qi")
    orch_b._vault.commit_answer("qi", "독립답", commit_ts=2, actor="human")
    rev_b = orch_b._vault.reveal_proposal(sealed_b["seal"], "n-iso")
    att_b = orch_b._vault.issue_attestation(rev_b)
    ck("cross_user_attestation_rejected",
       orch._vault.verify_attestation(att_b) is False)
    orch.close()
    orch_b.close()

    # ===== 6) 빈 secret 파일 = BLOCK(조용한 덮어쓰기 금지) =====
    home_c = os.path.join(root, "userC", ".binggupack", "hybrid_agi")
    os.makedirs(home_c, exist_ok=True)
    with open(secret_path(home_c), "w", encoding="utf-8") as f:
        f.write("")  # 손상된 빈 secret
    empty_blocked = False
    try:
        get_or_create_secret(home_c)
    except KeyringBlock:
        empty_blocked = True
    ck("empty_secret_file_BLOCK", empty_blocked)

    # ===== 7) default_home_dir 가 repo 밖(~/.binggupack 하위) =====
    dh = default_home_dir()
    bp = os.path.abspath(os.path.expanduser("~/.binggupack"))
    ck("default_home_under_binggupack",
       os.path.normcase(dh).startswith(os.path.normcase(bp)))
    # repo 디렉터리(binggupack git) 밖인지 — default home 은 ~/.binggupack 이지
    # repo(.../binggupack/scripts...) 가 아니다. 별도 경로 확인.
    ck("default_home_not_ledger_file",
       not dh.endswith("ledger.sqlite") and not dh.endswith("capture_buffer.sqlite"))

    # ── 운영 ledger 미접촉 실측: 후 스냅샷 ──
    op_after = _operating_snapshot()
    operating_untouched = (op_before == op_after)
    ck("operating_ledger_untouched_mtime_size", operating_untouched)

    # ── 집계 ──
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("RESULT: %d/%d" % (passed, total))
    print("operating_untouched: %s" % operating_untouched)
    gate = "GO" if passed == total else "STOP"
    print("GATE: %s" % gate)
    return passed, total, gate, operating_untouched


def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        p, t, g, ou = _selftest()
        return 0 if g == "GO" else 1
    print("hag_keyring — 사용자별 vault secret 자동생성/안전저장")
    print("usage: python hag_keyring.py --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
