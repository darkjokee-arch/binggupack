# 여기서 시작하세요

처음이라면 이 문서만 보면 됩니다.
BingguPack은 **내 판단·교훈·선호를 내 PC에 저장하고, 다음 작업 전에 다시 떠올려주는 개인 기억 엔진**입니다.

## 1. 무엇을 해주나요

AI와 일하다 보면 다음에도 기억해야 할 말이 생깁니다.

- "배포 전에 live endpoint를 먼저 확인한다."
- "다음엔 이 거래처를 먼저 검토한다."
- "이 방식은 위험하니 기본값으로 쓰지 않는다."
- "이번엔 AI 제안보다 내 판단이 맞았다."

BingguPack은 이런 말을 후보로 보여주고, **내가 직접 고른 것만** 저장합니다.
저장된 기억은 다음 작업 전에 `ask`나 `preflight`로 다시 꺼내 쓸 수 있습니다.

## 2. 5분 사용법

clone한 폴더에서는 `binggu` 대신 `python binggu.py`로 실행하면 됩니다.

```bash
# 1) 내 장부 만들기
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py start

# 2) 저장 후보 보기. 아직 저장되지 않습니다.
python binggu.py remember "배포 전에 live endpoint를 먼저 확인한다"

# 3) 화면에 나온 save 명령을 보고, 내가 고른 번호만 저장합니다.
python binggu.py save "배포 전에 live endpoint를 먼저 확인한다" \
  --preview-id <화면에 나온 id> --pick 1 --explicit --confirm "SAVE 1"

# 4) 다음 작업 전에 관련 기억을 물어봅니다.
python binggu.py ask "배포 전에 조심할 것?"

# 5) 상태 점검
python binggu.py doctor
```

핵심은 하나입니다.
**미리보기는 저장이 아니고, 저장은 내가 confirm할 때만 일어납니다.**

## 3. 자주 쓰는 명령 4개

| 명령 | 쉬운 뜻 |
|---|---|
| `python binggu.py start` | 내 장부를 만듭니다 |
| `python binggu.py remember "..."` | 기억할 만한 문장인지 미리 봅니다 |
| `python binggu.py ask "..."` | 관련 기억과 과거 실수 패턴을 찾습니다 |
| `python binggu.py doctor` | 장부와 환경이 정상인지 점검합니다 |

## 4. 어떤 문장이 후보가 되나요

BingguPack은 아무 말이나 모으지 않습니다.
**판단·교훈·선호·규칙**처럼 다음에도 쓸 만한 것만 후보로 올립니다.

| 후보가 됨 | 후보가 안 됨 |
|---|---|
| "이건 B안으로 간다" | "상태 보여줘" |
| "다음부터 백업 먼저" | "지금 이거 고쳐" |
| "짧은 답을 선호한다" | "커밋 완료" |
| "배포는 두 번 확인한다" | "오늘 수고했어" |
| "이 방식은 위험하다" | "토큰 버킷은 이런 뜻이다" |

`remember`와 `pair`처럼 내가 직접 입력한 문장은 조금 더 넓게 후보로 보여줍니다.
그래도 비밀번호·개인정보는 차단되고, 바로 저장되지 않습니다.

## 5. 내 말과 AI 의견을 같이 남기기

내 판단과 AI 의견을 함께 저장하면, 나중에 누가 더 잘 맞았는지도 볼 수 있습니다.

```bash
python binggu.py pair "이 건은 보류한다" "데이터가 부족하니 보수적 접근이 맞다" \
  --by ai --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"

python binggu.py trust
```

혼자 떠오른 직감만 남길 수도 있습니다.

```bash
python binggu.py pair "다음엔 이 거래처 먼저 검토" --confirm "PAIR owner:1"
```

## 6. 회상이 도움이 됐는지 기록하기

선택 기능입니다. 기본은 꺼져 있습니다.
켜면 BingguPack이 떠올린 기억이 실제로 도움이 됐는지 나중에 표시할 수 있습니다.

```bash
python binggu.py trace enable
python binggu.py trace review
python binggu.py trace mark 1 used
python binggu.py trace mark 2 ignored --note not_relevant
python binggu.py trace mark 3 corrected --note stale
```

trace는 문장 원문을 저장하지 않습니다.
node_id, 분류, 점수 같은 메타데이터만 저장하고, `used/ignored/corrected` 판정은 사람이 직접 표시합니다.

## 7. 꼭 알아야 할 말

| 용어 | 뜻 |
|---|---|
| 장부 / ledger | 내 기억이 저장되는 로컬 파일 `ledger.sqlite` |
| owner | 내가 한 말 |
| ai | AI가 한 말 |
| pair | 내 말과 AI 말을 관계까지 묶어 저장하는 것 |
| HOLD | 내가 승인하기 전까지 외부로 내보내지 않는 상태 |

## 8. BingguPack이 아닌 것

- RAG 문서 검색 엔진이 아닙니다.
- 팀 위키가 아닙니다.
- 자동 지식 수집기가 아닙니다.
- 클라우드 메모리 서비스가 아닙니다.

## 9. 다음 문서

- [README](../README.md) — 전체 소개
- [10분 튜토리얼](BINGGUPACK_TUTORIAL.md) — 단계별 따라하기
- [설치 가이드](../INSTALL.md) — OS별 설치
- [화자 축 설계](BINGGUPACK_SPEAKER_AXIS_DESIGN.md) — 내 말 / AI 말 따로 저장
- [거버넌스 설계](BINGGUPACK_GOVERNANCE_DESIGN.md) — 사람 승인 저장과 안전 경계
- [문서 전체 색인](INDEX.md) — 정본 문서와 기록 구분
