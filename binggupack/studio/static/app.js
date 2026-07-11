/* Binggu Studio Preview — read-only 대시보드.
 * 서버 JSON 은 오직 textContent 로만 DOM 에 삽입한다(원문 HTML 파싱 경로 미사용). 상대경로 API 만 호출.
 * 승인/저장은 실행하지 않는다 — 버튼은 CLI 명령을 클립보드에 복사만 한다.
 */
"use strict";
(function () {
  var REFRESH_MS = 5000;
  var auto = true;
  var timer = null;
  var activeTab = "all";
  var lastInbox = null;

  // ── API base: /s/<token>/ prefix 를 현재 경로에서 도출(trailing slash 유무 무관) ──
  function apiBase() {
    var p = window.location.pathname;
    var m = p.match(/^\/s\/[^/]+\//);
    if (m) { return m[0]; }
    var m2 = p.match(/^\/s\/[^/]+/);
    if (m2) { return m2[0] + "/"; }
    return "./";
  }
  function getJSON(name) {
    return fetch(apiBase() + "api/" + name, {
      cache: "no-store", credentials: "omit", headers: { "Accept": "application/json" }
    }).then(function (r) {
      if (!r.ok) { throw new Error(name + " HTTP " + r.status); }
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
    } else {
      fallback();
    }
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = cmd;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand && document.execCommand("copy");
        document.body.removeChild(ta);
        if (ok) { done(); } else { fail(); }
      } catch (e) { fail(); }
    }
  }
  function copyButton(label, cmd) {
    var b = el("button", "copy");
    b.type = "button";
    b.appendChild(el("span", null, label + "  "));
    var code = el("code", null, cmd);
    b.appendChild(code);
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
    var integrityKind = audit === "INTACT" ? "ok" : (audit === "BROKEN" ? "bad" : "");
    setChip("chip-integrity", "장부 " + audit, integrityKind);
    var cap = (home.services && home.services.capture) || "—";
    setChip("chip-capture", "capture " + cap.toUpperCase(), cap === "on" ? "ok" : "");
    var prov = (home.services && home.services.approval_provider) || "—";
    setChip("chip-provider", "provider " + prov.toUpperCase(), prov === "on" ? "ok" : "");
    var when = new Date();
    setChip("chip-updated", "갱신 " + when.toLocaleTimeString());
  }

  // ── home cards ──
  function card(k, v, attn) {
    var c = el("div", "card" + (attn ? " attn" : ""));
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v", v));
    return c;
  }
  function renderHome(home) {
    renderStatus(home);
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

  // ── inbox tabs ──
  var TABS = [
    { key: "all", label: "전체" },
    { key: "capture", label: "Capture" },
    { key: "hosted", label: "Hosted" },
    { key: "approvals", label: "Approvals" },
    { key: "due", label: "Due" }
  ];
  function renderTabs() {
    var nav = byId("inbox-tabs");
    clear(nav);
    TABS.forEach(function (t) {
      var b = el("button", "tab", t.label);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", t.key === activeTab ? "true" : "false");
      b.addEventListener("click", function () {
        activeTab = t.key;
        renderTabs();
        if (lastInbox) { renderInbox(lastInbox); }
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
    var act = el("div", "actions");
    act.appendChild(copyButton("확인·저장", "binggu capture preview"));
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
      var act = el("div", "actions");
      act.appendChild(copyButton("저장", "binggu hosted pull --select " + x.idx));
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
    if (sec.count > 0) {
      g.appendChild(el("p", "empty", "Studio 는 승인을 실행하지 않습니다 — 명령을 복사해 터미널에서 실행하세요."));
    }
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
    var act = el("div", "actions");
    act.appendChild(copyButton("검토 목록", "binggu reminders"));
    g.appendChild(act);
    return g;
  }
  var GROUP = { capture: groupCapture, hosted: groupHosted, approvals: groupApprovals, due: groupDue };
  var ORDER = ["capture", "hosted", "approvals", "due"];

  function renderInbox(inbox) {
    lastInbox = inbox;
    var body = byId("inbox-body");
    clear(body);
    var secs = inbox.sections || {};
    if (!inbox.ledger || !inbox.ledger.exists) {
      body.appendChild(el("p", "empty", "장부가 없어 검토할 항목이 없습니다.  시작: binggu start"));
      return;
    }
    var total = ORDER.reduce(function (n, k) { return n + ((secs[k] && secs[k].count) || 0); }, 0);
    if (total === 0) {
      body.appendChild(el("p", "empty", "검토할 항목이 없습니다. 장부는 최신 상태입니다."));
      return;
    }
    ORDER.forEach(function (k) {
      if (activeTab !== "all" && activeTab !== k) { return; }
      if (secs[k]) { body.appendChild(GROUP[k](secs[k])); }
    });
  }

  // ── load cycle ──
  function showError(msg) {
    var e = byId("global-error");
    e.textContent = "불러오기 오류: " + msg + " — 자동 갱신은 계속됩니다.";
    e.hidden = false;
  }
  function clearError() { byId("global-error").hidden = true; }

  function load() {
    return Promise.all([getJSON("home"), getJSON("inbox"), getJSON("meta")])
      .then(function (res) {
        clearError();
        renderHome(res[0]);
        renderInbox(res[1]);
        var m = res[2] || {};
        byId("foot-mode").textContent = (m.product || "BingguPack") + " · " + (m.mode || "read-only") +
          " · studio v" + (m.studio_version != null ? m.studio_version : "?");
      })
      .catch(function (err) { showError(err && err.message ? err.message : "unknown"); });
  }

  function schedule() {
    if (timer) { window.clearTimeout(timer); timer = null; }
    if (auto) { timer = window.setTimeout(tick, REFRESH_MS); }
  }
  function tick() { load().then(schedule); }

  function setAuto(on) {
    auto = on;
    var b = byId("btn-auto");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.textContent = "자동 갱신: " + (on ? "켬" : "끔");
    schedule();
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderTabs();
    byId("btn-refresh").addEventListener("click", function () { load().then(schedule); });
    byId("btn-auto").addEventListener("click", function () { setAuto(!auto); });
    tick();
  });
})();
