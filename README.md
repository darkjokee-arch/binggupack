# BingguPack

**Local-first AGI memory layer for human-confirmed AI context capture.**

AI와의 대화에서 건질 판단·상태·개념을 후보로 **자동 수집**하고, 사람이 직접 confirm 문구를 타이핑해야만 저장되는 **로컬 우선(local-first)** 지식장부입니다. 자동 수집은 켜지지만, **자동 저장은 없습니다.**

- **Latest: v1.4.6** — <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.4.6>
- **Python 3.10+ · 외부 런타임 의존성 0 · Windows/macOS/Linux**

---

## 왜? / Why

AI와 대화하다 보면 정작 남기고 싶은 것들 — **내 판단, 배운 점, 정한 방침** — 이 수십 개의 대화창에 흩어져 사라집니다. 검색도 안 되고, 다음 세션의 AI는 그걸 모릅니다.

그렇다고 전부 자동 저장하면 잡음과 민감정보가 쌓이고, 내가 통제권을 잃습니다.

**BingguPack의 답: 넓게 줍고, 좁게 저장한다 (collect broad, commit narrow).**

- **넓게** — 어느 도구(Claude·ChatGPT·폰·웹)에서 일하든 건질 문장을 *후보*로 자동 수집.
- **좁게** — 실제 저장은 사람이 `SAVE n` 을 직접 타이핑한 것만. **자동 저장은 없습니다.**
- **로컬** — 모든 것은 내 PC의 단일 `ledger.sqlite`. 외부 서버·업로드 없음.

결과: AI 대화에서 진짜 알맹이만, 내 손으로 고른 것만, 검색 가능한 개인 기억장부로 쌓입니다.

---

## 구조 / Architecture

```
  수집(넓게)                          확정(좁게, 사람만)
┌─────────────────────┐          ┌──────────────────────────────┐
│  Claude · ChatGPT    │          │                              │
│  폰 · 웹 (MCP 커넥터)  │          │   ┌─ preview (저장 0)         │
└──────────┬──────────┘          │   │   후보 미리보기            │
           │ 자동 후보 수집        │   ▼                          │
           ▼                      │  [ SAVE n 게이트 ]  ◀── 사람이 │
┌─────────────────────┐          │   │  confirm 문구 직접 타이핑   │
│  capture hook        │          │   ▼                          │
│  (global/privacy)    │──────────┼─▶ ledger.sqlite (로컬 장부)   │
└─────────────────────┘          │      candidate-only · 발췌만   │
           │                      └──────────────────────────────┘
  폰/웹 경로 │ save_intent                    ▲
           ▼                                 │ hosted pull --select
┌─────────────────────┐    hosted inbox      │ (고른 번호만 · 사람 confirm)
│ hosted worker inbox  │──(1회 회수·저장0)──▶ │
│ (평소 잠김·non-reten) │    local staging ────┘
└─────────────────────┘

  ✗ 자동 저장 없음   ✗ 상주 데몬 없음   ✗ 자동 pull 없음
  ─────────────────────────────────────────────────────────
  OpenCrab 업로드 · export · marketplace · 팀/공유  →  [HOLD · 미구현]
```

- **두 경로 모두 끝은 `SAVE n` 게이트** — 사람이 confirm 문구를 타이핑하기 전엔 ledger에 아무것도 안 들어갑니다.
- 폰/웹은 worker inbox에 *예약*만(휘발). PC가 `hosted inbox`로 1회 회수→로컬 staging 보존(저장 0), `hosted pull --select`로 고른 것만 확정.
- OpenCrab 전송·export·marketplace는 전부 **HOLD**(별도 결정 전 미구현).

---

## 핵심 개념 (4)

1. **자동 후보 수집 (AGI memory)** — `binggu init --agi-memory`가 만든 profile에서 **당신의 작업 전역**으로 candidate capture(시크릿/PII 발화는 자동 제외). 현재 workspace만 원하면 `binggu init`(privacy 모드). clone 직후엔 동작하지 않습니다.
2. **미리보기** — "빙구팩 저장해" 또는 `binggu capture preview`로 수집된 후보를 확인합니다.
3. **사람 승인 저장** — `SAVE n` confirm 없이는 저장 0. 의도·자동·번호 없는 저장은 전부 BLOCK.
4. **증거 기반 그래프** — 5종 노드 + 동사형(typed verb) 엣지 + 원문 증빙(evidence).

---

## 빠른 시작 / Quick start

```bash
git clone https://github.com/darkjokee-arch/binggupack
cd binggupack
python scripts/openbinggu_doctor.py --selftest      # 15/15 GATE=GO

python binggu.py init --agi-memory                  # 장부 + capture profile (전역 후보수집 = AGI memory, 기본 ON)
python binggu.py capture status                     # ON/OFF · scope · 버퍼 건수 · hook 등록
```

capture 제어:

```bash
python binggu.py capture pause       # 일시중지
python binggu.py capture resume      # 재개
python binggu.py capture preview     # 수집된 후보 목록 + 저장 명령 안내 (저장 0)
python binggu.py capture uninstall   # 완전 제거(rollback) — 장부 ledger.sqlite 는 보존
```

> **AGI memory** = `init --agi-memory`(또는 `--global`)로 **작업 전역** 후보수집이 기본 경험. 현재 workspace만 원하면 플래그 없이 `init`(privacy 모드). 저장은 어느 쪽이든 `SAVE n` 게이트만(자동 저장 0·시크릿/PII 자동 제외).

---

## 예시 출력 / Example output

실제 터미널 출력입니다(이미지 아님). 전부 **저장 0**인 read-only/안내 출력.

**후보 미리보기** — `conversation_capture_preview` 또는 `binggu preview "<텍스트>"`:

```text
# 캡처 미리보기 — 후보 2건 (전부 candidate, 미저장)

| # | 도장 | 문장                        | 분류근거          | 헌법판정 |
|---|------|----------------------------|------------------|---------|
| 1 | 판단 | 이 입찰은 마진이 낮아 보류한다. | judgment_verdict | PASS    |
| 2 | 상태 | 백필 작업이 진행 중이다.       | state_now        | PASS    |

미리보기일 뿐 아무것도 저장되지 않았습니다(nothing_saved=true). 등재는 로컬 승인 게이트에서만.
preview_id: ec2bf9ac
저장은 번호를 직접 골라서:  python binggu.py save "<같은 텍스트>" --preview-id ec2bf9ac --pick 1 --confirm "SAVE 1"
```

**hosted inbox** — 폰/웹이 보낸 후보를 PC로 1회 회수 후 read-only 요약(저장 0):

```text
hosted inbox: 대기 intent 2건 (read-only · 저장 0)
  [1] 이 기능은 위험이 커서 기본 비활성으로 잠가 둔다. | sha 2268387d | 0.0d전 | 후보 1
  [2] 마감 직전에 화면이 바뀌면 자동화가 깨지니 미리 점검한다. | sha 2f7bd1d0 | 0.0d전 | 후보 1

저장은 고른 번호만:  python binggu.py hosted pull --select <번호들> --confirm "LIVE SAVE <번호들>"
(전량 자동 적용 없음 · 고른 항목만 사람 confirm 게이트로 ledger 에 들어갑니다.)
```

**hosted pull** — confirm 없이 실행하면 안내만(저장 0):

```text
hosted pull = inbox 에서 본 번호만 골라 ledger 에 저장합니다(전량 자동 적용 없음).
  먼저:  python binggu.py hosted inbox            (대기 intent 번호 확인)
  저장:  python binggu.py hosted pull --select 1,3 --confirm "LIVE SAVE 1,3"
```

**list** — 저장된 후보 조회(read-only):

```text
조회 전용입니다 — 아무것도 변경되지 않았습니다. 표시는 저장된 발췌(≤80자)의 60자 cap, 원문 전문은 저장되어 있지 않습니다.
변경 작업의 confirm 문구에는 # 와 id 를 함께 적습니다 (예: DEPRECATE 3 a1b2c3d4).
```

---

## 안전 원칙 / Safety

- **clone 직후에는 아무것도 수집하지 않습니다** — `binggu init`을 실행한 사람의 profile 안에서만 동작.
- **AGI memory = 작업 전역 후보수집이 기본 경험**(`init --agi-memory`/`--global`). 현재 workspace로 좁히려면 플래그 없이 `init`(privacy 모드). 어느 scope든 **시크릿/PII 발화는 자동 후보 제외** + 시크릿 디렉토리는 deny.
- **자동 저장 없음** — 캡처 범위가 넓어도 저장은 preview → `SAVE n` confirm만. `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK.
- **ledger/active/confirmed/OpenCrab 자동 write 0.**
- **원문 전문 저장 없음** — 80자 이내 발췌만.
- pause / resume / uninstall 로 언제든 중단·완전 원복. 모든 변경 전 스냅샷 + checksum rollback + append-only audit chain.

이 불변식들은 약속이 아니라 selftest로 증명됩니다(아래 [검증](#검증--verify)).

---

## 명령어 치트시트 / Cheatsheet

```bash
# capture (AGI memory)
binggu.py init [--agi-memory] [--global] [--no-capture]
binggu.py capture status | pause | resume | preview | uninstall

# 저장 흐름 (사람 승인 게이트)
binggu.py preview "<대화/메모 텍스트>"                         # 후보 + preview_id (저장 0)
binggu.py save "<텍스트>" --preview-id <id> --pick 1,2 --confirm "SAVE 1,2"

# hosted (collect broad, commit narrow — 폰/웹이 모으고 PC가 검토·확정)
binggu.py hosted inbox [--since 7d] [--wait 60]               # 회수(저장 0) + 대기 intent read-only 요약
binggu.py hosted pull --select 1,3 --confirm "LIVE SAVE 1,3"  # inbox 에서 본 번호만 ledger 저장
#   inbox: worker 1회 회수 → 로컬 staging 보존(저장 0) → 80자 발췌·sha8·count·PII/secret flag 요약
#   pull : 고른 번호만 사람 confirm 게이트로 commit · 나머지는 staging 잔류(전량 자동 적용 없음)
#   경로: --workers-port <p> 또는 BINGGU_WORKERS_PORT · staging 만 보기: hosted inbox --no-fetch

# 후보 관리 (목록의 # 와 id8 을 함께 적어야 통과 — 목록 바뀌면 자동 차단)
binggu.py list [--status pending|deprecated|resolved] [--kind 판단|상태|개념|문서|증거]
binggu.py deprecate <n> <id8> --reason "..." --confirm "DEPRECATE <n> <id8>"
binggu.py replace  <n> <id8> --with "<수정문장>" --reason "..." --confirm "REPLACE <n> <id8> WITH <수정문장>"
binggu.py accept   <n> <id8> --reason "..." --confirm "ACCEPT <n> <id8>"
binggu.py unaccept <n> <id8> --reason "..." --confirm "UNACCEPT <n> <id8>"
binggu.py due      <n> <id8> --date 2026-07-01
binggu.py resolve  <n> <id8> --outcome 성공|실패|불확실|판정불가 --reason "..."
binggu.py reminders
```

> PowerShell에서는 `--pick "1,2"` 처럼 쉼표가 든 인자를 반드시 따옴표로 감싸고, save 는 한 줄로 실행하세요.

설계 원칙: **기각=삭제 아님**(보존+조회 제외) · **수정=덮어쓰기 아님**(원본 기각 + 신규 저장) · **수용(owner_accepted)=확정 아님**(append 이벤트, 노드 불변, `confirmed` 부재) · **검증 결과=기록일 뿐**(실패여도 자동 강등 0).

---

## MCP / hosted (선택)

- hosted **조회(read-only)** 와 **save-intent(폰→PC 저장 요청)** 는 각자 **자기 워커를 배포**하는 별도 구성입니다(`hosted/`). 공용 서버는 없습니다.
- **저장 흐름**: 폰/커넥터에서 미리보기 → `SAVE n` 발화 → save_intent가 worker inbox에 휘발 적재 → **PC 러너가 HMAC pull → 로컬 게이트 → candidate 저장**. worker는 통로일 뿐 장부 write 0, 최종 권한은 PC 러너의 사람 confirm 게이트.
- save-intent **inbox 는 평소 잠김(fail-closed)**, `SAVE n` 이 사람 승인 신호입니다(자동 저장 아님·candidate-only).
- **수집·확정 원칙 (collect broad, commit narrow):**
  - **mobile/web collects** — 폰/웹은 넓게 모으기만(candidate). 어디서든 SAVE n 으로 inbox 에 적재.
  - **PC review/confirm commits** — 실제 ledger 저장은 PC 에서 사람이 `hosted inbox` 로 보고 `hosted pull --select` 로 고른 것만.
  - **no daemon, no autopull, no autosave** — 상주 데몬 0 · 주기적 자동 pull 0 · 백그라운드 자동 write 0. 두 명령 모두 사람이 직접 실행해야만 동작한다.
  - worker 는 non-retention(pull=drain) 이라 `inbox` 가 1회 회수해 **로컬 staging 으로 보존(저장 0)** 하고, 번호는 `--since` 필터와 무관하게 **전체 기준 고정**(본 번호 == pull 번호).
- MCP 연결 예시는 `mcp.example.json`, hosted 배포는 [hosted/workers/README.md](hosted/workers/README.md), 라이브 E2E 결과는 [docs/BINGGUPACK_SAVE_INTENT_LIVE_E2E_RESULT.md](docs/BINGGUPACK_SAVE_INTENT_LIVE_E2E_RESULT.md).

---

## 증거 기반 그래프 문법 / Graph grammar

모든 pack은 [docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md)를 따르고 검증기가 fail-closed로 강제합니다.

- **노드 5종**(문서/증거/개념/상태/판단), 전부 핵심 문장형 — 단어 노드 금지
- **엣지는 동사형만**(predicate registry) — "이 증거가 이 판단의 근거" 등
- **전 엣지 원문 증빙 의무** — 출처+행+발췌 해시. 파서/폴더 유래는 증거가 아니라 출처표시(provenance)
- **검증 미달 = 보류(quarantine)** — 사유+복귀 조건과 보존, 매 빌드 재심사. 즉시 차단은 PII/시크릿뿐
- payload는 짧은 라벨만, 전체 문장은 evidence chunk — 절단은 빌드 실패

pack은 언제나 candidate이며 받는 쪽 운영 그래프에 자동 반영되지 않습니다.

---

## 검증 / Verify (실측 기대값)

```bash
python scripts/openbinggu_doctor.py --selftest        # 15/15
python binggu.py --selftest                           # 26/26 (장부 + capture + hosted 통합)
python scripts/binggu_capture_persist.py              # 16/16 (영속 candidate 버퍼)
python scripts/binggu_capture_profile.py              # 9/9  (profile · settings hook · pause/resume/uninstall)
python hooks/binggu_capture_hook.py --selftest        # 8/8  (UserPromptSubmit/Stop)
python scripts/openbinggu_public_tree_scan.py --tree .   # CLEAN
```

각 selftest는 마지막에 `GATE: GO` + exit code 0 이면 정상입니다. 더 많은 검증·따라하기는 [INSTALL.md](INSTALL.md), [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md).

---

## 문서 / Docs

- [INSTALL.md](INSTALL.md) — 설치·검증·capture 활성화
- [docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) — AGI memory capture 설치/scope/롤백
- [docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md) — 그래프 문법
- [hosted/workers/README.md](hosted/workers/README.md) — hosted 조회·save-intent 배포
- [docs/BINGGUPACK_SAVE_INTENT_LIVE_E2E_RESULT.md](docs/BINGGUPACK_SAVE_INTENT_LIVE_E2E_RESULT.md) — save-intent 라이브 E2E(폰→러너→candidate) 결과·신형 v2 서명
- [docs/BINGGUPACK_SEMANTIC_CLASSIFIER_DESIGN.md](docs/BINGGUPACK_SEMANTIC_CLASSIFIER_DESIGN.md) — semantic capture classifier **설계(HOLD·미구현)**. 기존 정규식 위에 얹는 보조 subtype 층으로만 설계됨(canonical 5종 스키마 불변)

---

## 프로젝트 구조 / Project structure

```
binggupack/
├── binggu.py            # 사용자 진입점 CLI (init·capture·preview·save·list·hosted·…)
├── scripts/             # 검증·게이트·러너 모듈 (doctor·save_selected·hosted inbox·outbox runner 등)
├── hooks/               # capture hook (UserPromptSubmit/Stop) — settings.json 에 등록
├── hosted/workers/      # 폰/웹 save-intent + read-only 조회 Cloudflare Worker (각자 배포·공용 서버 없음)
├── docs/                # 설계·문법·튜토리얼·E2E 결과 문서
├── tests/fixtures/      # selftest·eval 용 synthetic fixture (실 데이터 없음)
├── examples/            # 예시 pack·입력
├── mcp.example.json     # MCP 연결 예시(Claude/ChatGPT 커넥터)
├── INSTALL.md · README.md · LICENSE
```

각 모듈은 자체 `--selftest`를 갖고, `scripts/openbinggu_doctor.py`가 전체를 묶어 `GATE=GO`로 검증합니다.

---

## 보안 & 비목표 / Security & Non-goals

**보안 (전부 selftest로 증명):**

- **자동 저장 없음** — 저장은 preview → `SAVE n` 사람 confirm만.
- **SAVE n human gate** — `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK.
- **candidate-only** — 모든 노드는 후보 상태(`promotion_allowed=0`), 자동 확정(`confirmed`) 전이 0.
- **secret/PII hard block** — 시크릿/PII 발화는 정규식이 후보 단계에서 무조건 제외(semantic이 못 뒤집음).
- **inbox disabled by default** — 폰/웹 save-intent inbox는 평소 잠김(fail-closed), `SAVE n`이 사람 승인 신호.
- **신형 서명 전용** — `SAVE_SIG_V2_ONLY=1`, 구형 HMAC 차단.
- **원문 전문 저장 없음** — 80자 이내 발췌만. shadow/로그에도 원문 미보관.

**비목표 (HOLD · 미구현 — 별도 결정 전 동작 안 함):**

- OpenCrab 실 전송 · export · marketplace · 팀/공유/과금
- 자동 확정(confirmed) · 자동 업로드 · 상주 데몬 · 주기적 자동 pull
- **semantic capture classifier** — 설계 단계(HOLD). 동작하지 않으며, 구현 시에도 기존 정규식/그래프 스키마는 불변.

---

## License

**MIT License** — [LICENSE](LICENSE). Copyright (c) 2026 BingguPack contributors.
