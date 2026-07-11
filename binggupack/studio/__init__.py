# -*- coding: utf-8 -*-
"""Binggu Studio Preview — 로컬 read-only 웹 UI (Python stdlib only).

공개 진입점은 server.serve / server.build_server. Daily Console read model 재사용.
"""
from binggupack.studio.server import build_server, serve, studio_url

__all__ = ["serve", "build_server", "studio_url"]
