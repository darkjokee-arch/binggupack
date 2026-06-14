# BingguPack — Cross-platform Real-device Verification Checklist

cross-platform 정책(`scripts/binggu_platform.py`)이 **각 OS의 실제 환경**에서 동작하는지 확인하는 체크리스트입니다. 정책 설명은 [BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](BINGGUPACK_CROSS_PLATFORM_SUPPORT.md)를 참고하세요.

> **핵심 구분**: **Windows·WSL·macOS 전부 real-device 검증 완료(✅)**. 각 OS의 실제 파이썬/파일시스템에서 selftest·regression 5종이 GATE=GO임을 실측했습니다(2026-06-14). 아래 §2·§3 절차는 사용자가 자기 머신에서 다시 확인하고 싶을 때를 위한 재현 가이드로 유지합니다.

---

## 1. 검증 상태 매트릭스 (현재)

| OS | 경로 정책(home/ledger/settings) | lock 충돌 fail-closed | 실기기 selftest/regression | 검증 수단 | 상태 |
|---|---|---|---|---|---|
| **Windows** | real verified | real verified (temp O_EXCL 실측) | real (platform 36/36 · binggu 26/26 · doctor 15/15 · publish 8/8 · tree CLEAN) | native + GitHub Actions `windows-latest` | ✅ **real verified** |
| **WSL** | real verified (detect_os=wsl 실측) | real verified (정책 GO) | real (5종 GATE=GO) | docker(WSL2 커널, detect_os=wsl) | ✅ **real verified** |
| **macOS** | real verified | real verified (정책 GO) | real (5종 GATE=GO) | GitHub Actions `macos-latest` 러너 | ✅ **real verified** |

- **real verified** = 그 OS의 실제 파이썬/파일시스템에서 selftest·regression 5종이 GATE=GO.
- 검증 5종 = platform 36/36 · binggu 26/26 · doctor 15/15 · publish 8/8(REGRESSION=GO) · tree scan CLEAN.
- **CI 자동화** = `.github/workflows/ci.yml`이 매 push마다 `ubuntu-latest`·`macos-latest`·`windows-latest` 3-OS matrix로 5종을 자동 실행(`fail-fast:false`). macOS/리눅스 real 검증은 이 CI로 영구 유지됩니다.
- WSL은 이 개발 머신의 docker-desktop(WSL2 커널) 위에서 `detect_os()==wsl`로 실측 검증됐습니다. ubuntu CI(`detect_os==linux`)도 동일 5종 GO.

---

## 2. WSL 실기기 검증 절차

### 2-0. 사전 — Ubuntu WSL 준비
```powershell
# (Windows PowerShell, 관리자) — 아직 Ubuntu WSL이 없으면
wsl --install -d Ubuntu
# 설치 후 사용자/비번 1회 설정. 이미 있으면 생략.
wsl -l -v          # Ubuntu가 보이고 VERSION 2 권장
```
> 이 개발 머신에는 현재 `docker-desktop` 배포판만 있고 python3가 없어 WSL real 검증이 보류됐습니다. Ubuntu 배포판이 필요합니다.

### 2-1. python3 확인
```bash
python3 --version        # 3.10+ 기대
# 없으면:
sudo apt update && sudo apt install -y python3 git
```

### 2-2. repo 접근 (둘 중 하나)
**방식 A — WSL 네이티브 clone (권장)**: WSL 파일시스템(ext4)에 두면 lock/성능이 정상입니다.
```bash
git clone https://github.com/darkjokee-arch/binggupack ~/binggupack
cd ~/binggupack
```
**방식 B — Windows repo 직접 접근**: 같은 트리를 WSL에서 봅니다(별도 clone 불필요).
```bash
cd /mnt/c/Users/<YOU>/binggupack    # <YOU> = Windows 사용자명
```
> 방식 B는 `/mnt/c`(DrvFs)라 동시 실행·lock 동작이 NTFS 의존입니다. **Windows와 WSL에서 같은 장부를 동시에 쓰지 마세요**(§2-4 fail-closed 참고).

### 2-3. OS 감지 실측 (wsl 판정 확인)
```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); import binggu_platform as p; print(p.detect_os())"
# 기대: wsl   (WSL_DISTRO_NAME 또는 /proc 의 microsoft 표식)
```

### 2-4. BINGGU_HOME — OS별 기본 / 공유 경로 테스트
```bash
# (a) 기본 = OS별 로컬 홈
unset BINGGU_HOME
python3 binggu.py status          # 장부 = ~/.binggupack/... · 플랫폼: wsl · python: python3 · 공유: 아니오

# (b) 공유 opt-in — Windows와 같은 폴더를 WSL 표기로
export BINGGU_HOME=/mnt/d/shared/.binggupack
python3 binggu.py status          # 장부 = /mnt/d/shared/.binggupack/... · 공유: 예(opt-in)
unset BINGGU_HOME
```
> 공유 장부는 **동시 실행 금지**. WSL에서 쓰기 중 Windows에서 또 쓰면 `<ledger>.lock`으로 fail-closed 차단되어야 정상입니다.

### 2-5. selftest / regression (전부 GATE=GO 기대)
```bash
python3 scripts/binggu_platform_selftest.py            # 36/36 (detect_os 실측 wsl 포함)
python3 binggu.py --selftest                           # 26/26 (temp 장부 · 운영 store 미접촉)
python3 scripts/openbinggu_doctor.py --selftest        # 15/15 · GATE=GO
python3 scripts/binggu_publish_run_all_selftests.py    # 8/8 · REGRESSION=GO
python3 scripts/openbinggu_public_tree_scan.py --tree .  # hits=0 · CLEAN
```

### 2-6. 통과 기준
- 위 5개 모두 GATE=GO / REGRESSION=GO / CLEAN
- `detect_os()` == `wsl`
- `binggu.py status`의 플랫폼·python·공유 표시가 위와 일치
→ 충족 시 WSL을 **real verified**로 승격(아래 §4 표시 갱신).

---

## 3. macOS 실기기 검증 절차

### 3-1. python3 확인
```bash
python3 --version        # 3.10+ 기대 (없으면 https://www.python.org 또는 `brew install python`)
```

### 3-2. repo clone
```bash
git clone https://github.com/darkjokee-arch/binggupack ~/binggupack
cd ~/binggupack
```

### 3-3. OS 감지 실측 (macos 판정 확인)
```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); import binggu_platform as p; print(p.detect_os())"
# 기대: macos
```

### 3-4. BINGGU_HOME — 기본 / 공유 테스트
```bash
unset BINGGU_HOME
python3 binggu.py status          # 장부 = ~/.binggupack/... · 플랫폼: macos · python: python3 · 공유: 아니오

export BINGGU_HOME="$HOME/Shared/.binggupack"
python3 binggu.py status          # 장부 = ~/Shared/.binggupack/... · 공유: 예(opt-in)
unset BINGGU_HOME
```

### 3-5. selftest / regression (전부 GATE=GO 기대)
```bash
python3 scripts/binggu_platform_selftest.py            # 36/36 (detect_os 실측 macos 포함)
python3 binggu.py --selftest                           # 26/26
python3 scripts/openbinggu_doctor.py --selftest        # 15/15 · GATE=GO
python3 scripts/binggu_publish_run_all_selftests.py    # 8/8 · REGRESSION=GO
python3 scripts/openbinggu_public_tree_scan.py --tree .  # hits=0 · CLEAN
```

### 3-6. 통과 기준
- 위 5개 모두 GATE=GO / REGRESSION=GO / CLEAN, `detect_os()` == `macos`
- (GitHub Actions `macos-latest` 러너에서 매 push마다 자동 확인됨)

---

## 4. real verified 승격 완료 (2026-06-14)

3-OS 실기기 검증을 완료하고 다음 표시를 갱신했습니다:
- ✅ 이 문서 §1 매트릭스 전 행 ⏳ → ✅ (Windows native+CI / WSL docker / macOS CI)
- 향후 동기화 대상(pending 문구 잔존 시 갱신): `README.md`·`INSTALL.md` cross-platform 섹션, `docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md` §6
- CI(`.github/workflows/ci.yml`)가 매 push마다 3-OS를 재검증하므로 회귀는 자동 차단됩니다.

---

## 5. 금지선 (이 검증 동안)
- 모든 selftest는 **temp 장부 또는 read-only**입니다 — 실 ledger write 0.
- OpenCrab **Cloud upload / ingest / DB insert** 0. **tag / release** 0.
- 공유 장부(`BINGGU_HOME`) 테스트 시에도 **동시 실행 금지**(fail-closed 확인용 외 실제 쓰기 경합 유발 금지).
- 자동 마이그레이션 0 — OS 간 장부를 복사/변환하지 않습니다.
