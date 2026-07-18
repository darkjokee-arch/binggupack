# Memory PR — Implementation Mapping — v0.1-draft

> 규격 조항 ↔ BingguPack 실제 심볼. 이 문서는 Spec 이 **현재 구현을 정확히 서술**함을 보증하기 위한 추적표다. 기준 커밋: `c73c941` (main). **라인 번호(~NNN)는 참고용 스냅샷 — CLI 개편으로 drift함. 심볼명이 정본 앵커다**(심볼-only 조회 권장). 최근 CLI 이관 반영: `cmd_hosted`·`cmd_preflight`·`promote` 헬퍼 본체는 `binggupack/cli/`(hosted.py·preflight.py·promote.py)로 이관, `binggu.py`는 wrapper.

## Interactive Save Profile

| 규격 항목 | 파일 : 심볼 |
| :-- | :-- |
| 후보 생성(preview) | `scripts/openbinggu_conversation_capture_preview.py` : `capture_preview` |
| preview 표시·영속 | `binggu.py` : `cmd_preview` |
| last_preview 기록 | `binggupack/safety/gate_log.py` : `write_last_preview` (~197) |
| **preview_ref 산식** | `gate_log.py` : (~185) `sha256(_norm(s))[:16]` → `"\n".join("idx:sh")` → `[:16]` |
| **node_id 산식** | `scripts/openbinggu_conversation_candidate_save.py` : `_sent_hash` (~30) = `sha256(_norm(s))[:8]` |
| preview_id | `binggu.py` : `_preview_id` = `sha256(raw)[:8]` |
| SAVE n hook | `hooks/binggu_save_gate_hook.py` (~28) |
| 승인 3모드 판정 | `binggu.py` : `_resolve_human_ctx` |
| commit(INSERT) | `scripts/openbinggu_staging_write_selftest.py` : `apply_pack_in_txn` (~277) `candidate=1,promotion_allowed=0,state='active'` |
| freshness | `gate_log.py` : `gate_human_for_ref` (~252) · `GATE_WINDOW` (~116) |

## Trusted Event Profile

| 규격 항목 | 파일 : 심볼 |
| :-- | :-- |
| **binding_fields (operation별)** | `binggupack/safety/trusted_approval.py` : `binding_fields` (~142) |
| **canonical_payload_digest** | `trusted_approval.py` : `canonical_payload_digest` (~220) = `tae-1\x1fop\x1fjson(NFC)` → sha256 전체 64 |
| **compute_request_id** | `trusted_approval.py` : (~230) → `sha256[:24]` |
| nonce / mint | `trusted_approval.py` : `mint_approval` (~482) `token_hex(16)` |
| verify_event | `trusted_approval.py` : `verify_event` (~502) |
| 소비 게이트 | `binggupack/mcp/approval_gate.py` : `authorize` (~120) |
| import_edges 경로 | `scripts/hybrid_agi/hag_sync_adapter.py` : `import_confirmed_edges` (~256) |
| **import_edges receipt** | `hag_sync_adapter.py` (~388) = `{request_id,operation,imported_edge_ids,actor}` |
| derive_receipt | `trusted_approval.py` : `derive_receipt` (~631) = `{request_id,operation,node_ids,decision_id}` |
| approval_requests / consumptions 스키마 | `scripts/binggu_schema.py` (~162) |

## Hosted Relay Profile

| 규격 항목 | 파일 : 심볼 |
| :-- | :-- |
| 원격 intent 생성(MCP) | `hosted/workers/src/save_intent_mcp.ts` : `makeSaveMcpHandler` |
| **intent_id 산식** | `save_intent_mcp.ts` : `intentHash` (~30) ≡ `scripts/openbinggu_save_intent_outbox_runner.py` : `intent_hash` (~75) = `sha256(text\|indices\|confirm)[:16]` |
| hosted DO storage | `hosted/workers/src/save_intent_v2.ts` : `IntentInbox` |
| 로컬 pull / drain | `binggupack/cli/hosted.py` : `cmd_hosted` (binggu.py는 wrapper) · `binggu_hosted_inbox.fetch_to_staging` |
| **commit_bundle (Interactive 수렴)** | `scripts/binggu_hosted_bundle.py` : `commit_bundle` (~179) |
| **_archive 영구보존** | `binggu_hosted_bundle.py` : `_archive_member` (~117) = `_archive/<id>.processed.json` |
| 서명 검증 | `hosted/workers/src/*` : `verifySig` · `binggupack_sign_util.signed_headers` |

## 검증 도구

| 항목 | 파일 |
| :-- | :-- |
| 고정 KAT drift 검증 | `docs/memory-pr/tools/check_vectors.py` |
| Interactive/Trusted/Hosted selftest | `scripts/*_selftest.py` · `tests/hosted_boundary_e2e.py` |
| MGB 벤치마크 | `benchmark/` (MGB v0.1) |
