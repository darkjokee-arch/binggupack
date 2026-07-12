# -*- coding: utf-8 -*-
"""Binggu App Path read core — transport-independent read-only Pack Service Core.

입력: pack repository = pack 디렉터리들의 상위 디렉터리(§3-A). 각 pack 디렉터리는 canonical layout
(manifest.json + graph/nodes.jsonl + graph/edges.jsonl + evidence/index.jsonl · flat fallback 허용).

전부 read-only: 파일 read 만 · write/network/cache/index/log/temp 생성 0 · ledger 미접촉. pack 안전
게이트(manifest·JSON parse·pack_id 정규식·path traversal·symlink·크기/행수 제한·contract validate_pack
STOP 배제·required 파일·public-safe secret/PII scan)를 통과한 pack 만 서비스한다. malformed/unsafe pack 은
조용히 서비스하지 않는다(부분 서비스 0). candidate 상태 보존 · confirmed 자동 승격 0 · raw path/source
pointer/secret/PII 출력 0. handoff 는 Phase 3 handoff guide 형식 단일 정본을 코드로 생성한다.
"""
import base64
import json
import os
import re
import unicodedata

from binggupack.app import models as M

# pack_id/디렉터리 이름(파일시스템) — 경로 구분자·상위참조 배제.
_SAFE_PACK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# node_id(입력·dict key lookup·파일시스템 미사용) — ':' 등 관대하되 제어/구분자 배제.
_SAFE_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,255}$")


# ── 안전 텍스트 helper ──────────────────────────────────────────────────────────────
def _excerpt(text, cap=M.EXCERPT_CAP):
    """control/format(bidi) 제거 + 공백 정규화 + cap 절단(…). daily.safe_excerpt 규약 재사용."""
    try:
        from binggupack.cli import daily
        return daily.safe_excerpt(text, cap)
    except Exception:
        if not text:
            return ""
        s = unicodedata.normalize("NFC", str(text))
        out = [(" " if unicodedata.category(c) in ("Cc", "Cf") else c) for c in s]
        s = " ".join("".join(out).split())
        return (s[:cap].rstrip() + "…") if len(s) > cap else s


def _tokens(text):
    return [t for t in re.split(r"\W+", (text or "").lower()) if t]


def _lexical_score(qtokens, text):
    """deterministic term 겹침 비율(embedding/network 0). 한국어 조사(마진≠마진이) 대응을 위해
    query 토큰이 text 에 substring 으로 포함되는지로 판정한다(term-frequency lexical)."""
    if not qtokens or not text:
        return 0.0
    low = str(text).lower()
    hit = sum(1 for q in qtokens if q in low)
    return round(hit / len(qtokens), 4)


# ── public-safe(secret/PII) 텍스트 검사 ─────────────────────────────────────────────
def _public_safe_line(line):
    """pack .jsonl/manifest 라인의 secret/PII 검사(_TEXT_EXT 에 .jsonl 부재 → 직접 검사).
    public_tree_scan 의 정본 규칙(_secret_kv_match·_CONTENT·_NAMEPATH) 재사용. 위반 → False."""
    try:
        from binggupack.safety.public_tree_scan import _secret_kv_match, _CONTENT, _NAMEPATH
    except Exception:
        return True   # 게이트 부재 시 보수적 통과(pack 은 이미 로컬 publish guard 후 export 전제)
    if _secret_kv_match(line):
        return False
    for _code, rx in _CONTENT:
        if rx.search(line):
            return False
    for _code, rx in _NAMEPATH:
        if rx.search(line):
            return False
    return True


# ── opaque cursor(offset 만 · filesystem path/pack_id 목록 미포함) ─────────────────
def _encode_cursor(offset):
    return base64.urlsafe_b64encode(("o:%d" % offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor):
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        if raw.startswith("o:"):
            n = int(raw[2:])
            return n if n >= 0 else 0
    except Exception:
        return 0
    return 0


# ── pack 데이터 ─────────────────────────────────────────────────────────────────────
class _Pack:
    def __init__(self, pack_id, manifest, nodes, edges, evidence):
        self.pack_id = pack_id
        self.manifest = manifest
        self.nodes = nodes
        self.edges = edges
        self.evidence = evidence
        self.node_by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
        self.ev_ids = {e["evidence_id"] for e in evidence if isinstance(e, dict) and e.get("evidence_id")}

    # 필드 접근(properties/최상위 혼재 방어)
    @staticmethod
    def node_sentence(n):
        p = n.get("properties") or {}
        return p.get("sentence") or n.get("label") or n.get("sentence") or ""

    @staticmethod
    def node_candidate(n):
        p = n.get("properties") or {}
        if "candidate" in p:
            return bool(p.get("candidate"))
        return int(n.get("promotion_allowed", 0) or 0) == 0

    @staticmethod
    def node_ev_status(n):
        p = n.get("properties") or {}
        return p.get("evidence_status")

    @staticmethod
    def node_label(n):
        p = n.get("properties") or {}
        return p.get("label_kind") or n.get("node_type") or n.get("space")

    @staticmethod
    def edge_relation(e):
        p = e.get("properties") or {}
        return p.get("relation") or e.get("relation") or e.get("edge_type")

    def updated_at(self):
        m = self.manifest
        return m.get("updated_at") or m.get("created_at") or m.get("built_at")

    def title(self):
        m = self.manifest
        return m.get("title") or m.get("scope") or self.pack_id


# ── repository(안전 로드) ─────────────────────────────────────────────────────────
class PackRepository:
    """pack 디렉터리들의 상위 디렉터리. root 밖 파일은 절대 읽지 않는다."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        try:
            self.root_real = os.path.realpath(self.root)
        except OSError:
            self.root_real = self.root

    def pack_dir_names(self):
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out = []
        for name in sorted(names):
            if not _SAFE_PACK_ID.match(name):
                continue
            p = os.path.join(self.root, name)
            try:
                if os.path.islink(p) or not os.path.isdir(p):
                    continue
                if os.path.isfile(os.path.join(p, "manifest.json")):
                    out.append(name)
            except OSError:
                continue
        return out

    def _within_root(self, path):
        try:
            real = os.path.realpath(path)
            return os.path.commonpath([real, self.root_real]) == self.root_real
        except (OSError, ValueError):
            return False

    def _safe_json(self, path, cap):
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                return None
            if not self._within_root(path):
                return None
            if os.path.getsize(path) > cap:
                return None
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            return None
        for line in raw.splitlines():
            if not _public_safe_line(line):
                return "UNSAFE"
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _safe_jsonl(self, pack_dir, rels):
        for rel in rels:
            p = os.path.join(pack_dir, *rel.split("/"))
            try:
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                if not self._within_root(p) or os.path.getsize(p) > M.JSONL_MAX_BYTES:
                    return None
                rows = []
                with open(p, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= M.JSONL_MAX_ROWS:
                            return None
                        line = line.strip()
                        if not line:
                            continue
                        if not _public_safe_line(line):
                            return None   # secret/PII → pack unsafe
                        rows.append(json.loads(line))   # malformed → except → None(fail-closed)
                return rows
            except Exception:
                return None
        return None   # required 파일 부재

    def load_pack(self, name):
        """안전 로드 → _Pack 또는 None(unavailable). raw path/원문 출력 0."""
        if not _SAFE_PACK_ID.match(str(name)):
            return None
        pack_dir = os.path.join(self.root, name)
        try:
            if os.path.islink(pack_dir) or not os.path.isdir(pack_dir) or not self._within_root(pack_dir):
                return None
        except OSError:
            return None
        manifest = self._safe_json(os.path.join(pack_dir, "manifest.json"), M.MANIFEST_MAX_BYTES)
        if manifest == "UNSAFE" or not isinstance(manifest, dict):
            return None
        pack_id = manifest.get("pack_id") or name
        if not _SAFE_PACK_ID.match(str(pack_id)):
            return None
        try:
            from binggupack.pack.contract_validate import validate_pack
            if validate_pack(manifest).get("verdict") == "STOP":
                return None
        except Exception:
            return None
        nodes = self._safe_jsonl(pack_dir, ("graph/nodes.jsonl", "nodes.jsonl"))
        edges = self._safe_jsonl(pack_dir, ("graph/edges.jsonl", "edges.jsonl"))
        evidence = self._safe_jsonl(pack_dir, ("evidence/index.jsonl", "evidence_index.jsonl"))
        if nodes is None or edges is None or evidence is None:
            return None
        # 파일명/경로 기반 secret/PII(scan_public_tree 는 path 규칙 · .jsonl content 는 위에서 직접 검사)
        try:
            from binggupack.safety.public_tree_scan import scan_public_tree
            res = scan_public_tree(pack_dir)
            if isinstance(res, dict) and res.get("findings"):
                return None
        except Exception:
            return None
        return _Pack(str(pack_id), manifest, nodes, edges, evidence)


# ── service(5 tools) ──────────────────────────────────────────────────────────────
_CANDIDATE_NOTE = ("이 pack 의 노드/엣지는 candidate 이며 자동 확정 대상이 아닙니다. "
                   "소비자는 pack 내용을 자기 메모리에 자동 병합/승격하면 안 됩니다.")


class PackService:
    def __init__(self, repository):
        self.repo = repository

    # 6-1 pack_list
    def list_packs(self, cursor=None, limit=M.LIST_LIMIT_DEFAULT):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return M.error(M.ERR_INVALID_INPUT, "limit must be an integer")
        if not (1 <= limit <= M.LIST_LIMIT_MAX):
            return M.error(M.ERR_INVALID_INPUT, "limit out of range")
        offset = _decode_cursor(cursor)
        loaded, invalid = [], 0
        for name in self.repo.pack_dir_names():
            p = self.repo.load_pack(name)
            if p is None:
                invalid += 1
                continue
            loaded.append(p)
        # 정렬: updated_at DESC → pack_id (None updated_at 은 뒤로)
        loaded.sort(key=lambda p: ((p.updated_at() or ""), p.pack_id), reverse=True)
        loaded.sort(key=lambda p: p.pack_id)   # 결정적 tiebreak 안정화
        loaded.sort(key=lambda p: (p.updated_at() or ""), reverse=True)
        page = loaded[offset:offset + limit]
        packs = [{
            "pack_id": p.pack_id, "title": _excerpt(p.title(), 120),
            "counts": self._counts(p), "updated_at": p.updated_at(),
            "pack_type": p.manifest.get("pack_type"), "status": p.manifest.get("status"),
        } for p in page]
        nxt = _encode_cursor(offset + limit) if (offset + limit) < len(loaded) else None
        return {"schema_version": M.SCHEMA_VERSION, "packs": packs,
                "next_cursor": nxt, "invalid_pack_count": invalid}

    @staticmethod
    def _counts(p):
        return {"nodes": len(p.nodes), "edges": len(p.edges), "evidence": len(p.evidence)}

    # 6-2 pack_summary
    def get_pack_summary(self, pack_id):
        p = self._require(pack_id)
        if not isinstance(p, _Pack):
            return p
        topics = self._topics(p)
        ev_backed = sum(1 for n in p.nodes if isinstance(n, dict) and (n.get("evidence_refs")))
        total = len(p.nodes)
        return {
            "schema_version": M.SCHEMA_VERSION, "pack_id": p.pack_id, "title": _excerpt(p.title(), 120),
            "manifest_summary": {
                "license": p.manifest.get("license") or p.manifest.get("evidence_policy", {}).get("source"),
                "counts": self._counts(p), "pack_type": p.manifest.get("pack_type"),
            },
            "status": p.manifest.get("status"), "pack_type": p.manifest.get("pack_type"),
            "risk_level": p.manifest.get("risk_level"), "counts": self._counts(p),
            "topics": topics, "candidate_note": _CANDIDATE_NOTE,
            "evidence_coverage": {"nodes_with_evidence": ev_backed, "total_nodes": total,
                                  "ratio": round(ev_backed / total, 4) if total else 0.0},
        }

    @staticmethod
    def _topics(p):
        freq = {}
        for n in p.nodes:
            if not isinstance(n, dict):
                continue
            lab = _Pack.node_label(n)
            if lab:
                freq[str(lab)] = freq.get(str(lab), 0) + 1
        for t in (p.manifest.get("cross_pack_tags") or []):
            if isinstance(t, str):
                freq[t] = freq.get(t, 0) + 1
        ordered = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"label": _excerpt(k, 60), "count": v} for k, v in ordered[:M.TOPICS_MAX]]

    # 6-3 evidence_search
    def search_evidence(self, pack_id, query, limit=M.SEARCH_LIMIT_DEFAULT):
        q = _excerpt(query, M.QUERY_MAX + 1) if query else ""
        if not (M.QUERY_MIN <= len(q) <= M.QUERY_MAX):
            return M.error(M.ERR_QUERY_TOO_SHORT, "query length must be %d..%d" % (M.QUERY_MIN, M.QUERY_MAX))
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return M.error(M.ERR_INVALID_INPUT, "limit must be an integer")
        limit = max(1, min(limit, M.SEARCH_LIMIT_MAX))
        p = self._require(pack_id)
        if not isinstance(p, _Pack):
            return p
        qtok = _tokens(q)
        best = {}   # evidence_id → (score, sentence)
        for n in p.nodes:
            if not isinstance(n, dict):
                continue
            sent = _Pack.node_sentence(n)
            sc = _lexical_score(qtok, sent)
            if sc <= 0.0:
                continue
            for ev in (n.get("evidence_refs") or []):
                if ev not in p.ev_ids:
                    continue   # 없는 evidence_ref 를 새로 만들지 않음
                if ev not in best or sc > best[ev][0]:
                    best[ev] = (sc, sent)
        hits = [{"evidence_id": ev, "sentence_excerpt": _excerpt(s), "score": sc, "candidate": True}
                for ev, (sc, s) in best.items()]
        hits.sort(key=lambda h: (-h["score"], h["evidence_id"]))
        return {"schema_version": M.SCHEMA_VERSION, "hits": hits[:limit]}

    # 6-4 node_edge_lookup
    def lookup_node_edges(self, pack_id, node_id=None, keyword=None):
        p = self._require(pack_id)
        if not isinstance(p, _Pack):
            return p
        node = None
        if node_id:
            if not _SAFE_NODE_ID.match(str(node_id)):
                return M.error(M.ERR_NODE_NOT_FOUND, "node not found")
            node = p.node_by_id.get(node_id)   # exact only
            if node is None:
                return M.error(M.ERR_NODE_NOT_FOUND, "node not found")
        elif keyword:
            qtok = _tokens(keyword)
            scored = []
            for n in p.nodes:
                if not isinstance(n, dict) or not n.get("id"):
                    continue
                sc = _lexical_score(qtok, _Pack.node_sentence(n))
                if sc > 0.0:
                    scored.append((sc, n))
            scored.sort(key=lambda x: (-x[0], x[1]["id"]))
            if not scored:
                return M.error(M.ERR_NODE_NOT_FOUND, "no node matches keyword")
            if len(scored) > 1 and scored[0][0] == scored[1][0]:
                cands = [s[1]["id"] for s in scored[:M.KEYWORD_CANDIDATES_MAX]]
                return {"schema_version": M.SCHEMA_VERSION, "error_code": M.ERR_AMBIGUOUS_KEYWORD,
                        "message": "keyword matches multiple nodes", "candidate_ids": cands}
            node = scored[0][1]
        else:
            return M.error(M.ERR_NODE_OR_KEYWORD_REQUIRED, "node_id or keyword required")
        return {"schema_version": M.SCHEMA_VERSION, "node": self._node_view(node),
                "edges": self._edges_view(p, node["id"])}

    @staticmethod
    def _node_view(n):
        return {"id": n.get("id"), "label": _excerpt(_Pack.node_label(n), 60),
                "sentence": _excerpt(_Pack.node_sentence(n)), "candidate": _Pack.node_candidate(n),
                "evidence_status": _Pack.node_ev_status(n)}

    def _edges_view(self, p, nid):
        out = []
        for e in p.edges:
            if not isinstance(e, dict):
                continue
            src, tgt = e.get("source"), e.get("target")
            if src == nid:
                direction, peer = "outgoing", tgt
            elif tgt == nid:
                direction, peer = "incoming", src
            else:
                continue
            rel = _Pack.edge_relation(e)
            refs = [r for r in (e.get("evidence_refs") or []) if r in p.ev_ids]
            out.append({"id": e.get("id"), "relation": rel, "verb": rel, "direction": direction,
                        "peer_id": peer, "evidence_refs": refs,
                        "candidate": True,
                        "evidence_backed": bool(refs)})   # evidence 없으면 숨기지 않고 표시
        return out

    # 6-5 handoff_context
    def build_handoff_context(self, pack_id, topic=None, max_nodes=M.HANDOFF_MAX_NODES_DEFAULT):
        try:
            max_nodes = int(max_nodes)
        except (TypeError, ValueError):
            return M.error(M.ERR_INVALID_INPUT, "max_nodes must be an integer")
        max_nodes = max(1, min(max_nodes, M.HANDOFF_MAX_NODES_MAX))
        p = self._require(pack_id)
        if not isinstance(p, _Pack):
            return p
        selected = self._select_nodes(p, topic, max_nodes)
        md, truncated = render_handoff_markdown(p, selected, topic)
        return {"schema_version": M.SCHEMA_VERSION, "context_markdown": md, "truncated": truncated}

    @staticmethod
    def _select_nodes(p, topic, max_nodes):
        nodes = [n for n in p.nodes if isinstance(n, dict) and n.get("id")]
        if topic:
            qtok = _tokens(topic)
            scored = [(_lexical_score(qtok, _Pack.node_sentence(n)), n) for n in nodes]
            scored = [(s, n) for s, n in scored if s > 0.0]
            scored.sort(key=lambda x: (-x[0], x[1]["id"]))
            return [n for _s, n in scored[:max_nodes]]
        # topic 없음 → deterministic: evidence-backed 우선 → id
        nodes.sort(key=lambda n: (0 if n.get("evidence_refs") else 1, n["id"]))
        return nodes[:max_nodes]

    def _require(self, pack_id):
        if not _SAFE_PACK_ID.match(str(pack_id or "")):
            return M.error(M.ERR_PACK_NOT_FOUND, "pack not found")
        p = self.repo.load_pack(pack_id)   # exact pack_id(디렉터리) — prefix/fuzzy 없음
        if p is None or p.pack_id != pack_id:
            return M.error(M.ERR_PACK_NOT_FOUND, "pack not found")
        return p


# ── handoff Markdown 단일 정본(Phase 3 handoff guide 형식) ──────────────────────────
_CONSUMER_RULES = [
    "1. Answer ONLY from evidence_refs. If a claim has no evidence_ref, say \"no evidence in pack\".",
    "2. Treat every node/edge as a candidate — do not confirm, merge, or promote.",
    "3. Cite node_id / evidence_id you relied on (ids only, never raw paths/secrets).",
    "4. If a contradicts edge exists, present both sides; do not resolve it yourself.",
]


def render_handoff_markdown(p, nodes, topic):
    """Phase 3 Multi-Agent Handoff Guide 형식의 단일 정본(이중 template 금지). deterministic ·
    LLM 호출 0 · 원문 전체/raw path/secret 미포함 · candidate-only 표시. cap 초과 시 안전 truncate."""
    lines = []
    lines.append("# BingguPack handoff context — %s" % _excerpt(p.title(), 80))
    lines.append("")
    lines.append("You are reading a BingguPack (evidence-backed context pack). All nodes/edges are **candidate**.")
    lines.append("")
    lines.append("## Consumer rules")
    lines.extend(_CONSUMER_RULES)
    lines.append("")
    lines.append("## Pack summary")
    lines.append("- pack_id: `%s`" % p.pack_id)
    lines.append("- pack_type: %s · status: %s · risk: %s"
                 % (p.manifest.get("pack_type"), p.manifest.get("status"), p.manifest.get("risk_level")))
    lines.append("- counts: nodes %d · edges %d · evidence %d" % (len(p.nodes), len(p.edges), len(p.evidence)))
    if topic:
        lines.append("- topic filter: %s" % _excerpt(topic, 60))
    lines.append("")
    lines.append("## Nodes (candidate)")
    sel_ids = set()
    for n in nodes:
        sel_ids.add(n["id"])
        lines.append("- `%s` [%s] %s" % (n["id"], _excerpt(_Pack.node_label(n), 20),
                                         _excerpt(_Pack.node_sentence(n))))
    lines.append("")
    lines.append("## Edges (candidate · evidence_refs required)")
    for e in p.edges:
        if not isinstance(e, dict):
            continue
        if e.get("source") in sel_ids or e.get("target") in sel_ids:
            refs = [r for r in (e.get("evidence_refs") or []) if r in p.ev_ids]
            tag = "" if refs else "  ⚠ no-evidence(candidate proposal only)"
            lines.append("- `%s` --%s--> `%s`  evidence:%s%s"
                         % (e.get("source"), _Pack.edge_relation(e), e.get("target"),
                            ",".join(refs) if refs else "-", tag))
    lines.append("")
    lines.append("_All content above is candidate; do not merge or promote. Answer only from evidence_refs._")
    md = "\n".join(lines)
    data = md.encode("utf-8")
    if len(data) > M.HANDOFF_CAP_BYTES:
        md = data[:M.HANDOFF_CAP_BYTES - 64].decode("utf-8", "ignore").rstrip() + \
            "\n\n_[truncated — output cap reached; request a narrower topic]_"
        return md, True
    return md, False
