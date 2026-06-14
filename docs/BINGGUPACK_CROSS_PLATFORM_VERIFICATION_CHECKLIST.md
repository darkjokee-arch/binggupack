# BingguPack — Cross-platform Real-device Verification Checklist

cross-platform 정책(`scripts/binggu_platform.py`)이 **각 OS의 실제 환경**에서 동작하는지 확인하는 체크리스트입니다. 정책 설명은 [BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](BINGGUPACK_CROSS_PLATFORM_SUPPORT.md)를 참고하세요.

> **핵심 구분**: 지금까지 **Windows는 실기기 검증(real)**, **WSL/macOS는 synthetic 검증(입력 주입)만** 완료된 상태입니다. 아래 절차는 WSL/macOS를 **실기기에서** 직접 돌려 `real verified`로 승격하기 위한 것입니다.

---

## 1. 검증 상태 매트릭스 (현재)

| OS | 경로 정책(home/ledger/settings) | lock 충돌 fail-closed | 실기기 selftest/regression | 상태 |
|---|---|---|---|---|
| **Windows** | real verified | real verified (temp O_EXCL 실측) | real (platform 36/36 · binggu 26/26 · publish 8/8 · tree CLEAN) | ✅ **real verified** |
| **WSL** | **synthetic only** (os_name/HOME 주입) | synthetic (정책) / Windows에서 실 lock 실측 | **pending** | ⏳ **synthetic verified only** |
| **macOS** | **synthetic only** (os_name/HOME 주입) | synthetic (정책) | **pending** | ⏳ **synthetic verified only** |

- **synthetic verified** = `binggu_platform_selftest.py`가 OS 이름과 홈 경로를 주입해 정책의 정확성을 한 머신에서 검증(36/36). 경로 규칙은 옳음이 증명됨.
- **real verified** = 그 OS의 실제 파이썬/파일시스템에서 selftest·regression이 GATE=GO.
- WSL/macOS는 **real-device verification pending** — 아래 절차로 사용자가 직접 승격합니다.

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
python3 scripts/binggu_publish_run_all_selftests.py    # 8/8 · REGRESSION=GO
python3 scripts/openbinggu_public_tree_scan.py --tree .  # hits=0 · CLEAN
```

### 2-6. 통과 기준
- 위 4개 모두 GATE=GO / REGRESSION=GO / CLEAN
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
python3 scripts/binggu_publish_run_all_selftests.py    # 8/8 · REGRESSION=GO
python3 scripts/openbinggu_public_tree_scan.py --tree .  # hits=0 · CLEAN
```

### 3-6. 통과 기준
- 위 4개 모두 GATE=GO / REGRESSION=GO / CLEAN, `detect_os()` == `macos`
→ 충족 시 macOS를 **real verified**로 승격.

---

## 4. real verified 승격 시 갱신할 곳

실기기 통과를 확인하면 다음 표시를 함께 갱신합니다(owner 승인 후):
- 이 문서 §1 매트릭스의 해당 행 ⏳ → ✅
- `README.md` cross-platform 섹션의 "WSL/macOS real-device verification pending" 문구
- `INSTALL.md` cross-platform 검증 섹션의 동일 문구
- `docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md` §6 의 pending 주석

---

## 5. 금지선 (이 검증 동안)
- 모든 selftest는 **temp 장부 또는 read-only**입니다 — 실 ledger write 0.
- OpenCrab **Cloud upload / ingest / DB insert** 0. **tag / release** 0.
- 공유 장부(`BINGGU_HOME`) 테스트 시에도 **동시 실행 금지**(fail-closed 확인용 외 실제 쓰기 경합 유발 금지).
- 자동 마이그레이션 0 — OS 간 장부를 복사/변환하지 않습니다.
