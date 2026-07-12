# -*- coding: utf-8 -*-
"""Binggu App Path — transport-independent read-only Pack Service Core (v1.21-A).

미래의 HTTPS MCP / @BingguPack 앱이 호출할 5개 read 도구(pack_list·pack_summary·evidence_search·
node_edge_lookup·handoff_context)의 순수 core. HTTP/MCP/OAuth/cloud/ledger 에 의존하지 않는다.
"""
from binggupack.app.read_core import PackRepository, PackService

__all__ = ["PackRepository", "PackService"]
