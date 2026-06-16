# BingguPack — Cross-platform Support (Windows / WSL / macOS)

BingguPack은 Windows 중심으로 개발됐지만, **Windows · WSL · macOS에서 같은 정책으로 안전하게** 동작합니다. 이 문서는 OS별 사용법과 같은 장부를 공유하는 방법, 그리고 지켜야 할 제약을 정리합니다.

정책의 단일 원천은 `scripts/binggu_platform.py`이며, `scripts/binggu_platform_selftest.py`로 증명됩니다(아래 [검증](#검증)).

---

## 1. 한눈에 / TL;DR

| 항목 | Windows | WSL | macOS |
|---|---|---|---|
| 권장 python 런처 | `py` | `python3` | `python3` |
| 기본 장부 위치 | `%USERPROFILE%\.binggupack` | `~/.binggupack` | `~/.binggupack` |
| settings.json (Claude hook) | `%USERPROFILE%\.claude\settings.json` | `~/.claude/settings.json` | `~/.claude/settings.json` |

- **기본은 OS별 로컬 홈입니다.** 각 OS는 자기 홈 아래 `.binggupack`를 씁니다.
- **같은 장부 공유는 자동으로 추측하지 않습니다.** 공유하려면 `BINGGU_HOME`을 명시(opt-in)해야 합니다.
- **Windows 기존 동작은 그대로 보존됩니다** — `BINGGU_HOME` 미설정 시 경로는 종전과 동일합니다.

---

## 2. OS별 사용법

### Windows (PowerShell / cmd)

```powershell
py scripts\openbinggu_doctor.py --selftest
py binggu.py init --agi-memory
py binggu.py status
```

> PowerShell에서는 쉼표가 든 인자를 따옴표로 감싸세요: `--pick "1,2"`.

### WSL (Ubuntu 등 · bash)

```bash
python3 scripts/openbinggu_doctor.py --selftest
python3 binggu.py --selftest
python3 scripts/openbinggu_public_tree_scan.py --tree .
```

> WSL은 보통 `python3`입니다. `py`는 Windows 전용 런처라 WSL/macOS에는 없습니다.

### macOS (Terminal · zsh/bash)

```bash
python3 scripts/openbinggu_doctor.py --selftest
python3 binggu.py init --agi-memory
python3 binggu.py status
```

`binggu.py status`는 현재 플랫폼·권장 python 런처·공유 장부(opt-in) 여부를 함께 표시합니다.

---

## 3. 같은 장부를 OS 간 공유하기 — `BINGGU_HOME` (opt-in)

기본은 OS별 로컬 홈이라 Windows와 WSL은 **서로 다른** 장부를 씁니다. 한 장부를 공유하려면 **양쪽에서 같은 위치를** `BINGGU_HOME`으로 가리킵니다. (자동 추측은 하지 않습니다 — 반드시 명시해야 합니다.)

```powershell
# Windows (PowerShell) — 예: D 드라이브의 공유 폴더
$env:BINGGU_HOME = "D:\shared\.binggupack"
py binggu.py status
```

```bash
# WSL — 같은 폴더를 WSL 표기로
export BINGGU_HOME=/mnt/d/shared/.binggupack
python3 binggu.py status
```

`BINGGU_HOME`이 설정되면 ledger / capture buffer / publish 경로가 **모두 그 아래**를 씁니다. 경로 변환(예: `C:\...` ↔ `/mnt/c/...`)은 **표시·안내용 helper**(`to_wsl_path`/`from_wsl_path`/`display_path`)로만 제공되며, **파일을 자동으로 옮기거나 변환하지 않습니다.**

### ⚠️ OS 간 동시 실행 금지 (fail-closed)

같은 장부를 공유할 수 있지만 **동시에 두 OS/터미널에서 쓰기를 실행하면 안 됩니다.** SQLite ledger는:

- `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` 으로 짧게만 대기하고,
- 쓰기 진입 시 `<ledger>.lock` (O_EXCL) lock 파일을 잡습니다.

다른 실행이 이미 lock을 잡고 있으면 **자동 재시도·우회 없이 즉시 차단**(`staging_write_locked: concurrent writer detected`)합니다. 이것이 **fail-closed**입니다. 한쪽을 끝낸 뒤 다시 실행하세요.

> 같은 장부를 마이그레이션하지 마세요. BingguPack은 OS 간 장부를 자동으로 복사·변환하지 않습니다(데이터 손상 방지).

---

## 4. OpenCrab Desktop / Claude hook 은 OS별 세션 기준

- **Claude capture hook**은 그 OS의 `~/.claude/settings.json`(Windows는 `%USERPROFILE%\.claude\settings.json`)에 등록됩니다. 공유 장부(`BINGGU_HOME`)를 쓰더라도 **hook·세션은 각 OS의 로컬 세션**을 따릅니다.
- `binggu init`이 등록하는 hook 실행 명령의 python 런처도 OS별입니다(Windows `py`, WSL/macOS `python3`).
- **OpenCrab Desktop**은 각 OS에 설치된 앱이며 세션/앱 위치가 OS마다 다릅니다.
- **OpenCrab Cloud / ingest 는 계속 HOLD** — cross-platform 지원과 무관하게 Cloud 업로드·재인제스트는 보류이며, 어떤 OS에서도 AI/MCP/CLI 자동 업로드를 하지 않습니다.

---

## 5. 경로 정책 요약 (binggu_platform.py)

| 함수 | 역할 |
|---|---|
| `detect_os()` | `windows` / `wsl` / `macos` / `linux` 판정 (WSL은 `WSL_DISTRO_NAME` 또는 `/proc` 의 microsoft 표식) |
| `binggu_home()` | `BINGGU_HOME` 우선, 없으면 OS별 홈/`.binggupack` |
| `default_ledger()` / `default_settings()` | 위 정책 기반 ledger / settings 경로 |
| `python_cmd()` | 안내용 런처 — Windows `py`, 그 외 `python3` |
| `to_wsl_path` / `from_wsl_path` / `display_path` | 표시·안내용 경로 변환 (자동 마이그레이션 0) |
| `apply_ledger_pragmas()` | WAL + busy_timeout (동시 접근 fail-closed 일관) |
| `lock_conflict_message()` | lock 경합 시 사용자 안내 문구 |

---

## 6. 검증

```bash
# Windows
py scripts\binggu_platform_selftest.py            # cross-platform 정책 40/40 GATE=GO
py scripts\binggu_publish_run_all_selftests.py    # 회귀 묶음 13/13 REGRESSION=GO
py scripts\openbinggu_public_tree_scan.py --tree .

# WSL / macOS
python3 scripts/binggu_platform_selftest.py
python3 scripts/openbinggu_doctor.py --selftest
python3 binggu.py --selftest
python3 scripts/openbinggu_public_tree_scan.py --tree .
```

**WSL/macOS path policy는 synthetic 테스트로 커버됩니다.** `binggu_platform_selftest.py`는 OS 이름과 홈 디렉터리(`USERPROFILE`/`HOME`)를 **주입**해 Windows·WSL·macOS 경로 규칙을 한 머신에서 모두 검증합니다(가짜 입력으로 정책의 정확성 확인). lock 충돌 fail-closed는 temp 장부에 실제 O_EXCL lock을 만들어 실측합니다(운영 store 미접촉).

> macOS path policy covered by synthetic tests.
> WSL path policy covered by synthetic tests (이 개발 머신에는 WSL python3 미설치 — 실 실행 검증은 WSL에 python3 설치 후 위 명령으로 가능).

### 검증 상태 (real vs synthetic)

| OS | 상태 |
|---|---|
| Windows | ✅ **real verified** (native + GitHub Actions `windows-latest`, selftest 5종 GATE=GO) |
| WSL | ✅ **real verified** (docker WSL2 커널, `detect_os==wsl` 실측) |
| macOS | ✅ **real verified** (GitHub Actions `macos-latest` 러너) |

3-OS 전부 2026-06-14 real-device 검증 완료. 매 push마다 CI(`.github/workflows/ci.yml`)가 `ubuntu`/`macos`/`windows` matrix로 selftest 5종을 자동 재검증합니다. 자기 머신 재현 절차는 **[BINGGUPACK_CROSS_PLATFORM_VERIFICATION_CHECKLIST.md](BINGGUPACK_CROSS_PLATFORM_VERIFICATION_CHECKLIST.md)** 를 참고하세요.
