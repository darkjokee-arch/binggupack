# BingguPack 아키텍처 (코어 구조)

> 이 문서는 코드 구조를 처음 읽는 개발자를 위한 지도입니다. 제품 개념은 [README](../README.md)와
> [START_HERE](START_HERE.md)를, 안전 경계는 [GOVERNANCE](BINGGUPACK_GOVERNANCE_DESIGN.md)를 보세요.

## 한눈에

BingguPack은 **로컬 우선 개인 기억 엔진**입니다. 원본 기억은 내 PC의 `ledger.sqlite`에 있고,
자동 영구 저장은 없으며, 사람이 승인한 것만 남습니다.

코드는 세 층으로 나뉩니다.

| 층 | 위치 | 역할 |
|---|---|---|
| **진입점** | `binggu.py` · `python -m binggupack` | CLI 명령 파싱·디스패치 |
| **코어 로직(정본)** | `binggupack/` 패키지 | 순수 판정·변환·스키마·안전 규칙 |
| **오케스트레이션/도구** | `scripts/` | 파일 I/O·운영 실행·CLI selftest·hosted/opencrab 연동 |

## `binggupack/` 패키지 레이아웃

| 서브패키지 | 담는 것 |
|---|---|
| `cli/` | 대화형 저장(interactive_save) |
| `capture/` | 캡처 buffer·preview·session·CLI |
| `classifier/` | 저장 후보 분류(capture_classifier)·canonical label_kind 매핑 |
| `policy/` | match policy(중복·근접 판정) |
| `schema/` | 동사형 엣지 6종 스키마(verb_edge) |
| `storage/` | 로컬 스토리지 스키마·경로 |
| `pack/` | 팩 코어 — 그래프(knowledge_graph)·수집(collection/ingest)·랭킹(p1_ranking)·회상(recall)·엣지(pack_edges/edge_mvp21)·PII 배치(batch_m1)·근거 추천(rationale_suggest)·공개차단 판정(scope_envelope)·merkle·person sync 등 |
| `safety/` | 안전 경계 — PII(pii)·경로안전(path_safety)·거버넌스(confirmed_governance)·SAVE 게이트(save_gate/gate_log)·과거사 필터(t3_filter)·서명(sign_util)·공개 트리 스캔(public_tree_scan) |
| `review/` | 검토 라우팅(resolver)·검토 샌드박스(resolver_sandbox)·reviewed plan preview·세션 마감(session_close) |
| `mcp/` | MCP 서버 핸들러(server_handlers)·경로 게이트(path_gate_adapter) |
| `workspace/` | 작업공간 정리(organize)·플랫폼(platform) |

## strangler 이관 패턴 (진행 중)

레거시 코드는 `scripts/openbinggu_*.py`·`scripts/watcher_*.py`·`scripts/binggu_*.py`에 있었습니다.
이를 **한 번에 갈아엎지 않고**, 순수 로직만 `binggupack/` 정본으로 옮기고 원본은 얇은 위임(shim)만
남기는 strangler-fig 방식으로 점진 이관 중입니다.

- **정본(binggupack/)** = 파일 I/O·`__file__` 경로·store 접근이 없는 **순수 판정/변환/스키마/규칙**.
- **shim(scripts/)** = 정본을 `import *` + 명시 re-export 하는 backward-compatible wrapper.
  기존 호출처(`from openbinggu_x import fn` / `import watcher_x as m`)는 **그대로 동작**합니다.
- **오케스트레이션 잔류** = `build_pack`처럼 그 자체가 파일 I/O 본체인 코드는 억지로 옮기지 않고
  scripts/에 둡니다(순수 코어가 빈약하면 이관하지 않는 것이 정답).

이관된 각 정본은 `--selftest` GATE(GO/NO-GO)와 `shim === 정본` 동일 객체(identity) 검증,
그리고 전체 회귀 게이트를 통과한 뒤에만 반영됩니다.

## 검증 게이트

```bash
# 전체 selftest 회귀 (핵심 게이트)
python scripts/binggu_publish_run_all_selftests.py     # → 30/30 PASS · REGRESSION=GO

# 공개 트리 PII/경로 스캔
python scripts/openbinggu_public_tree_scan.py --tree . --public   # → CLEAN

# 정적 검사(F 계열)
python -m ruff check scripts/ binggupack/ --select F --statistics
```

## 안전 불변 (코드로 강제)

- **자동 영구 저장 0** — AI·hook·hosted 경로는 후보만 만든다. 확정은 사람 `SAVE n`.
- **PII/secret 차단** — 후보 단계에서 마스킹·차단(`safety/pii`, `safety/t3_filter`).
- **공개 fail-closed** — 마스킹 불명/미승인은 전부 차단(`pack/scope_envelope` publish guard).
- **로컬이 정본** — 원본 기억은 `ledger.sqlite`. hosted/클라우드는 보조 경로.

자세한 경계는 [GOVERNANCE_DESIGN](BINGGUPACK_GOVERNANCE_DESIGN.md) ·
[HOSTED_BOUNDARY](BINGGUPACK_HOSTED_BOUNDARY.md) · [헌법](BINGGUPACK_CONSTITUTION_2026-06-17.md)을 보세요.
