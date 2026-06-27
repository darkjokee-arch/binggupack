# 여기서 시작하세요 (START HERE)

> 처음 오셨나요? 이 한 장이면 빙구팩이 뭔지 알고, **5분 안에 첫 사용**까지 갈 수 있습니다.

## 빙구팩이 뭔가요? (한 문단)

AI와 대화하다 보면 정작 남기고 싶은 것 — **내 판단, 배운 점, 정한 방침** — 이 수십 개 대화창에 흩어져 사라집니다. 빙구팩은 그걸 **넓게 줍고, 내가 직접 고른 것만** 내 PC 안 파일 하나에 저장하는 개인 지식 노트입니다. 자동으로 저장되는 건 아무것도 없고, 쓸수록 "내가 어떻게 판단하는지"가 쌓여 다음에 비슷한 일이 오면 먼저 짚어줍니다.

## 5분 사용법

```bash
# 1) 받아서 내 노트 만들기
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py init

# 2) 내 직감 한 줄 저장하기
python binggu.py pair "다음엔 이 거래처 먼저 검토" --confirm "PAIR owner:1"

# 3) 내 말 ↔ AI 의견을 한 번에 묶어 저장하기
#    인자 순서는 항상 (내 말, AI 말). --by 는 "반응한 쪽"을 가리킴
python binggu.py pair "이 건은 보류한다" "데이터가 부족하니 보수적 접근이 맞다" \
    --by ai --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"

# 4) 누가 더 잘 맞았는지 보기 / 뭘 할지 헷갈리면 안내받기
python binggu.py trust
python binggu.py route "..."
```

이게 전부입니다. 저장은 항상 **내가 `--confirm` 으로 직접 도장 찍을 때만** 일어납니다.

## 꼭 알아야 할 용어 5개

| 용어 | 쉬운 뜻 |
|---|---|
| **노트(ledger)** | 내 PC 안에 저장이 모이는 파일 하나(`ledger.sqlite`). 원본은 여기에만 있음 |
| **화자 축(owner / ai)** | 내가 한 말(owner)과 AI가 한 말(ai)을 **따로** 쌓아 누가 말했는지 구분 |
| **페어(pair)** | 내 말 + AI 말 + 둘의 관계(수용/반박/수정)를 **한 번에** 묶어 저장하는 단위 |
| **팩(pack)** | 모은 지식을 다른 도구에 넘길 수 있게 묶은 **지식 꾸러미** |
| **HOLD** | "내가 승인하기 전까지 멈춤" 상태. 외부로 나가는 일은 기본이 HOLD |

## 아직 안 되는 것 / HOLD 상태

빙구팩은 안전을 위해 **위험한 동작은 기본적으로 멈춰** 둡니다.

- **외부 업로드는 멈춤(HOLD)** — 다른 실행 도구로 내보내기·클라우드 전송은 내가 명시 승인하기 전까지 자동으로 나가지 않습니다.
- **AI 자동 저장은 아예 없음** — AI나 자동 경로는 저장 못 합니다. 저장은 내가 고른 것만.
- **AI가 직접 판단을 떠올려 쓰는 기능(LLM 연동)은 시제품 단계** — 아직 실험용이며 기본 기능은 LLM 없이 동작합니다.
- **민감정보는 후보 단계에서 자동 제외** — 비밀번호·개인정보는 저장 후보에도 오르지 않습니다.

## 다음에 볼 문서

- [README](../README.md) — 전체 기능 한눈에 보기
- [10분 튜토리얼](BINGGUPACK_TUTORIAL.md) — 단계별 따라하기
- [설치 가이드](../INSTALL.md) — OS별 설치(Windows · WSL · macOS · Linux)
- [화자 축 설계](BINGGUPACK_SPEAKER_AXIS_DESIGN.md) — 내 말 / AI 말 따로 쌓기
- [거버넌스 설계](BINGGUPACK_GOVERNANCE_DESIGN.md) — 사람 승인 저장·안전 경계
- [📑 문서 전체 색인](INDEX.md) — 정본 문서와 설계·기록 구분
