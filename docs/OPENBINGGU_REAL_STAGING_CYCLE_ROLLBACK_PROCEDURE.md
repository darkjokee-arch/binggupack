# Real staging 실연 — rollback 복구 절차 (실행 전 문서화, 2026-06-11)

> **v0.8 쓰기 루프 1사이클(conv_save)에도 동일 적용** — 사이클 러너가 실행 직전 `snap_v08_before_<checksum>.sqlite`를 자동 확보. 복구 = 아래 §복구 절차에서 스냅샷 파일명만 교체. checksum 원복 검증 절차 동일(6/11 apply_once에서 기실증). v0.8 추가 변경 표면: nodes·edges·evidence·applied_registry·audit_log + **judgment_reviews**(판단 due) — 전부 단일 DB 파일이므로 스냅샷 복원으로 일괄 원복.
> 스냅샷 평문 잔존 정책: 실연 검증 완료 후 owner 보고 시점에 보존/파기 결정(민감 노드 tombstone 시 이전 스냅샷 동반 파기).

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
