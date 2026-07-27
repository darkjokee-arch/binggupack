"""빙구팩 영속 candidate 캡처 버퍼 — 기본 OFF · scope 게이트 · TTL · rollback.

설계: 영속 candidate buffer 선행 과제 (hook 실등록은 본 모듈 GO 이후 별도 단계).
원칙(전부 본 모듈에서 강제·셀프테스트로 증명):
  - candidate-only: state 항상 'captured_candidate' (active/confirmed/ledger write 0)
  - 원문(대화 전문) 미저장: 발화 TEXT_CAP(문장 전체 보존 상한) 초과분 truncate
  - 기본 OFF: ~/.binggupack/capture_enabled 플래그 있을 때만 동작
  - scope 게이트: repo/session 화이트리스트(fail-closed) + deny 우선 → example-project 등 타 세션 제외
  - TTL 자동 폐기: captured_at + ttl_days 경과분 lazy purge
  - rollback: capture_buffer.sqlite 단일 파일 삭제 1회로 완전 원복 (운영 ledger.sqlite 미접촉)

hook은 등록하지 않음(미래 별도 GO). 본 모듈은 should_capture 게이트 + 영속 store만 제공.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from binggu_capture_classifier import EXPLICIT_SAVE, PREVIEW_TRIGGER, _any, classify

TEXT_CAP = 1000         # 자동수집 버퍼 발화 보존 상한 = capture_preview.MAX_NODE_SENTENCE 정합(owner GO 2026-06-15).
AI_CONTEXT_CAP = 400    # B(대화쌍) pair 재료(직전 AI말) 발췌 상한 — 전문 저장 금지(§3-2 원문 write0 원리).
# dialectic 판정: owner 발화가 직전 AI 말에 대한 반응(반박·반문·수정)일 때만 그 AI말을 pair 재료로 보관.
#   설계 §9 Phase3·§5 L6 정합 — 반문/AI교정/약한교정 = why 발화 = 대화쌍 후보.
#   좁게 시작(명시 dialectic 신호만) — 분류기 완화=노이즈 범람(2026-07-21 traj F) 경계.
DIALECTIC_SIGNALS = frozenset({"약한교정(맥락1턴)", "AI교정", "반문"})

# B-3(2026-07-21): dialectic 신호 → owner↔AI 관계 제안값(owner 가 SAVE 로 수용 · 틀리면 재저장
#   교정). AI 발화가 먼저·owner 가 반응 주체라 전부 owner_* (save_paired: owner_ prefix=source).
#   정확한 관계는 owner 판단 — 이건 '제안'이고 SAVE 앵커가 owner 승인(§8-1 정체성 축 자동확정 금지).
DIALECTIC_RELATION = {"AI교정": "owner_refutes", "반문": "owner_refutes",
                      "약한교정(맥락1턴)": "owner_revises"}
DEFAULT_PAIR_RELATION = "owner_revises"   # 신호 매핑 실패 시 중립(수정) 기본


def relation_from_signals(signals):
    """dialectic 신호 목록 → owner↔AI 관계 제안(owner_refutes/owner_revises). refutes(교정·반문)
    우선 → revises(약한교정) → 매치 없으면 중립 DEFAULT_PAIR_RELATION. AI 자동확정 아님(제안)."""
    sset = set(signals or [])
    for sig in ("AI교정", "반문", "약한교정(맥락1턴)"):
        if sig in sset:
            return DIALECTIC_RELATION[sig]
    return DEFAULT_PAIR_RELATION
                        # 문장 전체 보존(80자 발췌 폐기 — 개인 온톨로지 정체성). 1000 초과 = 대화 덩어리 → 절단(원문=대화 전문 저장 금지).
DEFAULT_TTL_DAYS = 7    # TTL 자동 폐기 기본값

# ── capture 유실 방지(MF1.5) ──────────────────────────────────────────────────
# 실패 시나리오(적대검토 실증): owner 발화 → hook 이 _conn() → 다른 프로세스가 buffer 를 잠금 →
#   ALTER 가 OperationalError → 통짜 `except: pass` 가 삼킴 → 직후 INSERT 가 `no such column`
#   으로 raise → feed() 전체 예외 → **그 발화가 버퍼에도 안 남고 사라진다**.
# 방어 4겹: ① busy_timeout ② 컬럼별 개별 try ③ ALTER 후 PRAGMA 재조회로 INSERT 컬럼 동적 구성
#   (신규 컬럼 부재 시 레거시 INSERT 자동 강등) ④ feed() 절대 raise 금지 + 최후 fallback jsonl.
CAPTURE_BUSY_TIMEOUT_MS = 5000
CAPTURE_FALLBACK_NAME = "capture_fallback.jsonl"   # <binggu_home>/ 하위(운영홈 리터럴 미사용)
# 하위호환 ALTER 대상 — (컬럼명, DDL). 신규 홈은 CREATE TABLE 에 이미 포함돼 ALTER 0.
_CAPTURE_ADDABLE = (
    ("session_id", "session_id TEXT"),
    ("ai_context", "ai_context TEXT"),
)
#   src_id/src_sha = 앞막이 좌표 재료(어느 세션/원문 전체 sha). 저장 경로(save_paired)의
#   evidence_locator 가 이 값을 source_id/container_sha 로 받아 '어느 대화 어디' 를 동결한다.
# ★ 이 2종은 **evloc 플래그 뒤**에 둔다. 이 파일이 저장되는 순간부터 hook(UserPromptSubmit)·
#   MCP 가 _conn() 을 호출하므로, 게이트가 없으면 플래그 OFF 인데도 다음 프롬프트에 운영
#   capture_buffer 가 ALTER 된다 = "OFF 면 기존 동작 불변" 최상위 제약 위반.
#   컬럼이 없어도 저장은 정상이다 — _live_cols 동적 구성이 degraded_columns 로 흡수한다.
_CAPTURE_ADDABLE_EVLOC = (
    ("src_id", "src_id TEXT"),
    ("src_sha", "src_sha TEXT"),
)


def _addable_columns():
    """이번 open 에서 ALTER 를 시도할 컬럼 목록. evloc OFF 면 기존 2종만(운영 무접촉)."""
    try:
        from binggu_schema import evloc_enabled
    except Exception:
        return _CAPTURE_ADDABLE          # 스키마 모듈 미가용 = 보수적으로 OFF 취급
    if evloc_enabled():
        return _CAPTURE_ADDABLE + _CAPTURE_ADDABLE_EVLOC
    return _CAPTURE_ADDABLE

# 대화 덩어리/붙여넣기/AI 응답문 veto 임계 — 길이 단독 아닌 "길이 + 줄바꿈 밀도"(2026-07-10 Fable5 2각 수렴).
#   실측(owner buffer 79건): owner 진짜 판단 med 91자·줄바꿈 med 0 / noise+AI응답 med 1000자·줄바꿈 med 17.
#   BULK_SOFT+NL_MIN 게이트가 noise+ai_resp 43/43(100%) 차단·owner 판단 14/16 보존(장문 2건은 명시저장 회수).
#   ★줄바꿈 조건이 기존 T7b(1110자·줄바꿈0 단일문 판단) 보존 — 순수 길이 veto 회귀 회피.
BULK_SOFT_LEN = 300     # 이 이상 + 줄바꿈 다수 = 붙여넣기/AI응답 덩어리로 판정
BULK_NL_MIN = 3         # 줄바꿈 밀도 임계(owner 타이핑 판단은 줄바꿈 0~2, 붙여넣기는 3+)
BULK_HARD_LEN = 2000    # 줄바꿈 없어도 이 이상 = 덩어리(단일행 초장문 안전망)

# 시스템 주입 텍스트 마커 — task-notification·hook feedback·command·reminder 등은 사용자 판단이
# 아니라 시스템/하네스가 prompt 에 주입한 텍스트다. capture 오염(2026-07-10 발견: preview 에
# task-notification 원문이 candidate 로 뜸)을 차단하기 위해 하나라도 포함되면 capture skip.
_SYSTEM_NOISE_MARKERS = (
    "<task-notification>", "</task-notification>", "<task-id>", "<tool-use-id>",
    "<output-file>", "<command-message>", "<command-name>", "<local-command",
    "<system-reminder>", "</system-reminder>", "hook feedback", "PreToolUse:",
    "hook additional context", "Stop hook", "<result>\n<name>", "task-notification>",
)


def _is_system_noise(utterance):
    """시스템 주입 텍스트(알림·hook·command·reminder)인지 — 사용자 판단 아님(capture 오염 차단)."""
    if not utterance or not str(utterance).strip():
        return True
    u = str(utterance)
    return any(m in u for m in _SYSTEM_NOISE_MARKERS)


def _is_explicit_signal(utterance):
    """명시 저장/preview 신호(EXPLICIT_SAVE·PREVIEW_TRIGGER 패턴)인지 — A3 scope 우회 판정.

    owner 가 개인 온톨로지 저장을 이름으로 지목한 발화("빙구팩 저장해"·"이거 저장해")는 cwd
    화이트리스트(중립 시작 위치=system32)에 막히면 안 된다(Fable5-A: 빙구팩이 빙구팩 게이트에
    막히는 부조리). classify 최상위 우선 패턴만 정규식으로 선검사한다 — 전체 classify 를 scope
    앞으로 빼지 않음(타 세션 일반 발화의 전면 분류 0 유지). enabled·deny 는 여전히 존중(feed)."""
    if not utterance or not str(utterance).strip():
        return False
    t = str(utterance).strip()
    return bool(_any(t, PREVIEW_TRIGGER) or _any(t, EXPLICIT_SAVE))


def _is_bulk_text(utterance):
    """대화 덩어리/붙여넣기/AI 응답문 판정 — 길이 + 줄바꿈 밀도(2026-07-10 Fable5 2각 + 실측 수렴).

    owner 진짜 판단은 짧고(med 91자) 줄바꿈 없음(med 0). 노이즈/AI응답은 길고(med 1000자) 줄바꿈 다수(med 17).
    C(문장 발췌)는 AI 응답문에서 문장을 뽑아 owner 온톨로지에 넣는 '화자축 오염'(owner 3회 지적 교훈:
    owner=자연어 원문 그대로·AI 정리 저장 금지)이라 기각 → 덩어리는 발췌 아닌 veto(안 담음).
    명시저장('이거 저장해')은 feed 에서 이 함수 앞에서 우회하므로 긴 의도적 저장 경로는 보존."""
    t = str(utterance or "")
    n = len(t)
    return n > BULK_HARD_LEN or (n > BULK_SOFT_LEN and t.count("\n") >= BULK_NL_MIN)


def binggu_home(home=None):
    """buffer 루트. 테스트는 home 인자/ BINGGU_HOME 으로 운영 경로 미접촉."""
    if home:
        return Path(home)
    env = os.environ.get("BINGGU_HOME")
    if env:
        return Path(env)
    return Path.home() / ".binggupack"


def _norm(p):
    return str(Path(p)).replace("\\", "/").lower()


class CaptureScope:
    """capture 2중 게이트: 기본 OFF 플래그 AND repo/session scope 화이트리스트."""

    def __init__(self, home=None):
        self.home = binggu_home(home)
        self.flag = self.home / "capture_enabled"
        self.paused_flag = self.home / "capture_paused"
        self.disabled_flag = self.home / "capture_disabled"  # owner sticky OFF 정책(영구 OFF)
        self.scope_file = self.home / "capture_scope.json"
        self.sem_preview_flag = self.home / "semantic_preview_enabled"  # opt-in 기본 OFF
        self.rationale_preview_flag = self.home / "rationale_preview_enabled"  # 2층 opt-in 기본 OFF

    def enabled(self):
        """기본 OFF: capture_enabled 플래그 존재 AND capture_paused 없음 AND sticky OFF 마커 없음.
        owner sticky OFF(capture_disabled)는 enabled 플래그 존재보다 우선(정책 영구 OFF)."""
        if self.disabled_flag.exists():
            return False
        return self.flag.exists() and not self.paused_flag.exists()

    def paused(self):
        return self.paused_flag.exists()

    def _scope(self):
        empty = {"global": False, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}
        if not self.scope_file.exists():
            return empty
        try:
            d = json.loads(self.scope_file.read_text(encoding="utf-8"))
            return {
                "global": bool(d.get("global", False)),
                "allowed_cwd_prefixes": list(d.get("allowed_cwd_prefixes", [])),
                "denied_cwd_substrings": list(d.get("denied_cwd_substrings", [])),
            }
        except Exception:
            return empty

    def _denied(self, cwd):
        """deny substring 매치(명시 배제 프로젝트) 여부. in_scope 와 A3 explicit bypass 가 공유하는
        '절대 우선 차단' 판정 — global 이든 explicit 명시신호든 deny 는 항상 존중한다."""
        cwd_n = _norm(cwd)
        return any(_norm(d) in cwd_n for d in self._scope()["denied_cwd_substrings"])

    def in_scope(self, cwd):
        """deny 우선 → global(전역) → allow 화이트리스트. allow 비면 fail-closed(False).
        global=true 라도 denied_cwd_substrings 는 항상 우선 차단."""
        if self._denied(cwd):
            return False
        sc = self._scope()
        if sc["global"]:
            return True
        allow = sc["allowed_cwd_prefixes"]
        if not allow:
            return False
        cwd_n = _norm(cwd)
        return any(cwd_n.startswith(_norm(a)) for a in allow)

    def should_capture(self, cwd):
        return self.enabled() and self.in_scope(cwd)

    def semantic_preview(self):
        """opt-in 기본 OFF: semantic_preview_enabled 플래그 있을 때만 shadow subtype 보조 라벨 표시.
        capture 결정과 무관 — 표시 전용 게이트."""
        return self.sem_preview_flag.exists()

    def rationale_preview(self):
        """opt-in 기본 OFF: rationale_preview_enabled 플래그 있을 때만 2층 근거/엣지 추천 표시.
        추천만 — capture 결정·저장과 무관(read-only)."""
        return self.rationale_preview_flag.exists()


class PersistentCaptureBuffer:
    """발화 간 누적되는 영속 candidate 버퍼. 게이트 통과 발화만 적재."""

    def __init__(self, home=None, ttl_days=DEFAULT_TTL_DAYS):
        self.home = binggu_home(home)
        self.scope = CaptureScope(home)
        self.ttl_days = ttl_days
        self.db_path = self.home / "capture_buffer.sqlite"
        self.ledger_path = self.home / "ledger.sqlite"  # 미접촉 대상(경로 참조만)
        self.fallback_path = self.home / CAPTURE_FALLBACK_NAME
        self._live_cols = []      # 마지막 _conn() 시점 실재 컬럼(동적 INSERT/SELECT 구성용)
        self._alter_errors = {}   # 컬럼별 ALTER 실패 사유(삼키지 않고 표면화)

    # ---- store ----
    def _conn(self):
        self.home.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(self.db_path))
        # ① 동시 접근 대비 — 다른 프로세스(MCP recall 등)가 잠근 순간 즉시 실패하지 않게.
        #    journal_mode 는 건드리지 않는다(운영 buffer 파일 포맷을 바꾸는 영구 변경 회피).
        try:
            c.execute("PRAGMA busy_timeout=%d" % CAPTURE_BUSY_TIMEOUT_MS)
        except sqlite3.Error:
            pass
        c.execute(
            """CREATE TABLE IF NOT EXISTS capture_candidates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                confidence TEXT,
                signals TEXT,
                state TEXT NOT NULL DEFAULT 'captured_candidate',
                captured_at REAL NOT NULL,
                cwd TEXT,
                session_id TEXT,
                ai_context TEXT,
                src_id TEXT,
                src_sha TEXT)"""
        )
        # 대화 덩어리 veto 카운트(무음 폐기 방지 — preview 에 "긴 발화 n건 제외" 노출). 원문 미저장(길이만).
        c.execute(
            """CREATE TABLE IF NOT EXISTS bulk_vetoes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at REAL NOT NULL,
                cwd TEXT,
                session_id TEXT,
                length INTEGER)"""
        )
        # ② 하위호환 ALTER — **컬럼별 개별 try**. 통짜 try/except 는 한 컬럼 실패가 뒤 컬럼을
        #    통째로 건너뛰게 해 '컬럼 일부만 있는' 상태를 만든다. 실패는 사유를 남긴다(삼키지 않음).
        have = {r[1] for r in c.execute("PRAGMA table_info(capture_candidates)")}
        for name, ddl in _addable_columns():
            if name in have:
                self._alter_errors.pop(name, None)
                continue
            try:
                c.execute("ALTER TABLE capture_candidates ADD COLUMN %s" % ddl)
                c.commit()
                self._alter_errors.pop(name, None)
            except sqlite3.Error as ex:
                self._alter_errors[name] = "%s: %s" % (type(ex).__name__, ex)
        # ③ ALTER 결과를 **재조회** — INSERT/SELECT 컬럼을 실재 기준으로 구성한다(부재 시 자동 강등).
        try:
            self._live_cols = [r[1] for r in c.execute("PRAGMA table_info(capture_candidates)")]
        except sqlite3.Error:
            self._live_cols = []
        return c

    def feed(self, utterance, cwd, prev_turn=None, now=None, session_id=None, source_id=None):
        """발화 1건. 게이트 통과 + captured_candidate 만 영속.
        게이트 차단 시 classify 조차 호출 안 함(타 세션 발화 미분류).
        session_id: 세션 경계용(세션 마무리 preview 가 그 세션 발화만 표시하도록 태깅).
        source_id: 앞막이 출처 id(transcript 경로·세션 id 등). 미지정 시 session_id 로 파생.

        ★이 함수는 **어떤 경우에도 raise 하지 않는다**(MF1.5) — 캡처 유실이 절단보다 큰 손실이다.
        저장 실패 시 stored=False + store_note(사유)를 반환하고 fallback jsonl 에 원문을 남긴다."""
        now = time.time() if now is None else now
        # 시스템 주입 텍스트(task-notification·hook feedback·command 등)는 사용자 판단 아님 → 미수집.
        #   명시신호 우회보다 먼저 차단(task-notification 원문이 "저장" 포함해도 오염 안 되게).
        if _is_system_noise(utterance):
            return {"action": "system_noise", "stored": False}
        # A3: 명시 저장/preview 신호는 cwd allow 화이트리스트를 우회(중립 cwd=system32 에서도 통과).
        #   enabled(기능 ON)·deny(명시 배제 프로젝트)는 존중 — 우회는 allow 미스에만(오염 표면 ≈0).
        explicit = _is_explicit_signal(utterance)
        if explicit:
            if not self.scope.enabled() or self.scope._denied(cwd):
                return {"action": "skipped_scope", "stored": False,
                        "enabled": self.scope.enabled(), "in_scope": self.scope.in_scope(cwd)}
        elif not self.scope.should_capture(cwd):
            return {"action": "skipped_scope", "stored": False,
                    "enabled": self.scope.enabled(), "in_scope": self.scope.in_scope(cwd)}
        # 대화 덩어리/붙여넣기/AI 응답문 veto(길이+줄바꿈) — 단 명시저장(explicit)은 owner 의도라 우회.
        #   veto 시 원문 미저장, 카운트만 남겨 preview 에 "긴 발화 n건 제외" 노출(무음 폐기 방지).
        if not explicit and _is_bulk_text(utterance):
            self._record_bulk_veto(now, cwd, session_id, len(str(utterance)))
            return {"action": "bulk_veto", "stored": False, "length": len(str(utterance))}
        v = classify(utterance, prev_turn)
        if v["state"] == "preview_trigger":
            self._purge(now)
            return {"action": "preview", "verdict": v, "preview": self.render_preview(now)}
        if v["state"] != "captured_candidate":
            return {"action": "ignored", "verdict": v, "stored": False}
        text = utterance.strip()
        truncated = len(text) > TEXT_CAP
        if truncated:
            text = text[:TEXT_CAP]  # 원문 전문 저장 금지
        # B(2026-07-21 owner "대화쌍 통째"): owner 발화가 직전 AI 말에 대한 반응(dialectic)이면
        #   그 AI 말 발췌를 pair 재료로 candidate 에 보관(저장 아님). owner 가 PAIR 로 확정할 때
        #   save_paired(owner_text, ai_text, relation)의 ai_text 재료가 된다. 발췌 cap(전문 금지) +
        #   dialectic 신호일 때만(무분별 AI말 저장 회피 · 분류기 완화=노이즈 traj F 경계).
        ai_ctx = None
        if prev_turn and (DIALECTIC_SIGNALS & set(v.get("signals") or [])):
            ai_ctx = str(prev_turn).strip()[:AI_CONTEXT_CAP]
        # 앞막이 좌표 재료 — src_sha 는 **절단 전 원문 전체**의 sha256(64hex). TEXT_CAP 로 잘려도
        # '원래 무엇이었는지' 는 남는다(저장 문장 ↔ 원문 대조·회수 키).
        src_sha = hashlib.sha256(str(utterance).encode("utf-8")).hexdigest()
        src_id = source_id or (("session:" + str(session_id)) if session_id else None)
        payload = {
            "text": text, "pinned": 1 if v["pinned"] else 0, "confidence": v["confidence"],
            "signals": json.dumps(list(v["signals"]), ensure_ascii=False),
            "state": "captured_candidate", "captured_at": now, "cwd": str(cwd),
            "session_id": session_id, "ai_context": ai_ctx,
            "src_id": src_id, "src_sha": src_sha,
        }
        stored, note = self._store_candidate(payload, now)
        out = {"action": "captured", "verdict": v, "stored": stored, "truncated": truncated}
        if note:
            out["store_note"] = note   # best-effort 는 침묵이 아니라 사유 반환
        return out

    def _store_candidate(self, payload, now):
        """candidate 1행 영속. (stored, note) 반환 — **raise 하지 않는다**.

        3단 방어: 동적 컬럼 INSERT → (실패) 레거시 최소 INSERT → (실패) fallback jsonl.
        """
        c = None
        try:
            c = self._conn()
            live = set(self._live_cols)
            cols = [k for k in payload if k in live]
            if not cols:
                raise sqlite3.OperationalError("capture_candidates 컬럼 조회 실패(스키마 부재)")
            c.execute("INSERT INTO capture_candidates(%s) VALUES(%s)"
                      % (",".join(cols), ",".join("?" * len(cols))),
                      [payload[k] for k in cols])
            c.commit()
            self._purge(now, conn=c)
            missing = [k for k in payload if k not in live]
            return True, ("degraded_columns:%s" % ",".join(missing)) if missing else None
        except Exception as ex:
            note = "%s: %s" % (type(ex).__name__, ex)
            try:   # 최종 방어 1 — 텍스트만이라도 저장(NOT NULL 3컬럼)
                if c is not None:
                    c.execute("INSERT INTO capture_candidates(text,captured_at,state)"
                              " VALUES(?,?,?)",
                              (payload["text"], payload["captured_at"], payload["state"]))
                    c.commit()
                    return True, "minimal_insert(%s)" % note
            except Exception as ex2:
                note += " / minimal:%s" % type(ex2).__name__
            fb = self._fallback_append(payload, note)   # 최종 방어 2 — ledger 미접촉 jsonl
            return False, "fallback_jsonl(%s)%s" % (note, "" if fb else " [FALLBACK-WRITE-FAILED]")
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass

    def _fallback_append(self, payload, note):
        """DB 저장 전멸 시 원문 보존 — <binggu_home>/capture_fallback.jsonl append. 성공 여부 반환."""
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            rec = dict(payload)
            rec["_error"] = note
            with open(self.fallback_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False

    def fallback_pending(self):
        """fallback jsonl 에 대기 중인 복구 대상 건수(0=정상). render_preview 상단에 상시 노출한다 —
        무음 유실 금지(적대검토 NEW2.11: '조용히 비는 SAVE 목록' 재발 방지)."""
        try:
            if not self.fallback_path.exists():
                return 0
            with open(self.fallback_path, "r", encoding="utf-8") as f:
                return sum(1 for ln in f if ln.strip())
        except Exception:
            return 0

    def _record_bulk_veto(self, now, cwd, session_id, length):
        """대화 덩어리 veto 1건 카운트 기록(원문 미저장 — 길이만). preview 노출용·graceful."""
        try:
            c = self._conn()
            try:
                c.execute(
                    "INSERT INTO bulk_vetoes(captured_at,cwd,session_id,length) VALUES(?,?,?,?)",
                    (now, str(cwd), session_id, int(length)))
                c.commit()
            finally:
                c.close()
        except Exception:
            pass  # 카운트 실패는 capture 결정에 무영향(veto 자체는 이미 확정)

    def _bulk_veto_count(self, now, session_id=None, conn=None):
        """TTL 유효 bulk veto 건수(세션 경계). session_id 지정 시 그 세션만."""
        cutoff = now - self.ttl_days * 86400
        own = conn is None
        c = conn or self._conn()
        try:
            if session_id is not None:
                return c.execute("SELECT COUNT(*) FROM bulk_vetoes WHERE session_id=? AND captured_at>=?",
                                 (session_id, cutoff)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM bulk_vetoes WHERE captured_at>=?", (cutoff,)).fetchone()[0]
        finally:
            if own:
                c.close()

    def _purge(self, now, conn=None):
        """TTL 경과분 삭제. 반환=삭제 건수(candidate + bulk_vetoes)."""
        cutoff = now - self.ttl_days * 86400
        own = conn is None
        c = conn or self._conn()
        try:
            n = c.execute("DELETE FROM capture_candidates WHERE captured_at < ?", (cutoff,)).rowcount
            c.execute("DELETE FROM bulk_vetoes WHERE captured_at < ?", (cutoff,))
            c.commit()
            return n
        finally:
            if own:
                c.close()

    def render_preview(self, now=None, semantic=None, session_id=None):
        """captured 후보 목록. session_id 지정 시 그 세션 발화만(세션 경계 — 이전 세션 잔존 배제·
        2026-07-10). semantic 인자는 테스트 주입용(미지정 시 opt-in ON에서 lazy 생성)."""
        now = time.time() if now is None else now
        # 동적 SELECT — 구 ledger(신규 컬럼 없음)에서 컬럼 하드코딩은 조용한 무동작/에러가 된다(R6).
        _want = ["text", "pinned", "confidence", "cwd", "ai_context", "signals", "src_id", "src_sha"]
        c = self._conn()
        try:
            self._purge(now, conn=c)
            live = set(self._live_cols)
            sel = [col for col in _want if col in live]
            base = "SELECT %s FROM capture_candidates " % ",".join(sel)
            if session_id is not None and "session_id" in live:
                rows = c.execute(base + "WHERE session_id=? ORDER BY pinned DESC, id ASC",
                                 (session_id,)).fetchall()
            else:
                rows = c.execute(base + "ORDER BY pinned DESC, id ASC").fetchall()
            bulk_vetoed = self._bulk_veto_count(now, session_id, conn=c)
        finally:
            c.close()
        items = []
        for i, raw_row in enumerate(rows, 1):
            r = dict(zip(sel, raw_row))
            text, pinned, conf = r.get("text"), r.get("pinned"), r.get("confidence")
            cwd, ai_ctx, sig_json = r.get("cwd"), r.get("ai_context"), r.get("signals")
            tags = []
            if pinned:
                tags.append("PINNED")
            if conf == "weak":
                tags.append("약함")
            # B-2/B-3(대화쌍): 직전 AI말 발췌(ai_context) 보유 후보 = SAVE 시 save_paired pair
            #   저장 대상. 관계는 dialectic 신호로 제안(pair_relation) — owner SAVE 가 승인.
            pair_rel = None
            if ai_ctx:
                tags.append("대화쌍")
                try:
                    pair_rel = relation_from_signals(json.loads(sig_json) if sig_json else [])
                except Exception:
                    pair_rel = DEFAULT_PAIR_RELATION
            # 출처(Fable5-A 권장): candidate 가 명시 배제(deny) cwd 에서 유입됐으면 SAVE 식별용 경고
            #   태그. explicit bypass 가 deny 를 존중하므로 대개 안 뜸(과거/global 데이터 대비). cwd
            #   필드는 소비처가 출처를 참고하도록 데이터로 상시 노출(label 소음은 최소).
            foreign = bool(cwd and self.scope._denied(cwd))
            if foreign:
                tags.append("타 세션?")
            tag = f" [{' · '.join(tags)}]" if tags else ""
            items.append({
                "idx": i, "text": text, "pinned": bool(pinned), "confidence": conf,
                "cwd": cwd, "foreign": foreign, "ai_context": ai_ctx, "pair_relation": pair_rel,
                # 앞막이 좌표 재료(저장 경로가 evidence_locator 로 넘긴다). 번호축·label 불변.
                "src_id": r.get("src_id"), "src_sha": r.get("src_sha"),
                "label": f"{i}. {text}{tag}", "state": "captured_candidate",
            })
        if self.scope.semantic_preview():
            self._attach_semantic(items, semantic)
        result = {"count": len(items), "items": items, "bulk_vetoed": bulk_vetoed,
                  "note": "owner 승인 전 candidate (active 아님)"}
        _fb = self.fallback_pending()
        if _fb:   # 무음 유실 금지 — 복구 대기분을 목록 위에 상시 노출
            result["fallback_pending"] = _fb
            result["note"] += " · ⚠ 저장 실패로 파일 대기 %d건(%s)" % (_fb, self.fallback_path.name)
        if self.scope.rationale_preview():
            self._attach_rationale(items, result)
        return result

    def _attach_rationale(self, items, result):
        """opt-in ON 시 2층 근거/엣지 추천을 read-only 부착. 추천만 — 저장·DB·ledger 미접촉.
        capture 단계 후보엔 evidence 미첨부라 edge는 보통 보류(rationale만). 실패 시 무변화(graceful)."""
        if not items:
            return
        try:
            from binggu_rationale_suggest import suggest_rationale
            from openbinggu_label_kind_map import classify_label_kind
            cands = [{
                "text": it["text"],
                "label_kind": classify_label_kind(it["text"])[0],   # canonical(정규식·정본)
                "semantic_subtype": (it.get("semantic") or {}).get("subtype"),  # 1층 보조(있으면)
                "confidence": it.get("confidence"),
                "evidence_refs": [],   # capture 후보엔 원문증빙 없음(pack 빌드 단계에서 첨부)
            } for it in items]
            rec = suggest_rationale(cands)
            for it, ra in zip(items, rec["rationale"]):
                it["rationale"] = {"why": ra["rationale"], "caveat": ra["caveat"]}
            result["suggested_edges"] = rec["suggested_edges"]   # 보통 [](evidence 보류)
            result["rationale_note"] = rec["note"]
        except Exception:
            return

    def _attach_semantic(self, items, semantic=None):
        """opt-in ON 시 captured 후보에만 shadow subtype 보조 라벨 부착 (read-only).
        방어선: cos = subtype 추천/설명 전용 — capture 결정·state·DB·ledger 일체 미접촉.
        shadow/Ollama 실패 시 무변화(graceful) — 기존 출력 보존."""
        if not items:
            return
        try:
            if semantic is None:
                from binggu_semantic_shadow import get_cached_shadow
                semantic = get_cached_shadow()  # 프로세스 내 캐시(centroid 재계산 1회)
            for it in items:
                sug = semantic.subtype_suggestion(it["text"])
                if sug:
                    it["semantic"] = {  # canonical 아님 — shadow 보조 필드
                        "subtype": sug["sem_subtype"], "score": sug["sem_conf"], "band": sug["band"],
                        "note": f"추천 subtype(보조·cos): {sug['sem_subtype']} ({sug['band']}, {sug['sem_conf']})",
                    }
        except Exception:
            return

    @property
    def size(self):
        if not self.db_path.exists():
            return 0
        c = self._conn()
        try:
            return c.execute("SELECT COUNT(*) FROM capture_candidates").fetchone()[0]
        finally:
            c.close()

    # ---- rollback ----
    def backup(self):
        if not self.db_path.exists():
            return None
        bak = self.db_path.with_suffix(".sqlite.bak")
        shutil.copy2(self.db_path, bak)
        return bak

    def rollback(self):
        """buffer 완전 삭제 = rollback. 운영 ledger.sqlite 미접촉."""
        existed = self.db_path.exists()
        if existed:
            self.db_path.unlink()
        return existed


# ---------------- 셀프테스트 (temp home 전용, 운영 ~/.binggupack 미접촉) ----------------
def _selftest():
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        mark = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{mark}] {msg}")
        return cond

    tmp = Path(tempfile.mkdtemp(prefix="bgp_capture_persist_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        # 운영 ledger 미접촉 검증용 더미
        ledger = home / "ledger.sqlite"
        ledger.write_bytes(b"LEDGER-SENTINEL")
        ledger_mtime0 = ledger.stat().st_mtime_ns

        repo_cwd = "C:/Users/fixture-user/binggupack"
        other_cwd = "C:/Users/fixture-user/example-org/example-project"

        scope = CaptureScope(home=home)

        # T1 기본 OFF: 플래그 없음 → 저장 0
        buf = PersistentCaptureBuffer(home=home)
        r = buf.feed("B안으로 결정", repo_cwd)
        check(r["action"] == "skipped_scope" and not r["stored"] and buf.size == 0,
              "T1 기본 OFF(플래그 없음) → skipped, 저장 0")

        # 플래그 ON
        scope.flag.write_text("1", encoding="utf-8")

        # T2 scope 미설정(allow 비어있음) → fail-closed
        r = buf.feed("B안으로 결정", repo_cwd)
        check(r["action"] == "skipped_scope" and buf.size == 0,
              "T2 플래그 ON + scope 화이트리스트 비어있음 → fail-closed 저장 0")

        # scope 설정: binggu 허용, example-project/example-org deny
        scope.scope_file.write_text(json.dumps({
            "allowed_cwd_prefixes": ["C:/Users/fixture-user/binggupack"],
            "denied_cwd_substrings": ["example-project", "example-org"],
        }, ensure_ascii=False), encoding="utf-8")

        # T3 플래그 ON + scope 일치 → captured 영속
        r = buf.feed("이거 저장해", repo_cwd)
        check(r["action"] == "captured" and r["stored"] and buf.size == 1,
              "T3 플래그 ON + scope 일치 → captured 영속(size=1)")

        # T4 타 repo(example-project) → 게이트 차단 (allow 미매치 + deny 매치)
        r = buf.feed("B안으로 결정", other_cwd)
        check(r["action"] == "skipped_scope" and buf.size == 1,
              "T4 example-project 세션 발화 → 제외(size 불변=1)")

        # T5 deny 우선: 허용 prefix 안이라도 deny substring이면 차단
        r = buf.feed("결정했다", "C:/Users/fixture-user/binggupack/example-project-notes")
        check(r["action"] == "skipped_scope" and buf.size == 1,
              "T5 deny substring 우선 → 허용 prefix 내부라도 차단")

        # T6 ignored 발화 → 저장 0
        r = buf.feed("ㅋㅋ 웃기네", repo_cwd)
        check(r["action"] == "ignored" and not r["stored"] and buf.size == 1,
              "T6 ignored 발화 → 저장 0(size 불변)")

        # T7 원문(대화 덩어리) 전문 미저장: TEXT_CAP(=문장 전체 상한) 초과만 truncate.
        #     짧은 발화는 온전히 보존(문장 전체 정체성) — 1000 초과 덩어리만 절단.
        midtext = "B안으로 결정한다 " + ("가" * 300)  # ~308자 < TEXT_CAP → 전체 보존(절단 0)
        r_mid = buf.feed(midtext, repo_cwd)
        pv_mid = buf.render_preview()
        kept = next(it["text"] for it in pv_mid["items"] if it["text"].startswith("B안으로 결정한다 가"))
        check(not r_mid.get("truncated") and kept == midtext.strip(),
              f"T7 문장 전체 보존(< {TEXT_CAP}자 절단 0)")
        longtext = "B안으로 결정한다 " + ("가" * 1100)  # > TEXT_CAP → 대화 덩어리로 간주 절단
        r = buf.feed(longtext, repo_cwd)
        pv = buf.render_preview()
        stored_text = max((it["text"] for it in pv["items"]
                           if it["text"].startswith("B안으로 결정한다 가")), key=len)
        check(r.get("truncated") and len(stored_text) == TEXT_CAP,
              f"T7b 대화 덩어리(> {TEXT_CAP}자) 절단(원문 전문 저장 금지)")

        # T8 candidate-only: state 항상 captured_candidate
        check(all(it["state"] == "captured_candidate" for it in pv["items"]),
              "T8 candidate-only(active/confirmed 0)")

        # T9 영속 round-trip: 새 인스턴스 재오픈 시 누적 유지 (hook 발화간 누적 핵심)
        size_before = buf.size
        buf2 = PersistentCaptureBuffer(home=home)
        check(buf2.size == size_before and size_before == 3,
              "T9 영속 round-trip(새 인스턴스 재오픈 누적 유지, size=3)")

        # T10 TTL 자동 폐기: 미래 시각으로 preview → 경과분 삭제
        future = time.time() + (DEFAULT_TTL_DAYS + 1) * 86400
        pv_future = buf2.render_preview(now=future)
        check(pv_future["count"] == 0 and buf2.size == 0,
              "T10 TTL 경과 → 자동 purge(size=0)")

        # T11 pinned 상단 정렬 (재적재 후 확인)
        buf2.feed("B안으로 결정한다", repo_cwd)  # normal
        buf2.feed("이거 저장해", repo_cwd)  # pinned
        pv2 = buf2.render_preview()
        check(pv2["count"] == 2 and pv2["items"][0]["pinned"],
              "T11 preview pinned 상단 정렬")
        check(all("cwd" in it and "foreign" in it for it in pv2["items"])
              and not any(it["foreign"] for it in pv2["items"]),
              "T11b preview items 출처 cwd/foreign 필드(정상 cwd → foreign False)")

        # T12 rollback: 파일 삭제 → size 0
        bak = buf2.backup()
        existed = buf2.rollback()
        check(existed and buf2.size == 0 and not buf2.db_path.exists(),
              "T12 rollback(buffer 파일 삭제 → size 0)")
        check(bak is not None and bak.exists(), "T12b 백업 파일 생성됨")

        # T13 운영 ledger.sqlite 미접촉
        check(ledger.read_bytes() == b"LEDGER-SENTINEL" and ledger.stat().st_mtime_ns == ledger_mtime0,
              "T13 운영 ledger.sqlite 미접촉(내용·mtime 불변)")

        # T14 pause: capture_paused 플래그 → enabled여도 should_capture False
        (home / "capture_paused").write_text("1", encoding="utf-8")
        check(scope.paused() and not scope.should_capture(repo_cwd),
              "T14 pause 플래그 → should_capture False")
        (home / "capture_paused").unlink()
        check(scope.should_capture(repo_cwd), "T14b resume → should_capture True")

        # T15 global scope: 타 cwd 허용 · deny 는 여전히 우선 차단
        scope.scope_file.write_text(json.dumps({
            "global": True, "allowed_cwd_prefixes": [], "denied_cwd_substrings": ["example-project"],
        }, ensure_ascii=False), encoding="utf-8")
        check(scope.in_scope("D:/anywhere/else") and not scope.in_scope(other_cwd),
              "T15 global scope → 타 cwd 허용 · deny(example-project) 차단 유지")

        # T16~T19 semantic shadow preview opt-in 실배선 (read-only 보조 라벨)
        import hashlib
        import math
        from binggu_semantic_shadow import SemanticShadow

        def mock_embed(text, timeout=10):  # 결정적 mock — Ollama 미접촉
            h = hashlib.sha256(text.encode("utf-8")).digest()
            v = [h[i % len(h)] / 255.0 for i in range(64)]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n for x in v]

        buf3 = PersistentCaptureBuffer(home=home)
        buf3.feed("B안으로 결정한다", repo_cwd)  # captured 후보 1건 확보
        scope2 = CaptureScope(home=home)
        sem = SemanticShadow(embed_fn=mock_embed)

        # T16 opt-in 기본 OFF → semantic 필드 없음(완전 무변화)
        check(not scope2.semantic_preview(), "T16 semantic preview 기본 OFF")
        pv_off = buf3.render_preview(semantic=sem)
        check(pv_off["count"] >= 1 and all("semantic" not in it for it in pv_off["items"]),
              "T16b OFF → 보조 필드 없음(기존 출력 무변화)")

        # T17 opt-in ON → captured 후보에만 subtype 보조 라벨
        scope2.sem_preview_flag.write_text("1", encoding="utf-8")
        ledger_mtime_b = ledger.stat().st_mtime_ns
        db_mtime_b = buf3.db_path.stat().st_mtime_ns
        pv_on = buf3.render_preview(semantic=sem)
        valid_sub = {"교훈", "결정", "선호", "설계결정", "버그패턴", "사실"}
        check(pv_on["count"] >= 1 and all("semantic" in it and it["semantic"]["subtype"] in valid_sub
              for it in pv_on["items"]),
              "T17 ON → captured 후보에 subtype 보조 라벨")
        check(all(it["state"] == "captured_candidate" for it in pv_on["items"]),
              "T17b 보조 라벨이 capture state 미변경(여전히 candidate)")

        # T18 semantic preview = read-only: ledger·buffer DB write 0
        check(ledger.read_bytes() == b"LEDGER-SENTINEL" and ledger.stat().st_mtime_ns == ledger_mtime_b,
              "T18 ON preview → 운영 ledger 미접촉(write 0)")
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_b,
              "T18b ON preview → buffer DB 미변경(read-only)")

        # T19 ignored 발화는 buffer 미저장 → preview(ON)에도 미표시
        before = buf3.render_preview(semantic=sem)["count"]
        buf3.feed("ㅋㅋ 웃기네", repo_cwd)  # ignored
        pv2 = buf3.render_preview(semantic=sem)
        check(pv2["count"] == before and all("웃기네" not in it["text"] for it in pv2["items"]),
              "T19 ignored 발화 → preview ON 에도 미표시(should_capture 결정 불변)")
        scope2.sem_preview_flag.unlink()

        # T20 캐시 hit: preview ON 2회 → SemanticShadow 1회만 생성(centroid 재계산 병목 제거)
        import binggu_semantic_shadow as bss
        bss._SHADOW_CACHE.clear()
        build_count = {"n": 0}
        _orig_shadow = bss.SemanticShadow

        class _CountShadow(_orig_shadow):
            def __init__(self, *a, **k):
                build_count["n"] += 1
                k.setdefault("embed_fn", mock_embed)  # Ollama 미접촉
                super().__init__(*a, **k)

        bss.SemanticShadow = _CountShadow
        db_mtime_c = buf3.db_path.stat().st_mtime_ns
        ledger_mtime_c = ledger.stat().st_mtime_ns
        try:
            scope2.sem_preview_flag.write_text("1", encoding="utf-8")
            buf3.render_preview()   # semantic=None → get_cached_shadow → 생성 1
            pv_c2 = buf3.render_preview()   # 캐시 hit → 생성 0
            scope2.sem_preview_flag.unlink()
        finally:
            bss.SemanticShadow = _orig_shadow
            bss._SHADOW_CACHE.clear()
        check(build_count["n"] == 1, "T20 캐시 hit: preview ON 2회 → SemanticShadow 1회만 생성")
        check(all("semantic" in it for it in pv_c2["items"]) and len(pv_c2["items"]) >= 1,
              "T20b 캐시 경로도 captured 후보에 subtype 보조 라벨 정상")
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_c and ledger.stat().st_mtime_ns == ledger_mtime_c,
              "T20c 캐시 preview → buffer DB·ledger write 0(read-only)")

        # T21~T23 2층 rationale preview opt-in (read-only)
        # T21 기본 OFF → rationale 필드 없음(무변화)
        check(not scope2.rationale_preview(), "T21 rationale preview 기본 OFF")
        pv_r_off = buf3.render_preview(semantic=sem)
        check(pv_r_off.get("count", 0) >= 1 and all("rationale" not in it for it in pv_r_off["items"])
              and "suggested_edges" not in pv_r_off,
              "T21b OFF → rationale/suggested_edges 없음(무변화)")

        # T22 ON → captured 후보에만 rationale, evidence 미첨부라 edge 보류
        scope2.rationale_preview_flag.write_text("1", encoding="utf-8")
        db_mtime_r = buf3.db_path.stat().st_mtime_ns
        ledger_mtime_r = ledger.stat().st_mtime_ns
        pv_r_on = buf3.render_preview(semantic=sem)
        check(all("rationale" in it and "why" in it["rationale"] and "candidate" in it["rationale"]["caveat"]
                  for it in pv_r_on["items"]),
              "T22 ON → captured 후보에 rationale(why+candidate caveat)")
        check(pv_r_on.get("suggested_edges") == [],
              "T22b capture 단계 evidence 미첨부 → edge 보류(rationale만)")
        check(all(it["state"] == "captured_candidate" for it in pv_r_on["items"]),
              "T22c rationale이 capture state 미변경")

        # T23 read-only: buffer DB·ledger write 0
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_r and ledger.stat().st_mtime_ns == ledger_mtime_r,
              "T23 rationale preview ON → buffer DB·ledger write 0")
        scope2.rationale_preview_flag.unlink()

        # ── T24~T28 A3: 명시 저장/preview 신호의 cwd allow 우회(deny·disabled 는 존중) ──
        buf4 = PersistentCaptureBuffer(home=home)
        buf4.rollback()  # 깨끗한 버퍼(운영 ledger 미접촉)
        scope3 = CaptureScope(home=home)
        scope3.flag.write_text("1", encoding="utf-8")
        scope3.scope_file.write_text(json.dumps({
            "global": False,
            "allowed_cwd_prefixes": ["C:/Users/fixture-user/binggupack"],
            "denied_cwd_substrings": ["example-project"],
        }, ensure_ascii=False), encoding="utf-8")
        neutral = "C:/WINDOWS/system32"    # allow 미스 · deny 미스(중립 시작 위치)
        denied = "C:/Users/fixture-user/example-project"  # deny 매치
        # T24 명시 preview 신호 "빙구팩 저장해" → 중립 cwd allow 우회(preview)
        r = buf4.feed("빙구팩 저장해", neutral)
        check(r["action"] == "preview", "T24 명시 preview 신호 → 중립 cwd allow 우회(preview)")
        # T25 명시 저장 "이거 저장해" → 중립 cwd captured(pinned·scope 우회)
        r = buf4.feed("이거 저장해", neutral)
        check(r["action"] == "captured" and r["stored"] and buf4.size == 1,
              "T25 명시 저장 신호 → 중립 cwd allow 우회 captured")
        # T26 일반 판단(비명시)은 A3 우회 없음 → 중립 cwd 에서 여전히 skipped_scope
        r = buf4.feed("B안으로 결정한다", neutral)
        check(r["action"] == "skipped_scope" and buf4.size == 1,
              "T26 일반 발화(비명시) → 중립 cwd 우회 없음(skipped·size 불변)")
        # T27 명시신호라도 deny 매치 cwd 는 차단(명시 배제 프로젝트 존중)
        r = buf4.feed("이거 저장해", denied)
        check(r["action"] == "skipped_scope" and buf4.size == 1,
              "T27 명시 저장 + deny cwd → 차단(deny 존중·size 불변)")
        # T28 명시신호라도 capture OFF(플래그 제거)면 차단(enabled 존중)
        scope3.flag.unlink()
        r = buf4.feed("이거 저장해", neutral)
        check(r["action"] == "skipped_scope" and not r["stored"],
              "T28 명시 저장 + capture OFF → 차단(enabled 존중)")
        scope3.flag.write_text("1", encoding="utf-8")

        # ── T29~T34 대화 덩어리/붙여넣기/AI 응답문 veto (길이 + 줄바꿈 밀도) ──
        buf5 = PersistentCaptureBuffer(home=home)
        buf5.rollback()  # 깨끗한 버퍼(scope3: binggupack allow · example-project deny · flag ON)
        # T29 긴 붙여넣기(>300자 + 줄바꿈 3+) → bulk_veto 미저장
        long_paste = "이건 붙여넣기 " + ("가나다 결정한다 위험 항상\n" * 25)  # ~383자 · 줄바꿈 25
        r = buf5.feed(long_paste, repo_cwd)
        check(r["action"] == "bulk_veto" and not r["stored"] and buf5.size == 0,
              "T29 긴 붙여넣기(>300자+줄바꿈3+) → bulk_veto 미저장")
        # T30 긴 단일문 판단(줄바꿈 0) → bulk 아님 · captured + TEXT_CAP 절단(★T7b 보존 = 순수길이veto 회귀 회피)
        long_single = "이 방법이 더 낫다 " + ("가" * 1100)  # ~1109자 · 줄바꿈 0 < HARD 2000
        r = buf5.feed(long_single, repo_cwd)
        check(r["action"] == "captured" and r.get("truncated") and buf5.size == 1,
              "T30 긴 단일문(줄바꿈0) → bulk 아님·captured+truncated(T7b 보존)")
        # T31 명시저장 + 긴 덩어리 → explicit 우회(veto 면제) → captured
        r = buf5.feed("이거 저장해\n" + ("결정 위험\n" * 60), repo_cwd)
        check(r["action"] == "captured" and buf5.size == 2,
              "T31 명시저장+긴 덩어리 → explicit 우회 captured(veto 면제)")
        # T32 짧은 판단(줄바꿈 0) → 정상 captured(veto 무관)
        r = buf5.feed("B안으로 결정한다", repo_cwd)
        check(r["action"] == "captured" and buf5.size == 3,
              "T32 짧은 판단 → 정상 captured(veto 무관)")
        # T33 render_preview 에 bulk_vetoed 카운트 노출(무음 폐기 방지)
        pv = buf5.render_preview()
        check(pv.get("bulk_vetoed", 0) >= 1,
              "T33 preview bulk_vetoed 카운트 노출(긴 발화 제외 인지)")
        # T34 2000자+ 줄바꿈 0 → HARD veto(단일행 초장문 안전망)
        r = buf5.feed("가" * 2100, repo_cwd)
        check(r["action"] == "bulk_veto",
              "T34 2000자+ 줄바꿈0 → HARD veto(단일행 초장문)")

        # T35 B(대화쌍): dialectic 발화(AI교정/약한교정 signal + prev_turn) → 직전 AI말 발췌를
        #     ai_context 로 보관(pair 재료) · 독립판단(prev_turn 있어도)·무맥락(prev_turn 없음)은 NULL
        import sqlite3 as _sq
        buf6 = PersistentCaptureBuffer(home=home)
        buf6.feed("아니 그게 아니라 A가 맞다", repo_cwd, prev_turn="B안 추천")      # dialectic(AI교정)
        buf6.feed("여긴 왜 이렇게 진행하지", repo_cwd, prev_turn="C안 설명")        # dialectic(반문·설계 §9 Phase3)
        buf6.feed("C로 결정한다", repo_cwd, prev_turn="B안 추천")                # 독립판단(방향결정)
        buf6.feed("이게 더 맞다", repo_cwd)                                     # prev_turn 없음
        _con = _sq.connect(str(buf6.db_path))
        _rows = _con.execute(
            "SELECT ai_context FROM capture_candidates WHERE ai_context IS NOT NULL ORDER BY id").fetchall()
        _con.close()
        check([r[0] for r in _rows] == ["B안 추천", "C안 설명"],
              "T35 dialectic(AI교정·반문 §9 Phase3) 만 ai_context 저장 · 독립판단/무맥락 NULL")

        # T36 B-2: render_preview 가 대화쌍 후보(ai_context 보유)를 노출 + '대화쌍' 태그.
        #     save_paired ai_text 재료를 세션 마무리 preview 가 owner 에게 보여준다(저장 0·표시만).
        _pvB = buf6.render_preview()
        _pair = [it for it in _pvB["items"] if it.get("ai_context")]
        check(len(_pair) == 2 and all("대화쌍" in it["label"] for it in _pair)
              and sorted(it["ai_context"] for it in _pair) == ["B안 추천", "C안 설명"],
              "T36 render_preview 대화쌍 후보 노출(ai_context 필드 + '대화쌍' 태그)")

        # T37 B-3: 신호→관계 매핑(제안값) + render_preview pair_relation 노출
        check(relation_from_signals(["AI교정"]) == "owner_refutes"
              and relation_from_signals(["반문"]) == "owner_refutes"
              and relation_from_signals(["약한교정(맥락1턴)"]) == "owner_revises"
              and relation_from_signals([]) == "owner_revises",
              "T37 신호→관계 매핑(교정/반문=refutes · 약한=revises · 무매치=중립 revises)")
        check(all(it.get("pair_relation") in ("owner_refutes", "owner_revises") for it in _pair),
              "T37b render_preview 대화쌍 후보에 pair_relation(관계 제안) 노출")

        # ── T38~T43 MF1.5: ALTER 안전화 · 동적 INSERT/SELECT 강등 · feed no-raise · 유실 표면화 ──
        # 홈 이름은 기존 selftest 홈(home/.binggupack)과 분리(과거 home5·home7 충돌로 남의 outcome
        # 을 오염시킨 사고 2회 — 삽입 전 grep 확인 완료).
        home_mf15 = tmp / "home_capture_mf15"
        home_mf15.mkdir(parents=True)
        CaptureScope(home=home_mf15).flag.write_text("1", encoding="utf-8")
        (home_mf15 / "capture_scope.json").write_text(
            json.dumps({"global": True, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}),
            encoding="utf-8")

        # T38 레거시 테이블(session_id/ai_context/src_* 전부 없음) → 컬럼별 ALTER 보강 + 기존 행 보존
        legacy_db = home_mf15 / "capture_buffer.sqlite"
        lc = sqlite3.connect(str(legacy_db))
        lc.execute("""CREATE TABLE capture_candidates(
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0, confidence TEXT, signals TEXT,
            state TEXT NOT NULL DEFAULT 'captured_candidate', captured_at REAL NOT NULL, cwd TEXT)""")
        lc.execute("INSERT INTO capture_candidates(text,captured_at) VALUES('옛 발화', ?)",
                   (time.time(),))
        lc.commit(); lc.close()
        buf7 = PersistentCaptureBuffer(home=home_mf15)
        r38 = buf7.feed("B안으로 결정한다", repo_cwd, session_id="S-MF15")
        cols38 = [c[1] for c in sqlite3.connect(str(legacy_db)).execute(
            "PRAGMA table_info(capture_candidates)")]
        old38 = sqlite3.connect(str(legacy_db)).execute(
            "SELECT text FROM capture_candidates WHERE id=1").fetchone()
        check(r38["action"] == "captured" and r38["stored"] and not buf7._alter_errors
              and all(cn in cols38 for cn in ("session_id", "ai_context", "src_id", "src_sha"))
              and old38[0] == "옛 발화",
              "T38 레거시 buffer → 컬럼별 ALTER 보강(4종) + 기존 행 보존 + 저장 정상")

        # T39 앞막이 좌표 재료: src_id(세션)·src_sha(**절단 전 원문 전체** sha256) 적재 + preview 노출
        raw39 = "이 방법이 더 낫다 " + ("가" * 1100)      # TEXT_CAP 초과 → text 는 잘려 저장
        r39 = buf7.feed(raw39, repo_cwd, session_id="S-MF15")
        it39 = next(it for it in buf7.render_preview()["items"] if it["text"].startswith("이 방법이"))
        check(r39.get("truncated") and it39["src_id"] == "session:S-MF15"
              and it39["src_sha"] == hashlib.sha256(raw39.encode("utf-8")).hexdigest()
              and len(it39["text"]) == TEXT_CAP,
              "T39 src_id/src_sha 적재+preview 노출(절단돼도 원문 전체 sha 보존)")

        # T40 신규 컬럼을 만들 수 없는 환경 → INSERT/SELECT 동적 강등(레거시 저장 성공·raise 0)
        home_deg = tmp / "home_capture_degraded"
        home_deg.mkdir(parents=True)
        CaptureScope(home=home_deg).flag.write_text("1", encoding="utf-8")
        (home_deg / "capture_scope.json").write_text(json.dumps({"global": True}), encoding="utf-8")
        dg = sqlite3.connect(str(home_deg / "capture_buffer.sqlite"))
        dg.execute("""CREATE TABLE capture_candidates(
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0, confidence TEXT, signals TEXT,
            state TEXT NOT NULL DEFAULT 'captured_candidate', captured_at REAL NOT NULL, cwd TEXT)""")
        dg.commit(); dg.close()
        _orig_addable = globals()["_CAPTURE_ADDABLE"]
        globals()["_CAPTURE_ADDABLE"] = ()      # ALTER 전멸 시뮬(잠금·권한으로 못 늘린 상태)
        try:
            buf8 = PersistentCaptureBuffer(home=home_deg)
            r40 = buf8.feed("C안으로 결정한다", repo_cwd, session_id="S-DEG")
            pv40 = buf8.render_preview()
        finally:
            globals()["_CAPTURE_ADDABLE"] = _orig_addable
        check(r40["action"] == "captured" and r40["stored"]
              and "degraded_columns" in (r40.get("store_note") or "")
              and pv40["count"] == 1 and pv40["items"][0]["src_id"] is None
              and pv40["items"][0]["text"] == "C안으로 결정한다",
              "T40 신규 컬럼 부재 → 동적 INSERT/SELECT 강등 저장(raise 0 · 사유 반환)")

        # T41 외부 프로세스가 EXCLUSIVE 잠금 → feed raise 0 · 원문은 fallback jsonl 로 보존
        home_lock = tmp / "home_capture_locked"
        home_lock.mkdir(parents=True)
        CaptureScope(home=home_lock).flag.write_text("1", encoding="utf-8")
        (home_lock / "capture_scope.json").write_text(json.dumps({"global": True}), encoding="utf-8")
        buf9 = PersistentCaptureBuffer(home=home_lock)
        buf9.feed("A안으로 결정한다", repo_cwd)          # 테이블 생성 선행
        _orig_to = globals()["CAPTURE_BUSY_TIMEOUT_MS"]
        globals()["CAPTURE_BUSY_TIMEOUT_MS"] = 150      # 셀프테스트 지연 억제(운영 기본값 불변)
        blocker = sqlite3.connect(str(buf9.db_path))
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            blocker.execute("INSERT INTO capture_candidates(text,captured_at) VALUES('lock',?)",
                            (time.time(),))
            raised41 = None
            try:
                r41 = buf9.feed("D안으로 결정한다", repo_cwd, session_id="S-LOCK")
            except Exception as ex:                      # feed 는 절대 raise 하면 안 된다
                raised41, r41 = ex, {}
        finally:
            blocker.rollback(); blocker.close()
            globals()["CAPTURE_BUSY_TIMEOUT_MS"] = _orig_to
        fb_txt = buf9.fallback_path.read_text(encoding="utf-8") if buf9.fallback_path.exists() else ""
        check(raised41 is None and r41.get("action") == "captured" and r41.get("stored") is False
              and "fallback_jsonl" in (r41.get("store_note") or "")
              and "D안으로 결정한다" in fb_txt,
              "T41 외부 EXCLUSIVE 잠금 → feed raise 0 · 원문 fallback jsonl 보존")

        # T42 유실 표면화(NEW2.11) — preview 상단에 '복구 대기 n건' 상시 노출
        pv42 = buf9.render_preview()
        check(pv42.get("fallback_pending") == 1 and "복구" not in pv42["note"]
              and "대기 1건" in pv42["note"],
              "T42 fallback 대기건수 preview 노출(무음 유실 금지)")

        # T43 잠금 해제 후 정상 복귀(일시 실패가 영구 고장이 아님)
        r43 = buf9.feed("E안으로 결정한다", repo_cwd, session_id="S-LOCK")
        check(r43["stored"] and not r43.get("store_note")
              and any(it["text"] == "E안으로 결정한다" for it in buf9.render_preview()["items"]),
              "T43 잠금 해제 후 정상 저장 복귀")

        gate = "GO" if ok else "NO-GO"
        print(f"\nGATE={gate}")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
