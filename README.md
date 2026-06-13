# BingguPack

**Local-first AGI memory layer for human-confirmed AI context capture.**

AI와의 대화에서 건질 판단·상태·개념을 후보로 **자동 수집**하고, 사람이 직접 confirm 문구를 타이핑해야만 저장되는 **로컬 우선(local-first)** 지식장부입니다. 자동 수집은 켜지지만, **자동 저장은 없습니다.**

- **Latest: v1.4.4** — <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.4.4>
- **Python 3.10+ · 외부 런타임 의존성 0 · Windows/macOS/Linux**

---

## 핵심 개념 (4)

1. **자동 후보 수집 (AGI memory)** — `binggu init --agi-memory`가 만든 profile에서 **사장님 작업 전역**으로 candidate capture(시크릿/PII 발화는 자동 제외). 현재 workspace만 원하면 `binggu init`(privacy 모드). clone 직후엔 동작하지 않습니다.
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
python binggu.py --selftest                           # 21/21 (장부 + capture 통합)
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

---

## 범위 밖 / Out of scope

자동 확정(confirmed) · 자동 업로드 · 팀/공유/마켓플레이스/과금 · OpenCrab 실 전송. (전부 별도 결정/미구현)

---

## License

**MIT License** — [LICENSE](LICENSE). Copyright (c) 2026 BingguPack contributors.
