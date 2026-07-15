# Memory Pull Request Specification — v0.1-draft

> **상태 (정직 고지)**
> - 🟡 **프로젝트 작성 초안(v0.1-draft)** — 확정 규격 아님.
> - **독립 구현·상호운용성 증거 없음.** 표준단체(IETF/W3C 등) 문서가 아니다.
> - 이 문서는 **BingguPack reference implementation** 의 현재 동작을 서술한다.
> - **주장하지 않는 것**: 업계 표준 · 범용 Memory PR 프로토콜 · vendor-neutral 상호운용 · 타 프로젝트 채택 · 모든 기억 시스템과 호환 · 세 프로필 간 digest 호환.

## 목적

BingguPack 이 기억(memory)과 mutation 을 **어떻게 제안·검토·승인·확정**하는지 공개적으로 문서화하고, 외부 클라이언트가 **현재 지원되는 경로를 정확히 재현**할 수 있게 한다.

## Memory PR 이란

> **Git for AI memory.** AI 는 기억 후보를 *제안(propose)* 할 수 있고, *확정(commit)* 은 오직 사람만 한다.

Git 의 Pull Request 가 "제안 → 검토 → 승인 → 병합" 단계를 코드에 적용하듯, **Memory PR** 은 같은 단계를 AI 의 기억에 적용한 **BingguPack 의 공개 설명 모델**이다. (범용 프로토콜이 아니라 이 구현의 설명 모델이다.)

| Git | BingguPack Memory PR |
| :-- | :-- |
| Pull Request | Candidate / Preview |
| Review | Reviewable Preview (사람이 보는 미리보기) |
| Approve | Human Decision (SAVE n / approve event) |
| Merge | Commit / Event Consumption |
| — (merge·branch 개념 없음) | 물리 삭제 없는 Supersede/Retire |

## 문서 구조 (4층)

```
Memory PR Core Model            ← 공통 의미 어휘 (같은 바이트 산식을 주장하지 않음)
├── Interactive Save Profile    ← 대표 흐름 · 사람 SAVE n
├── Trusted Event Profile       ← 비저장 mutation · owner approve 이벤트
└── Hosted Relay Profile        ← 원격 intent 전달 → Interactive 로 수렴
```

- [core-model-v0.1-draft.md](./core-model-v0.1-draft.md) — 공통 어휘·불변식·Request/Event·상태
- [interactive-save-profile-v0.1-draft.md](./interactive-save-profile-v0.1-draft.md)
- [trusted-event-profile-v0.1-draft.md](./trusted-event-profile-v0.1-draft.md)
- [hosted-relay-profile-v0.1-draft.md](./hosted-relay-profile-v0.1-draft.md)
- [implementation-mapping.md](./implementation-mapping.md) — 규격 조항 ↔ 실제 심볼·commit SHA
- [security-limitations.md](./security-limitations.md) — 정직한 한계
- [mgb-crosswalk.md](./mgb-crosswalk.md) — MGB(행위 검증) ↔ Spec(필드 정의) 대응
- [vectors/](./vectors/) — 재현용 test vector (고정 KAT + illustrative)
- [tools/check_vectors.py](./tools/check_vectors.py) — 고정 KAT drift 검증 도구

## 3 프로필 한눈에

| 프로필 | 승인 증명 | canonicalization | 최종 저장 |
| :-- | :-- | :-- | :-- |
| **Interactive Save** | UserPromptSubmit hook 의 SAVE n 기록 | 공백정규화(`_norm`)·preview_ref[:16]·node_id[:8] (NFC/bidi 없음) | `commit_bundle` / `apply_pack_in_txn` |
| **Trusted Event** | owner approve 이벤트(nonce·TTY) | `tae-1 \x1f op \x1f json(NFC)` → sha256 전체 64 | approval 소비(비저장 mutation) |
| **Hosted Relay** | (전달만) → 로컬 SAVE n | intent_id `sha256(text\|indices\|confirm)[:16]` | **Interactive commit_bundle 로 수렴** |

> 세 프로필의 canonicalization 은 **근본적으로 다른 산식**이다. 하나로 통합하지 않고 각각 공개한다. `TAE-1` 하나가 모든 경로를 대표하지 않는다.

## 공통 불변식 (전 프로필)

1. preview / intent 만으로는 저장되지 않는다.
2. 저장된 노드는 `candidate=1, promotion_allowed=0` (= 정본 미승격 개인 기억). "승인 전"이 아니라 "정본으로 승격되지 않음"을 뜻한다.
3. fail-closed 기본값 `actor=reader`.
4. 사람 승인(모드별 상이)만이 write 를 만든다.

## 정직한 경계 (상세: [security-limitations.md](./security-limitations.md))

- **same-host attacker 위협모델 밖** — Filesystem/Bash MCP 를 동반한 호스트에서는 하드 보장 아님.
- **tamper-proof 아님** — 서명/HMAC 미도입(같은 머신 키 = 보안 연극).
- **Hosted 본문 평문 잔존** — commit 후에도 원문이 `_archive/*.processed.json` 에 영구 보존.
- **approve 이벤트 자동생성은 UNSUPPORTED** — TTY 전용이라 공개 CLI/CI 로 재현 불가.

## 재현 (test vector)

- **고정 KAT** (`vectors/kat/`): canonicalization digest 순수함수. approve/hook 불필요·결정적 재계산. `tools/check_vectors.py` 가 CI 에서 drift 를 감지.
- **illustrative-only** (`vectors/illustrative/`): 사람 기원(SAVE n·approve)·실서비스 의존. **CI 로 실제 생성 불가**(UNSUPPORTED). 재현 가이드만 제공.
