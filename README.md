# BingguPack

**AI와 일하면서 생긴 내 판단·교훈·선호를 내 PC에 저장하고, 다음 작업 전에 다시 떠올려주는 로컬 우선 기억·맥락 엔진입니다.**

> 현재 `main`: 분류 기준 통합, `remember`/`pair` 명시 입력 경로, 회상 효용 trace, `storage`/`mcp` facade까지 반영.
> 최신 배포판: **v1.15.0** · [Release](https://github.com/darkjokee-arch/binggupack/releases/tag/v1.15.0) · [PyPI](https://pypi.org/project/binggupack/)
> 로컬 우선 · 자동 저장 없음 · 내가 고른 것만 저장 · MIT License

### 처음이라면 여기부터

**[docs/START_HERE.md](docs/START_HERE.md)** 에서 5분 흐름만 따라가면 됩니다.

---

## 한 줄로 말하면

AI와 대화하다 보면 "이건 다음에도 기억해야겠다" 싶은 말이 생깁니다.
예를 들면 이런 것들입니다.

- "배포 전에 live endpoint를 먼저 확인한다."
- "이 거래처는 다음에 우선 검토한다."
- "이 방식은 위험하니 기본값으로 쓰지 않는다."
- "AI가 제안했지만, 이번엔 내 판단이 더 맞았다."

BingguPack은 이런 문장을 **후보로 보여주고**, 내가 직접 고른 것만 **내 PC의 장부(`ledger.sqlite`)** 에 저장합니다.
나중에 비슷한 일을 시작하면 관련 기억과 과거 실수 패턴을 먼저 보여줍니다.

## 무엇까지 들어 있나요

1차 목표는 "내 판단을 내가 승인해서 저장하고 다시 꺼내 쓰는 것"입니다.
그 목표를 실제 작업 흐름에 붙이기 위해 아래 기능들이 같이 들어 있습니다.

| 영역 | 기능 | 쉬운 설명 |
|---|---|---|
| 저장 후보 선별 | `remember` / `preview` | 문장을 바로 저장하지 않고, 판단·교훈·선호·규칙 후보인지 먼저 보여줍니다. |
| 사람 승인 저장 | `save --confirm "SAVE n"` | 번호 선택과 confirm 문구가 맞을 때만 로컬 장부에 저장합니다. |
| 작업 전 회상 | `ask` / `preflight` | 비슷한 과거 기억, 실수 패턴, 조심할 점을 작업 전에 꺼냅니다. |
| Claude Code 연결 | capture / preflight hook | 원하면 발화 후보 수집과 작업 전 회상을 Claude Code 흐름에 붙일 수 있습니다. 기본은 opt-in입니다. |
| 내 말과 AI 말 분리 | `pair` / `trust` | owner 발화와 AI 의견을 따로 저장하고, 나중에 어느 쪽 판단이 맞았는지 비교합니다. |
| 회상 품질 기록 | `trace review` / `trace mark` | 떠올린 기억이 실제로 도움됐는지 사람이 `used`, `ignored`, `corrected`로 표시합니다. |
| 기억 정리 | `replace` / `deprecate` / `due` / `resolve` | 오래된 기억을 교체·폐기하거나, 나중에 다시 볼 항목으로 남깁니다. |
| MCP/패키지 통합 | stdio MCP server / PyPI CLI | Claude Code MCP 서버와 `binggu` CLI로 설치해서 쓸 수 있습니다. |
| 선택형 cloud inbox | `hosted inbox` / `hosted pull` | 다른 기기에서 저장 의도만 잠깐 받아두고, 내 PC에서 고른 항목만 로컬 장부로 가져옵니다. |
| 외부 소스 후보화 | `harvest` | 사람이 등록한 소스만 읽어 후보로 올립니다. 영구 저장은 여전히 `SAVE n` 게이트를 통과해야 합니다. |
| 점검·검증 | `doctor`, smoke/selftest | 장부, hook, MCP, hosted 경계가 깨졌는지 빠르게 확인합니다. |

공통 원칙은 같습니다.
**자동 영구 저장 없음, 민감정보 차단, 로컬 장부가 정본, 클라우드와 hook은 보조 경로**입니다.

## 빙구팩이 아닌 것

범위를 일부러 좁게 잡았습니다.

- 문서 검색용 RAG가 아닙니다.
- 팀 위키가 아닙니다.
- 모든 대화를 자동으로 수집하는 도구가 아닙니다.
- 클라우드에 원본 기억을 맡기는 서비스가 아닙니다.

핵심은 **내 판단을 내가 승인해서 남기고, 다음 작업 전에 꺼내 쓰는 것**입니다.

## 5분 사용법

clone해서 바로 쓸 수 있습니다. 아래에서 `binggu`라고 부르는 명령은 clone한 폴더에서는 `python binggu.py`로 실행하면 됩니다.

일반 사용자는 PyPI 설치가 가장 짧습니다.

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

## 자주 쓰는 명령

| 하고 싶은 일 | 명령 | 뜻 |
|---|---|---|
| 처음 시작 | `python binggu.py start` | 내 장부 만들기 |
| 기억 후보 보기 | `python binggu.py remember "..."` | 저장 전 미리보기 |
| 관련 기억 찾기 | `python binggu.py ask "..."` | 회상 |
| 상태 점검 | `python binggu.py doctor` | 장부·환경 확인 |
| 내 말과 AI 의견 묶기 | `python binggu.py pair "내 말" "AI 말" ...` | 누가 맞았는지 나중에 비교 |
| 회상이 도움됐는지 표시 | `python binggu.py trace review` / `trace mark` | 효용 기록(opt-in) |

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

## 어떻게 데이터가 움직이나요

```text
내 문장
  -> preview/remember 에서 후보 확인
  -> 내가 번호를 골라 confirm
  -> 내 PC의 ledger.sqlite 에 저장
  -> ask/preflight 가 다음 작업 전에 다시 보여줌
```

원본 기억의 기준점은 로컬 장부입니다.
외부 도구나 hosted inbox는 보조 경로이고, 원본을 대신하지 않습니다.

## 안전 약속

- **자동 저장 없음**: AI나 자동 경로는 durable 저장을 못 합니다.
- **사람 승인 필요**: 저장은 내가 번호를 고르고 confirm할 때만 됩니다.
- **민감정보 차단**: 비밀번호·개인정보는 후보 단계에서 제외합니다.
- **원본은 내 PC**: 기본 장부는 로컬 `ledger.sqlite`입니다.
- **되돌릴 수 있음**: 저장 전 snapshot과 audit log를 남깁니다.
- **회상 효용 trace도 opt-in**: 켜야만 기록되고, 원문 대신 node_id·분류·점수 같은 메타데이터만 저장합니다.

## 조금 더 깊게 쓰기

| 주제 | 문서 |
|---|---|
| 처음 따라하기 | [START_HERE](docs/START_HERE.md) |
| 10분 튜토리얼 | [BINGGUPACK_TUTORIAL](docs/BINGGUPACK_TUTORIAL.md) |
| 설치 | [INSTALL](INSTALL.md) |
| 내 말 / AI 말 따로 저장 | [BINGGUPACK_SPEAKER_AXIS_DESIGN](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) |
| 사람 승인과 안전 경계 | [BINGGUPACK_GOVERNANCE_DESIGN](docs/BINGGUPACK_GOVERNANCE_DESIGN.md) |
| hosted inbox 경계 | [BINGGUPACK_HOSTED_BOUNDARY](docs/BINGGUPACK_HOSTED_BOUNDARY.md) |
| 팩 형식 | [OPENBINGGU_PACK_CONTRACT](docs/OPENBINGGU_PACK_CONTRACT.md) |
| 전체 문서 색인 | [docs/INDEX.md](docs/INDEX.md) |

## 개발자용 짧은 점검

```bash
python scripts/openbinggu_doctor.py --selftest
python scripts/smoke_test.py --home ./_binggu_smoke_home
python binggu.py --selftest
```

문서와 스크립트 이름에 남아 있는 `OpenBinggu`는 예전 내부 코드네임입니다. 현재 공개 제품명은 BingguPack이고, 새로 읽을 문서는 `BINGGUPACK_*`와 `START_HERE`를 우선 보세요.

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
