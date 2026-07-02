# -*- coding: utf-8 -*-
"""정본 ledger 스키마 — 패키지 경로 shim.

정본 구현은 `scripts/binggu_schema.py` 에 있다(대다수 소비자가 scripts/ 이기 때문).
이 모듈은 패키지 경로(`binggupack.storage.schema`)에서도 동일 심볼을 쓰도록 re-export 한다.

    from binggupack.storage.schema import apply_schema, SCHEMA_VERSION, schema_version
"""
from __future__ import annotations

import os
import sys

# repo 루트/scripts 를 sys.path 에 얹어 bare-name import 를 해소.
# binggupack/storage/schema.py → binggupack/ → <repo>
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggu_schema import (  # noqa: E402,F401  (정본 re-export)
    SCHEMA_VERSION,
    apply_schema,
    schema_version,
)

__all__ = ["SCHEMA_VERSION", "apply_schema", "schema_version"]
