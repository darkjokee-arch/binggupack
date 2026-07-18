"""approval CLI 명령 — trusted approval 요청 조회/승인/거절/revoke + keychain-init.

binggu.py 에서 정확 이관(순수 위치 이동 · 본체 byte 불변). ★최고위험 금고:
승인 EVENT/서명키 정본 — isatty 하드게이트·BINGGU_TRUSTED_CLI 백도어 봉인·
input() 정확문구·sk(서명키) 평문 미노출·mint_approval(channel="cli_tty") 라벨·
keychain-init 대화형 게이트를 문구 그대로 보존. 약화 0.

백본은 binggu.py 잔류(selftest_embed·daily·preflight 선례). 이 모듈이 참조하는
백본 심볼은 stdlib os·sys 뿐(BASE/HINT/OUTCOMES/DEFAULT_LEDGER 미사용). 함수 본문
지역 import(trusted_approval·open_g3·keychain_backend·signing_provider)는 함수와 동반.
_approval_home 은 cmd_approvals/cmd_approval 이 쓰므로 co-move.
"""
import os
import sys


def _approval_home(a):
    """approval store home = ledger 디렉토리(MCP 핸들러 _operating_home() 과 일치)."""
    return os.path.dirname(os.path.abspath(a.ledger))


def cmd_approvals(a):
    """대기 중인 trusted approval 요청 목록(조회 only)."""
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3
    home = _approval_home(a)
    os.makedirs(home, exist_ok=True)   # 신규/부재 home 도 graceful(open_g3 는 부모 dir 필요)
    db = open_g3(a.ledger)
    try:
        reqs = ta.list_requests(db.con)
    finally:
        db.close()
    if ta.provider_for(home) is None:
        print("※ trusted approval provider 미구성 — 활성화: %s 에 {\"enabled\": true}"
              % ta.config_path(home))
    if not reqs:
        print("대기 중인 승인 요청이 없습니다.")
        return 0
    print("승인 요청 (요청ID · 작업 · 요약 · 상태 · 만료):")
    for r in reqs:
        print("  %s  %-14s  %s  [%s]  ~%s"
              % (r["request_id"], r["operation"], r["summary"], r["state"], r["expires_at"]))
    print("\n검토: binggu approval show <요청ID>   ·   승인: binggu approval approve <요청ID>")
    return 0


def cmd_approval(a):
    """approval show/approve/reject/revoke <request-id>. approve 는 대화형 TTY 필수(비대화형 거부)."""
    import time as _t
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3
    action, rid = a.action, a.request_id
    home = _approval_home(a)
    os.makedirs(home, exist_ok=True)   # 신규/부재 home 도 graceful(open_g3 는 부모 dir 필요)

    if action == "keychain-init":
        # ★ AI 대행 절대 금지(승인 대행과 동급 · owner 전용):
        #   이 명령은 Ed25519 서명키(sk)를 OS keychain 에 생성/앵커한다. AI(모델 tool surface)가 이
        #   경로로 키를 만들면 '같은 머신 키 = 보안 연극'을 owner 이름으로 세우는 것이라 금지한다.
        #   비대화형 stdin·환경변수·자동화는 owner 권한이 아니다 → isatty 하드 게이트(approve 와 동일).
        try:
            interactive = sys.stdin.isatty()
        except Exception:
            interactive = False
        if not interactive:
            print("BLOCK: keychain-init 는 대화형 터미널에서만 가능합니다(비대화형 stdin·환경변수 거부·no-write).")
            print("  사장님이 직접 터미널에서 'binggu approval keychain-init' 를 실행하세요(AI 대행 금지).")
            return 2
        from binggupack.safety import keychain_backend as kb
        from binggupack.safety import signing_provider as sp
        backend = kb.get_backend()   # 실 OS keychain(inject 없음 = 운영 백엔드)
        if not backend.available():
            print("BLOCK: 이 플랫폼/환경에서 OS keychain 서명 백엔드 미가용(headless/미지원) — 키 생성 불가.")
            print("  fail-closed: L1 평문으로 자동 강등하지 않습니다(안 써짐이 최악보다 낫다).")
            return 1
        key_id = sp.KeychainProvider._DEFAULT_KEY_ID
        existed = backend.peek_key_present(key_id)
        try:
            sk, pk = backend.load_or_create_signing_key(key_id)   # 부재→생성·put / 존재→로드(idempotent)
        except kb.KeychainError as e:
            print("BLOCK: keychain 서명키 생성/로드 실패 (%s)." % e)
            return 1
        pin = sp.describe_secret(pk)   # 공개키 핀(sha256 hash8 + 길이)만 — sk 평문 0
        del sk                          # sk 참조 즉시 제거(노출/로깅 0)
        print("keychain 서명키 %s" % ("확인(기존 존재)" if existed else "생성 완료"))
        print("  key_id     : %s" % key_id)
        print("  public key : sha256=%s  len=%d  (핀 — sk/공개키 원문 미출력)"
              % (pin["sha256_hash8"], pin["length"]))
        print("\n활성화: %s 에 {\"enabled\": true, \"kind\": \"keychain\"} 설정 시 approve EVENT 가 "
              "Ed25519 로 서명·검증됩니다." % ta.config_path(home))
        print("정직 경계(§6): 같은 머신 셸이 keychain sk 를 로드하면 유효 서명이 가능하다(=보안 연극).")
        print("  config kind 는 모델-writable 평문이라 kind:local_owner 한 줄로 서명 검증이 skip 된다.")
        print("  L2 의 실질 값은 hosted/locked 배포(모델이 셸/keychain/config 미접촉)에서만 나온다.")
        return 0

    if rid is None:
        print("요청ID 를 지정하세요: binggu approval %s <요청ID>" % action)
        return 2

    db = open_g3(a.ledger)
    try:
        req = ta.get_request(db.con, rid)
    finally:
        db.close()
    if not req:
        print("요청을 찾을 수 없습니다: %s" % rid)
        return 1

    def _render():
        rev = ta.read_review(home, rid)
        print("요청ID : %s" % rid)
        print("작업   : %s" % req["operation"])
        print("대상 ledger : %s" % req["ledger_id"])
        print("만료   : %s" % req["expires_at"])
        print("상태   : %s" % req["state"])
        print("─ 실제 저장/변경 내용 ─")
        if rev:
            for it in rev.get("items", []):
                print("  %s: %s" % (it["label"], it["value"]))
        else:
            print("  (검토 레코드 없음 — 만료/정리됨)")

    if action == "show":
        _render()
        print("\n승인: binggu approval approve %s   ·   거절: binggu approval reject %s" % (rid, rid))
        return 0

    if action == "approve":
        # P1-A.1: 승인 EVENT 발행은 사장님이 직접 대화형 터미널에서 실행하는 데서만 나온다. 비대화형
        # (pipe/redirect/자동화)·환경변수는 승인 권한이 아니다(RFC §6). 하드 거부(exit 2·no-mint).
        # ★ BINGGU_TRUSTED_CLI 우회 제거: env truthy 로 비대화형 mint 하던 백도어(AOB-1 Critical) 봉인.
        try:
            interactive = sys.stdin.isatty()
        except Exception:
            interactive = False
        if not interactive:
            print("BLOCK: approval 은 대화형 터미널에서만 가능합니다(비대화형 stdin·환경변수 거부·no-write).")
            print("  사장님이 직접 터미널에서 'binggu approval approve %s' 를 실행하세요." % rid)
            return 2
        _render()
        ans = input("\n승인하려면 정확히 'APPROVE %s' 입력: " % rid[:8])   # 대화형 필수 · typed phrase 항상
        if ans.strip() != ("APPROVE %s" % rid[:8]):
            print("승인 취소(문구 불일치).")
            return 1
        cfg = ta.load_config(home) or {}
        ttl = int(cfg.get("ttl_seconds", ta.DEFAULT_TTL_SECONDS))
        ta.mint_approval(home, req, ttl, _t.time(), channel="cli_tty")   # isatty 검증 후에만 = 정직한 라벨
        print("승인 발행 완료: %s (만료 %ds). owner CLI 의 --approval-id 경로에서 이 작업이 "
              "정확히 1회 실행됩니다(MCP 표면은 approval 소비 불가·2026-07-13 제거)." % (rid, ttl))
        return 0

    if action in ("reject", "revoke"):
        ta.tombstone(home, req, action, _t.time())
        ta.purge_review(home, rid)
        print("%s 처리 완료: %s" % (action, rid))
        return 0

    print("알 수 없는 action: %s (show/approve/reject/revoke)" % action)
    return 2
