<div align="center">

<img src="assets/logo.svg" width="110" alt="빙구팩 로고">

# BingguPack

**기억을 얼려, 신선하게 —
AI와 일하면서 쌓이는 내 판단·실수·취향을 내 PC 안의 기억 장부로**

[![PyPI](https://img.shields.io/pypi/v/binggupack?color=3775A9&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/binggupack/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://pypi.org/project/binggupack/)
[![CI](https://github.com/darkjokee-arch/binggupack/actions/workflows/ci.yml/badge.svg)](https://github.com/darkjokee-arch/binggupack/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/darkjokee-arch/binggupack?label=Release&color=success)](https://github.com/darkjokee-arch/binggupack/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**자동 저장 없음 · 내가 승인한 것만 · 로컬 우선**

[⚡ 시작하기](#7--시작하기) · [✨ 무엇을 해주나](#3--무엇을-해주나) · [🛡 왜 믿을 만한가](#4--왜-믿을-만한가) · [🗂 문서](#8--더-깊게-보기)

</div>

---

## 1 · 왜 만들었나

AI와 오래 일하다 보면 이런 일이 반복됩니다.

- 매번 "나는 짧게 답하는 걸 좋아한다"고 다시 설명합니다.
- 예전에 터졌던 실수를 다른 작업에서 또 조심해야 합니다.
- AI가 맞았는지, 내 직감이 맞았는지 시간이 지나면 흐려집니다.
- 자동 저장은 편하지만, 내 대화와 민감정보가 마음대로 쌓이는 건 싫습니다.

**BingguPack은 모든 대화를 긁어모으는 도구가 아닙니다.**
다음에도 써먹을 판단·교훈·선호·규칙만 후보로 보여주고, **내가 직접 고른 것만** 로컬 장부(`ledger.sqlite`)에 남깁니다.

## 2 · 해법 한 장

<p align="center"><img src="assets/flow.svg" width="880" alt="대화 → 미리보기 → SAVE 승인 → 내 PC 기억 장부 → 자동 회상 · 지식 그래프 → 전문가 팩"></p>

1. **골라서 보여주기** — 대화 중 "기억할 만한" 말만 추려서 보여줘요 *(아직 저장 안 함)*
2. **내가 승인** — 내가 고른 것만 내 PC에 저장돼요 *(자동 저장 절대 없음)*
3. **다시 꺼내주기** — 다음에 일할 때 관련 기억과 지난 실수를 먼저 보여줘요

<p align="center"><img src="assets/demo.gif" width="760" alt="binggu preview → SAVE 승인 → recall 데모"></p>

## 3 · 무엇을 해주나

| 기능 | 무엇을 해주나 |
|---|---|
| 🧠 **기억 장부** | 내가 승인한 판단·교훈만 내 PC의 파일 하나에 차곡차곡 쌓여요 |
| 🔎 **자동 회상** | 새 작업을 시작하면 관련 기억과 지난 실수를 알아서 먼저 보여줘요 |
| 🗣️ **내 말 / AI 말 구분** | 누가 한 말인지 나눠서 저장해요 — 나중에 "누구 판단이었지?"가 안 헷갈려요 |
| 📊 **직감 점수** | 내 감이 맞았는지 AI가 맞았는지 공정하게 숫자로 세어줘요 |
| 🕸️ **지식 그래프** | 기억들을 근거로 연결해 지도처럼 묶어요 — 없는 관계는 지어내지 않아요 |
| 📦 **전문가 팩 자동 생성** | 그 지도가 오픈크랩(익스퍼트플랜)에 전문가 지식 팩으로 자동으로 올라가요 |
| 🌾 **지식 수확** | 주제만 말하면 웹·문서를 대신 뒤져 기억 후보를 모아와요 *(바로 저장은 안 함)* |
| 🧹 **기억 정리** | 틀려진 기억은 "이제 틀렸어" 한 마디로 교체·폐기돼요 |
| ⏰ **리마인더** | "나중에 다시 보자" 하면 때가 됐을 때 알려줘요 |
| 💬 **어디서나 사용** | Claude Code는 물론 ChatGPT 채팅·웹/앱 커넥터에서도 같은 기억을 써요 |
| 🚀 **원클릭 온보딩** | `binggu onboard` 한 번이면 저장 채널·자동 동기화·개인 팩까지 셋업 끝 |
| 💾 **백업·복원** | 장부를 통째로 백업/내보내기하고, 실수했으면 언제든 되돌려요 |

## 4 · 왜 믿을 만한가

| 안전장치 | 뜻 |
|---|---|
| 🔒 **민감정보 차단** | 비밀번호나 이혼 같은 개인사는 애매하면 **무조건** 안 내보내요 |
| 🛡️ **변조 감지** | 누가 내 기억 장부를 몰래 고치거나 지우면 바로 들통나요 |
| ✋ **자동 저장 없음** | 아무리 자동화돼도 저장의 마지막 단계는 항상 **내 승인**이에요 |
| ✅ **거짓말 안 함** | 없는 근거를 지어내지 않아요 — 진짜 있는 것만 연결해요 |
| 🔁 **항상 일관** | 같은 상황이면 늘 똑같이 동작해요 *(들쭉날쭉 없음)* |

그리고 세 가지 원칙 위에 서 있습니다 — **내 PC가 정본**(클라우드는 잠깐 거쳐 가는 보조 통로), **기억도 고칠 수 있음**(교체·폐기·재검토), **범위는 좁게**(문서 검색 RAG도, 팀 위키도, 전체 대화 수집기도 아닙니다).

## 5 · 이렇게 씁니다 *(명령어 몰라도 됩니다)*

1. **설치** *(처음 한 번만)*
2. **자유롭게 사용** — 평소처럼 AI와 일해요. 빙구팩이 기억할 만한 걸 알아서 지켜봐요.
3. **"빙구팩 저장해"** — 이 한 마디면 오늘 나온 판단·교훈이 저장돼요. 끝.

<details>
<summary><b>안에서 실제로 일어나는 일</b> — 5단계 처리 · 무엇이 후보가 되나 (펼치기)</summary>

저장할 때 안에서 5단계로 처리합니다: `1 분류 → 2 근거 → 3 지도(그래프) → 4 검증 → 5 내 승인`.
아무리 깊이 처리해도 **마지막은 항상 내 승인**이라 자동 저장은 없습니다.

1. **무엇이 잡히나** — 판단·교훈·선호·규칙·위험 감지 같은 "생각의 단위"만 후보로 골라요. 단순 조회·일회성 지시·잡담은 안 잡습니다. *(정규식 게이트가 판정하고, AI는 보조 역할만 — 게이트를 못 뒤집어요.)*
2. **5종으로 분류** — 문서 · 증거 · 개념 · 상태 · 판단으로 나눠요. *(애매하면 "판단"으로 — 오분류보다 안전.)*
3. **근거로 연결** — 연결마다 근거(evidence)가 반드시 있어야 해요. 근거 없는 연결은 아예 안 만들어요.
4. **내 말 / AI 말 구분** — 화자(나 / AI)를 나눠서 기록해요.
5. **승인 → 로컬 저장** — 내가 승인한 것만 내 PC 장부에 저장돼요.

| 후보가 됨 | 후보가 안 됨 |
|---|---|
| 판단: "이건 B안으로 간다" | 조회: "상태 보여줘" |
| 교훈: "다음부터 백업 먼저" | 일회성 지시: "지금 이거 고쳐" |
| 선호: "나는 짧은 답을 선호한다" | 운영 보고: "커밋 완료" |
| 규칙: "배포는 두 번 확인" | 잡담: "오늘 수고했어" |
| 위험 감지: "이 방식은 터질 수 있다" | 순수 지식: "토큰 버킷은 ..." |

`remember`·`pair`처럼 직접 "기억하라"고 입력한 경로는 조금 더 넓게 받되, 안전 게이트는 그대로 유지됩니다.
</details>

### 나를 이해하는 팩 — 빙구팩의 목표

저장한 판단·교훈이 쌓이면 **"나를 이해하는 팩"**(내 사고방식·가치관 지도)으로 오픈크랩에 자동으로 올라가요. 빙구팩이 궁극적으로 지향하는 **"나만의 개인 AI"**가 이렇게 만들어져요.

- **스키마 팩으로 올라가요** *(CrabAgent 경로 · 개념/주장/증거 계층 — 서버가 얕게 재추출하는 옛 방식 아님)*
- 저장이 생기면 **5분 안에 자동 반영** *(변화 없으면 통신 0 · 서버 일시 장애는 자동 재시도)*
- 민감정보는 여기서도 **무조건 제외** *(T3 하드 차단 + PII/경로 자동 세척)*
- 내 문서를 더 넣고 싶으면 `~/.binggupack/person_pack_sources/` 폴더에 파일을 두면 같이 병합돼요
- 쌓일수록 AI가 나를 더 잘 이해해요 — 판단 습관·선호·기준까지

<details>
<summary><b>자동 동기화 켜기</b> (펼치기)</summary>

`~/.binggupack/person_pack.json` 에 한 줄이면 켜져요 (기본은 꺼짐 — 내 승인 없이 아무것도 안 올라가요):

```json
{ "crab_auto_sync": true }
```

전제: 오픈크랩 **Expert 요금제**(CrabAgent 업로드) + 클라우드 연결 설정(`cloud_ingest.json` — 온보딩이 안내).
수동 1회 실행: `python scripts/binggu_person_crab_sync.py --live --confirm`
</details>

### 수확 — 주제만 말하면 팩이 됩니다

*"바다낚시 정보 수집해서 오픈크랩에 팩으로 올려줘"* — 이 한 마디면 웹·문서를 뒤져 후보를 모으고, 완성된 팩이 자동으로 올라가요.

<details>
<summary><b>깊이(탐색 단계) 정하기</b> (펼치기)</summary>

주제를 몇 단계까지 파고들지 정해요 *(기본 4단계)*.

- **1단계**: 큰 갈래만 *(바다낚시 → 장비 · 장소 · 시기 · 어종)*
- **2단계**: 갈래별 세부까지 *(장비 → 낚싯대 · 릴 · 미끼)*
- **3단계**: 더 깊은 세부까지 *(낚싯대 → 원투대 · 루어대 · 찌낚싯대)*
- **4단계**: 가장 촘촘하게 *(기본값 · 원투대 → 길이 · 강도 · 가격대)*
- 5단계 이상도 되지만 그만큼 분량이 많아져요 *(전체 수집량으로 폭주는 막아요)*.
</details>

## 6 · 어디서나 씁니다

<p align="center"><img src="assets/channels.svg" width="880" alt="Claude Code·ChatGPT·웹 커넥터 → 내 PC 장부 → 오픈크랩 전문가 팩"></p>

- 🌐 **웹/앱 커넥터(HTTP 모드)** — 로컬 MCP 서버를 HTTP 모드(`--http`)로 열고 Cloudflare Tunnel 뒤에 두면, Claude 웹/앱 커넥터에서도 같은 **30도구**를 그대로 씁니다. 접근은 경로 토큰으로 보호돼요. *(quick tunnel은 재시작마다 주소가 바뀌므로, 고정 주소가 필요하면 named tunnel — [INSTALL](INSTALL.md#webapp-connector--http-모드-optional) 참조.)*
- 💬 **ChatGPT 저장 채널** — 채팅 중 `SAVE n`으로 승인한 것만 클라우드 inbox에 잠깐 담기고, 내 PC가 서명키로 가져와(pull) 로컬 장부에 반영해요. 여기서도 자동 저장은 없어요.
- ☁️ **클라우드 읽기 도구** — `cloud_recall`/`cloud_packs`로 오픈크랩의 지식·팩을 조회만 해요 *(읽기 전용 · 민감정보 마스킹)*.

## 7 · 시작하기

```bash
pip install binggupack
binggu start            # 내 PC에 기억 장부 만들기 — 끝
```

- 로컬 CLI + **stdio MCP(Claude Code·Codex)**는 `pip install`만으로 충분합니다([INSTALL](INSTALL.md#install-claude-code-mcp-sandbox-entry)에 등록 방법).
- **ChatGPT/웹 커넥터 저장 채널**(`binggu onboard`)은 hosted worker 소스가 필요해 **`git clone` 후** 실행하세요(pip 배포판엔 `hosted/`가 포함되지 않음 · [INSTALL](INSTALL.md#chatgpt-저장-채널-optional--hosted) 참조).

| 다음 단계 | 문서 |
|---|---|
| 5분 따라하기 | [START_HERE](docs/START_HERE.md) |
| 10분 튜토리얼 | [BINGGUPACK_TUTORIAL](docs/BINGGUPACK_TUTORIAL.md) |
| 설치·연결(Claude Code MCP·커넥터) | [INSTALL](INSTALL.md) |
| 캡처 hook 셋업 | [BINGGUPACK_CAPTURE_HOOK_SETUP](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) |
| 버전별 변경 내역 | [CHANGELOG](CHANGELOG.md) |

## 8 · 더 깊게 보기

| 주제 | 문서 |
|---|---|
| 내 말 / AI 말 따로 저장 | [BINGGUPACK_SPEAKER_AXIS_DESIGN](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) |
| 사람 승인과 안전 경계 | [BINGGUPACK_GOVERNANCE_DESIGN](docs/BINGGUPACK_GOVERNANCE_DESIGN.md) |
| hosted inbox 경계 | [BINGGUPACK_HOSTED_BOUNDARY](docs/BINGGUPACK_HOSTED_BOUNDARY.md) |
| 팩 형식 | [BINGGUPACK_PACK_CONTRACT](docs/BINGGUPACK_PACK_CONTRACT.md) |
| 코어 구조(개발자) | [BINGGUPACK_ARCHITECTURE](docs/BINGGUPACK_ARCHITECTURE.md) |
| 전체 문서 색인 | [docs/INDEX.md](docs/INDEX.md) |

<details>
<summary><b>🛠 개발자 부록</b> — 직접 명령어 · 기능 전체 지도 · 점검 (펼치기)</summary>

### 직접 명령어로 쓰기

PyPI 설치(위 시작하기) 없이 clone해서 바로 쓸 수도 있습니다. clone한 폴더에서는 `python binggu.py`, 설치했다면 `binggu` 또는 `python -m binggupack`으로 실행합니다.

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py start
python binggu.py remember "배포 전에 live endpoint를 먼저 확인한다"   # 미리보기(저장 안 됨)
python binggu.py save "..." --preview-id <화면에 나온 id> --pick 1 --explicit --confirm "SAVE 1"
python binggu.py ask "배포 전에 조심할 것?"
python binggu.py doctor
```

### 기능 전체 지도

- **미리보기·저장(preview → SAVE)**: 기억할 만한 판단·교훈만 후보로 보여주고, 내가 고른 것만 로컬 장부에 저장합니다.
- **회상(recall/ask)**: 작업 시작 전에 관련 기억과 과거 실수 패턴을 다시 꺼내줍니다.
- **근거 추적(trace)**: 그 회상이 실제로 도움이 됐는지 기록해 회상 품질을 다듬습니다.
- **화자 분리(pair)**: 내 말과 AI 말을 나눠 저장하고, 내 직감 적중률을 셉니다.
- **기억 정리(deprecate/replace)**: 오래된 판단을 교체·폐기하고, 리마인더를 겁니다.
- **수확(harvest)**: 외부 웹·문서에서 저장 후보 소스를 긁어옵니다(후보만).
- **깊이 탐색(explore `--depth`)**: 주제에서 하위 개념을 재귀(BFS)로 파고들어 소스·후보를 넓게 찾습니다(폭 `--breadth`·관련도 `--rel-min`).
- **그래프·팩(graph/pack)**: 기억을 근거로 연결해 그래프·팩으로 묶어 내보냅니다(근거 2층·3층·5층).
- **클라우드 팩**: 묶은 그래프·팩을 오픈크랩(ExpertPlan)에 전문가 지식 팩으로 자동 생성합니다.
- **Claude Code 연결(hook·MCP)**: 캡처/회상 hook과 MCP 30도구(stdio + HTTP 모드)로 붙습니다.
- **폰·웹·ChatGPT 동기화(hosted)**: 다른 기기에서 승인해 받아둔 메모를 로컬 장부로 가져옵니다.
- **안전장치(governance/selftest)**: PII·secret 차단, 사람 승인 경계, 자체 검증.

| 영역 | 들어 있는 기능 |
|---|---|
| **로컬 기억 장부** | `init/start`, `doctor/status`, `list`, 로컬 `ledger.sqlite`, snapshot/audit log |
| **후보 선별·저장** | `preview/remember`, `reflect`, `save`, 사람 `SAVE n` 승인, PII/secret 차단 |
| **작업 전 회상** | `ask/recall/why`, `preflight`, `trace`, 회상 근거·효용 기록 |
| **기억 정리** | `deprecate`, `replace`, `accept/unaccept`, `due`, `reminders`, `resolve`, `route` |
| **내 말 vs AI 말** | `pair`, `trust`, owner/ai 화자 분리, 수용·반박·수정 관계, 직감 적중률 |
| **Claude Code·커넥터 연결** | `capture` hook, `preflight` hook, save-gate hook, stdio MCP 서버(Claude Code·Codex), HTTP 모드(`--http`), MCP 30도구(read 20 · dry-run 2 · confirm 게이트 쓰기 8) |
| **폰·웹·클라우드 보조** | `hosted inbox/pull`, `setup-cloud`, ChatGPT 저장 채널(inbox→서명키 pull), `cloud_recall`/`cloud_packs`, TTL/HMAC/purge 경계 |
| **외부 소스·그래프·팩** | `harvest`, `confirm-edges`, graph schema, pack contract, local ingest, watcher 계열 |
| **안전·개발자 도구** | path safety, match policy, classifier, governance, selftest, publish/export |

```text
CLI 전체: init/start · status/doctor · preview/remember · reflect · save · list
          recall/ask/why · trace · preflight · deprecate/replace/accept/unaccept
          due/reminders/resolve · pair/trust/route · capture · hosted · harvest
          confirm-edges · setup-cloud · onboard · backup/export/restore
```

### 짧은 점검

```bash
python scripts/openbinggu_doctor.py --selftest
python scripts/smoke_test.py --home ./_binggu_smoke_home
python binggu.py --selftest
```

스크립트 파일명(`scripts/openbinggu_*.py`)에 남은 `OpenBinggu`는 예전 내부 코드네임입니다. 현재 공개 제품명은 BingguPack이고, 코어 로직은 `binggupack/` 패키지가 정본입니다(strangler-fig · `scripts/`는 하위호환 shim). 코어 지도는 [BINGGUPACK_ARCHITECTURE](docs/BINGGUPACK_ARCHITECTURE.md)를 보세요.

</details>

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
