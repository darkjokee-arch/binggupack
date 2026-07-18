<div align="center">

<img src="assets/logo.svg" width="110" alt="빙구팩 로고">

# BingguPack

### Git for AI memory

**AI는 기억을 *제안*하고, 무엇을 활성 기억으로 *확정*할지는 내가 정합니다.**
모든 기억은 먼저 검토 요청이 되고, **내가 승인한 것만** 활성 기억이 됩니다.

> *"Git for AI memory" 는 **Git 같은 검토·커밋 워크플로**를 뜻하는 비유입니다 — 실제 Git 저장소나 Pull Request 프로토콜을 구현한 것은 아닙니다.*

[![PyPI](https://img.shields.io/pypi/v/binggupack?color=3775A9&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/binggupack/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://pypi.org/project/binggupack/)
[![CI](https://github.com/darkjokee-arch/binggupack/actions/workflows/ci.yml/badge.svg)](https://github.com/darkjokee-arch/binggupack/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/darkjokee-arch/binggupack?label=Release&color=success)](https://github.com/darkjokee-arch/binggupack/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Consent-first · Local-first · Auditable · Model-agnostic**
*후보는 자동으로 모여도, 장부에 **확정**되는 건 내가 승인한 것만.*

[⚡ 60초 체험](#-60초-체험) · [🧭 작동 방식](#-작동-방식) · [🛡 안전 모델](#4--왜-믿을-만한가) · [🇬🇧 English](README.en.md) · [🗂 문서](#8--더-깊게-보기)

</div>

---

## ⚡ 60초 체험

```bash
pip install binggupack
binggu demo            # 설치만으로 · 네트워크 0 · 격리 임시 장부(내 장부 안 건드림)
```

`binggu demo` 한 번이면 빙구팩의 핵심을 눈으로 봅니다 — 대화에서 **기억 후보 발견 → 내가 승인 → 승인한 것만 로컬 장부에 확정 → 새 프로세스에서 회상 → 무엇에 근거했는지 확인.** 데모는 임시 폴더에서 돌고 끝나면 스스로 정리합니다. 실제 장부는 `binggu init` 으로 시작하세요.

> CI·자동화용 비대화형: `binggu demo --non-interactive` *(승인은 데모 격리 홈에서만 시뮬레이션 — 운영 승인 우회 아님)*

**설치 후 매일** — 명령을 외우지 않아도 됩니다. 인자 없이 `binggu` 만 치면 홈 화면이 뜹니다.

```bash
binggu                 # 홈 — 지금 상태와 다음 할 일을 한 화면에서 (로컬·읽기 전용)
binggu inbox           # 통합 검토함 — 후보·원격 의도·승인 요청·검토 예정을 한 번에 (로컬·읽기 전용)
binggu studio          # 같은 화면을 브라우저 로컬 UI 로 (loopback 전용·읽기 전용 preview)
binggu recall "질문"    # 관련 기억 회상
binggu explain <id>    # 그 기억의 근거·이력
```

`binggu`·`binggu inbox` 는 **읽기 전용**입니다 — 상태만 보여줄 뿐 저장/승인/교체는 기존 명령과 owner 승인 경계를 그대로 씁니다. `binggu inbox` 는 기본적으로 네트워크를 건드리지 않고 로컬 스냅샷만 보여주며, 원격 저장 의도를 새로 가져오려면 `binggu hosted inbox` 를 씁니다.

<details>
<summary><b>🖥 <code>binggu studio</code> — 로컬 브라우저 UI (읽기 전용 preview · 펼치기)</b></summary>

Home + 통합 Inbox 를 브라우저에서 봅니다. Daily Console(`binggu home/inbox --json` schema v1)을 그대로 재사용하는 **읽기 전용 preview** 입니다.

```bash
binggu studio            # loopback 임시 포트에서 실행 + 기본 브라우저 자동 열기
binggu studio --no-open  # 브라우저를 열지 않음(headless/원격)
```

- **local browser UI** — 127.0.0.1 loopback 에만 bind(외부 접속 불가). 실행마다 새 ephemeral session URL(`/s/<token>/`).
- **read-only preview** — GET/HEAD 만. mutation endpoint 0·외부 asset/network 0·CORS/cache 비활성. Ctrl+C 로 종료합니다.
- **Home + unified Inbox** — 활성 기억·자동 수집 후보·원격 저장 의도·승인 요청·검토 예정을 카드/탭으로. 각 항목의 버튼은 CLI 명령을 클립보드에 복사만 합니다.
- **Memories** — 저장된 기억을 브라우저에서 탐색합니다. active/deprecated·종류·subtype 필터, 문장 검색, **읽기 전용 lexical 회상**(의미 검색 설정·캐시를 만들지 않습니다), 기억 상세와 근거 사슬(evidence 발췌·관계·owner 승인 요약)을 봅니다. 카드/상세의 버튼은 `binggu explain <id>`·`binggu recall "<질문>"` 명령을 클립보드에 복사만 하며, **저장·폐기·교체·승인 같은 mutation 은 아직 Studio 에서 실행하지 않습니다**(기존 CLI 와 owner 승인 경계를 사용).
- **Approvals** — 승인 요청의 exact 내용·operation/payload/ledger binding·상태·이력(timeline)·소비 결과(receipt)를 봅니다. effective 상태(pending/approved/consuming/consumed/rejected/revoked/expired)를 기존 verifier·consumption 으로 read-only 해석하고, review 파일은 무결성(operation/payload_digest 일치)을 검증해 표시합니다. **Studio 는 승인을 실행하지 않습니다** — owner 가 별도 로컬 터미널에서 실행할 `binggu approval show/approve/reject/revoke <request-id>` 명령을 복사만 제공하는 **read-only handoff UI** 입니다(approval nonce·private path·provider config 미노출).
- **mutation 은 전부 기존 CLI 경계를 그대로 사용**합니다 — 저장은 preview + 사람의 `SAVE n` 입력, 승인 mutation 은 owner approval 경계. Studio 자체는 저장·승인을 실행하지 않습니다.

</details>

## 1 · 왜 만들었나

AI와 오래 일하다 보면 이런 일이 반복됩니다.

- 매번 "나는 짧게 답하는 걸 좋아한다"고 다시 설명합니다.
- 예전에 터졌던 실수를 다른 작업에서 또 조심해야 합니다.
- AI가 맞았는지, 내 직감이 맞았는지 시간이 지나면 흐려집니다.
- 자동 저장은 편하지만, 내 대화와 민감정보가 마음대로 쌓이는 건 싫습니다.

**BingguPack은 모든 대화를 긁어모으는 도구가 아닙니다.**
다음에도 써먹을 판단·교훈·선호·규칙만 후보로 보여주고, **내가 직접 고른 것만** 로컬 장부(`ledger.sqlite`)에 남깁니다.

## 🧭 작동 방식

**Memory PR** — 빙구팩의 한 문장: **"모든 기억은 먼저 검토 요청(Memory PR)이 되고, 내가 승인한 것만 활성 기억이 된다."**
*"Memory PR"은 이 흐름을 Git 개발자에게 설명하기 위한 **별칭**입니다 — 내부 명령·데이터 모델은 그대로입니다.*

|  | AI가 하는 일 | 내가 하는 일 | 결과 |
|---|---|---|---|
| 🟡 **Memory PR** | 대화에서 기억 후보를 *검토 요청*으로 제안 | — | 아직 활성 기억 아님 |
| ✅ **Human commit** | — | 내가 본 정확한 후보만 `SAVE n` | 로컬 장부에 확정 |
| 🔎 **Recall** | — | 승인한 기억을 새 프로세스·세션에서 회상 | 근거와 함께 다시 |

**Git ↔ BingguPack**

| Git | BingguPack | 명령 |
|---|---|---|
| Pull Request | 기억 후보 (Memory PR) | *(자동)* · `binggu preview "<텍스트>"` |
| Review | 후보 검토 | `binggu inbox` |
| Approve & Merge | 사람이 `SAVE n` → 활성 기억 확정 | 채팅 `SAVE n` · `binggu save` |
| Commit history | 승인·교체·폐기 이력 | `binggu explain <memory-id>` |
| Blame / provenance | 누가 말했고 어떤 근거인지 | `binggu explain <memory-id>` |
| Revert / supersede | 틀려진 기억 교체·폐기 | `binggu replace` · `binggu forget <id>` |

> Git 과 달리 실제 병합(merge)·분기(branch)·diff 프로토콜은 없습니다. 위 대응은 **개념 비유**이고, 확정 게이트는 사람이 입력하는 `SAVE n` 하나입니다.

<p align="center"><img src="assets/flow.svg" width="880" alt="대화 → 미리보기 → SAVE 승인 → 내 PC 기억 장부 → 자동 회상 · 지식 그래프 → 전문가 팩"></p>

**Core(pip) vs Bridge(원격 통로).** 로컬 CLI·stdio MCP·장부·회상·설명은 `pip install` 만으로 **오프라인** 동작합니다(=Core). 폰·웹·ChatGPT에서 표시한 저장 **의도**를 받아오는 원격 통로(hosted worker)는 별도이며(=Bridge), **데이터 정본은 언제나 로컬** — 원격은 의도만 전달하고, 장부 확정은 PC 에서 미리보기를 보고 사람이 `SAVE n` 을 입력한 뒤에만 일어납니다(원격의 장부 write 0). 자세히는 [안전 모델](#4--왜-믿을-만한가)·[6 · 어디서나 씁니다](#6--어디서나-씁니다).

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
| 🚀 **원클릭 온보딩** | `binggu onboard` 한 번이면 저장 채널·자동 동기화·개인 팩까지 셋업 끝 *(저장 채널은 hosted worker 소스가 필요 — pip 설치본엔 미포함, git clone 후 실행)* |
| 💾 **백업·복원** | 장부를 통째로 백업/내보내기하고, 실수했으면 언제든 되돌려요 |

## 4 · 왜 믿을 만한가

| 안전장치 | 뜻 |
|---|---|
| 🔒 **민감정보 차단** | 비밀번호나 이혼 같은 개인사는 애매하면 **무조건** 안 내보내요 |
| 🛡️ **손상·변조 감지** | 장부가 실수로 바뀌거나 일부가 깨지면 바로 알아채요 *(장부 파일을 직접 다루는 사람은 대상 밖 — [SECURITY](SECURITY.md))* |
| ✋ **확정은 내 승인만** | 임시 후보는 자동으로 모여도, 내 장부에 **확정 저장**되는 건 언제나 내 승인 한 번을 거쳐요 |
| ✅ **거짓말 안 함** | 없는 근거를 지어내지 않아요 — 진짜 있는 것만 연결해요 |
| 🔁 **항상 일관** | 같은 상황이면 늘 똑같이 동작해요 *(들쭉날쭉 없음)* |

그리고 세 가지 원칙 위에 서 있습니다 — **내 PC가 정본**(클라우드는 잠깐 거쳐 가는 보조 통로), **기억도 고칠 수 있음**(교체·폐기·재검토), **범위는 좁게**(문서 검색 RAG도, 팀 위키도, 전체 대화 수집기도 아닙니다).

## 5 · 이렇게 씁니다 *(명령어 몰라도 됩니다)*

1. **설치** *(처음 한 번만)*
2. **자유롭게 사용** — 평소처럼 AI와 일해요. 빙구팩이 기억할 만한 걸 알아서 지켜봐요. *(자동 감시는 기본 꺼짐 — `capture install` 후 켜져요. 꺼져 있어도 아래 3번 저장은 됩니다.)*
3. **"빙구팩 저장해"** — 이 한 마디면 오늘 나온 판단·교훈이 미리보기로 뜨고, 번호로 한 번 더 확정하면 저장돼요. *(내 승인 한 번을 꼭 거쳐요.)*

<details>
<summary><b>안에서 실제로 일어나는 일</b> — 5단계 처리 · 무엇이 후보가 되나 (펼치기)</summary>

저장할 때 안에서 5단계로 처리합니다: `1 분류 → 2 근거 → 3 지도(그래프) → 4 검증 → 5 내 승인`.
기억할 만한 말은 자동으로 임시 후보에 담기지만, 아무리 깊이 처리해도 **장부 확정은 항상 내 승인**이라 승인 없이 장부에 확정 저장되는 일은 없습니다.

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

- 🌐 **웹/앱 커넥터(HTTP 모드)** — 로컬 MCP 서버를 HTTP 모드(`--http`)로 열고 Cloudflare Tunnel 뒤에 두면, Claude 웹/앱 커넥터에서도 같은 MCP 도구를 그대로 씁니다. 접근은 경로 토큰으로 보호돼요. *(quick tunnel은 재시작마다 주소가 바뀌므로, 고정 주소가 필요하면 named tunnel — [INSTALL](INSTALL.md#webapp-connector--http-모드-optional) 참조.)*
- 💬 **ChatGPT/폰 저장 채널** — 폰·웹에서 `SAVE n`으로 표시한 건 **저장 승인이 아니라 저장 "의도"**예요. 그 의도만 클라우드 inbox에 잠깐 담기고, 내 PC가 서명키로 가져와(pull) 화면에 묶음으로 보여줘요. **실제 로컬 장부 확정은 PC에서 그 묶음의 미리보기를 보고 내가 직접 `SAVE n` 을 입력해야** 일어나고, 전부 저장되거나 전부 안 되거나(all-or-nothing)예요. 폰이 직접 내 장부에 쓰는 경로는 없어요. (`onboard`가 등록하는 옵트인 auto-pull 스케줄러도 후보를 **staging 까지만** 자동 회수하고, 실제 로컬 장부 확정은 언제나 PC에서 내가 `SAVE n` 을 쳐야 일어나요 — 무인 반영 0.)
- ☁️ **클라우드 읽기 도구** — `cloud_recall`/`cloud_packs`로 오픈크랩의 지식·팩을 조회만 해요 *(읽기 전용 · 민감정보 마스킹)*.

## 7 · 시작하기

```bash
pip install binggupack
binggu start            # 내 PC에 기억 장부 만들기 — 끝
```

- 로컬 CLI + **stdio MCP(Claude Code·Codex)**는 `pip install`만으로 충분합니다([INSTALL](INSTALL.md#install-claude-code-mcp-sandbox-entry)에 등록 방법).
- **ChatGPT/웹 커넥터 저장 채널**(`binggu onboard`)은 hosted worker 소스가 필요합니다 — **sdist 배포판엔 `hosted/workers/src` 포함, wheel 배포판엔 미포함**입니다. wheel로 설치했다면 **sdist**(`pip download --no-binary :all: binggupack`) 또는 **`git clone`**으로 받으세요(관리형 SaaS는 범위 밖 · [INSTALL](INSTALL.md#chatgpt-저장-채널-optional--hosted) 참조).

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
- **Claude Code 연결(hook·MCP)**: 캡처/회상 hook과 MCP 도구(stdio + HTTP 모드)로 붙습니다.
- **폰·웹·ChatGPT 동기화(hosted)**: 다른 기기에서 표시한 저장 **의도**를 받아와, PC에서 미리보기를 보고 사람이 `SAVE n` 을 입력해야만 로컬 장부에 확정합니다(폰 직접 write 없음 · all-or-nothing).
- **안전장치(governance/selftest)**: PII·secret 차단, 사람 승인 경계, 자체 검증.

| 영역 | 들어 있는 기능 |
|---|---|
| **로컬 기억 장부** | `init/start`, `doctor/status`, `list`, 로컬 `ledger.sqlite`, snapshot/audit log |
| **후보 선별·저장** | `preview/remember`, `reflect`, `save`, 사람 `SAVE n` 승인, PII/secret 차단 |
| **작업 전 회상** | `ask/recall/why`, `preflight`, `trace`, 회상 근거·효용 기록 |
| **기억 정리** | `deprecate`, `replace`, `accept/unaccept`, `due`, `reminders`, `resolve`, `route` |
| **내 말 vs AI 말** | `pair`, `trust`, owner/ai 화자 분리, 수용·반박·수정 관계, 직감 적중률 |
| **Claude Code·커넥터 연결** | `capture`/`preflight`/save-gate hook, stdio MCP 서버(Claude Code·Codex), HTTP 모드(`--http`), MCP 도구 — **조회**(read-only) · **미리보기**(dry-run/preview) · **사람 앵커 기반 저장**(`save_candidate` — 키보드 `SAVE n` 앵커가 있을 때만 확정). `pair`/`deprecate`/`replace`/`mark`/`harvest` 는 MCP 로는 미리보기만 가능하고 실행은 항상 fail-closed 입니다(2026-07-13 MCP approval 소비 배선 제거 — `approval_id` 무효·실제 mutation 은 owner 로컬 CLI). 배포 형태 의존 경계는 [SECURITY.md](SECURITY.md) 위협모델 참조 |
| **폰·웹·클라우드 보조** | `hosted inbox/pull`, `setup-cloud`, ChatGPT 저장 채널(inbox→서명키 pull), `cloud_recall`/`cloud_packs`, TTL/HMAC/purge 경계 |
| **외부 소스·그래프·팩** | `harvest`, `confirm-edges`, graph schema, pack contract, local ingest, watcher 계열 |
| **안전·개발자 도구** | path safety, match policy, classifier, governance, selftest, publish/export |

```text
주요 CLI: init/start · status/doctor · preview/remember · reflect · save · save-batch · list
          recall/ask/why/explain · trace · preflight · deprecate/replace/accept/unaccept
          verdict · outcome · promote · approval · learn-consume · forget · demo · studio
          due/reminders/resolve · pair/trust/route · capture · hosted · harvest
          confirm-edges · setup-cloud · onboard · backup/export/restore
          (전체 목록은 `binggu --help` 정본)
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
