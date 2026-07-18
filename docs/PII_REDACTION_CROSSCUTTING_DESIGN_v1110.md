# PII Redaction 횡단 레이어 설계 (v1.11.0 stage0, Track E)

> 상태: **design-only / no code change**. 이 문서는 인터페이스·적용 지점·원칙만 정의한다. 코드는 후속 stage에서.
> 불변식 정합: stdlib-only(`re`) / local-first(네트워크 0) / production write 0 / fail-closed.

> **정본 수렴 현황 (v1.22)**: canonical = `binggupack/safety/pii.py`(facade) → `binggupack.pack.batch_m1`(batch_redact + scan_residual_pii, 정규식 정본 byte-identical 이관). harvest(binggu_harvest.py)·t3_filter·cloud_query_wire가 이 정본을 재사용('복붙 아님' — 3곳 분산 아님). 다만 본 설계 고유 형태(`redact()` 단일함수·`RedactionError` raise·4출구 전면 게이트)는 **미채택**, pii.py의 나머지 6개 진입 함수 이관은 **backlog**. 아래 본문은 원안 설계.

---

## 0. 한 줄 요약

candidate 텍스트가 프로세스 밖으로 나가는 **모든 출구**(저장 경로 · 화면 preview · interactive echo · LLM 입력)에
**단일 redaction 레이어** `redact(text) -> text` 를 강제 적용하고, redaction 자체가 실패하면 텍스트를 내보내지 않고 **차단(fail-closed)** 한다.

---

## 1. 현황 (기존 코드 인용)

PII/secret 탐지·마스킹 로직은 이미 존재하나 **세 곳에 분산**되어 있고, 출구마다 호출 방식이 제각각이다.
단일 진입점이 없어 "새 출구가 생기면 redaction을 빠뜨릴" 구조적 위험이 있다.

### 1-1. `scripts/watcher_batch_m1.py` — PII shape 정본
탐지 패턴 정본. 두 묶음으로 나뉜다.

- `PII_SHAPES` (마스킹 대상): `rrn`(주민번호), `phone_mobile`, `phone_landline`, `credit_card`, `email`, `aws_akia`, `vendor_token`, `bearer_token`, `b64_secret` + `BIZNO_SHAPE`(사업자번호).
- `_SCAN_SHAPES` + `scan_residual_pii(text)` (잔존 탐지 전용, 별도 로직):
  `scan_rrn`, `scan_rrn_nohp`, `scan_mobile`, `scan_landline`, `scan_credit_card`, `scan_email`, `scan_aws`, `scan_vendor`, `scan_bearer`, `scan_kv`, `scan_b64`.

```python
# watcher_batch_m1.py:179
def scan_residual_pii(text):
    ...
    for kind, pat in _SCAN_SHAPES:
        ...   # 독립 scanner — redactor 와 별도 로직(이중 방어)
```

> 핵심 설계 의도(기존): **마스킹 redactor 와 잔존 scanner 를 일부러 분리**해 "redact 후에도 scan 으로 재검증"하는 이중 레이어.

### 1-2. `scripts/openbinggu_incoming_to_staging.py` — `SECRET_PATTERNS` 정본
secret 계열 정규식(`sk-live-`, `ghp_`, `AKIA`, `password|token|api_key`, `private key`, `bearer`, PEM, base64 40+). v0.11 라인부터 재사용되는 secret 탐지 정본.

### 1-3. `scripts/openbinggu_conversation_capture_preview.py` — preview 표면 보강
```python
# :37  hosted 외부 표면용 추가 PII (공용 scanner 는 "도메인 식별자 보존" 정책이라 무수정)
_PREVIEW_PII_EXTRA = [
    ("scan_bizno_fmt",  re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")),
    ("scan_bizno_bare", re.compile(r"(?<!\d)\d{10}(?!\d)")),
]
```
preview 경로는 `scan_residual_pii(sent) + _PREVIEW_PII_EXTRA` 를 합쳐서 검사 → 걸리면 candidate 제외(`excl(...)`).

### 1-4. `scripts/watcher_capture_mvp1.py` — 마스킹 헬퍼
```python
# :43
def redact_text(text):
    """v0.11 SECRET_PATTERNS 로 매치를 [REDACTED:len] 치환. (redacted_text, hit_count) 반환."""
```
`[REDACTED:len]` 마커(길이 힌트만, 복원 불가) 정본. consumer 측(`openbinggu_pack_consumer_smoke.py:36 REDACT_MARK`)은 이 마커를 복원하지 않도록 검증.

### 1-5. 현황 요약 — 문제점
| 출구 | 현재 적용 | 위험 |
| :--- | :--- | :--- |
| 저장(candidate save) | `scan_residual_pii + _PREVIEW_PII_EXTRA` 후 reject | 호출 누락 시 PII 적재 |
| 화면 preview | preview 경로 자체 재스캔 | 경로별 중복 구현 |
| interactive echo (CLI 출력) | **명시적 공통 레이어 없음** | echo 직출력 시 누출 |
| LLM 입력 | **명시적 공통 레이어 없음** | 외부 모델로 원문 전송 위험 |

→ 탐지 자산은 충분하나 **출구 게이트가 출구마다 흩어져 있고 일부 출구는 누락**. 이것을 단일 레이어로 수렴한다.

---

## 2. 단일 레이어를 두는 이유

1. **출구 단일화** — 텍스트가 밖으로 나가는 지점은 4종(저장/preview/echo/LLM)뿐. 이 4종이 모두 같은 함수를 통과하면, 새 출구가 생겨도 "이 함수만 통과시켜라"가 단일 규칙이 된다.
2. **누락 불가능화** — 탐지 정규식이 흩어져 있으면 새 코드가 한두 개를 빠뜨린다. 정본 패턴을 한 곳에서 모아(`watcher_batch_m1` + `incoming_to_staging` + `_PREVIEW_PII_EXTRA` import 재사용) 단일 함수가 전부 적용.
3. **이중 방어 유지** — 기존 "redactor + 별도 scanner" 분리 설계를 레이어 내부에 그대로 흡수: `redact()` 가 마스킹 후 `scan_residual_pii()` 로 자기검증.
4. **fail-closed 일원화** — 차단 정책(잔존 시 STOP)을 출구마다 if 문으로 반복하지 않고 레이어가 책임.
5. **정책 변경 1점 수정** — PII 종 추가/마커 포맷 변경 시 한 파일만 고치면 4출구 동시 반영.

---

## 3. 인터페이스 제안

### 3-1. 핵심 함수
```python
def redact(text: str) -> str:
    """
    입력 text 에서 PII 8종을 [REDACTED:<len>] 으로 치환한 text 를 반환한다.
    - 반환 타입은 항상 str (입력과 동일 타입 계약).
    - 마스킹 후 scan_residual_pii() 로 재검증 → 잔존 시 RedactionError raise (fail-closed).
    - 외부 의존성 0 (stdlib re 만). 네트워크 0. 부작용 0(순수 함수).
    """
```

보조(필요 시):
```python
def redact_report(text: str) -> tuple[str, list[str]]:
    """ (redacted_text, residual_kinds) 반환. residual_kinds 비어있어야 통과."""

class RedactionError(Exception):
    """redaction 후에도 PII 잔존 = fail-closed 차단 신호."""
```

### 3-2. PII 8종 (정본 분류)
기존 분산 패턴을 8개 카테고리로 정규화(내부적으로 정본 정규식 재사용):

| # | 종 (kind) | 매핑 정본 | 마커 예시 |
| :- | :--- | :--- | :--- |
| 1 | `rrn` 주민등록번호 | PII_SHAPES.rrn / scan_rrn(_nohp) | `[REDACTED:14]` |
| 2 | `phone` 전화(휴대/유선) | phone_mobile/landline | `[REDACTED:13]` |
| 3 | `email` 이메일 | email / scan_email | `[REDACTED:18]` |
| 4 | `credit_card` 카드번호 | credit_card | `[REDACTED:19]` |
| 5 | `bizno` 사업자등록번호 | BIZNO_SHAPE / _PREVIEW_PII_EXTRA | `[REDACTED:12]` |
| 6 | `secret_key` API키/토큰/비밀번호 | SECRET_PATTERNS + vendor/bearer/aws/kv | `[REDACTED:40]` |
| 7 | `private_key` 개인키(PEM) | SECRET_PATTERNS PEM | `[REDACTED:n]` |
| 8 | `b64_blob` 고엔트로피 base64 | b64_secret / scan_b64 | `[REDACTED:n]` |

> 마커는 **길이 힌트만**(`[REDACTED:len]`), 원문 복원 불가. consumer 는 복원 금지(기존 정책 §1-4 유지).

### 3-3. 타입·계약 불변식
- 입력 str → 출력 str. None/비문자열은 호출 측 책임(레이어는 str 만 받음).
- 멱등성: `redact(redact(t)) == redact(t)` (이미 마스킹된 `[REDACTED:n]` 은 재매칭 안 됨 — `_REDACT_RE` 보호).
- 순수성: I/O·전역상태·네트워크 0.

---

## 4. 출구별 적용 지점

> 규칙: **밖으로 나가는 마지막 한 줄 직전**에 `redact()` 통과. "최종 출구 게이트" 원칙.

### 4-1. 저장 경로 (candidate save / staging write)
- 지점: candidate sentence 를 파일/DB 로 쓰기 **직전**.
- 현재 `openbinggu_conversation_candidate_save.py:105` 의 `scan_residual_pii + _PREVIEW_PII_EXTRA` reject 로직을 `redact()` 호출로 수렴.
- 동작: `redact()` 통과 텍스트만 저장. `RedactionError` 시 해당 candidate **excl/reject**(기존 `pii_or_secret` 사유 유지). DB 무변경.

### 4-2. 화면 preview (capture_preview / inbox 표면)
- 지점: `capture_preview()` 가 excerpt/sentence 를 사용자 화면 dict 로 반환하기 **직전**.
- `binggu_hosted_inbox.py` 의 excerpt(80자) 등 모든 화면 노출 문자열에 `redact()`.
- 동작: PII 포함 candidate 는 `pii_secret=True` flag(기존) + 노출 문자열 자체도 마스킹되어 출력.

### 4-3. interactive echo (CLI stdout)
- 지점: `binggu_capture_cli.py` 등 CLI 가 candidate 본문을 stdout 으로 print 하기 **직전**.
- 동작: print 대상 문자열을 `redact()` 통과시켜 출력. **현재 명시 레이어 없는 출구 — 신규 게이트 지정.**

### 4-4. LLM 입력 (외부 모델 프롬프트)
- 지점: candidate 텍스트를 LLM 프롬프트 문자열로 조립하기 **직전**(프롬프트 빌더 최종 단계).
- 동작: 원문 대신 `redact()` 결과만 프롬프트에 삽입. `RedactionError` 시 해당 candidate 를 프롬프트에서 **제외**(LLM 호출 자체를 막거나 candidate skip).
- 주: 본 stage0 는 네트워크 0 이므로 실제 LLM 호출 코드는 없음. **이 출구가 생기는 순간 반드시 redact() 를 통과시킨다**는 계약만 박제.

### 4-5. 적용 지점 요약표
| 출구 | 파일(현재) | 게이트 위치 | RedactionError 처리 |
| :--- | :--- | :--- | :--- |
| 저장 | conversation_candidate_save.py | DB/파일 write 직전 | candidate reject(`pii_or_secret`) |
| preview | conversation_capture_preview.py / hosted_inbox.py | dict 반환 직전 | flag=True + 문자열 마스킹 |
| echo | binggu_capture_cli.py | print 직전 | 마스킹 출력(또는 라인 skip) |
| LLM | (후속 stage 프롬프트 빌더) | 프롬프트 조립 직전 | candidate 제외 |

---

## 5. fail-closed 원칙

> **redaction 이 안전을 보장하지 못하면, 텍스트를 내보내지 않는다.** 열려있는 쪽으로 실패하지 않는다.

1. **잔존 시 차단** — `redact()` 가 마스킹 후 `scan_residual_pii()` 재검증에서 PII 가 남으면 `RedactionError` raise. 호출 측은 해당 텍스트를 출구로 내보내지 않음(저장 reject / preview 마스킹·flag / echo skip / LLM 제외).
2. **예외 = 차단** — 정규식 오류·인코딩 오류 등 redaction 도중 예외가 나도 "원문 통과"가 아니라 **차단**으로 귀결. try/except 에서 원문을 fallthrough 시키지 않는다.
3. **이중 방어** — 마스킹 redactor 와 잔존 scanner 의 로직을 분리 유지(기존 설계 계승). redactor 가 놓쳐도 scanner 가 잡고, scanner 가 잡으면 차단.
4. **복원 금지** — `[REDACTED:len]` 은 길이 힌트만. 어떤 출구도 원문 복원 정보를 함께 내보내지 않는다.
5. **selftest 격리** — 본 레이어 selftest 는 운영 home(`~/.binggupack`) 미변경, `BINGGU_HOME=임시격리` 로만. 순수 함수라 파일 I/O 자체가 없어 production write 0 자연 충족.

---

## 6. 불변식 정합 점검

| 불변식 | 충족 방식 |
| :--- | :--- |
| stdlib-only | `re` 만 사용. pyproject 의존성 추가 0. |
| local-first | 패턴 매칭만, 네트워크 호출 0. |
| production write 0 | 순수 함수 `redact()` 는 I/O 없음. selftest 는 임시격리. |
| fail-closed | §5 — 잔존/예외 모두 차단. |
| candidate-first | 저장 출구에서 PII candidate 는 reject, 자동 승격 없음. |

---

## 7. 후속 stage 작업(이 문서 범위 밖)
1. `scripts/binggu_pii_redact.py`(가칭) 신설 — 정본 패턴 import 재사용으로 `redact()` 구현.
2. 4출구에 게이트 삽입(§4) — 기존 분산 호출을 `redact()` 로 수렴.
3. `binggu_pii_redact_selftest.py` — 8종 마스킹·멱등성·fail-closed·잔존차단 검증(BINGGU_HOME 격리).
