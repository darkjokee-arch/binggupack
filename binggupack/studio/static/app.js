/* Binggu Studio Preview — read-only 대시보드 + Memory Explorer.
 * 서버 JSON 은 오직 textContent 로만 DOM 에 삽입한다(원문 HTML 파싱 경로 미사용). 상대경로 API 만 호출.
 * 승인/저장/폐기 등은 실행하지 않는다 — 버튼은 CLI 명령을 클립보드에 복사만 한다.
 */
"use strict";
(function () {
  var REFRESH_MS = 5000;
  var auto = true;
  var timer = null;
  var view = "home";
  var inboxTab = "all";
  var lastInbox = null;
  var mem = { mode: "list", offset: 0, limit: 30, count: 0, total: 0,
              q: "", state: "active", type: "", subtype: "", loaded: false };

  // ── API base ──
  function apiBase() {
    var p = window.location.pathname;
    var m = p.match(/^\/s\/[^/]+\//);
    if (m) { return m[0]; }
    var m2 = p.match(/^\/s\/[^/]+/);
    if (m2) { return m2[0] + "/"; }
    return "./";
  }
  function getJSON(path) {
    return fetch(apiBase() + "api/" + path, {
      cache: "no-store", credentials: "omit", headers: { "Accept": "application/json" }
    }).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    });
  }

  // ── DOM helpers (textContent only) ──
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text != null) { e.textContent = String(text); }
    return e;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function byId(id) { return document.getElementById(id); }

  function toast(msg) {
    var t = byId("toast");
    t.textContent = msg;
    t.hidden = false;
    window.clearTimeout(toast._t);
    toast._t = window.setTimeout(function () { t.hidden = true; }, 1800);
  }

  function copyCmd(cmd) {
    function done() { toast("복사됨: " + cmd); }
    function fail() { toast("복사 실패 — 직접 선택해 복사하세요"); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(done, fallback);
    } else { fallback(); }
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = cmd; ta.setAttribute("readonly", "");
        ta.style.position = "fixed"; ta.style.left = "-9999px";
        document.body.appendChild(ta); ta.select();
        var ok = document.execCommand && document.execCommand("copy");
        document.body.removeChild(ta);
        if (ok) { done(); } else { fail(); }
      } catch (e) { fail(); }
    }
  }
  function copyButton(label, cmd) {
    var b = el("button", "copy"); b.type = "button";
    b.appendChild(el("span", null, label + "  "));
    b.appendChild(el("code", null, cmd));
    b.addEventListener("click", function () { copyCmd(cmd); });
    return b;
  }

  // ── status strip ──
  function setChip(id, text, kind) {
    var c = byId(id);
    c.textContent = text;
    c.className = "chip" + (kind ? " " + kind : "") + (id === "chip-updated" ? " chip-time" : "");
  }
  function renderStatus(home) {
    var audit = (home.ledger && home.ledger.audit) || "—";
    setChip("chip-integrity", "장부 " + audit, audit === "INTACT" ? "ok" : (audit === "BROKEN" ? "bad" : ""));
    var cap = (home.services && home.services.capture) || "—";
    setChip("chip-capture", "capture " + cap.toUpperCase(), cap === "on" ? "ok" : "");
    var prov = (home.services && home.services.approval_provider) || "—";
    setChip("chip-provider", "provider " + prov.toUpperCase(), prov === "on" ? "ok" : "");
    setChip("chip-updated", "갱신 " + new Date().toLocaleTimeString());
  }
  function renderMeta(m) {
    m = m || {};
    byId("foot-mode").textContent = (m.product || "BingguPack") + " · " + (m.mode || "read-only") +
      " · studio v" + (m.studio_version != null ? m.studio_version : "?");
  }

  // ── home cards ──
  function card(k, v, attn) {
    var c = el("div", "card" + (attn ? " attn" : ""));
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v", v));
    return c;
  }
  function renderHome(home) {
    var host = byId("home-cards");
    clear(host);
    if (!home.ledger || !home.ledger.exists) {
      var e = el("div", "empty");
      e.appendChild(el("p", null, "아직 로컬 기억 장부가 없습니다."));
      e.appendChild(el("p", null, "시작:  binggu start"));
      e.appendChild(el("p", null, "60초 체험:  binggu demo"));
      host.appendChild(e);
      return;
    }
    var q = home.queues || {};
    host.appendChild(card("활성 기억", home.ledger.active || 0));
    host.appendChild(card("자동 수집 후보", q.capture || 0, (q.capture || 0) > 0));
    host.appendChild(card("원격 저장 의도", q.hosted || 0, (q.hosted || 0) > 0));
    host.appendChild(card("승인 요청", q.approvals || 0, (q.approvals || 0) > 0));
    host.appendChild(card("검토 예정", q.due || 0, (q.due || 0) > 0));
  }

  // ── inbox ──
  var TABS = [
    { key: "all", label: "전체" }, { key: "capture", label: "Capture" },
    { key: "hosted", label: "Hosted" }, { key: "approvals", label: "Approvals" }, { key: "due", label: "Due" }
  ];
  function renderTabs() {
    var nav = byId("inbox-tabs"); clear(nav);
    TABS.forEach(function (t) {
      var b = el("button", "tab", t.label); b.type = "button"; b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", t.key === inboxTab ? "true" : "false");
      b.addEventListener("click", function () {
        inboxTab = t.key; renderTabs(); if (lastInbox) { renderInbox(lastInbox); }
      });
      nav.appendChild(b);
    });
  }
  function itemBox(idxText, previewText) {
    var it = el("div", "item");
    var l1 = el("div", "line1");
    if (idxText != null) { l1.appendChild(el("span", "idx", idxText)); }
    l1.appendChild(el("span", "preview", previewText || "(내용 없음)"));
    it.appendChild(l1);
    return it;
  }
  function groupCapture(sec) {
    var g = el("div", "group");
    g.appendChild(el("h3", null, "자동 수집 후보 " + sec.count + "개"));
    if (sec.count === 0) { g.appendChild(el("p", "empty", "새 후보가 없습니다.")); }
    (sec.items || []).forEach(function (x) {
      var it = itemBox("[" + x.idx + "]", x.preview);
      if (x.pinned) { it.querySelector(".line1").appendChild(el("span", "tag pin", "PINNED")); }
      g.appendChild(it);
    });
    var act = el("div", "actions"); act.appendChild(copyButton("확인·저장", "binggu capture preview"));
    g.appendChild(act);
    return g;
  }
  function groupHosted(sec) {
    var g = el("div", "group");
    g.appendChild(el("h3", null, "원격 저장 의도 " + sec.count + "개  (로컬 staging · fetch 0)"));
    if (sec.count === 0) { g.appendChild(el("p", "empty", "가져온 원격 의도가 없습니다.")); }
    (sec.items || []).forEach(function (x) {
      var it = itemBox("[" + x.idx + "]", x.preview);
      var meta = el("div", "meta");
      var age = (x.age_days != null) ? (Number(x.age_days).toFixed(1) + "일 전") : "?";
      meta.appendChild(el("span", null, "sha " + (x.text_sha || "?")));
      meta.appendChild(el("span", null, age));
      meta.appendChild(el("span", null, "후보 " + (x.candidates || 0)));
      if (x.pii_secret) { meta.appendChild(el("span", "flag-warn", "⚠ PII/secret")); }
      if (x.expired) { meta.appendChild(el("span", "flag-warn", "⚠ 만료")); }
      it.appendChild(meta);
      var act = el("div", "actions"); act.appendChild(copyButton("저장", "binggu hosted pull --select " + x.idx));
      it.appendChild(act);
      g.appendChild(it);
    });
    return g;
  }
  function groupApprovals(sec) {
    var g = el("div", "group");
    g.appendChild(el("h3", null, "승인 요청 " + sec.count + "개  (PENDING)"));
    if (sec.count === 0) { g.appendChild(el("p", "empty", "대기 중인 승인 요청이 없습니다.")); }
    (sec.items || []).forEach(function (x) {
      var rid = x.request_id || "";
      var shortId = rid.length > 14 ? (rid.slice(0, 14) + "…") : rid;
      var it = itemBox(null, x.operation || "(operation)");
      var meta = el("div", "meta");
      meta.appendChild(el("span", null, x.summary || ""));
      meta.appendChild(el("span", null, "만료 " + (x.expires_at || "?")));
      meta.appendChild(el("span", "tag", "ID " + shortId));
      it.appendChild(meta);
      var act = el("div", "actions");
      act.appendChild(copyButton("내용 확인", "binggu approval show " + rid));
      act.appendChild(copyButton("승인", "binggu approval approve " + rid));
      it.appendChild(act);
      g.appendChild(it);
    });
    if (sec.count > 0) { g.appendChild(el("p", "empty", "Studio 는 승인을 실행하지 않습니다 — 명령을 복사해 터미널에서 실행하세요.")); }
    return g;
  }
  function groupDue(sec) {
    var g = el("div", "group");
    g.appendChild(el("h3", null, "검토 예정 " + sec.count + "개"));
    if (sec.count === 0) { g.appendChild(el("p", "empty", "예정된 검토가 없습니다.")); }
    (sec.items || []).forEach(function (x) {
      var it = itemBox("[" + x.id + "]", x.preview);
      it.querySelector(".line1").appendChild(el("span", "tag", x.due_date + " 예정"));
      g.appendChild(it);
    });
    var act = el("div", "actions"); act.appendChild(copyButton("검토 목록", "binggu reminders"));
    g.appendChild(act);
    return g;
  }
  var GROUP = { capture: groupCapture, hosted: groupHosted, approvals: groupApprovals, due: groupDue };
  var ORDER = ["capture", "hosted", "approvals", "due"];
  function renderInbox(inbox) {
    lastInbox = inbox;
    var body = byId("inbox-body"); clear(body);
    var secs = inbox.sections || {};
    if (!inbox.ledger || !inbox.ledger.exists) {
      body.appendChild(el("p", "empty", "장부가 없어 검토할 항목이 없습니다.  시작: binggu start")); return;
    }
    var total = ORDER.reduce(function (n, k) { return n + ((secs[k] && secs[k].count) || 0); }, 0);
    if (total === 0) { body.appendChild(el("p", "empty", "검토할 항목이 없습니다. 장부는 최신 상태입니다.")); return; }
    ORDER.forEach(function (k) {
      if (inboxTab !== "all" && inboxTab !== k) { return; }
      if (secs[k]) { body.appendChild(GROUP[k](secs[k])); }
    });
  }

  // ── memories ──
  function badge(cls, text) { return el("span", cls, text); }
  function memCard(it) {
    var c = el("article", "memcard"); c.tabIndex = 0; c.setAttribute("role", "button");
    var top = el("div", "memcard-top");
    top.appendChild(badge("badge-type", it.node_type || "?"));
    if (it.semantic_subtype) { top.appendChild(badge("badge-sub", it.semantic_subtype)); }
    if (it.state) { top.appendChild(badge("badge-state st-" + it.state, it.state)); }
    c.appendChild(top);
    c.appendChild(el("p", "memcard-claim", it.claim || "(내용 없음)"));
    var meta = el("div", "memcard-meta");
    meta.appendChild(el("span", null, "id " + it.display_id));
    if (it.created_at) { meta.appendChild(el("span", null, it.created_at)); }
    if (it.evidence_count != null) { meta.appendChild(el("span", null, "근거 " + it.evidence_count)); }
    if (it.relevance != null) { meta.appendChild(el("span", null, "관련도 " + it.relevance)); }
    c.appendChild(meta);
    function open() { openDetail(it.node_id); }
    c.addEventListener("click", open);
    c.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
    return c;
  }
  function renderMemoryList(items, reset) {
    var host = byId("mem-list");
    if (reset) { clear(host); }
    if (reset && (!items || !items.length)) {
      host.appendChild(el("p", "empty", "일치하는 기억이 없습니다.")); return;
    }
    (items || []).forEach(function (it) { host.appendChild(memCard(it)); });
  }

  function readFilters() {
    mem.q = byId("mem-q").value.trim();
    mem.state = byId("mem-state").value;
    mem.type = byId("mem-type").value;
    mem.subtype = byId("mem-subtype").value.trim();
  }
  function memError() {
    var host = byId("mem-list"); clear(host);
    host.appendChild(el("p", "empty", "검색 중 오류가 발생했습니다. 다시 시도하세요."));
  }
  function memCountBar(text, withRecallCopy) {
    var bar = byId("mem-count"); clear(bar);
    bar.appendChild(el("span", null, text));
    if (withRecallCopy && mem.q) { bar.appendChild(copyButton("recall 복사", 'binggu recall "' + mem.q + '"')); }
  }
  function doMemorySearch(reset) {
    readFilters();
    mem.mode = "list";
    mem.loaded = true;
    if (reset) { mem.offset = 0; }
    byId("mem-lexnote").textContent = "";
    var p = new URLSearchParams();
    p.set("state", mem.state); p.set("limit", String(mem.limit)); p.set("offset", String(mem.offset));
    if (mem.q) { p.set("q", mem.q); }
    if (mem.type) { p.set("type", mem.type); }
    if (mem.subtype) { p.set("subtype", mem.subtype); }
    getJSON("memories?" + p.toString()).then(function (j) {
      mem.total = j.total;
      renderMemoryList(j.items, reset);
      var shown = (byId("mem-list").querySelectorAll(".memcard")).length;
      memCountBar("총 " + j.total + "개 · 표시 " + shown + "개", true);
      byId("mem-pager").hidden = shown >= j.total;
    }).catch(memError);
  }
  function doRecall() {
    readFilters();
    if (!mem.q) { toast("회상하려면 검색어를 입력하세요"); return; }
    mem.mode = "recall";
    mem.loaded = true;
    var p = new URLSearchParams(); p.set("q", mem.q); p.set("limit", "10");
    getJSON("recall?" + p.toString()).then(function (j) {
      renderMemoryList(j.items, true);
      byId("mem-lexnote").textContent = j.note || "";
      memCountBar("회상 " + j.count + "건 (관련도순 · lexical)", true);
      byId("mem-pager").hidden = true;
    }).catch(memError);
  }
  function openDetail(nodeId) {
    var d = byId("mem-detail"); d.hidden = false; clear(d);
    d.appendChild(el("p", "loading", "불러오는 중…"));
    getJSON("memory/" + encodeURIComponent(nodeId)).then(renderDetail).catch(function () {
      clear(d); d.appendChild(el("p", "empty", "상세를 불러오지 못했습니다.")); addClose(d);
    });
  }
  function addClose(d) {
    var close = el("button", "memclose", "닫기"); close.type = "button";
    close.addEventListener("click", function () { d.hidden = true; });
    d.insertBefore(close, d.firstChild);
  }
  function renderDetail(j) {
    var d = byId("mem-detail"); clear(d);
    var head = el("div", "memcard-top");
    head.appendChild(badge("badge-type", j.node_type || "?"));
    if (j.semantic_subtype) { head.appendChild(badge("badge-sub", j.semantic_subtype)); }
    if (j.state) { head.appendChild(badge("badge-state st-" + j.state, j.state)); }
    d.appendChild(head);
    d.appendChild(el("p", "detail-claim", j.claim || ""));
    var meta = el("dl", "detail-meta");
    function kv(k, v) { meta.appendChild(el("dt", null, k)); meta.appendChild(el("dd", null, v == null ? "—" : String(v))); }
    kv("display ID", j.display_id); kv("state", j.state); kv("type", j.node_type);
    kv("subtype", j.semantic_subtype); kv("생성", j.created_at); kv("use count", j.use_count);
    kv("review", j.review_status); kv("근거", j.evidence_count); kv("관계", j.relation_count);
    kv("confidence", j.confidence);
    d.appendChild(meta);
    if (j.acceptance) {
      var acc = el("p", "detail-acc");
      acc.appendChild(el("span", "tag", "owner " + j.acceptance.event));
      acc.appendChild(el("span", null, " " + (j.acceptance.reason || "")));
      d.appendChild(acc);
    }
    if (j.explain_summary) { d.appendChild(el("p", "detail-explain", j.explain_summary)); }
    if (j.evidence && j.evidence.length) {
      d.appendChild(el("h4", null, "근거"));
      j.evidence.forEach(function (e) {
        var row = el("div", "provrow");
        row.appendChild(el("span", "tag", "ev " + e.display_id));
        row.appendChild(el("span", "prov-ex", e.excerpt || "(발췌 없음)"));
        d.appendChild(row);
      });
    }
    if (j.relations && j.relations.length) {
      d.appendChild(el("h4", null, "관계"));
      j.relations.forEach(function (r) {
        var row = el("div", "provrow");
        var arrow = r.direction === "out" ? "→" : "←";
        row.appendChild(el("span", "tag", r.relation + " " + arrow));
        row.appendChild(el("span", "prov-ex", r.peer_excerpt || (r.dangling ? "(끊긴 연결)" : "")));
        row.appendChild(el("span", "tag" + (r.dangling ? " flag-warn" : ""), "peer " + r.peer_display_id));
        d.appendChild(row);
      });
    }
    var acts = el("div", "actions");
    acts.appendChild(copyButton("explain 복사", "binggu explain " + j.node_id));
    d.appendChild(acts);
    addClose(d);
  }

  // ── view routing ──
  function setView(name) {
    view = name;
    ["home", "inbox", "memories"].forEach(function (v) { byId("view-" + v).hidden = (v !== name); });
    var btns = document.querySelectorAll(".navbtn");
    Array.prototype.forEach.call(btns, function (b) {
      if (b.getAttribute("data-view") === name) { b.setAttribute("aria-current", "page"); }
      else { b.removeAttribute("aria-current"); }
    });
    loadStatus();
    if (name === "memories" && !mem.loaded) { doMemorySearch(true); }
    schedule();
    if (name !== "memories") { tick(); }
  }

  // ── load cycle (home/inbox) ──
  function showError(msg) {
    var e = byId("global-error");
    e.textContent = "불러오기 오류: " + (msg && msg.message ? msg.message : msg) + " — 자동 갱신은 계속됩니다.";
    e.hidden = false;
  }
  function clearError() { byId("global-error").hidden = true; }

  function loadStatus() {
    return Promise.all([getJSON("home"), getJSON("meta")]).then(function (r) {
      clearError(); renderStatus(r[0]); renderMeta(r[1]);
      if (view === "home") { renderHome(r[0]); }
    }).catch(showError);
  }
  function loadHomeInbox() {
    return loadStatus().then(function () {
      if (view === "inbox") { return getJSON("inbox").then(renderInbox); }
    }).catch(showError);
  }
  function schedule() {
    if (timer) { window.clearTimeout(timer); timer = null; }
    if (auto && view !== "memories") { timer = window.setTimeout(tick, REFRESH_MS); }
  }
  function tick() { loadHomeInbox().then(schedule); }
  function setAuto(on) {
    auto = on;
    var b = byId("btn-auto");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.textContent = "자동 갱신: " + (on ? "켬" : "끔");
    schedule();
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderTabs();
    Array.prototype.forEach.call(document.querySelectorAll(".navbtn"), function (b) {
      b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
    });
    byId("mem-form").addEventListener("submit", function (e) { e.preventDefault(); doMemorySearch(true); });
    byId("mem-recall").addEventListener("click", doRecall);
    byId("mem-more").addEventListener("click", function () {
      mem.offset = (byId("mem-list").querySelectorAll(".memcard")).length;
      doMemorySearch(false);
    });
    byId("btn-refresh").addEventListener("click", function () {
      if (view === "memories") { mem.mode === "recall" ? doRecall() : doMemorySearch(true); }
      else { loadHomeInbox().then(schedule); }
    });
    byId("btn-auto").addEventListener("click", function () { setAuto(!auto); });
    setView("home");
  });
})();
