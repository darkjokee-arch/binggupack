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

---

## 9. R2 4cli 착수 게이트 (2026-06-13 확정 — 이 조건 전부 충족 전 shadow PoC 착수 금지)

> 4cli R2 판정 = **REFINE**(영구 BLOCK 0). 전략 논점(할 가치/본업 우선)은 owner 지시로 제외, 안전조건만.

**핵심 불변 (B 요약):** *원문이 LLM·로그·예외·디스크 어디에도 남지 않고, 실패가 항상 rule fallback 으로 닫히는 구조.*

| # | 조건 | 출처 |
|---|---|---|
| 1 | L1 PII/secret 정규식을 **embedding/LLM 입력 직전 재적용 어서션** — hit 이면 호출 skip→rule + leak_guard 카운터 | A·C |
| 2 | shadow log **전문 저장 금지** — text_sha8·len·rule_label·sem_label·conf·reason_codes·latency·model_digest·band 만 | A·B |
| 3 | L3 `reason_codes` **enum 고정**, 위반/JSON 깨짐 → 무조건 rule 강등 + enum_violation 카운터·알람 | A·B·C |
| 4 | **shadow log 역추적 방어** — 짧은 원문은 해시 brute 복원 가능 → `text_sha8` 에 **salt+HMAC**(해시도 평문 아님) | C |
| 5 | **Ollama 자원경합 통제** — §10 실측 기반: L3 기본 OFF·max concurrency=1·rate cap·timeout→rule | A·B·C·D |
| 6 | **비결정성 게이트** — temp=0 + seed 고정 + N회 일치율 게이트(없으면 drift regression 자체가 거짓) | C |
| 7 | **L1 런타임 우회 방지** — 파일 byte-diff 0 뿐 아니라 **호출스택 단위 동등성 어서션**(모든 경로에서 L1 선실행 보장) | C |
| 8 | **재현성 스냅샷** — model_digest + prompt_hash + enum_version + threshold/band config + classifier_build_hash | B |
| 9 | **메트릭 분리** — shadow 지연/에러가 운영 관측 지표를 오염시키지 않게 메트릭 파이프라인 논리 분리 | D |
| 10 | LLM/embedding 호출 = **leak_guard wrapper 단일 경유 강제**(밖 직접호출 금지) | B |
| 11 | golden = 6라벨 클래스 균형 + **애매 band 중심**(rule fallback_judgment 문장) + 판단 편향 금지 + PII 함정 | A·B·C |
| 12 | leak=0 = golden PII 함정 L1 제외 100% ∧ shadow.jsonl 원문/PII 0 ∧ LLM 입력 PII 카운터 0 | A·B |
| 13 | default 승격 = 코드 **hard-disable**, (골든회귀 ∧ drift 0 ∧ leak 0 ∧ opt-in 데이터) 전 영구 잠금 | A·C |
| 14 | shadow.jsonl retention·chmod/ACL·rotation·secret scanner 통과 | B |
| 15 | 검증 = leak scan + byte-diff + 호출스택 동등성 + golden regression + drift check **단일 selftest** | B·C |
| 16 | PoC 범위 = 저장 0 · ledger write 0 · preview 출력 영향 0 · shadow report only | A |

---

## 10. Ollama 자원경합 실측 (2026-06-13, 코드 0·관찰만)

**환경:** RTX 4070 SUPER **12GB(단일 GPU)** · Ollama 11434 = **127.0.0.1 only**(외부 0) · 클라이언트 = `pajae_rag_server.py`(박제 RAG, bge 공유) · `safety-app` Ollama 미사용(grep 0)·bid-engine established 클라이언트 0.

| 항목 | 실측 | 함의 |
|---|---|---|
| qwen2.5:14b VRAM | **9.7GB** (GPU 상주) | bge 동시 GPU 상주 불가(여유 851MB) |
| bge-m3 배치 | **CPU 추론**(GPU 여유 부족 → ollama 자동 분리) | **qwen 안 밀어냄 = 역할 분리(qwen=GPU, bge=CPU)** |
| bge embed warm | p50 **148ms** · p95 201ms (cold 2.8s=로드) | CPU치고 충분히 빠름 |
| bge 동시 3 | 각 ~160ms · **wall 171ms ≈ 병렬**(순차 380ms 대비) | queue 직렬화 경미 — L2 shadow 경합 낮음 |
| qwen JSON generate | **810ms**(GPU·JSON 유효·temp0 seed42) | 짧은 분류엔 빠르나 GPU 9.7GB 점유 |
| qwen keep-alive | 만료 시 **언로드**(GPU 회수) → 다음 호출 cold 재로드 | L3 드물게 부르면 매번 cold 비용 |

**자원경합 위험 판단:**
- **L2(bge)** = CPU 추론·동시 거의 병렬 → **GPU 경합 0, 위험 낮음**. pajae_rag 와 bge 큐만 공유(경미).
- **L3(qwen)** = GPU 9.7GB 점유가 경합의 원천. 자주 부르면 다른 GPU 작업과 충돌, idle 시 언로드.
- 본업: safety-app 미사용 확인 / bid-engine LLM 클라이언트 미관측(GPU 사용 여부는 별도 확인 권장).

**권장 운영 분리안 (실측 기반):**
1. **L3(qwen) 기본 OFF, L2(bge) shadow 우선** — 경합 원천이 qwen GPU 점유이므로. embedding-only shadow 면 GPU 거의 무접촉. ✅ 1순위
2. **max concurrency=1 + rate cap** — pajae_rag bge 큐와 공유하므로 폭주 차단. ✅
3. **timeout(L2 300ms·L3 4s) + 실패→rule fallback**. ✅
4. **본업 시간대 pause**(bid-engine GPU 사용 확인 후 우선순위 결정).
5. **별도 인스턴스/포트는 실익 낮음** — GPU 1장이라 인스턴스 분리해도 같은 GPU 경합. **rate/concurrency 제어 + L3 OFF 가 정답**(포트 분리보다 효과적). 단 `OLLAMA_NUM_PARALLEL` 직렬화는 활용 가능.

**결론:** shadow PoC 를 **L2(embedding)-only 로 시작**하면 자원경합 위험은 낮다(bge CPU·병렬·GPU 무접촉). L3(qwen)은 기본 OFF 로 두고, 켤 때만 GPU 점유·concurrency=1·rate cap 적용. §9 게이트의 #5 가 본 실측으로 구체화됨.

---

## 11. L2-only shadow PoC 최소범위 (착수 후보 — 다음 GO 대상, 본 문서는 범위 정의만·코드 0)

§10 실측에서 L2(bge embedding)-only 가 자원경합 위험이 낮음이 확인됨. 첫 PoC 는 **L3(qwen) 없이 L2 만**으로 좁혀, R2 16게이트의 절반을 L3 도입 시로 연기한다.

### 11-1. 목적
- **rule classifier 동작 변화 0** — 기존 분류·preview·저장 경로 그대로.
- rule 이 `fallback_judgment` 로 뭉개는 문장의 **의미 라벨 후보를 shadow 로만 기록**(어느 seed 라벨에 가까운지 + 유사도).
- **preview / ledger / save 영향 0** — 추천을 사람에게 표시조차 안 함(로깅 전용).

### 11-2. L2-only 에서 필수인 R2 게이트
| 게이트 | 내용 |
|---|---|
| L1 hard gate 선적용 | bge 호출 **직전** 정규식(`scan_residual_pii`·`SECRET_PATTERNS`·`_PREVIEW_PII_EXTRA`) 재적용 — hit 이면 호출 skip→rule + leak_guard 카운터 |
| salt+HMAC shadow id | shadow 레코드 식별자 = `HMAC(salt, text)` — 원문/평문 해시 역추적 차단 |
| concurrency=1 + rate cap | bge 호출 직렬화(pajae_rag 큐 공유 보호) + 호출 간 최소 간격 |
| model name/digest 고정 | `bge-m3` + ollama digest 매 레코드 기록, digest 변경 시 골든 재평가 트리거 |
| 호출스택 동등성 | 파일 byte-diff 0 뿐 아니라 런타임에서 L1 이 모든 경로 선실행됨을 selftest 로 어서션 |
| golden / leak=0 / default 잠금 / 저장0 | golden(클래스 균형·애매 band 중심·PII 함정) · leak=0(PII 함정 L1 제외 100% ∧ shadow.jsonl 원문 0) · default 승격 코드 hard-disable · 저장/ledger/preview 0 |

### 11-3. L3 로 연기되는 것 (이번 PoC 범위 밖)
- qwen / local LLM 호출 일체
- `reason_codes` enum + enum 위반 rule 강등
- JSON schema 강제 / 파싱 실패 처리
- `prompt_hash` 재현성 스냅샷(프롬프트가 없으므로)
- LLM 로그·프롬프트 평문 잔류 통제(qwen 미사용이라 해당 없음)
- L3 GPU 점유·keep-alive·cold 재로드 관리

> bge embedding 은 deterministic(temperature 무관) → R2 #6 비결정성 게이트는 L2 에서는 model_digest 고정만으로 충족, temp0/seed/N회 일치율은 L3 도입 시 적용.

### 11-4. 최소 산출물
- `scripts/binggu_semantic_shadow.py` — leak_guard wrapper → bge embed(CPU) → 6라벨 seed cos 유사도 → band 판정 → shadow logger + selftest
- seed 6라벨 (판단·교훈·운영결정·사용자선호·설계결정·버그패턴) — **owner 검수 후 작성**(본 단계에서는 미작성)
- golden fixture (`tests/fixtures/semantic/golden.jsonl`)
- 단일 selftest (leak scan + 정규식 byte-diff/호출스택 동등성 + golden + 저장0)
- `~/.binggupack/semantic/shadow.jsonl` — **원문 없이** `hmac_id·len·rule_label·sem_label·sem_conf·latency_ms·model_digest·band` 만(ACL)

### 11-5. 금지 (불변)
- 기존 정규식 classifier(`classify_label_kind`·`SECRET_PATTERNS`·`scan_residual_pii`·`_PREVIEW_PII_EXTRA`·`classify_node`) **변경 금지**
- preview 표시 변경 금지 · ledger write 금지 · 자동 저장 금지 · **L3(qwen) 호출 금지**

**상태:** 범위 정의 완료. 구현·seed 작성은 **별도 GO** 후. 정규식 무변경·저장0·preview0 유지.
