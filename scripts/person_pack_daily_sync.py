# -*- coding: utf-8 -*-
"""person_pack_daily_sync — 개인 온톨로지 팩 일 1회 자동 갱신 orchestrator.

각 사용자의 ~/.claude/memory 자산을 재조립(person_pack_assemble) → 변경 버킷만
제자리 교체 업로드(person_pack_split_upload --daily·안 바뀌면 NO_CHANGE·네트워크 0).
스케줄러(pythonw·일 1회)에서 호출한다. pythonw 는 콘솔이 없어 stdout 이 None →
모든 출력을 로그 파일로 리다이렉트해야 print 가 안전.
로그: <home>/.claude/logs/person_daily_sync.log

── Track R: near-real-time 배선점(기본 OFF) ─────────────────────────────────────
일 1회(daily) 외에 '짧은 주기 save-트리거'로 팩을 near-real-time 갱신할 수 있는 **배선점**만
스크립트 레벨에 둔다. 실제 이벤트 배선(save hook → 이 seam 호출)·스케줄러·인프라 신설은
**owner 결정** — 이 파일은 seam(run_on_save_trigger) + 옵트인 게이트 + debounce 만 제공한다.

  · 기본 OFF — person_pack.json 에 "near_real_time_sync": true 가 없으면 어떤 트리거도 no-op
    (DISABLED_OFF). 활성화는 owner 가 config 를 켜는 행위로만 발생(AI 자동 활성화 0).
  · cli/daily.py 및 sync_anywhere_vendor 대상 모듈(hosted/workers/anywhere/core 벤더)은 **미편집**
    (7/12 LFI drift 재발 방지). 이 파일은 기존 person_pack_assemble/split_upload 정본을 호출만 한다.
  · debounce — 옵트인 상태라도 min_interval_sec(기본 300s) 안의 연속 save 는 스킵(throttle).
    상태는 <home>/.claude/state/person_nrt_last.json 하나에만 기록(운영 장부·벤더 미접촉).
"""
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
LOG = Path.home() / ".claude" / "logs" / "person_daily_sync.log"

# near-real-time 배선점 상수(스크립트 레벨 — 인프라 아님).
_PACK_CONFIG = Path.home() / ".binggupack" / "person_pack.json"
_NRT_STATE = Path.home() / ".claude" / "state" / "person_nrt_last.json"
_NRT_DEFAULT_INTERVAL_SEC = 300      # 옵트인 시에도 5분 debounce(연속 save 폭주 방지)


def _run_sync(daily=True):
    """정본 파이프라인 호출 — assemble → split_upload(--daily). daily 경로 정본 미편집(호출만)."""
    import person_pack_assemble as ASM
    import person_pack_split_upload as PSU
    print("[1/2] assemble → person_split_sources ...")
    ASM.main()
    print("[2/2] split_upload --daily (변경 버킷만) ...")
    rc = PSU.main(daily=True)
    print("rc=%s" % rc)
    return rc


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    old = sys.stdout, sys.stderr
    rc = 1
    with open(LOG, "a", encoding="utf-8") as f:
        sys.stdout = sys.stderr = f
        try:
            print("\n=== person_pack_daily_sync %s ===" % time.strftime("%Y-%m-%dT%H:%M:%S"))
            rc = _run_sync(daily=True)
        except Exception:
            traceback.print_exc()
            rc = 1
        finally:
            sys.stdout, sys.stderr = old
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# near-real-time 배선점 — 기본 OFF. 활성화는 owner 가 person_pack.json 을 켜는 것으로만.
# ─────────────────────────────────────────────────────────────────────────────
def near_real_time_config():
    """near-real-time 옵트인 상태 해석(read-only). 기본 OFF.

    반환 {enabled, min_interval_sec, source}. person_pack.json 이 없거나 키가 없으면 enabled=False.
    owner 가 {"near_real_time_sync": true, "near_real_time_min_interval_sec": 300} 를 넣어야 켜진다.
    """
    cfg = {}
    try:
        if _PACK_CONFIG.is_file():
            cfg = json.loads(_PACK_CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        cfg = {}
    enabled = bool(cfg.get("near_real_time_sync", False))
    interval = cfg.get("near_real_time_min_interval_sec", _NRT_DEFAULT_INTERVAL_SEC)
    try:
        interval = max(1, int(interval))
    except (TypeError, ValueError):
        interval = _NRT_DEFAULT_INTERVAL_SEC
    return {"enabled": enabled, "min_interval_sec": interval,
            "source": str(_PACK_CONFIG) if cfg else "default(off)"}


def _nrt_last_run():
    try:
        if _NRT_STATE.is_file():
            return float(json.loads(_NRT_STATE.read_text(encoding="utf-8")).get("last_run", 0))
    except (OSError, ValueError, TypeError):
        pass
    return 0.0


def _nrt_mark_run(ts):
    try:
        _NRT_STATE.parent.mkdir(parents=True, exist_ok=True)
        _NRT_STATE.write_text(json.dumps({"last_run": ts}), encoding="utf-8")
    except OSError:
        pass


def run_on_save_trigger(now=None, force_config=None, do_sync=None):
    """save-트리거 seam — save hook 이 호출할 수 있는 진입점. **기본 OFF**.

    흐름:
      · 옵트인 아님 → DISABLED_OFF (no-op · 정본 파이프라인 호출 0).
      · 옵트인이지만 debounce 창(min_interval_sec) 안 → THROTTLED (스킵).
      · 옵트인 + debounce 통과 → 정본 sync 실행(assemble→split_upload --daily) → SYNCED.

    파라미터(테스트/주입용): now(현재 epoch), force_config(config dict 주입), do_sync(sync 콜러블 주입).
    실 이벤트 배선(save hook → 이 함수)·스케줄 등록은 owner 결정 — 이 함수는 seam 일 뿐이다.
    """
    now = time.time() if now is None else now
    cfg = force_config if force_config is not None else near_real_time_config()
    if not cfg.get("enabled"):
        return {"status": "DISABLED_OFF", "note": "near_real_time_sync 옵트인 없음 — no-op(활성화=owner)"}
    last = _nrt_last_run()
    interval = cfg.get("min_interval_sec", _NRT_DEFAULT_INTERVAL_SEC)
    if now - last < interval:
        return {"status": "THROTTLED", "since_last_sec": round(now - last, 1),
                "min_interval_sec": interval,
                "note": "debounce 창 안 — 스킵(연속 save 폭주 방지)"}
    runner = do_sync if do_sync is not None else _run_sync
    rc = runner(daily=True)
    _nrt_mark_run(now)
    return {"status": "SYNCED", "rc": rc, "at": now}


def _wiring_status():
    cfg = near_real_time_config()
    return {"daily": "always-on (schtasks 일1회 · main())",
            "near_real_time": cfg,
            "seam": "run_on_save_trigger(save hook 이 호출 가능 · 실 배선은 owner 결정)",
            "debounce_state": str(_NRT_STATE),
            "guard": "기본 OFF · cli/daily.py 및 벤더 모듈 미편집 · 인프라 신설 0"}


def _selftest():
    """near-real-time 배선점 게이트 검증(정본 sync 는 주입 콜러블로 대체 · 실 업로드/네트워크 0)."""
    ok = True

    def ck(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))

    calls = {"n": 0}

    def fake_sync(daily=True):
        calls["n"] += 1
        return 0

    # (1) 기본 OFF(옵트인 주입 없음) → DISABLED_OFF · 정본 sync 호출 0.
    r = run_on_save_trigger(now=1000.0, force_config={"enabled": False}, do_sync=fake_sync)
    ck(r["status"] == "DISABLED_OFF" and calls["n"] == 0, "기본 OFF → DISABLED_OFF · sync 호출 0")

    # (2) 옵트인 + debounce 통과(last=0) → SYNCED · sync 1회.
    global _nrt_last_run, _nrt_mark_run
    _orig_last, _orig_mark = _nrt_last_run, _nrt_mark_run
    _fake_last = {"v": 0.0}
    _nrt_last_run = lambda: _fake_last["v"]                       # noqa: E731
    _nrt_mark_run = lambda ts: _fake_last.__setitem__("v", ts)    # noqa: E731
    try:
        r2 = run_on_save_trigger(now=1000.0,
                                 force_config={"enabled": True, "min_interval_sec": 300},
                                 do_sync=fake_sync)
        ck(r2["status"] == "SYNCED" and calls["n"] == 1, "옵트인+debounce 통과 → SYNCED · sync 1회")

        # (3) 같은 창 안 재트리거(now=1100 < 1000+300) → THROTTLED · sync 추가 0.
        r3 = run_on_save_trigger(now=1100.0,
                                 force_config={"enabled": True, "min_interval_sec": 300},
                                 do_sync=fake_sync)
        ck(r3["status"] == "THROTTLED" and calls["n"] == 1, "debounce 창 안 → THROTTLED · sync 추가 0")

        # (4) 창 밖(now=1400 >= 1000+300) → SYNCED · sync 1회 추가.
        r4 = run_on_save_trigger(now=1400.0,
                                 force_config={"enabled": True, "min_interval_sec": 300},
                                 do_sync=fake_sync)
        ck(r4["status"] == "SYNCED" and calls["n"] == 2, "debounce 창 밖 → SYNCED · sync 1회 추가")
    finally:
        _nrt_last_run, _nrt_mark_run = _orig_last, _orig_mark

    print("person_pack_daily_sync near-real-time selftest: %s" % ("GO" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--wiring-status" in sys.argv:
        print(json.dumps(_wiring_status(), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if "--near-real-time" in sys.argv:
        # save-트리거 seam 을 수동 실행(옵트인 게이트가 그대로 적용 — 기본 OFF).
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _res = run_on_save_trigger()
        print(json.dumps(_res, ensure_ascii=False))
        raise SystemExit(0)
    raise SystemExit(main())
