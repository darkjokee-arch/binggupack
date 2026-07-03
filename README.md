# BingguPack

**AI와 일하면서 쌓이는 내 판단, 실수, 취향, 규칙을 내 PC 안의 기억 장부로 바꾸는 로컬 우선 AI 작업 메모리입니다.**

## 빙구팩이 해주는 일 (한눈에)

**핵심 3단계 — 이것만 알면 됩니다.**

1. **골라서 보여주기** — 대화 중 "기억할 만한" 말만 추려서 보여줘요 *(아직 저장 안 함)*
2. **내가 승인** — 내가 고른 것만 내 PC에 저장돼요 *(자동 저장 절대 없음)*
3. **다시 꺼내주기** — 다음에 일할 때 관련 기억과 지난 실수를 먼저 보여줘요

**추가로 해주는 것들**

- 🗣️ 내 말과 AI 말을 **따로** 저장하고, 내 감이 맞았는지 세어줘요
- 🧹 오래된 기억은 바꾸거나 지우고, 다시 볼 건 **알림**을 걸어요
- 🌐 인터넷·문서에서 기억할 자료를 **대신 찾아와요** *(바로 저장은 안 함 · 후보만)*
- 🔎 하나의 주제를 **깊이 파고들어** 관련된 걸 넓게 찾아요
- 🕸️ 기억들을 서로 연결해 **지도처럼** 묶어요
- 📦 그 지도가 **오픈크랩(익스퍼트플랜)에 자동으로 팩(전문가 지식 묶음)으로** 만들어져요
- 🔌 Claude Code에 자동으로 붙고, 폰·웹에 적어둔 메모도 가져와요

**왜 믿을 만한가 — 빙구팩만의 강점**

- 🔒 **민감정보 차단** — 비밀번호나 이혼 같은 개인사는 애매하면 **무조건** 안 내보내요
- 🛡️ **변조 감지** — 누가 내 기억 장부를 몰래 고치거나 지우면 바로 들통나요
- 📊 **직감 점수** — 내 감이 맞았는지 AI가 맞았는지 **공정하게 숫자로** 알려줘요
- ✅ **거짓말 안 함** — 없는 근거를 지어내지 않아요, 진짜 있는 것만 연결해요
- 🔁 **항상 일관** — 같은 상황이면 늘 똑같이 동작해요 *(들쭉날쭉 없음)*

> 전부 **자동 저장 없이, 내가 승인한 것만, 로컬 우선**입니다.
> 명령어·용어까지 자세히는 아래 [기능 전체 지도](#기능-전체-지도)를 보세요.

> 현재 `main`: 분류 기준 통합, `remember`/`pair` 명시 입력 경로, 회상 효용 trace, `storage`/`mcp` facade, 코어 로직 `binggupack/` 패키지 이관(strangler·[ARCHITECTURE](docs/BINGGUPACK_ARCHITECTURE.md)), ruff/mypy 툴체인·브랜드 통일까지 반영.
> 최신 소스(git clone): **v1.16.0** · [Release](https://github.com/darkjokee-arch/binggupack/releases/tag/v1.16.0)
> PyPI 현재 배포: **1.15.0** · [PyPI](https://pypi.org/project/binggupack/) — `pip install binggupack`로 받는 버전입니다. v1.16.0 기능은 아래 `git clone`으로 사용하세요.
> 로컬 우선 · 자동 저장 없음 · 내가 고른 것만 저장 · MIT License

[처음 시작하기](docs/START_HERE.md) · [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) · [설치](INSTALL.md) · [Claude Code MCP](INSTALL.md#install-claude-code-mcp-sandbox-entry) · [캡처 hook](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) · [문서 색인](docs/INDEX.md)

AI와 오래 일하다 보면 이런 일이 반복됩니다.

- 매번 "나는 짧게 답하는 걸 좋아한다"고 다시 설명합니다.
- 예전에 터졌던 실수를 다른 작업에서 또 조심해야 합니다.
- AI가 맞았는지, 내 직감이 맞았는지 시간이 지나면 흐려집니다.
- 다른 기기에서 떠오른 메모를 나중에 내 작업 장부로 옮기고 싶습니다.
- 자동 저장은 편하지만, 내 대화와 민감정보가 마음대로 쌓이는 건 싫습니다.

BingguPack은 모든 대화를 긁어모으는 도구가 아닙니다.
다음에도 써먹을 **판단·교훈·선호·규칙**만 후보로 보여주고, 내가 직접 고른 것만 로컬 장부(`ledger.sqlite`)에 남깁니다.

## 30초 흐름

```text
AI와 작업하다가 "이건 다음에도 기억해야겠다"는 말이 생김
  -> BingguPack이 저장 후보로 미리 보여줌
  -> 내가 번호를 고르고 SAVE n으로 승인
  -> 내 PC의 ledger.sqlite에 저장
  -> 다음 작업 시작 전에 관련 기억과 과거 실수 패턴을 다시 보여줌
```

## 기능 전체 지도

> 위 "빙구팩이 해주는 일"을 명령어·용어와 함께 정리한 개발자용 목록입니다.

**명령어별 한 줄 설명**

- **미리보기·저장(preview → SAVE)**: 기억할 만한 판단·교훈만 후보로 보여주고, 내가 고른 것만 로컬 장부에 저장합니다.
- **회상(recall/ask)**: 작업을 시작하기 전에 관련 기억과 과거 실수 패턴을 다시 꺼내줍니다.
- **근거 추적(trace)**: 그 회상이 실제로 도움이 됐는지를 기록해 회상 품질을 다듬습니다.
- **화자 분리(pair)**: 내 말과 AI 말을 나눠 저장하고, 내 직감이 맞았는지 적중률을 셉니다.
- **기억 정리(deprecate/replace)**: 오래된 판단을 교체·폐기하고, 다시 볼 것에 리마인더를 겁니다.
- **수확(harvest)**: 외부 웹·문서에서 저장 후보가 될 소스를 긁어옵니다(바로 저장 아님·후보만).
- **깊이 탐색(explore `--depth`)**: 주제 하나에서 하위 개념을 재귀(BFS)로 `--depth`만큼 파고들어, 관련 소스·기억 후보를 넓게 찾아냅니다(폭 `--breadth`·관련도 `--rel-min`도 조절).
- **그래프·팩(graph/pack)**: 기억을 근거로 연결해 그래프를 만들고, 팩 형식으로 묶어 내보냅니다. 근거는 2층(추천)·3층(그래프)·5층(사람 승인)으로 깊이를 더할 수 있습니다.
- **클라우드 팩(오픈크랩/익스퍼트플랜)**: 묶은 그래프·팩을 오픈크랩(ExpertPlan)에 전문가 지식 팩으로 자동 생성합니다.
- **Claude Code 연결(hook·MCP)**: 캡처/회상 hook과 MCP 8도구로 Claude Code에 붙습니다.
- **폰·웹 동기화(hosted)**: 다른 기기에서 잠깐 받아둔 메모를 로컬 장부로 가져옵니다.
- **안전장치(governance/selftest)**: PII·secret 차단, 사람 승인 경계, 자체 검증으로 안전을 강제합니다.

전체 기능을 길게 펼치면 오히려 안 읽히기 때문에, 아래처럼 한 장으로 압축했습니다.
세부 명령과 설치 절차는 [설치 가이드](INSTALL.md)와 [문서 색인](docs/INDEX.md)에 모아두었습니다.

| 영역 | 들어 있는 기능 |
|---|---|
| **로컬 기억 장부** | `init/start`, `doctor/status`, `list`, 로컬 `ledger.sqlite`, snapshot/audit log |
| **후보 선별·저장** | `preview/remember`, `reflect`, `save`, 사람 `SAVE n` 승인, PII/secret 차단 |
| **작업 전 회상** | `ask/recall/why`, `preflight`, `trace`, 회상 근거·효용 기록 |
| **기억 정리** | `deprecate`, `replace`, `accept/unaccept`, `due`, `reminders`, `resolve`, `route` |
| **내 말 vs AI 말** | `pair`, `trust`, owner/ai 화자 분리, 수용·반박·수정 관계, 직감 적중률 |
| **Claude Code 연결** | `capture` hook, `preflight` hook, save-gate hook, stdio MCP 서버, MCP 8도구 |
| **폰·웹·클라우드 보조** | `hosted inbox/pull`, `setup-cloud`, Cloudflare Worker save-intent, TTL/HMAC/purge 경계 |
| **외부 소스·그래프·팩** | `harvest`, `confirm-edges`, graph schema, pack contract, local ingest, watcher 계열 |
| **안전·개발자 도구** | path safety, match policy, classifier, interactive save, session close, governance, selftest, publish/export, reviewed plan, hybrid AGI 실험 모듈 |

```text
CLI 전체: init/start · status/doctor · preview/remember · reflect · save · list
          recall/ask/why · trace · preflight · deprecate/replace/accept/unaccept
          due/reminders/resolve · pair/trust/route · capture · hosted · harvest
          confirm-edges · setup-cloud
```

## 핵심 원칙

- **내 PC가 정본입니다.** 기본 장부는 로컬 `ledger.sqlite`입니다.
- **자동 영구 저장은 없습니다.** AI, hook, hosted 경로는 후보를 만들 수 있을 뿐입니다.
- **민감정보는 후보 단계에서 차단합니다.** 비밀번호·개인정보를 기억으로 쌓지 않는 쪽이 기본값입니다.
- **클라우드는 보조 경로입니다.** hosted inbox는 잠깐 받아두는 통로이고, 원본 기억을 맡기는 서비스가 아닙니다.
- **기억도 고칠 수 있습니다.** 오래된 판단은 교체, 폐기, 재검토할 수 있습니다.

### 처음이라면 여기부터

**[docs/START_HERE.md](docs/START_HERE.md)** 에서 5분 흐름만 따라가면 됩니다.

## 빙구팩이 아닌 것

범위를 일부러 좁게 잡았습니다.

- 문서 검색용 RAG가 아닙니다.
- 팀 위키가 아닙니다.
- 모든 대화를 자동으로 수집하는 도구가 아닙니다.
- 클라우드에 원본 기억을 맡기는 서비스가 아닙니다.

핵심은 **내 판단을 내가 승인해서 남기고, 다음 작업 전에 꺼내 쓰는 것**입니다.

## 5분 사용법

clone해서 바로 쓸 수 있습니다. 아래에서 `binggu`라고 부르는 명령은 clone한 폴더에서는 `python binggu.py`로 실행하면 됩니다.

일반 사용자는 PyPI 설치가 가장 짧습니다. (PyPI 현재 배포 = `1.15.0`. v1.16.0 기능은 아래 `git clone`으로 사용하세요.)

```bash
pip install binggupack
binggu start
binggu remember "배포 전에 live endpoint를 먼저 확인한다"
binggu doctor
```

`python -m binggupack doctor`처럼 모듈 실행도 됩니다.

소스에서 바로 실행하려면 clone해서 `python binggu.py`를 쓰면 됩니다.

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py start
```

내가 기억하고 싶은 말을 미리 봅니다. 이 단계에서는 저장되지 않습니다.

```bash
python binggu.py remember "배포 전에 live endpoint를 먼저 확인한다"
```

출력에 저장 명령이 같이 나옵니다. 그 명령을 보고 내가 고른 번호만 저장합니다.

처음 보는 문서가 많다면 [START_HERE](docs/START_HERE.md) → [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) → [설치 가이드](INSTALL.md)까지만 보면 됩니다.

```bash
python binggu.py save "배포 전에 live endpoint를 먼저 확인한다" \
  --preview-id <화면에 나온 id> --pick 1 --explicit --confirm "SAVE 1"
```

다음 작업 전에 관련 기억을 불러옵니다.

```bash
python binggu.py ask "배포 전에 조심할 것?"
```

상태 점검은 이렇게 합니다.

```bash
python binggu.py doctor
```

## 무엇이 저장 후보가 되나요

자동 캡처와 미리보기는 같은 기준을 씁니다.
아무 문장이나 저장 후보로 올리지 않습니다.

| 후보가 됨 | 후보가 안 됨 |
|---|---|
| 판단: "이건 B안으로 간다" | 조회: "상태 보여줘" |
| 교훈: "다음부터 백업 먼저" | 일회성 지시: "지금 이거 고쳐" |
| 선호: "나는 짧은 답을 선호한다" | 운영 보고: "커밋 완료" |
| 규칙: "배포는 두 번 확인" | 잡담: "오늘 수고했어" |
| 위험 감지: "이 방식은 터질 수 있다" | 순수 지식: "토큰 버킷은 ..." |

`remember`와 `pair`처럼 사용자가 직접 "이걸 기억하라"고 입력한 경로는 조금 더 넓게 받습니다.
그래도 비밀번호·개인정보·자동 저장 차단 같은 안전 게이트는 그대로 유지됩니다.

원본 기억의 기준점은 로컬 장부입니다.
외부 도구나 hosted inbox는 보조 경로이고, 원본을 대신하지 않습니다.

## 조금 더 깊게 쓰기

| 주제 | 문서 |
|---|---|
| 처음 따라하기 | [START_HERE](docs/START_HERE.md) |
| 10분 튜토리얼 | [BINGGUPACK_TUTORIAL](docs/BINGGUPACK_TUTORIAL.md) |
| 설치 | [INSTALL](INSTALL.md) |
| 내 말 / AI 말 따로 저장 | [BINGGUPACK_SPEAKER_AXIS_DESIGN](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) |
| 사람 승인과 안전 경계 | [BINGGUPACK_GOVERNANCE_DESIGN](docs/BINGGUPACK_GOVERNANCE_DESIGN.md) |
| hosted inbox 경계 | [BINGGUPACK_HOSTED_BOUNDARY](docs/BINGGUPACK_HOSTED_BOUNDARY.md) |
| 팩 형식 | [BINGGUPACK_PACK_CONTRACT](docs/BINGGUPACK_PACK_CONTRACT.md) |
| 코어 구조(개발자) | [BINGGUPACK_ARCHITECTURE](docs/BINGGUPACK_ARCHITECTURE.md) |
| 전체 문서 색인 | [docs/INDEX.md](docs/INDEX.md) |

## 개발자용 짧은 점검

```bash
python scripts/openbinggu_doctor.py --selftest
python scripts/smoke_test.py --home ./_binggu_smoke_home
python binggu.py --selftest
```

스크립트 파일명(`scripts/openbinggu_*.py`)과 일부 외부 식별자에 남아 있는 `OpenBinggu`는 예전 내부 코드네임입니다. 현재 공개 제품명은 BingguPack이고, 문서는 이미 `BINGGUPACK_*`로 정리돼 있으니 새로 읽을 때는 `BINGGUPACK_*`와 `START_HERE`를 우선 보세요.

코어 로직은 `binggupack/` 패키지로 이관 중입니다(strangler-fig). 순수 판정·변환·안전 규칙은 패키지 정본으로 옮기고, `scripts/`의 레거시 파일은 정본을 재노출하는 얇은 위임(shim)으로 바뀌므로 기존 실행/임포트는 그대로 동작합니다. 코어 지도는 [BINGGUPACK_ARCHITECTURE](docs/BINGGUPACK_ARCHITECTURE.md)를 보세요.

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
