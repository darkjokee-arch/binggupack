---
name: ponytail-review
description: >
  Code review focused exclusively on over-engineering. Finds what to delete:
  reinvented standard library, unneeded dependencies, speculative abstractions,
  dead flexibility. One line per finding: location, what to cut, what replaces
  it. Use when the user says "review for over-engineering", "what can we
  delete", "is this over-engineered", "simplify review", or invokes
  /ponytail-review. Complements correctness-focused review, this one only
  hunts complexity.
---

Review diffs for unnecessary complexity. One line per finding: location, what
to cut, what replaces it. The diff's best outcome is getting shorter.

## Format

`L<line>: <tag> <what>. <replacement>.`, or `<file>:L<line>: ...` for
multi-file diffs.

Tags:

- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Examples

❌ "This EmailValidator class might be more complex than necessary, have you
considered whether all these validation rules are needed at this stage?"

✅ `L12-38: stdlib: 27-line validator class. "@" in email, 1 line, real validation is the confirmation mail.`

✅ `L4: native: moment.js imported for one format call. Intl.DateTimeFormat, 0 deps.`

✅ `repo.py:L88: yagni: AbstractRepository with one implementation. Inline it until a second one exists.`

✅ `L52-71: delete: retry wrapper around an idempotent local call. Nothing replaces it.`

✅ `L30-44: shrink: manual loop builds dict. dict(zip(keys, values)), 1 line.`

## Scoring

End with the only metric that matters: `net: -<N> lines possible.`

If there is nothing to cut, say `Lean already. Ship.` and stop.

## Boundaries

Scope: over-engineering and complexity only. Correctness bugs, security holes,
and performance are explicitly out of scope. Route them to a normal review
pass, not this one. A single smoke test or `assert`-based
self-check is the ponytail minimum, not bloat, never flag it for deletion.
Does not apply the fixes, only lists them.
"stop ponytail-review" or "normal mode": revert to verbose review style.

---

## 프로젝트 로컬 보호 규칙 (BingguPack · 2026-09-03)

> 위 본문은 upstream ponytail **v4.9.0** 원문 그대로다. 어긋나면 **아래가 이긴다**.
> 정본: `CLAUDE.md` §🔴 절대 경계 · 전역 `~/.claude/CLAUDE.md` §3·§8-1.

### 「지우자」로 올리면 안 되는 것 (protected complexity)

- **사람 승인 게이트** — `save_gate`·`confirm`·`owner binding`·`trusted_approval`,
  그리고 그 값을 한 곳에서 내주는 접근자(`p1_config.confirm_actor()` 같은 «단일 원천» 래퍼).
  래퍼를 걷고 dict 를 직접 읽으면 불변식이 호출처마다 흩어진다.
- **provenance·audit trail** — digest·bundle·`*_note`·`*_used_for_ranking` 같은 공개 필드.
  「저장소 안에서 아무도 안 읽는다」는 감사 산출물에 쓰면 안 되는 판정 기준이다.
- **원자적 커밋·롤백·immutable bundle·STOP 조건·autosave 금지** 경로
- **모듈 안 `_selftest()` / `main()`** — 이 프로젝트의 검증 게이트다.
  wheel 에는 `tests/` 가 안 실려서 설치본 `binggu --selftest` 가 이것으로 돈다.
- **읽기 전용 경로 분리** — `*_ro()` 계열은 운영홈 ledger 를 만들지도 옮기지도 않는다는
  보장이다. 플래그 하나로 합치자는 제안은 그 보장을 런타임 인자로 낮춘다.
- **호환용 레거시**(`*_legacy`) — 바깥(hosted worker)이 아직 부를 수 있다.
- **stdlib-only 헌법** — 「의존성을 추가하자」는 제안은 그 자체로 설계 위반이다.
  반대로 서드파티를 **stdlib 로 되돌리는** 제안은 환영한다.

### 형식

- 각 항목 끝에 `근거:` — grep 결과·호출처 수. 확인 못 했으면 `미확인`.
- **고치지 않는다.** 목록만. 반영은 사장님이 고른 항목만.
