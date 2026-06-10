> OpenBinggu is the legacy/internal codename for BingguPack.

# OpenBinggu Public Release Policy — Public Skeleton / Private Data Separation

> OpenBinggu는 향후 GitHub에 **framework/skeleton**으로 공개될 수 있다.
> 공개 대상은 **골조·정책·템플릿·검증기·워크플로 코드**이며, **작성자의 실제 데이터는 절대 포함하지 않는다.**
> 이번 문서는 문서화만. production write·GitHub push·repo 생성 0.

## 1. 공개 원칙

OpenBinggu를 공개할 경우 공개 저장소는 **framework/skeleton**이어야 한다. 작성자의 실제 로컬 evidence·graph·review decision·CLI log·production graph는 포함하지 않는다.

**공개 가능:**
scripts · validators · merge adapter · match policy · apply gate · review workflow · transactional runner · production gate · watcher skeleton · docs · templates · synthetic fixtures · toy examples · default policies

**공개 금지:**
reingest_pack_draft 실제 원본 · localcrab_index.sqlite · localbinggu_production_graph.yaml · 실제 reports · 실제 reviews · 실제 evidence_index · 실제 CLI logs · 실제 git diff/test result · 실제 Claude Code traj/handoff · 실제 user decisions · 실제 프로젝트 경로 · 실제 업무/운영 지식 · .env·token·API key·cookie·private key·credential·secret·PII

## 2. Core ↔ Private Data 분리

| 저장소 | 역할 |
| --- | --- |
| **openbinggu-core** (공개) | framework · schema · validators · templates · synthetic tests · docs · default policies |
| **openbinggu-private** (비공개 로컬) | user packs · real evidence · local production graph · local sqlite index · review decisions · transaction reports · raw/sanitized captures · CLI watcher outputs |

→ **OpenBinggu Core는 배포 가능하지만, 사용자의 private graph는 각자 로컬에서 생성**해야 한다.

## 3. 다른 사용자의 사용 방식

다른 사용자는 **작성자의 데이터를 그대로 쓰지 않는다.** 자기 OpenBinggu를 이 흐름으로 만든다:

1. GitHub에서 `openbinggu-core` clone
2. setup 실행
3. 빈 local graph 생성
4. 자기 프로젝트/CLI/문서 경로 연결
5. 자기 capture/evidence 생성
6. 자기 incoming graph 생성
7. 자기 review decision 기록
8. 자기 production graph 생성

→ OpenBinggu는 **"작성자의 기억을 배포하는 도구"가 아니라 "각 사용자가 자기 evidence/ontology bus를 만드는 framework"**다.

## 4. 설정값 분리

**공개 가능 설정:** default safety policy · default match thresholds · relation vocabulary · pack strategy · review workflow · validator rules · prompt/template examples · production write guard policy

**비공개 설정:** 실제 프로젝트 경로 · 실제 domain 내용 · 실제 evidence id · 실제 review decisions · 민감 운영 정책 · 민감 개인 prompt · API 운영 정보 · secrets

**권장 구조:**
```
config/default.yaml   # 공개
config/example.yaml   # 공개
config/local.yaml     # 비공개
config/secrets.yaml   # 비공개
packs/private/        # 비공개
reports/              # 비공개 기본
reviews/              # 비공개 기본
captures/             # 비공개 기본
```

## 5. .gitignore 정책

공개 repo에는 반드시 `.gitignore`를 둔다. 기본 ignore 후보:
```
.env
*.sqlite
*.db
localcrab_index.sqlite
localbinggu_production_graph.yaml
reingest_pack_draft/
reports/
reviews/
captures/
tmp/
state/
logs/
packs/private/
evidence/private/
*.bak.*
*_secret*
*_token*
credentials*
cookies*
private_key*
```
단, synthetic fixture·toy example은 `tests/fixtures/synthetic/` 또는 `examples/toy_project/` 아래에만 둔다.

## 6. 공개 repo 권장 구조

```
openbinggu/
├─ README.md
├─ LICENSE
├─ pyproject.toml
├─ .gitignore
├─ docs/
│  ├─ ARCHITECTURE.md
│  ├─ OPENBINGGU_COMMON_BUS.md
│  ├─ OPENBINGGU_PUBLIC_RELEASE_POLICY.md
│  ├─ LOCALBINGGU_OPENCRAB_FM_MAPPING.md
│  ├─ LOCALBINGGU_PACK_STRATEGY.md
│  ├─ LOCALBINGGU_AUTOMATION_POLICY.md
│  └─ LOCALBINGGU_PRODUCTION_WRITE_POLICY.md
├─ openbinggu/
│  ├─ core/  validator/  merge/  review/  transaction/  watcher/  tools/
├─ templates/
│  ├─ pack_manifest.template.yaml
│  ├─ incoming_nodes.example.jsonl
│  ├─ incoming_edges.example.jsonl
│  ├─ evidence_index.example.jsonl
│  └─ localbinggu_production_graph.template.yaml
├─ tests/fixtures/synthetic/
└─ examples/toy_project/
```

## 7. README 문구

**EN:**
> OpenBinggu does not ship with the author's private evidence graph. This repository contains the framework, schemas, templates, validators, and workflow gates. Each user builds their own local evidence/ontology bus from their own data.

**KO:**
> 이 저장소는 작성자의 실제 로컬 그래프나 evidence를 포함하지 않습니다. OpenBinggu는 각 사용자가 자기 데이터로 자기 로컬 evidence/ontology bus를 구축하는 프레임워크입니다.

## 8. OpenBinggu Common Bus와의 관계

OpenBinggu는 여러 모델 앱·CLI 도구가 공유하는 **common evidence/ontology bus**가 될 수 있다. 하지만 **GitHub 공개판은 이 bus의 엔진과 프로토콜만 제공**한다. 실제 bus 데이터는 각 사용자의 private data root에 저장된다.

| 구성 | 공개 |
| --- | --- |
| OpenBinggu Core | ✅ 공개 가능 |
| LocalBinggu Core | ✅ 공개 가능 |
| OpenBinggu Watcher skeleton | ✅ 공개 가능 |
| MCP/REST/CLI tool layer | ✅ 공개 가능 |
| User evidence/graph/reviews/reports | ❌ 비공개 |
| Production graph | ❌ 비공개 |

## 9. v1.0 production write와의 관계

- v1.0 첫 production write는 **공개 repo와 무관**하다.
- v1.0은 현재 로컬 환경에서 별도 `localbinggu_production_graph.yaml`에 reviewed plan을 처음 반영하는 **제한된 write**.
- **GitHub 공개 여부와 production write는 분리**한다.
- production write가 성공하더라도 그 결과 파일은 **공개 repo에 포함하지 않는다.**

---

> 참조: [OPENBINGGU_PUBLIC_RELEASE_CHECKLIST.md](OPENBINGGU_PUBLIC_RELEASE_CHECKLIST.md) · `OPENBINGGU_COMMON_BUS.md`(internal design doc — not included in public repo) · `OPENBINGGU_EVALUATION_PROTOCOL.md`(internal design doc — not included in public repo)
