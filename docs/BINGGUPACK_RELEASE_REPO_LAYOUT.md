> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# BingguPack 1차 배포 — GitHub repo 구성 후보 (C)

> **상태: repo 구성 후보(2026-06-08). docs only · 실 repo/push 0 · enum 확정 0.**
> 상위: [FIRST_RELEASE_GITHUB_MCP_DESIGN](BINGGUPACK_FIRST_RELEASE_GITHUB_MCP_DESIGN.md).

---

## 1. 공개 repo 트리 후보

> *아래 `openbinggu/` 가상 트리는 2026-06-08 설계 당시 후보 — 실물 공개 repo는 `darkjokee-arch/binggupack`, 패키지 `binggupack/`. 실물 구조는 repo 루트/`pyproject.toml` 참조.*

```
openbinggu/                         # 공개 repo 루트
├── README.md                       # = BINGGUPACK_PUBLIC_README_DRAFT.md 기반
├── LICENSE                         # 위치만 준비 (license enum 확정은 HOLD)
├── .gitignore                      # 공개 제외 목록 (§3)
├── INSTALL.md                      # 설치/실행/selftest 가이드
├── mcp.example.json                # MCP config 예시 (§MCP_EXPOSURE)
├── scripts/                        # 개인용 RC 실행 코드 (write/apply/push 제외)
│   ├── openbinggu_scope_envelope_dryrun.py
│   ├── watcher_pack_builder_m0.py
│   ├── openbinggu_pack_validate.py
│   └── openbinggu_pack_consumer_smoke.py
├── docs/                           # 설계·정책·체크리스트 (공개 가능 문서만)
│   ├── BINGGUPACK_PUBLIC_RELEASE_CHECKLIST.md
│   ├── BINGGUPACK_SANITIZER_POLICY_BLOCK_ONLY.md
│   ├── BINGGUPACK_PERSONAL_TRACK_BASELINE_AND_GITHUB_PUBLISH.md
│   └── BINGGUPACK_RELEASE_PREFLIGHT_CHECKLIST.md
├── examples/
│   └── toy_project/                # synthetic toy (개인 데이터 아님)
└── tests/
    └── fixtures/synthetic/         # 합성 fixture (sanitization·portability)
        ├── toy_public_pack_cross_root_read_ok.json
        └── track1_failclosed_masking_unknown_bad.json
```

> 실제 코드 분리(개인용 RC 실행에 필요한 모듈만 추려 공개 트리로 복사)는 **별도 GO**. 본 문서는 트리 후보·포함/제외 기준까지.

---

## 2. 포함 / 제외 기준

| 경로 | 포함? | 사유 |
|---|---|---|
| `scripts/openbinggu_*`·`watcher_pack_builder_m0` | ✅ | 개인용 RC 핵심, write/apply/push 없음 |
| `scripts/*_e2e.py`·apply/ingest/merge 류 | ❌(1차) | 운영/적용 경로 — 1차 제외, 2차 검토 |
| `docs/` 공개 정책·체크리스트·README | ✅ | 데이터 없음 |
| `docs/` 내부 운영/threat model 상세 | ⚠️ 선별 | 민감 내부 설계는 1차 제외 가능(선별) |
| `examples/toy_project/` | ✅ | synthetic |
| `tests/fixtures/synthetic/` | ✅ | 합성 fixture |
| `tests/fixtures/` 내부 실 시나리오 | ❌ | 실/운영 색채 데이터 제외 |
| 실 그래프/DB/reports/reviews/captures | ❌ | 작성자 실데이터 |
| `.env`·credential·token·key | ❌ | secret |
| bid-engine·OpenCrab 운영 코드 | ❌ | 무관 운영 |

---

## 3. .gitignore 공개 제외 목록 후보

```gitignore
# secrets / credentials
.env
*.env
*_secret*
*_token*
credentials*
private_key*
*.pem
*.key

# 실 데이터 / 운영 저장소
*.sqlite
*.db
localcrab_index.sqlite
*_graph.yaml
localbinggu_production_graph.yaml
reingest_pack_draft/
reports/
reviews/
captures/
packs/private/

# 빌드/임시
tmp/
__pycache__/
*.bak_*
```

> 공개 전 `git check-ignore` 로 추적 누락 검증(별도 GO 시 실행).

---

## 4. LICENSE / README 위치

- `LICENSE`: 루트에 파일 자리만. **license enum 값 확정/기재는 HOLD**(데스크탑 publishing UI 앱 소스 실측 전 금지).
- `README.md`: private data 미포함 문구(EN/KO) 필수. install/selftest/사용예시/금지 포함.

## 5. 안전

docs only. 실 파일 생성/repo write 0. enum 확정·sanitizer·team_paid·marketplace·push 0. operating store mtime 불변.
