# BingguPack App Path — Read-only Pack Service Core (v1.21-A)

미래의 HTTPS MCP / `@BingguPack` 앱이 호출할 **transport-independent read-only core**. HTTP/MCP JSON-RPC/OAuth/Cloudflare/운영 ledger/hosted worker/approval provider 에 의존하지 않는다. transport 는 이후 PR(v1.21-B/C/D)이 이 core 를 호출한다.

구현: `binggupack/app/read_core.py` (`PackRepository`, `PackService`) · `binggupack/app/models.py` (상수/에러) · `binggupack/app/conformance.py` (harness).

## 5 tools

| tool | 입력 | 출력 |
|---|---|---|
| `list_packs` | cursor?, limit(기본 20·최대 100) | `{schema_version, packs:[{pack_id,title,counts,updated_at,pack_type,status}], next_cursor, invalid_pack_count}` |
| `get_pack_summary` | pack_id(exact) | `{pack_id,title,manifest_summary,status,risk_level,counts,topics(≤10),candidate_note,evidence_coverage}` |
| `search_evidence` | pack_id, query(2~200자), limit(기본 5·최대 20) | `{hits:[{evidence_id,sentence_excerpt(≤200),score,candidate}]}` |
| `lookup_node_edges` | pack_id, node_id(exact) 또는 keyword | `{node:{id,label,sentence,candidate,evidence_status}, edges:[{id,relation,verb,direction,peer_id,evidence_refs,evidence_backed}]}` |
| `build_handoff_context` | pack_id, topic?, max_nodes(기본 15·최대 50) | `{context_markdown, truncated}` (≤40KB) |

- pagination cursor 는 **opaque string**(offset 만 · filesystem path/pack_id 목록 미포함).
- 오류는 `{error_code, message}` — absolute path/stack/secret/SQL/사용자 이름 미노출. 코드: `PACK_NOT_FOUND`·`QUERY_TOO_SHORT`·`NODE_NOT_FOUND`·`AMBIGUOUS_KEYWORD`·`INVALID_INPUT`.

## Input pack boundary

입력 = **pack 디렉터리들의 상위 디렉터리**(pack repository). 각 pack 디렉터리는 canonical layout:

```
<pack_id>/
  manifest.json
  graph/nodes.jsonl     (또는 flat nodes.jsonl)
  graph/edges.jsonl     (또는 flat edges.jsonl)
  evidence/index.jsonl  (또는 flat evidence_index.jsonl)
```

신규 포맷을 만들지 않는다 — 기존 `binggu_cloud_pack_export` / Phase 3 handoff guide 정본 layout 을 그대로 서비스한다. **repository 밖 파일은 절대 읽지 않는다**(realpath 격리).

서비스 대상 허용 조건(하나라도 실패 → pack unavailable · 조용히 제외 · 부분 서비스 0):
manifest 존재 · JSON/JSONL parse 성공 · pack_id 정규식 안전 · path traversal 없음 · symlink 거부 · 파일 크기/행수 제한 · contract `validate_pack` STOP 배제 · required graph/evidence 파일 존재 · **public-safe(secret/PII) scan CLEAN**.

## Read-only guarantee

- 파일 read 만 · **write/network/cache/index/log/lock/temp/SQLite WAL 생성 0** · 운영 ledger 미접촉.
- `validate_pack` / `scan_public_tree` 의 순수 read-only 모드만 사용(validation report write 0).
- 모든 tool 호출·pagination·invalid input·malformed pack·반복 호출 전후 repository byte-identical.

## Candidate / evidence contract

- 모든 node/edge 는 **candidate**(promotion_allowed=0) — confirmed 자동 승격 0.
- evidence 없는 내용을 handoff/search 에 생성하지 않는다(없는 evidence_ref 를 지어내지 않음 · 근거 없으면 빈 hits).
- edge 의 evidence_refs 가 pack 에 실재하는 것만 반환하고, 없으면 숨기지 않고 `evidence_backed=false` 로 표시.
- `handoff_context` 는 Phase 3 Multi-Agent Handoff Guide 형식 **단일 정본**(이중 template 금지): evidence_refs 기반 답변·추측 금지·candidate 표시·contradicts 보존·자동 병합/승격 금지 규칙 포함.
- raw filesystem path·source_pointer 원문·source hash 전문·secret/PII 미노출.

## Conformance 실행법

```bash
python -m binggupack.app.conformance --selftest
```

synthetic pack 10종(valid minimal/multi·contradicting·missing-ref·malformed·path-traversal·symlink·oversized·ambiguous·PII/secret)으로 5 tool 계약을 검증한다(운영 ledger/네트워크 0).

## 남은 범위 (v1.21-B/C/D · 별도 GO)

- **B**: HTTPS MCP transport(streamable HTTP · stateless JSON) — 이 core 를 호출.
- **C**: auth/user isolation(플랫폼 OAuth · 사용자별 pack namespace · cross-user deny-by-default).
- **D**: upload 게이트(publish guard 연동) · hosted 배포(Cloudflare Workers 등).

`@BingguPack` 은 아직 사용 가능하지 않다(remote transport/배포 미구현).
