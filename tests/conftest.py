"""pytest 전역 안전망 — 운영 홈(~/.binggupack) 오염 원천 차단.

배경(2026-07-13 실측): `--ledger <tmp>` 만으로 격리했다고 믿은 테스트들이 save-n 앵커
(last_preview_candidates.json)·gate log 를 전역 home 에 써서, pytest 를 돌릴 때마다
owner 의 '세이브 n' 발화 도장이 fixture 문장으로 덮였다(승인 게이트 실사용 결함).
데이터(ledger)와 앵커(gate_home)가 다른 축을 쓰는 비대칭이 근본 원인 — 코드 쪽은
ledger-home 통일로 수정했고, 여기서는 어떤 테스트가 격리를 빠뜨려도 운영 홈에
닿지 않도록 BINGGU_HOME 을 테스트별 temp 로 강제한다.

- 각 테스트의 os.environ 에 주입 → subprocess 계열(_run 의 dict(os.environ) 복사)도 상속.
- 명시적으로 다른 BINGGU_HOME 이 필요한 테스트는 env_extra/monkeypatch 로 override 하면 됨.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_binggu_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("bgp_iso_home")
    monkeypatch.setenv("BINGGU_HOME", str(home))
    yield
