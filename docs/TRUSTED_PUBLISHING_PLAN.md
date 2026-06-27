# PyPI Trusted Publishing (OIDC) 전환 설계

> **상태: 설계/초안.** 이 문서와 `.github/workflows/publish.yml` 초안은 **신규 파일 생성만** 한 것이며,
> 실제 자동 publish 활성화는 아래 **owner 수동 단계 + 수동 트리거 검증** 후에만 이루어진다.
> 현재 운영 publish 경로(로컬 `~/.pypirc` + `twine upload`)는 **변경되지 않고 폴백으로 유지**된다.

대상 패키지: **binggupack** (PyPI) / 저장소: **darkjokee-arch/binggupack** / 워크플로: **publish.yml**

---

## 1. Trusted Publishing(OIDC)란

- **API 토큰 불필요.** 기존 방식은 PyPI에서 발급한 장기 API 토큰을 로컬 `~/.pypirc`(또는 CI 시크릿)에
  저장해 `twine`으로 업로드한다. 토큰이 유출되면 임의 업로드가 가능해 위험하다.
- **단기 OIDC 토큰 교환.** Trusted Publishing은 GitHub Actions가 실행 중 발급하는 **단기 OIDC 토큰**을
  PyPI가 검증한다. PyPI는 "이 토큰이 darkjokee-arch/binggupack 저장소의 publish.yml 워크플로에서
  나온 것인가"를 확인하고, 맞으면 그 자리에서 단명(短命) 업로드 자격을 내준다.
- 결과: **저장소/CI에 PyPI 토큰을 저장할 필요가 없다.** 시크릿 0. 토큰 회수/로테이션 부담 없음.

핵심 컴포넌트:
- GitHub Actions OIDC provider (자동) — `permissions: id-token: write` 필요.
- PyPI의 Trusted Publisher 등록 정보(아래 §2) — owner가 PyPI 웹에서 수동 등록.
- `pypa/gh-action-pypi-publish@release/v1` — OIDC 교환 + 업로드를 수행하는 공식 액션.

---

## 2. Owner 수동 단계 (자동화 금지 — 반드시 사람이 직접)

> 아래는 **PyPI 웹 콘솔에서 owner가 직접** 수행해야 한다. 스크립트/CI/AI 자동화 대상이 아니다.
> 이 단계를 마치기 전에는 워크플로를 실행해도 publish 단계에서 인증이 거부된다(정상 동작).

1. **PyPI 로그인** → 프로젝트 **binggupack** 페이지로 이동
   (최초 publish 전이라 프로젝트가 아직 없다면, 같은 화면의 "pending publisher"로 사전 등록 가능).
2. **Manage(관리) → Publishing → "Add a new publisher" → GitHub** 선택.
3. 아래 값을 정확히 입력:
   - **Owner:** `darkjokee-arch`
   - **Repository name:** `binggupack`
   - **Workflow name:** `publish.yml`  ← `.github/workflows/` 기준 파일명
   - **Environment name:** `pypi`  ← (선택) 워크플로의 `environment.name` 과 반드시 일치. 비워서 등록하면 워크플로의 `environment` 블록도 제거.
4. 저장. 이후 해당 저장소의 publish.yml 워크플로만 토큰 없이 업로드 가능해진다.
5. (권장) **TestPyPI**에도 동일하게 Trusted Publisher를 별도 등록해 두면, 실 PyPI 전에 시험 업로드 가능.

> 이 4~5단계가 **owner 전용 수동 작업**이며, 본 설계 작업 범위(파일 2개 생성)에는 포함되지 않는다.

---

## 3. GitHub 측 설정

- **Environment `pypi` 생성 (선택):** Settings → Environments → "New environment" → `pypi`.
  필수 리뷰어/대기 시간 등 보호 규칙을 걸면 publish 직전 **수동 승인 게이트**로 쓸 수 있다.
  Environment 를 쓰지 않기로 했다면 §2-3의 Environment 칸을 비우고, 워크플로의 `environment` 블록을 삭제.
- **워크플로 권한:** 잡(job)에 `permissions: id-token: write` (OIDC 발급) + `contents: read` (체크아웃).
  `id-token: write` 는 시크릿이 아니라 "OIDC 토큰을 발급받을 권한"일 뿐이다.
- **시크릿 0:** 저장소 Secrets에 PyPI 토큰을 추가하지 않는다. 워크플로에도 토큰 입력 없음.

---

## 4. 동작 흐름

```
[owner] PyPI Trusted Publisher 등록 (§2)  ── 선행 1회
        │
[trigger] (기본) workflow_dispatch 수동 실행
          (옵션) git tag vX.Y.Z push  또는  GitHub Release published
        │
[GitHub Actions] publish.yml 트리거
        │  checkout(tag) → setup-python → pip install build twine
        │  → tag/version 정합 확인 → python -m build → twine check
        │  → pypa/gh-action-pypi-publish@release/v1 (OIDC 토큰 발급·교환)
        ▼
[PyPI] OIDC 토큰 검증(owner/repo/workflow/environment 일치) → 업로드 수락
```

---

## 5. 안전 원칙

- **시크릿/토큰 0.** 워크플로에는 PyPI 토큰·시크릿이 전혀 없다. OIDC 교환만 사용.
- **기본 트리거는 수동(`workflow_dispatch`)** 으로 두어 의도치 않은 자동 publish를 방지.
  `release: published` 자동 트리거는 주석으로 제공하되 **기본 비활성**.
- **tag/version 정합 확인 단계 포함.** git tag(`vX.Y.Z`)로 트리거된 경우 태그 버전과
  `pyproject.toml`의 `version`이 일치하는지 검증하고, 불일치 시 즉시 실패한다
  (수동 실행 시에는 태그가 없으므로 검증을 건너뛰고 pyproject version으로 업로드).
- **활성화 순서 권장:**
  1) owner가 Trusted Publisher 등록(§2) →
  2) (가능하면) TestPyPI로 시험 →
  3) 실 PyPI에 **수동(`workflow_dispatch`)** 1회 검증 →
  4) 안정 확인 후에만 필요 시 `release: published` 자동 트리거 주석 해제.
- **build 의존성 0.** 프로젝트는 stdlib only(dependencies=[]). CI에서 설치하는 건 빌드 도구(`build`, `twine`)뿐.

---

## 6. 기존 수동 경로와의 관계 (폴백 유지)

- 현재 운영 경로: 로컬 `~/.pypirc`의 API 토큰 + `python -m build` + `twine upload dist/*`.
- 이 경로는 **삭제/변경하지 않고 폴백으로 유지**한다.
  - GitHub Actions OIDC 경로에 문제가 생기거나(액션 장애 등), Trusted Publisher 등록 전 긴급 release가
    필요할 때 기존 방식으로 업로드할 수 있다.
- 단, 같은 버전을 양쪽으로 중복 업로드할 수 없으므로(PyPI는 동일 파일 재업로드 거부),
  하나의 release는 **한 경로로만** 업로드한다. OIDC 경로가 안정화되면 일상 publish는 OIDC로,
  `~/.pypirc` 경로는 비상 폴백으로만 사용한다.
- 장기적으로 OIDC 경로가 충분히 검증되면 `~/.pypirc`의 토큰은 PyPI에서 폐기(revoke)하여
  공격 표면을 줄이는 것을 권장(이는 owner 판단 후 별도 수동 작업).

---

## 7. 검증 체크리스트 (활성화 시)

- [ ] (owner) PyPI에 Trusted Publisher 등록 완료 (owner/repo/workflow/environment 정확).
- [ ] GitHub Environment `pypi` 생성 여부 결정(쓰면 워크플로 `environment`와 일치, 안 쓰면 양쪽 제거).
- [ ] Actions에서 `workflow_dispatch`로 수동 1회 실행 → `twine check` 통과 → PyPI 업로드 성공 확인.
- [ ] (선택) TestPyPI 사전 시험 완료.
- [ ] 안정 확인 후에만 `release: published` 자동 트리거 주석 해제 여부 결정.
- [ ] 워크플로/저장소에 PyPI 토큰·시크릿이 0인지 재확인.

---

## 부록 A. 파일

- 워크플로 초안: `.github/workflows/publish.yml`
- 본 설계 문서: `docs/TRUSTED_PUBLISHING_PLAN.md`

## 부록 B. 참고

- PyPI Trusted Publishers: https://docs.pypi.org/trusted-publishers/
- 공식 액션: https://github.com/pypa/gh-action-pypi-publish
