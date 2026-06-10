> OpenBinggu is the legacy/internal codename for BingguPack.

# BingguPack — Promotion Preview 설계 (read-only)

> 도구: `scripts/openbinggu_promotion_preview.py` · selftest 12/12 기대.
> **이 도구는 preview/plan 전용입니다. 어떤 write도 하지 않으며, 승격 실행기는 이 RC에 포함되지 않습니다.**

## 1. 무엇인가

batch pack(candidate)을 자기 로컬 **운영형 그래프 DB**로 승격(promotion)하기 전에:
- 어떤 변환(D1~D4)이 적용되는지
- 기존 그래프와 id 충돌이 있는지
- FTS 색인에 무엇이 추가될 계획인지
- backup/rollback을 어떻게 준비해야 하는지
- 실행한다면 몇 row가 write될지

를 **미리 보여주는** 도구입니다. target DB는 항상 `mode=ro`(read-only)로만 열고,
INSERT/UPDATE/DELETE 코드 자체가 없습니다. `OPENBINGGU_OPERATING_DB` 미지정 시
synthetic temp DB로 시연하므로 사용자 데이터 없이도 전 과정을 체험할 수 있습니다.

candidate-first 원칙은 preview에서도 유지됩니다: 승격 계획상 모든 노드는
`candidate=1`·`promotion_allowed=0`이며, `confirmed`는 계획에도 등장하지 않습니다
(confirmed는 사람 승인 기반 별도 단계).

## 2. 변환 규칙 (D1~D4)

| ID | 규칙 |
|---|---|
| **D1** | `label` = `sentence` **80자 절단** (80자 이하면 원문 유지) |
| **D2** | `space` = node_type 자동 매핑(Claim→claim, Document→resource, Evidence→evidence, Concept→concept) / `label_kind` = pack properties 값 / `domain` = 호출자가 1개 지정(`D1`~`D99`) |
| **D3** | `verb` = relation→동사 매핑표(아래) / `edge_sentence_ko` = pack properties.sentence 우선, 없으면 매핑표 기반 자동 생성 / **미등록 relation·node_type = fail-closed 전체 STOP** (부분 계획 금지) |
| **D4** | FTS `node_search.domain_title`은 **NULL 유지** — domain 값은 `nodes.domain` 컬럼에만 기록 (의미 필드 확장은 별도 단계) |

D3 매핑표 (단일 기준):

| relation | verb |
|---|---|
| contains | 포함한다 |
| describes | 설명한다 |
| supports / supports_judgment / evidence_supports | 뒷받침한다 |
| contradicts | 모순된다 |
| depends_on | 의존한다 |
| blocks | 차단한다 |
| enables | 가능하게 한다 |
| refines | 정밀화한다 |

## 3. target schema contract (운영형 그래프 최소 계약)

preview가 대조하는 대상 DB의 최소 스키마:

- `nodes(id PK, space, node_type, label, domain, label_kind, sentence, candidate, evidence_status, promotion_allowed, json)`
- `edges(id PK, source, target, relation, verb, edge_sentence_ko, candidate, promotion_allowed, json)`
- `evidence(evidence_id PK, domain, kind, source_path, note, promotion_allowed, json)`
- FTS5(**contentless**, `content=''`): `node_search(id,label,sentence,domain_title)` / `edge_search(id,edge_sentence_ko,verb,relation)` / `evidence_search(evidence_id,note,source_path)`

## 4. contentless FTS 검증법 ⭐

`content=''` FTS5는 **색인만 저장하고 컬럼 값을 저장하지 않습니다**. 따라서:
- `SELECT id FROM node_search ...` 류 값 되읽기는 **항상 NULL** (`typeof()`까지 null) — "값이 안 보인다"는 버그가 아니라 설계입니다.
- 승격 후 FTS 정합 검증은 값 읽기가 아니라 다음 3종으로 합니다:
  1. **count** — FTS row 수 == 본 테이블 row 수
  2. **MATCH** — 새 콘텐츠 키워드로 `WHERE node_search MATCH '<키워드>'` 검색이 hit
  3. **rowid join** — `SELECT id FROM nodes WHERE rowid = <MATCH 결과 rowid>` 로 검색 결과가 올바른 본 테이블 row에 매핑되는지 확인
- contentless 테이블은 일반 DELETE를 지원하지 않으므로 승격은 **순수 INSERT** 흐름과 잘 맞습니다.

## 5. fail-closed 규칙

다음 중 하나라도 검출되면 **전체 STOP** (부분 계획·부분 승격 금지):
- manifest가 `pack_type=candidate` + `promotion_allowed_default=false`가 아님
- 미등록 relation / 미등록 node_type / `label_kind` 누락 / edge `evidence_refs` 빈값
- PII/secret 잔존 재스캔 검출 (kind만 출력, raw 미출력)
- target과 id 충돌 1건 이상 → verdict **NO-GO** (실행 단계라면 전체 중단 대상)

## 6. preview 출력 항목

pack 요약 → D1 변환 샘플(id·길이만) → D2/D3 적용 계획 → target 대조(본 테이블·FTS count, id 충돌) → FTS insert 계획(D4 NULL 명시) → backup 계획(승격 직전 파일 copy + checksum 대조) → rollback 계획(snapshot 복원 + checksum·count 원복 확인) → 실행 시 write될 row 수(단일 transaction 권장).

## 7. 이 RC에 포함되지 않는 것 (별도 단계·별도 승인)

- **승격 실행기**(운영 DB write) — preview의 plan을 실제 적용하는 도구는 비공개/별도 결정
- confirmed 생성·승급 / tombstone·supersede(운영 UPDATE/DELETE)
- OpenCrab finalize/upload/apply · Neo4j
- 자동수집 hook/daemon

## 8. 사용법

```bash
python scripts/openbinggu_promotion_preview.py --selftest      # 12/12 PASS GATE=GO 기대
# 자기 pack을 synthetic target으로 preview:
python scripts/openbinggu_promotion_preview.py --pack-dir <batch_pack 디렉터리> --domain D10
# 자기 운영형 DB와 대조(read-only로만 엽니다):
OPENBINGGU_OPERATING_DB=<자기 DB 경로> python scripts/openbinggu_promotion_preview.py --pack-dir <dir> --domain D10
```
