---
name: ponytail-audit
description: >
  Whole-repo audit for over-engineering. Like ponytail-review, but scans the
  entire codebase instead of a diff: a ranked list of what to delete, simplify,
  or replace with stdlib/native equivalents. Use when the user says "audit this
  codebase", "audit for over-engineering", "what can I delete from this repo",
  "find bloat", "ponytail-audit", or "/ponytail-audit". One-shot report, does
  not apply fixes.
---

ponytail-review, repo-wide. Scan the whole tree instead of a diff. Rank
findings biggest cut first.

## Tags

Same as ponytail-review:

- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Hunt

Deps the stdlib or platform already ships, single-implementation interfaces,
factories with one product, wrappers that only delegate, files exporting one
thing, dead flags and config, hand-rolled stdlib.

## Output

One line per finding, ranked: `<tag> <what to cut>. <replacement>. [path]`.
End with `net: -<N> lines, -<M> deps possible.` Nothing to cut: `Lean already. Ship.`

## Boundaries

Scope: over-engineering and complexity only. Correctness bugs, security holes,
and performance are explicitly out of scope. Route them to a normal review
pass. Lists findings, applies nothing. One-shot.
"stop ponytail-audit" or "normal mode" to revert.

---

## 프로젝트 로컬 보호 규칙 (BingguPack · 2026-09-03)

> 위 본문은 upstream ponytail **v4.9.0** 원문 그대로다. 어긋나면 **아래가 이긴다**.

### 「지우자」로 올리면 안 되는 것

`.claude/skills/ponytail-review/SKILL.md` 의 보호 목록과 같다 — 사람 승인 게이트·provenance·
원자적 커밋/롤백·모듈 `_selftest()`·읽기 전용(`*_ro`) 분리·호환용 레거시·stdlib-only 헌법.

### 감사 범위를 먼저 좁힌다

`hosted/workers/anywhere/core/` 는 `binggupack/` 의 **byte-identical 벤더 사본**이다
(`scripts/sync_anywhere_vendor.py --check` 가 강제). 중복이라고 올리면 안 된다.
`_backup/`·`build/`·`_binggu_*_home/`·`.ruff_cache/` 도 대상 밖이다.

### 형식

- 각 항목 끝에 `근거:` — grep 결과·호출처 수. 확인 못 했으면 `미확인`.
- **고치지 않는다.** 목록만.
