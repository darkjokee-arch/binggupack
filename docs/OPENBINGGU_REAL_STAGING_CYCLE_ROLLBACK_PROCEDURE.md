# Real staging 1사이클 실연 — rollback 복구 절차 (실행 전 문서화, 2026-06-11)

> owner 조건 8 "rollback 복구 절차는 실행 전 문서화" 이행 문서. 사이클 실행 **전에** 작성됨.

## 대상
- DB: `tmp/real_staging/openbinggu_real_staging.sqlite` (운영과 물리 분리)
- 사이클 실행 직전 자동 확보 스냅샷: `tmp/real_staging/snapshots/snap_cycle_before_<checksum>.sqlite`

## 복구 절차 (수동 1줄 + 검증 2줄)
```powershell
# 1) 복구 (스냅샷을 DB 위에 복사)
Copy-Item tmp\real_staging\snapshots\snap_cycle_before_<checksum>.sqlite tmp\real_staging\openbinggu_real_staging.sqlite -Force
# 2) WAL/SHM 잔재 제거 (있으면)
Remove-Item tmp\real_staging\openbinggu_real_staging.sqlite-wal, tmp\real_staging\openbinggu_real_staging.sqlite-shm -ErrorAction SilentlyContinue
# 3) 검증: store_checksum == 스냅샷 파일명의 <checksum> 이면 원상복구 완료
python -c "import sys; sys.path.insert(0,'scripts'); from openbinggu_staging_write_selftest import StagingDB; db=StagingDB(r'tmp/real_staging/openbinggu_real_staging.sqlite'); print(db.store_checksum())"
```

## 검증 근거 (이미 실증된 절차)
- 동일 절차(snapshot copy → checksum 대조)는 6/11 `openbinggu_real_staging_apply_once` GATE=GO에서 **rollback_checksum == before_checksum 원상복구 실증 완료** (traj_20260611_binggupack_real_staging_persist).

## 사이클이 추가하는 것 (복구 시 함께 사라지는 것)
edge_proposals 테이블 row(승인분) + edges 1건(볼펜 확정) + audit_log 항목들 — 전부 스냅샷 복원으로 일괄 원복. 운영 store는 사이클이 건드리지 않으므로 복구 불요.
