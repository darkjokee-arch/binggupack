> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# OpenBinggu 1차 배포 — MCP 기능 노출 후보 (D)

> **상태: MCP 노출 후보(2026-06-08). docs only · MCP 서버 코드 구현 0 · 실 노출 0.**
> 상위: [FIRST_RELEASE_GITHUB_MCP_DESIGN](OPENBINGGU_FIRST_RELEASE_GITHUB_MCP_DESIGN.md).
> 본 문서는 "무엇을 MCP 도구로 노출할지/하지 말지" 기준까지. 실제 MCP 서버 구현은 별도 GO.

---

## 1. local MCP 서버로 노출할 기능 (허용 후보)

> 전부 **로컬 read/dry-run 성격**, operating store write 0, 외부 전송 0.

| MCP tool(후보) | 매핑 | 동작 | write |
|---|---|---|---|
| `openbinggu.pack_build` | watcher_pack_builder_m0 | 로컬 자료 → candidate pack 빌드(temp) + source pointer 판정 | temp only |
| `openbinggu.pack_validate` | openbinggu_pack_validate | pack 검증(verdict) | read-only |
| `openbinggu.consumer_smoke` | openbinggu_pack_consumer_smoke | pack 소비(읽기) smoke | temp only |
| `openbinggu.publish_guard_dryrun` | openbinggu_scope_envelope_dryrun | 공개 fail-closed 게이트 dry-run(dirty/unknown→BLOCK) | read-only |
| `openbinggu.selftest` | 4 selftest 묶음 | GATE=GO/EXIT=0 자가검증 | read-only |

- 모든 도구 출력은 **count/reason_code/source_pointer_id·verdict·GATE 만**. raw PII/secret/private path 미출력.
- 모든 pack 산출은 **candidate**(promotion_allowed=false). 자동 승격 0.

> **사용자 주도 OpenCrab 업로드(1차 포함 가능, 자동화 금지)**: OpenCrab은 가입자가 자기 pack을 자기 의지로 올리는 곳. 업로드 도구는 **fail-closed gate(dirty/unknown→BLOCK·raw 차단) 통과 + user 1회 수동 승인 후 사용자가 실행**하는 형태로만 1차 포함 가능(현재 미구현·별도 GO). **자동/일괄 업로드는 금지**(§2 GitHub push 자동화와 동일 원칙). 우리 시스템/운영자의 자동 store write/apply/ingest는 §2 HOLD로 별개.

---

## 2. MCP로 노출하면 위험한 기능 (제외 — HOLD/BLOCK)

| 제외 기능 | 사유 | 상태 |
|---|---|---|
| 우리 시스템/운영자의 OpenCrab store/graph 자동 write/apply/ingest | 운영 그래프 자동 오염 | HOLD |
| apply / ingest / merge | 운영 반영 | HOLD |
| GitHub push 자동화 | 되돌릴 수 없는 공개, owner 승인 우회 | HOLD |
| sanitizer 자동치환 | 마스킹 누락=영구 유출, 정책상 차단만 | HOLD(미구현) |
| team_paid / billing | 트랙2 | DEFER |
| enum 확정(release_mode/license/entitlement) | 앱 소스 실측 전 | HOLD |
| marketplace 거래/정산 | 제품 목표 아님 | BLOCK |
| raw evidence/secret/path 반환 | 유출 | 영구 금지 |

> **원칙: MCP는 "만들고·검증하고·읽고·공개 가능 여부를 판정"까지만. "운영에 반영"·"외부로 내보냄"은 도구로 노출하지 않는다.**

> **경로 입력 안전(S3/X1)**: MCP 도구가 받는 모든 경로 입력은 `scripts/openbinggu_path_safety_gate.py`의 `classify_path(input, allow_root)`를 거쳐 **ALLOW만 처리**한다. allow_root 밖·symlink/junction·UNC/ADS/8.3·parent 탈출·bid-engine/NPKI·인증서/secret/OpenCrab store/타프로젝트 경로는 **BLOCK**(reason_code만, raw 경로 미출력). gate selftest 15/15 GATE=GO.
>
> **실연결 adapter(2026-06-08)**: `scripts/openbinggu_mcp_path_gate_adapter.py`의 `guarded_tool_call(tool_fn, path_inputs, allow_root, recheck=True)`로 도구를 감싼다 — ① 1차 검사 BLOCK 시 underlying 미호출, ② 모두 ALLOW여도 **실행 직전 재검사**(TOCTOU 잔여 감소) 후 BLOCK이면 미호출, ③ 결과는 executed/path_id/reason_code/count만(raw 경로 미출력). adapter selftest 10/10 GATE=GO(ALLOW만 underlying 실행). 실제 MCP 서버 등록/공개는 별도 GO.

---

## 3. mcp.example.json 후보 (형태 예시 — 실 등록 별도)

```json
{
  "mcpServers": {
    "openbinggu": {
      "command": "python",
      "args": ["scripts/openbinggu_mcp_server.py"],
      "env": { "OPENBINGGU_MODE": "local_readonly" }
    }
  }
}
```

> `openbinggu_mcp_server.py`(stdio JSON-RPC wrapper)는 **구현됨**(initialize/tools·list/tools·call). 단 **실 MCP 설정 등록/공개는 미실행(별도 GO)** — `--serve <ROOT>`는 정의만, selftest는 호출 안 함. write/apply/push 도구는 tools/list에 노출되지 않음.

## 5. 핸들러 결선 후보 (2026-06-08, 실 서버 등록 前)

`scripts/openbinggu_mcp_server_handlers.py` — adapter `guarded_tool_call`을 실제 도구 핸들러에 결선한 후보:
- **노출 도구(read/dry-run only)**: `pack_build`·`pack_validate`·`consumer_smoke`·`publish_guard_dryrun`·`selftest`. 전부 mode=read/dry-run.
- **path 입력은 전부 `guarded_tool_call` 통과** → BLOCK 시 underlying 미호출(실행 직전 재검사 포함).
- **위험 도구 핸들러 부재**: `opencrab_write/apply/ingest`·`github_push`·`opencrab_upload`·`sanitizer_replace`·`enum_set`·`team_billing`·`marketplace_publish`·`db_write` 요청 → `tool_not_exposed`(REJECT, 미호출).
- 결과는 executed/verdict/reason_code/path_id/tool만 — **raw 경로/secret 미출력**.
- **selftest 10/10 GATE=GO**(ALLOW 3·BLOCK 4·REJECT 3, exposed_tools_read_or_dryrun_only=True, forbidden_not_exposed=True, raw_path_not_leaked=True).
- ⚠️ **실 서버 등록/공개는 미실행(별도 GO)**.

## 6. stdio JSON-RPC wrapper (2026-06-08)

`scripts/openbinggu_mcp_server.py` — `handle_tool`을 stdio JSON-RPC로 감싼 wrapper:
- **methods**: `initialize` · `tools/list`(=list_tools) · `tools/call`(=call_tool).
- **tools/list = read/dry-run 도구만**(pack_build·pack_validate·consumer_smoke·publish_guard_dryrun·selftest). 위험 도구는 목록에 부재.
- **tools/call → `handle_tool`로만 라우팅**. path 입력은 전부 path gate/adapter 통과, BLOCK/REJECT 시 underlying 미호출.
- **응답 sanitize**: tool/verdict/executed/reason_code/path_id/count만. raw 경로/secret 미출력.
- **malformed 안전 처리**: missing method(-32600)·unknown method(-32601)·invalid params(-32602)·not object(-32600).
- **selftest 13/13 GATE=GO**(initialize·tools/list read-only·call ALLOW·BLOCK 4·REJECT 2·malformed 4, raw_path_not_leaked=True).
- ⚠️ `--serve <ROOT>`는 실 stdio 루프 정의만. **실 MCP 등록/공개는 미실행(별도 GO)**. selftest는 serve를 호출하지 않음.

## 4. 안전

docs only. MCP 서버 코드·실 노출·operating store write 0. apply/ingest/push·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
