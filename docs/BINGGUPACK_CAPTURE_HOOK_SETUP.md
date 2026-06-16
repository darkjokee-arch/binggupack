# 자동 후보 수집 hook 설치 가이드 (opt-in)

BingguPack의 **자동 후보 수집 hook**을 Claude Code에 연결하는 방법입니다.

> ⚠️ 이 hook은 **자동 저장이 아닙니다.** 대화 발화에서 후보(candidate)만 버퍼에 쌓습니다.
> 실제 저장은 미리보기를 보고 직접 `SAVE n`(정확한 confirm)을 타이핑했을 때만 기존
> 저장 게이트(`save_selected`)로 진행됩니다. hook은 ledger/active/confirmed를 쓰지 않습니다.

설치하지 않으면 아무 동작도 하지 않습니다. 설치해도 **기본 OFF**라, 아래 활성화 2단계를 직접 밟기 전까지는 어떤 세션에서도 동작하지 않습니다.

---

## 1. 무엇이 추가되나

| 구성 | 역할 |
|---|---|
| `hooks/binggu_capture_hook.py` | UserPromptSubmit(발화 수집) / Stop(세션말 미리보기 건수 기록) 진입점 |
| `~/.binggupack/capture_enabled` | **opt-in 플래그.** 이 파일이 있을 때만 hook이 동작 |
| `~/.binggupack/capture_scope.json` | **scope 게이트.** 어느 작업 디렉토리에서만 수집할지 화이트리스트 |
| `~/.binggupack/capture_buffer.sqlite` | candidate 버퍼(발췌만, TTL 자동 폐기, 단일 파일) |

`binggu_capture_hook.py`는 같은 repo의 `scripts/binggu_capture_persist.py`를 재사용합니다.

---

## 1.5. 가장 쉬운 길 — `binggu init` (권장)

수동 단계(2·3) 대신 `binggu init --agi-memory` 한 번이면 capture profile 생성 + settings.json hook 등록 + **전역 후보수집(AGI memory mode)** 활성화를 자동으로 합니다. 현재 위치만 원하면 `binggu init`(privacy 모드).

```bash
python binggu.py init --agi-memory    # 전역 후보수집(AGI memory mode, 기본 ON)
python binggu.py init                 # 현재 위치만(privacy 모드)
python binggu.py init --global        # --agi-memory 와 동일(작업 전역)
python binggu.py init --no-capture    # 장부만(capture 생략)
```

- **clone 직후(init 전)에는 아무 것도 수집하지 않습니다.** init 을 실행한 사람의 profile 안에서만 동작합니다.
- settings.json 은 백업 후 idempotent 하게 편집됩니다(기존 hook 보존, binggu hook 만 추가).

제어:
```bash
python binggu.py capture status       # ON/OFF · scope · 버퍼 건수 · hook 등록 여부
python binggu.py capture pause        # 일시중지
python binggu.py capture resume       # 재개
python binggu.py capture preview      # 수집 후보 목록 + 저장 명령 안내(저장 0)
python binggu.py capture uninstall    # 완전 제거(rollback) — 장부 ledger.sqlite 는 보존
```

아래 2·3은 `binggu init` 을 쓰지 않고 직접 설정할 때의 수동 경로입니다.

## 2. 설치 (hook 등록 — 수동)

1. 이 repo를 clone 한 절대경로를 확인합니다 (예: `/home/you/binggupack` 또는 `C:/Users/you/binggupack`).
2. 본인 `~/.claude/settings.json`의 `hooks`에 아래를 **MERGE** 합니다 — 통째 덮어쓰기 금지, 기존 hook 배열에 항목만 추가하세요. 예시: `hooks/settings.snippet.json`.

```jsonc
"UserPromptSubmit": [
  { "hooks": [ { "type": "command",
      "command": "python /ABSOLUTE/PATH/TO/binggupack/hooks/binggu_capture_hook.py",
      "async": true } ] }
],
"Stop": [
  { "hooks": [ { "type": "command",
      "command": "python /ABSOLUTE/PATH/TO/binggupack/hooks/binggu_capture_hook.py",
      "async": true } ] }
]
```

- `command` 경로를 1번에서 확인한 절대경로로 교체합니다.
- `async: true` 권장(비차단).
- hook 파일을 다른 곳으로 복사해서 쓰려면, `scripts/` 경로를 환경변수 `BINGGU_SCRIPTS`로 알려주세요. repo의 `hooks/`에 둔 채 호출하면 `../scripts`를 자동으로 찾습니다.

이 단계까지만 하면 hook은 등록됐지만 **플래그가 없어 아무 동작도 하지 않습니다.**

---

## 3. 활성화 (opt-in 2단계)

수집을 실제로 켜려면 두 파일을 직접 만들어야 합니다.

### 3-1. 플래그 켜기

```bash
touch ~/.binggupack/capture_enabled            # macOS/Linux
# Windows PowerShell:  New-Item ~/.binggupack/capture_enabled -ItemType File
```

### 3-2. scope 지정 (`~/.binggupack/capture_scope.json`)

**AGI memory(전역)** 는 `init --agi-memory` 가 아래 전역 형태로 자동 설정합니다(작업 전역 후보수집 = 기본 경험). privacy 형태는 특정 위치만 수집하고 싶을 때 수동 지정.

전역 (AGI memory · 권장):
```json
{ "global": true, "denied_cwd_substrings": [".ssh", ".gnupg", "credentials"] }
```

privacy (특정 위치만):
```json
{
  "global": false,
  "allowed_cwd_prefixes": ["/ABSOLUTE/PATH/TO/your-workspace"],
  "denied_cwd_substrings": [".ssh", "credentials"]
}
```

규칙:
- **global: true** 면 `allowed_cwd_prefixes`를 무시하고 **작업 전역 수집**(deny substring만 차단). AGI memory 기본.
- **deny 우선** — `denied_cwd_substrings` 중 하나라도 현재 작업 디렉토리 경로에 포함되면 수집 안 함(시크릿 디렉토리 등).
- privacy 형태(`global:false`)는 **fail-closed** — `allowed_cwd_prefixes`가 비어 있으면 수집 안 함.
- 어느 형태든 `capture_enabled` 플래그 **AND** scope 통과, 둘 다 만족해야 candidate가 쌓입니다. **시크릿/PII 발화는 분류 단계에서 자동 후보 제외**(scope와 별개).

> 경로는 본인 환경에 맞게 교체하세요. 대소문자 무시 + 슬래시 정규화로 비교합니다.

---

## 4. 동작 / 흐름

1. (활성 상태) 작업 중 발화가 판단/상태/개념 후보로 보이면 `capture_buffer.sqlite`에 **발췌만** 쌓입니다(원문 전문 미저장, 80자 cap).
2. `binggu preview`(또는 hosted preview)로 후보 목록을 보고, 저장할 것을 직접 `SAVE n`으로 confirm → 기존 게이트로 장부 저장.
3. 세션이 끝나면 Stop hook이 현재 후보 **건수만** `~/.binggupack/capture_last_preview.json`에 기록합니다(원문 출력 0).
4. TTL(기본 7일) 지난 후보는 자동 폐기됩니다.

---

## 5. 안전 보장 (셀프테스트로 증명)

```bash
python hooks/binggu_capture_hook.py --selftest      # GATE=GO (8/8)
python scripts/binggu_capture_persist.py            # GATE=GO
```

- 기본 OFF(플래그 없으면 import 전 즉시 종료 → 타 세션 부담 0)
- scope 게이트(타 repo 세션 제외)
- candidate-only(ledger/active/confirmed write 0)
- 원문 전문 미저장(발췌 cap)
- stdout 침묵 + 모든 예외 흡수(항상 exit 0) → 세션 방해/원문 출력 0

---

## 6. Rollback (완전 원복)

원하는 단계까지 되돌릴 수 있습니다. 각 단계는 독립적입니다.

```bash
# (a) 수집만 끄기 — 플래그 제거. hook 등록·버퍼는 유지
rm ~/.binggupack/capture_enabled

# (b) 버퍼 비우기 — candidate 전부 폐기(단일 파일 삭제). 장부(ledger.sqlite)는 무관·미접촉
rm ~/.binggupack/capture_buffer.sqlite

# (c) scope 해제
rm ~/.binggupack/capture_scope.json
rm -f ~/.binggupack/capture_last_preview.json

# (d) hook 완전 제거 — ~/.claude/settings.json 에서 2번에 추가한 항목 삭제(나머지 hook은 그대로)
```

- rollback은 **장부(`ledger.sqlite`)를 건드리지 않습니다.** 자동 저장이 없으므로 되돌릴 저장분도 없습니다.
- `settings.json` 편집 전 백업을 권장합니다: `cp ~/.claude/settings.json ~/.claude/settings.json.bak`.
