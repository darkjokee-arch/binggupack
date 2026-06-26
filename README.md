# BingguPack

**내가 일할수록 나를 알아가는, 내 PC 안의 개인 지식 노트.**

> 최신: **v1.12.0 — 화자 축(Speaker axis)** 🗣️ · 내 말과 AI 요약을 따로 쌓고 연결하고, 누가 더 잘 맞았는지까지 기억합니다.
> 🔒 로컬 우선 · 자동 저장 없음 · 내가 고른 것만 저장 · MIT License
> Release: <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.12.0>

---

## 빙구팩이 뭔가요?

AI와 대화하다 보면 정작 남기고 싶은 것 — **내 판단, 배운 점, 정한 방침** — 이 수십 개 대화창에 흩어져 사라집니다. 그렇다고 전부 자동 저장하면 잡음과 민감정보가 쌓이고 통제권을 잃죠.

빙구팩은 **넓게 줍고, 내가 고른 것만 저장하는** 개인 노트입니다.

- 원본은 전부 내 PC 안 파일 하나(`ledger.sqlite`)에 있습니다. 클라우드가 원본을 갖지 않습니다.
- **자동으로 저장되는 건 아무것도 없습니다.** 내가 직접 고른 것만 저장됩니다.
- 쓸수록 빙구팩은 "나"를 알아갑니다 — 내가 어떻게 판단하고, 뭘 선호하고, 어떤 실수를 했는지가 쌓여서, 다음에 비슷한 일이 오면 먼저 짚어줍니다.

## 무엇을 할 수 있나요

| 능력 | 한 줄 설명 |
|---|---|
| 🧹 **넓게 수집** | 어느 AI(Claude·ChatGPT·폰·웹)에서 일하든 남길 문장을 후보로 모음 |
| 👀 **미리보기** | 모은 후보를 먼저 보여줌 — 이 단계에선 저장 0 |
| ✍️ **내가 골라 저장** | 내가 직접 고른 것만 저장. AI는 저장 못 함 |
| 🗣️ **화자 축** | 내 말(직감·지적)과 AI 요약(수정·수용·반박)을 **따로** 쌓고, 수용/반박/수정으로 연결 |
| ⚖️ **양방향 신뢰도** | 내 직감과 AI 반박, **누가 더 잘 맞았나**를 기억. 한쪽 편 안 듦 — 나도 AI도 틀릴 수 있으니까 |
| 🔁 **자기수정** | 틀린 판단은 고치고, 예측은 결과로 검증해 다음 판단이 똑똑해짐 |
| 🧠 **회상·반문** | 일 시작 전 관련 기억과 과거 실수 패턴을 먼저 떠올려줌 |
| 📦 **팩 만들기·검증** | 모은 지식을 다른 도구가 쓸 "꾸러미(팩)"로 묶고 구조를 검증 |
| 🔀 **워크플로우 추천** | "이 목표엔 이런 팩·데이터가 필요해요"를 자동 제안(추천만 — 실행은 사람) |
| ☁️ **안전하게 내보내기** | 외부 실행 엔진(OpenCrab)으로 보내기 전 안전점검·업로드 준비. 실제 전송은 내가 승인할 때만 |
| 🔌 **Claude Code 연결** | `clone` 한 번으로 MCP 패키지 설치(도구 8개) |
| 💻 **어디서나** | Windows · WSL · macOS · Linux |

## 빠른 시작

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py init                         # 내 노트 만들기
```

**내 말과 AI 요약을 따로 쌓고 연결하기 (화자 축)**

```bash
# 내 직감 + AI 요약을 페어로 — relation: accepts(수용) / refutes(반박) / revises(수정)
python binggu.py pair "이 입찰은 보류한다" "데이터가 부족해 보수적 접근이 맞다" \
    --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"

python binggu.py pair "다음엔 이 거래처 우선 검토" --confirm "PAIR owner:1"   # 내 직감만

python binggu.py trust          # 누가 더 잘 맞나 (양방향 신뢰도)
python binggu.py resolve <n> <id8> --outcome 성공   # 결과 기록 → 적중률 누적
python binggu.py route "..."    # 뭘 할지 헷갈리면 안내해줌
```

> 더 자세히: [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) · [설치 가이드](INSTALL.md)

## 팩과 워크플로우 (외부 도구로 연결)

빙구팩은 모은 지식을 **혼자만 쓰는 게 아니라**, 다른 실행 도구(OpenCrab)가 받아 쓸 수 있게 **준비·검증·안전점검**까지 해줍니다.

```
목표 → 필요한 팩 추천 → 근거 모으기 → 팩 만들기 → 검증 → 발행 안전점검 → 내보내기 준비
```

- 각 단계가 fail-closed로 막혀, **깨지거나 근거 없는 팩은 외부로 못 나갑니다.**
- 관계·워크플로우는 **추천만** 합니다 — 무엇을 만들고 내보낼지는 사람이 정합니다.
- 실제 외부 업로드·클라우드 전송은 **내가 명시 승인하기 전까지 멈춤(HOLD)**. 자동으로 나가는 건 없습니다.

> 흐름 상세: [팩 계약](docs/OPENBINGGU_PACK_CONTRACT.md) · [업로드 흐름](docs/OPENBINGGU_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md) · [개인/팀 두 트랙](docs/OPENBINGGU_PRODUCT_DIRECTION_TWO_TRACK.md)

## 안전 약속

빙구팩의 안전은 말이 아니라 **자동 테스트로 증명**됩니다.

- **자동 저장 없음** — 내가 직접 고른 것만 저장. AI/자동 경로는 차단.
- **민감정보 차단** — 비밀번호·개인정보는 후보 단계에서 자동 제외.
- **언제든 되돌리기** — 모든 변경 전 백업 + 되돌리기 가능.
- **원본은 내 PC** — 클라우드가 내 원본을 갖지 않음. 외부 업로드는 내가 승인하기 전까지 멈춤.

## 더 알아보기

- [화자 축 설계](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) — 내 말/AI 요약 따로 쌓기, 양방향 신뢰도
- [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) · [설치 가이드](INSTALL.md) · [변경 이력](CHANGELOG.md)

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
