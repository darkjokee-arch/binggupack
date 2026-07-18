# BingguPack Cross-Client Integration — 커넥터 구분·재등록·복구 런북 (Track R)

> **성격**: 무배포·읽기/준비 전용 런북. 이 문서는 **실행 절차의 정본**이지 실행 그 자체가 아니다.
> 커넥터 등록·시크릿 생성·hosted pull 확정·배포는 **전부 owner 손** — 아래 각 절의 `owner_gate`
> 표시가 그 경계다. AI 는 준비(dry-run·preview·마스킹 존재확인)까지만 한다.
>
> **정본 위임**: 커넥터 URL 형식·마스킹 규칙 = `scripts/binggu_setup_save.py`(s6/s8) · 저장 채널 =
> `docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md` · read 서비스 drift 하니스 =
> `scripts/binggu_cross_client_e2e.py`. 이 문서는 그 위임처를 가리키는 런북이다.

---

## 0. 경계 요약 (must_fix)

| 항목 | AI 가 하는 것 | owner 가 하는 것(owner_gate) |
| :--- | :--- | :--- |
| 커넥터 등록 | 재등록 절차·URL **형식**만 안내 | 실제 등록(OAuth 승인/커넥터 붙여넣기)·성공/실패 1회 실측 회신 |
| 전체 URL/토큰 | **평문 미출력**·마스킹 **존재확인**만(hash8+길이) | `--show-url` 을 **본인 화면**에서 직접 실행·화면에서 복사 |
| hosted pull | 스크립트 준비 + **번호+원문 전문** preview 표시 | `--confirm "SAVE n"` 확정 실행(본인 머신·본인 손) |
| 배포 시크릿 | `secret put <NAME>` **명령 형태**(값 제외) + 배포후 read-only canary | 시크릿 **값 생성·stdin 입력**·`onboard --apply/--deploy` |
| near-real-time | 배선점(seam)만·기본 OFF | `person_pack.json` 옵트인으로 활성화 |

> **정직 원칙(7/12 실측 반영)**: 아래 어디에도 "OAuth 안 걸린다 / authless 신규등록 성립" 단정은
> 없다. 살아있던 커넥터는 **구정책 잔존 기존등록**이지 신규 authless 증거가 아니다. 재등록 성공
> 여부는 owner 가 1회 실측해 회신해야 확정된다(§8).

---

## 1. 커넥터 URL 2종 구분표

BingguPack 은 **읽기 표면**과 **저장 표면**이 다른 URL·다른 인증으로 분리돼 있다. 두 클라이언트
(Claude / ChatGPT)가 각자 어떤 표면을 무엇으로 읽/쓰는지 헷갈리지 않게 아래로 고정한다.

| 구분 | 경로 형식 | 용도 | 도구 수 | 인증 | 정본 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **읽기(로컬 MCP)** | `<WORKER_URL>/mcp/<path_token>` | 로컬 30 도구(recall·why·list·contrast 등) 웹/앱 커넥터 노출 (정본 위임: openbinggu_mcp_server.py) | 30도구(read+dry-run+write-gated) | 경로키(path_token) + Origin | `scripts/openbinggu_mcp_server.py` · `binggu_setup_save.py` s8(web MCP) |
| **저장(hosted)** | `<WORKER_URL>/mcp2/<save_path_token>` | 채팅에서 저장 intent 적재(폰·ChatGPT) → 로컬 inbox | save intent tools | 경로키(save_path_token, 추측불가 24자) + Origin(HMAC 헤더 불가한 커넥터 대응) | `BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md` |
| **OpenCrab 팩** | `https://…opencrab.<tld>/api/mcp/<token>` | Expert 가입 발행 전용 URL — OpenCrab 클라우드 팩 read/QA | OpenCrab 도구 | OpenCrab 발행 토큰 | `binggu_setup_save.py` s10(opencrab_url) |

- **`/mcp/` ≠ `/mcp2/`**: 앞은 로컬 도구(읽기 중심) 노출, 뒤는 저장 적재 채널. 토큰도 별개다.
- **읽기 중복 주의**: OpenCrab 커넥터가 이미 Claude/ChatGPT 에서 팩 read 를 제공하므로, hosted
  `/mcp/` 읽기와 OpenCrab read 는 **표면이 둘**이다. 두 표면이 같은 로컬 빌드를 반영하는지(=두 모델이
  다른 버전을 보는지)는 §7 의 drift 하니스로 확인한다(무배포·기존 스냅샷 소비만).

---

## 2. 마스킹·시크릿 경계 — 누가 무엇을 실행하나

전체 URL·경로키·서명 시크릿은 **비밀**이다(경로키 자체가 인증). 문서·로그·채팅 어디에도 평문으로
남기지 않는다.

### AI 가 하는 것 (마스킹 존재확인까지)
- 커넥터 URL **형식**만 안내: `<WORKER_URL>/mcp2/<save_path_token>` (실제 값 미치환).
- 토큰 파일이 **존재하고 마스킹 대상인지**를 값 노출 없이 확인 — `describe_secret`(hash8 + 길이):

  ```bash
  python -c "import json,os; from binggupack.safety.keychain_backend import describe_secret; \
    p=os.path.expanduser('~/.binggupack/mcp_http_token'); \
    print(json.dumps(describe_secret(open(p,'rb').read().strip())))"
  # → {"sha256_hash8": "xxxxxxxx", "length": NN}   ← 평문 0 · 존재+마스킹 확인만
  ```
- `binggu onboard` **dry-run 만**(변경 0). `--apply` / `--deploy` / `--show-url` 은 **실행 금지**.

### owner 가 하는 것 (owner_gate)
- **전체 URL 보기**: 본인 머신·본인 화면에서 직접 —
  `binggu onboard --show-url` (또는 `python binggu.py onboard --show-url`).
  화면에 뜬 전체 주소를 **본인이 복사**해 커넥터에 붙여넣는다. AI 는 이 명령을 대신 실행하거나
  출력을 중계하지 않는다(마스킹 우회 = 시크릿 유출).

---

## 3. 재등록 런북 — 2-branch (OAuth / authless path-token)

> **배경(7/12 실측)**: 외부 client(Claude·ChatGPT custom connector) 신규 등록은 현 정책상 **OAuth
> 또는 authless 만** 받는다. Bearer/path-token 헤더 신규등록은 미지원으로 관측됐고, Claude 에서
> "OAuth 클라이언트 ID 추가" 에러가 났다. 살아있던 기존 커넥터는 **구정책 잔존 기존등록**이지 신규
> authless 성립 증거가 아니다. 7/12 owner 가 Claude 커넥터를 삭제 → 재등록 갭이 열렸다.
> **7/16 실측 완료(마감)**: named tunnel(mcp.binggu.uk)로 서버 30도구 정상 노출 curl 실증, ChatGPT 커넥터 읽기+저장 실증(pair_bf8faf09), 저장 채널 무영향. 재등록 미해결 프레이밍은 해소됨. 아래 두 경로 서술은 절차 참조로 보존.

### Branch A — OAuth 경로 (현재 Claude 신규 등록에서 요구될 가능성 높음)
1. owner: 클라이언트(Claude) 커넥터 설정 → 새 커넥터 → **OAuth** 방식 선택.
2. owner: OAuth 승인 플로우(브라우저 로그인·동의)를 **본인 손**으로 진행.
3. 성공 시 커넥터가 읽기 표면(`/mcp/`)에 바인딩. 저장은 §1 의 `/mcp2/` 별도.
4. ⚠ "OAuth 클라이언트 ID 추가" 류 에러가 나면 → Branch B 를 시도하되, **미확인**임을 전제.

### Branch B — authless path-token 경로 (신규 등록 성립 여부 **미확인**)
1. owner: 커넥터 URL 을 **본인 화면**에서 확보 — `binggu onboard --show-url`(§2 owner_gate).
2. owner: authless(경로키 내장 URL) 방식으로 붙여넣기 시도.
3. **주의**: 7/12 관측상 외부 client 신규 커넥터는 path-token 헤더/URL 을 신규로 받지 않을 수 있다.
   성립하면 read 라인 동급(경로키+Origin), **안 되면 Branch A(OAuth)로 회귀**.

> 두 branch 모두 **AI 는 URL 형식·순서만 안내**한다. 실제 등록 클릭·OAuth 승인·성공/실패 판정은
> owner 몫이다.

---

## 4. hosted pull — 채팅 저장분 로컬 반영 (owner_gate · SAVE n all-or-nothing)

채팅(폰/ChatGPT)에서 `/mcp2/` 로 적재된 저장 intent 를 로컬 장부로 회수하는 절차. **전량 자동
적용은 없다** — 사람이 번호를 골라 `SAVE n` 으로 확정한다.

### AI 준비 (여기까지)
1. inbox preview 표시 — **번호 + 원문 전문**(요약 금지, session-close-ledger 규약):
   ```bash
   python binggu.py hosted inbox
   # [1] <원문 전문> … 후보 M
   # [2] <원문 전문> … 후보 M
   ```
2. AI 는 위 preview 를 **번호별 원문 그대로** 채팅에 표시한다. "N건 있습니다" 요약은 금지.

### owner 확정 (owner_gate)
```bash
# owner 본인 머신·본인 손. --confirm 문구는 고른 번호와 정확히 일치해야 한다(all-or-nothing).
python binggu.py hosted pull --select 1,3 --confirm "SAVE 1,3"
```
- **AI 우회 절대 금지**: AI 가 `--confirm "SAVE n"` 을 대신 채워 실행하면 **승인 위조**다. preview
  까지만 하고 정지한다(코드 주석 `auto_pull_hosted.py` + 이 런북 양쪽에 명시).
- all-or-nothing: 묶음은 원자적으로 확정된다(부분 저장 없음).

### 로컬 verify (read-only)
```bash
# 반영 전후 운영 장부 mtime 이 owner 확정 시점에만 바뀌었는지(AI 준비 단계는 불변)
python -c "import os;p=os.path.expanduser('~/.binggupack/ledger.sqlite');print(os.path.getmtime(p))"
```

---

## 5. 배포 시크릿 — SAVE_SIGN_SECRET / SAVE_PATH_TOKEN (owner stdin)

hosted 저장 워커의 서명·경로키 시크릿. **값 생성·입력은 100% owner stdin** 이다.

### AI 가 안내하는 것 (명령 형태만 · 값 제외)
```bash
# owner 가 본인 셸에서 실행 — 값은 프롬프트(stdin)로 직접 입력한다. 명령줄에 값 미기재.
wrangler secret put SAVE_SIGN_SECRET     # 서명 시크릿(값=owner stdin)
wrangler secret put SAVE_PATH_TOKEN      # 저장 경로키(값=owner stdin)
```
- `binggu onboard --apply`(키/KV/toml/스케줄러 생성)·keychain keygen 은 **owner 전용**. AI 는 실 OS
  keychain 에 "테스트 목적"으로도 절대 실행하지 않는다(in-memory fake seam 만 · 운영 keychain
  sentinel 불변).

### 배포후 canary (AI 가 하는 read-only 헬스체크 · 프로덕션 KV write 0)
- §7 헬스체크(read-only)로만 확인한다. write 프로브 0.

---

## 6. 복구 절차 (Claude 커넥터 삭제 → 재등록)

> ⚠ 7/16 실측 완료로 마감됨(30도구 정상·ChatGPT 저장 실증·저장 채널 무영향). 아래는 재발 시 복구 순서로 보존.

7/12 owner 가 Claude 커넥터를 삭제한 상태에서 열렸던 갭. 복구 순서:
1. **형식 확인(AI)**: §1 표로 읽기(`/mcp/`)·저장(`/mcp2/`) 표면과 토큰 존재를 §2 마스킹 확인으로 점검.
2. **URL 확보(owner)**: `binggu onboard --show-url` 본인 화면(§2 owner_gate).
3. **재등록(owner)**: §3 Branch A(OAuth) 우선 → 실패 시 Branch B 시도(미확인 전제).
4. **저장 채널 유지**: 기존 `/mcp2/` save 워커·로컬 MCP 터널은 그대로 둔다(§1). ChatGPT 커넥터는
   건드리지 않는다.
5. **실측 회신(owner)**: 등록 성공/실패를 §8 항목으로 1회 회신 → 이 런북의 미확인 항목 확정.

---

## 7. 헬스체크 (read-only canary · 배포 0)

프로덕션에 write 하지 않는 read-only 확인만. drift 하니스는 **기존 스냅샷 소비**만 하고 live fetch·
새 serving 경로를 신설하지 않는다.

```bash
# (a) cross-client 읽기표면 drift — 두 모델이 다른 버전을 보는지(기존 스냅샷 2종 diff)
python scripts/binggu_cross_client_e2e.py --selftest            # 로직 게이트(합성 픽스처)
python scripts/binggu_cross_client_e2e.py \
    --hosted-snapshot <hosted_kv_snapshot_path> \
    --opencrab-snapshot <opencrab_snapshot_path>
#   decision: IN_SYNC | DRIFT | UNSUPPORTED(스냅샷 부재=정직) · operating_home_unchanged 확인

# (b) 전파 배선(save→로컬 장부→sync) dry-run — 격리홈·confirm/upload/network 0
python scripts/binggu_cross_client_e2e.py --propagation-only

# (c) near-real-time 배선 상태(기본 OFF 확인)
python scripts/person_pack_daily_sync.py --wiring-status
```
- 어떤 명령도 운영 장부·프로덕션 KV 에 write 하지 않는다. drift 하니스는 전 구간 운영 장부 mtime
  sentinel 을 대조한다(불변이어야 통과).

---

## 8. owner_gate 회신 항목 (owner 1회 실측)

> ⚠ 7/16 실측 완료로 아래 대기 항목 해소: 서버 30도구 curl 실증·ChatGPT 읽기+저장 실증(pair_bf8faf09)·저장 채널 무영향 확인. 아래는 재등록 재발 시 회신 양식으로 보존.

아래는 AI 가 추측으로 채울 수 없는 항목 — owner 실측 회신으로만 이 런북이 확정된다.

1. **재등록 방식 실측**: Claude 신규 커넥터 등록 시 OAuth 요구됐는가 / authless path-token 성립했는가?
   (§3 Branch A vs B 판정)
2. **저장 채널 무영향**: 재등록 중 `/mcp2/` 저장·ChatGPT 커넥터가 유지됐는가?
3. **near-real-time 활성화 의사**: `person_pack.json` 옵트인을 켤지(기본 OFF 유지가 default).

> 회신 전까지 §3 의 authless 성립·§5 canary 통과는 **미확인(UNSUPPORTED)** 으로 남긴다 — 억지 확정 금지.
