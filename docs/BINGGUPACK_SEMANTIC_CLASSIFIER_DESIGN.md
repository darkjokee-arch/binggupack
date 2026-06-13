# BingguPack — Semantic Capture Classifier 설계 (DRAFT, 2026-06-13)

> **설계만 — 코드 0.** 구현은 owner 별도 GO 후. 기존 정규식은 **건드리지 않는다**(§7 확약).
> 원칙: 자동 저장 아님 · semantic 은 후보 추천만 · secret/PII hard block 은 정규식이 항상 최우선 ·
> 원문 전문 저장 금지 · 최종 저장은 preview 후 `SAVE n` 게이트만.

---

## 0. 문제 (왜)

현재 capture 분류는 `openbinggu_label_kind_map.classify_label_kind` = **5종 정규식 + 판단 fallback**:

| 단계 | 모듈 | 동작 |
|---|---|---|
| PII/secret 제외 | `watcher_batch_m1.scan_residual_pii` · `incoming_to_staging.SECRET_PATTERNS` · `capture_preview._PREVIEW_PII_EXTRA` | 후보에서 **제외**(hard) |
| 도장 분류 | `label_kind_map.classify_label_kind` | 증거/문서/개념/상태 명확 패턴만 → 아니면 **전부 "판단" fallback** |
| 헌법 판정 | `a0_node_dryrun.classify_node` | A0 verdict |

**한계:** `_RULES` 5개에 명확히 매칭 안 되면 `fallback_judgment`. 즉
- 애매한 교훈/운영결정/사용자 선호/설계결정/버그패턴이 전부 "판단"으로 뭉개짐(라벨 해상도 낮음).
- "이 문장이 **저장할 가치가 있나**(should_capture)" 판단 자체가 없음 — preview 는 의미문장이면 다 후보로.

→ semantic 레이어로 **라벨 해상도 + should_capture 추천**을 더한다. **단 추천만** — 저장 결정은 사람 게이트.

---

## 1. 파이프라인 (4 레이어, deny/hard 최우선)

```
입력 문장
  │
  ▼
[L1 rule layer] ──(secret/PII/block 매칭)──▶ HARD EXCLUDE (최우선·무조건·정규식)
  │  (명시 저장 문구·preview trigger 도 여기서 hard 표시)
  ▼ (통과한 문장만)
[L2 embedding layer] 로컬 임베딩 → seed 라벨군과 cos 유사도
  │   score 명확(≥hi) → 라벨 확정 추천          ─┐
  │   score 낮음(≤lo) → should_capture=false 추천 ─┤→ L3 건너뜀
  ▼   score 애매(lo<s<hi) ────────────────────────┘
[L3 local LLM layer] (애매 band 만 호출) → JSON{label_kind, confidence, reason_codes, should_capture}
  │
  ▼
[추천 병합] rule baseline(label_kind_map) + semantic 추천을 **나란히** preview 에 표시
  │   (불일치 시 양쪽 다 보여줌 — 사람이 판단)
  ▼
preview → 사람이 SAVE n → 기존 게이트(save_selected) commit
```

**불변 규칙**
- L1 은 **항상 먼저, 항상 정규식**. semantic 이 "저장 추천"해도 L1 이 secret/PII 면 **무조건 제외**(semantic 이 hard gate 를 못 뒤집는다).
- L2/L3 출력은 `recommendation` 필드일 뿐 — ledger write 0, 자동 저장 0.
- L3 는 **애매 band 만** 호출(비용·지연 절감). band 밖은 L2 로 끝.
- 모델 없음/실패 → **L1+기존 rule classifier 로 그대로 동작**(capture 전체가 멈추지 않음).

---

## 2. 모델 후보

### 2-1. 임베딩 (L2)
| 후보 | 실행 | 한국어 | 1인 비개발자 부담 | 평가 |
|---|---|---|---|---|
| **Ollama `bge-m3`** | 로컬 11434 (이미 가동) | 강함(다국어) | **추가 설치 0**(ollama pull 1회) | **1순위** — 인프라 재사용 |
| Ollama `nomic-embed-text` | 로컬 11434 | 보통(영어 위주) | 0 | 2순위(한국어 약함) |
| fastembed(onnx) `multilingual-e5-small` | pip + onnxruntime | 양호 | pip 의존 +1 | 대안(Ollama 불가 환경) |
| sentence-transformers `paraphrase-multilingual-MiniLM` | pip + torch | 양호 | torch 무거움 | 비추(1인 Windows) |

→ **bge-m3 via Ollama**. CLAUDE.md 에 Ollama(11434, qwen2.5:14b) 이미 상주 → 신규 런타임 0.

### 2-2. 로컬 LLM (L3)
| 후보 | 실행 | JSON 강제 | 평가 |
|---|---|---|---|
| **Ollama `qwen2.5:14b-instruct-q4_K_M`** | 로컬(이미 있음) | `format=json` + schema | **1순위**(보유 모델) |
| Ollama `qwen2.5:7b` | 로컬 | 동일 | 지연 민감 시 |
| Ollama `gemma2:9b` | 로컬 | 동일 | 대안 |

→ 보유 모델 재사용. `format:"json"` + 프롬프트에 JSON schema 명시 + 파싱 실패 시 L2 추천으로 강등.

**외부 API(OpenAI/Gemini) 영구 미사용** — 프라이버시(원문이 외부로 나가면 안 됨) + §3-4 외부 의존 정합.

---

## 3. 오프라인/로컬 실행 방식

- 전 레이어 **localhost only**. 외부 네트워크 호출 0(프라이버시 hard 요구).
- 호출 경로: `http://127.0.0.1:11434/api/embeddings`, `/api/generate`(format=json). UA 고정 불필요(로컬).
- **가용성 프로브**: capture 시작 시 `/api/tags` 1회 — 모델 없으면 semantic OFF flag, L1+rule 로 동작(로그만).
- **타임아웃**: L2 ≤300ms, L3 ≤4s. 초과 시 해당 문장은 rule 추천으로 강등(개별 fail-soft, 전체 중단 0).
- seed/임계/캐시는 `~/.binggupack/semantic/`(로컬). 임베딩 결과는 text_sha 키로 캐시(재계산 회피).

---

## 4. 성능·지연·프라이버시 tradeoff

| 축 | rule only(현행) | +L2 embedding | +L3 LLM(애매만) |
|---|---|---|---|
| 지연/문장 | <1ms | ~20–80ms | band 히트 시 +0.5–3s |
| 정확도(라벨 해상도) | 낮음(판단 뭉개짐) | 중상 | 상 |
| should_capture | 없음 | 있음(score) | 있음(근거 reason_codes) |
| 프라이버시 | 로컬 | 로컬 | 로컬(원문 LLM 전달, 외부 0·저장 0) |
| 1인 운영 부담 | 0 | ollama pull 1 | 보유 모델 |
| 실패 내성 | — | fail-soft→rule | fail-soft→L2→rule |

- capture hook 은 **async**(settings.json `"async": true`) → 사용자 입력 지연에 영향 0. L3 band 히트율을 낮게(예 10–20%) 유지해 평균 지연 관리.
- **프라이버시 핵심**: 원문은 로컬 모델까지만. ledger 는 여전히 ≤80자 발췌 + L1 이 secret/PII 제거. semantic 이 프라이버시를 **악화시키지 않음**(외부 전송 0).

---

## 5. selftest / eval fixture 설계

### 5-1. 골든셋 (`tests/fixtures/semantic/golden.jsonl`)
각 항목 `{text, expect_label, expect_should_capture, is_pii_secret, note}`:
- 6 라벨 양성: 판단·교훈·운영결정·사용자선호·설계결정·버그패턴 (각 ≥15문장, 명확/애매 혼합)
- 음성(저장 가치 낮음): 잡담·단순상태중계·코드 diff 라인·인사 (should_capture=false)
- **함정**: PII/secret 포함하되 의미상 "저장하고 싶어 보이는" 문장(예: "이 API key 꼭 기억: sk-...") → **L1 이 무조건 제외**해야(leak=0 검증의 핵심)

### 5-2. metric (eval 스크립트, temp 전용)
| metric | 목표 |
|---|---|
| label accuracy (vs golden) | rule baseline 대비 **+Δ 개선** |
| should_capture P/R | precision 우선(잡음 저장 억제) |
| **PII/secret leak rate** | **0 (hard)** — semantic 추천이 hard gate 우회 0 |
| 원문 전문 저장 | 0 (DB blob 에 full text 부재) |
| latency p50/p95 | p50 < 100ms, p95(L3 히트) < 4s |
| L3 호출률 | < 25%(band 폭 튜닝 지표) |

### 5-3. selftest(모듈 자체, mock·temp)
- L1 우선순위: secret/PII 문장은 semantic score 와 무관하게 EXCLUDE (mock 임베딩이 "저장 추천"해도 제외)
- 모델 다운: 프로브 실패 → semantic OFF, rule 결과와 동일(회귀 0)
- 타임아웃: L2/L3 지연 주입 → fail-soft 강등, crash 0
- JSON schema 위반: L3 malformed → L2 추천으로 강등
- 캐시: 동일 text 2회 → 임베딩 1회 호출
- 운영 store 불변 · 원문 DB 미저장

---

## 6. 단계별 rollout

| 단계 | 동작 | 게이트 |
|---|---|---|
| **shadow** | semantic 판정을 **로깅만**(`~/.binggupack/semantic/shadow.jsonl`), 실제 capture 동작 = rule 그대로. preview 표시 변화 0 | 셀프 비교 리포트(N건) 후 다음 |
| **opt-in** | `binggu capture --semantic` 또는 config flag ON 시에만 preview 에 semantic 추천 **표시**(rule 과 나란히). 저장 게이트 불변 | 사용 만족 + leak=0 확인 후 |
| **default** | 기본 ON. **rule fallback 영구 유지**(모델 없으면 자동 rule). 끄기: `--no-semantic` | shadow+opt-in 데이터로 임계 확정 |

- 각 단계 **저장 게이트(SAVE n)는 불변** — rollout 은 "추천 표시 범위"만 넓힌다.
- default 가 되어도 secret/PII 는 정규식 L1, 저장은 사람 게이트.

---

## 7. 어떤 파일을 바꾸나 (기존 정규식 무변경 확약)

### 신규(추가만)
- `scripts/binggu_semantic_classify.py` — 4 레이어 오케스트레이션 + Ollama 클라이언트 + fail-soft + selftest
- `scripts/binggu_semantic_seeds.py` (또는 json) — 6 라벨 seed 문장 + 임계
- `tests/fixtures/semantic/golden.jsonl` — eval 골든셋
- `scripts/binggu_semantic_eval.py` — eval(temp 전용, shadow 비교)
- `docs/BINGGUPACK_SEMANTIC_CLASSIFIER_*.md` — 본 설계 + 결과

### 수정(최소·옵션 훅, flag OFF 면 기존과 byte-identical 동작)
- `openbinggu_conversation_capture_preview.py` — semantic 추천 **주입점 1곳**(flag OFF 시 호출 0, 기존 경로 불변). candidate dict 에 `semantic_reco`(옵션 필드) 추가 표시만.
- `binggu.py` — `capture` 에 `--semantic/--no-semantic` 플래그 + shadow 서브명령(읽기 전용 리포트).

### **무변경(절대 안 건드림) — rule layer 정본**
- `openbinggu_label_kind_map.py` (`classify_label_kind`, `_RULES`) — baseline 으로 **호출만**
- `watcher_batch_m1.py` (`scan_residual_pii`)
- `openbinggu_incoming_to_staging.py` (`SECRET_PATTERNS`)
- `openbinggu_conversation_capture_preview.py` 의 PII/secret 제외 블록(`_PREVIEW_PII_EXTRA`·secret 루프) — L1 으로 **그대로 선실행**
- `openbinggu_a0_node_dryrun.py` (`classify_node`) — A0 판정 불변
- 저장 게이트(`save_selected`·`process_outbox`) — 불변

→ **정규식 hard gate 는 한 줄도 수정하지 않는다.** semantic 은 그 위에 얹는 추천 레이어이고, L1(정규식) 이 항상 먼저·항상 최종 거부권을 가진다.

---

## 8. 미해결/owner 결정 대기

- seed 문장 6 라벨 초안 = owner 검수 필요(빙구 실제 어휘 반영).
- L2 hi/lo 임계, L3 band 폭 = shadow 데이터로 보정(초기값은 보수적으로 L3 호출률 낮게).
- ~~bge-m3 pull 용량/RAM~~ → **실측 완료(2026-06-13): Ollama 11434 가동, `bge-m3:latest`(1.2GB)·`qwen2.5:14b-instruct-q4_K_M`(9.0GB)·`qwen2.5:32b`(19.9GB) 전부 보유 → 신규 pull/런타임 0.** L3 는 14b 기본, 정확도 필요 시 32b 선택지.
- shadow 로깅도 원문 발췌가 로컬 파일에 남음 → 보존 TTL/암호화 여부 owner 결정(현 ledger 와 동일 정책 제안).

**다음(별도 GO):** seed/임계 초안 → shadow 모듈 PoC(읽기 전용, 저장 0) → eval 골든셋 → 비교 리포트.
