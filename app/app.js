/* StitchCheck PWA. One state object, render functions per screen, one language
   at a time. Plain script, no framework. */
(function () {
  "use strict";

  var BACKEND = (window.STITCHCHECK_CONFIG && window.STITCHCHECK_CONFIG.BACKEND_URL) || "";
  var S = window.STR;

  var PARTS = ["collar", "pocket", "hem", "side_seam", "sleeve", "placket", "cuff", "shoulder"];
  var LABELS = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage", "open_seam"];
  var SEVS = ["critical", "major", "minor", "none"];

  var ICONS = {
    camera: '<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-2h6l2 2h3v11H4z"/><circle cx="12" cy="13" r="3.2"/></svg>',
    chart: '<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-8M21 20H3"/></svg>',
    doc: '<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg>'
  };

  var state = {
    screen: "inspect",
    lang: "te",
    session: null,
    bundleId: null,
    bundleNo: 1,
    garmentId: null,
    garmentNo: 0,
    dhu: "-",
    tasks: [],
    activeTask: -1,
    inspection: null,
    slowReview: false,
    garmentComplete: false,
    alertBanner: [],
    pollToken: 0,
    brains: [],
    brain: "spark",
    unlocked: false,
    dashTimer: 0,
    auditMode: false,
    auditBundleId: null,
    reportId: null,
    voice: null,
    setupOpen: false,
    showAll: {},
    sheetBuild: null,
    version: null,
    gallery: null,
    form: {
      fabric: "A", fabricCustom: "", lot: "",
      date: new Date().toISOString().slice(0, 10), style: "",
      crit: ["collar", "placket"],
      plan: [
        { part: "collar", operator_id: "OP07", machine_id: "7" },
        { part: "placket", operator_id: "OP11", machine_id: "12" }
      ]
    }
  };

  function $(id) { return document.getElementById(id); }

  /* ---------- strings: one language at a time ---------- */

  function t(sec, key, vars) {
    var p = (S[sec] && S[sec][key]) || { te: key, en: key };
    var te = p.te, en = p.en;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        te = te.split("{" + k + "}").join(String(vars[k]));
        en = en.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return { te: te, en: en };
  }

  function L(pair) {
    return state.lang === "en" ? pair.en : pair.te;
  }

  function txt(el, str) {
    el.textContent = str;
    return el;
  }

  function el(tag, cls, str) {
    var e = document.createElement(tag || "div");
    if (cls) { e.className = cls; }
    if (str !== undefined) { e.textContent = str; }
    return e;
  }

  function buzz() {
    try {
      if (navigator.vibrate) { navigator.vibrate(10); }
    } catch (e) { /* haptics unsupported */ }
  }

  function idem() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function fmt1(v) {
    var n = Number(v);
    return isFinite(n) ? n.toFixed(1) : "-";
  }

  function labelName(key) {
    var p = (S.labels && S.labels[key]) || { te: key, en: key };
    return L(p);
  }

  function partName(key) {
    var p = (S.parts && S.parts[key]) || { te: key, en: key };
    return L(p);
  }

  function sevName(key) {
    var p = (S.severity && S.severity[key]) || { te: key, en: key };
    return L(p);
  }

  /* ---------- safe markdown (headings, bullets, bold, paragraphs only) ---------- */

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function boldize(s) {
    return s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  function md(src) {
    var box = document.createElement("div");
    var lines = String(src || "").split("\n");
    var html = "";
    var inList = false;
    var para = [];
    function flushPara() {
      if (para.length) {
        html += "<p>" + boldize(para.join("<br>")) + "</p>";
        para = [];
      }
    }
    lines.forEach(function (raw) {
      var line = esc(raw.trim());
      if (!line) {
        if (inList) { html += "</ul>"; inList = false; }
        flushPara();
        return;
      }
      var m3 = line.match(/^###\s+(.*)/);
      var m2 = line.match(/^##\s+(.*)/);
      var m1 = line.match(/^#\s+(.*)/);
      var mb = line.match(/^-\s+(.*)/);
      if (m3 || m2 || m1) {
        if (inList) { html += "</ul>"; inList = false; }
        flushPara();
        var lvl = m3 ? "h3" : (m2 ? "h2" : "h1");
        html += "<" + lvl + ">" + boldize(m3 ? m3[1] : (m2 ? m2[1] : m1[1])) + "</" + lvl + ">";
      } else if (mb) {
        flushPara();
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + boldize(mb[1]) + "</li>";
      } else {
        para.push(line);
      }
    });
    if (inList) { html += "</ul>"; }
    flushPara();
    box.innerHTML = html;
    return box;
  }

  /* ---------- storage (convenience only) ---------- */

  function save(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); }
    catch (e) { /* storage unavailable */ }
  }

  function load(key) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function saveSess() {
    save("stitchcheck", {
      sessionId: state.session ? state.session.id : null,
      bundleId: state.bundleId,
      bundleNo: state.bundleNo,
      garmentId: state.garmentId,
      garmentNo: state.garmentNo,
      reportId: state.reportId
    });
  }

  /* ---------- api ---------- */

  function api(method, path, body, opts) {
    opts = opts || {};
    var ms = opts.timeout || (opts.long ? 90000 : 60000);
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, ms);
    var init = { method: method, signal: ctrl.signal, credentials: "include" };
    if (method !== "GET") {
      init.headers = { "Idempotency-Key": (opts && opts.key) || idem() };
    }
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      init.body = body;
    } else if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    function done() { clearTimeout(timer); }
    return fetch(BACKEND + path, init).then(function (res) {
      if (res.ok) {
        done();
        if (res.status === 204) { return null; }
        return res.json().catch(function () { return null; });
      }
      return res.json().catch(function () { return {}; }).then(function (data) {
        done();
        var err = new Error("http");
        err.status = res.status;
        err.detail = (data && (data.detail || data.message)) || "";
        throw err;
      });
    }).catch(function (err) {
      done();
      if (err && err.status) { throw err; }
      var net = new Error("network");
      net.status = 0;
      throw net;
    });
  }

  function userPair(err) {
    if (!err || !err.status) { return t("extra", "offline"); }
    if (err.status === 404) { return t("extra", "not_found"); }
    if (err.status === 409) { return t("extra", "still_reviewing"); }
    if (err.status === 429) { return t("extra", "too_many"); }
    if (err.status === 503) { return t("extra", "ai_down"); }
    if (err.status === 422 && err.detail) { return null; }
    return t("common", "error_generic");
  }

  function mediaUrl(url) {
    if (!url) { return ""; }
    return BACKEND + url;
  }

  function showError(box, err, retryFn) {
    box.hidden = false;
    box.textContent = "";
    box.className = "error";
    var d = el("div");
    var pair = userPair(err);
    if (pair) {
      txt(d, L(pair));
      box.appendChild(d);
      if (err && err.status === 422 && err.detail) {
        box.appendChild(el("div", null, err.detail));
      }
    } else {
      txt(d, err.detail);
      box.appendChild(d);
    }
    if (retryFn) {
      var b = el("button", null, L(t("extra", "retry")));
      b.type = "button";
      b.addEventListener("click", function () {
        box.hidden = true;
        retryFn();
      });
      box.appendChild(b);
    }
  }

  function lock(btn) { btn.disabled = true; }
  function unlock(btn) { btn.disabled = false; }

  /* ---------- chrome: tabs, title, foot, sheets ---------- */

  var TABS = ["inspect", "dashboard", "report", "more"];

  function tabLabel(s) {
    if (s === "inspect") { return L(t("extra", "nav_inspect")); }
    if (s === "dashboard") { return L(t("dashboard", "title")); }
    if (s === "report") { return L(t("extra", "nav_report")); }
    return L(t("extra", "nav_more"));
  }

  function paintChrome() {
    TABS.forEach(function (s) {
      var b = $("tab-" + s);
      txt(b.querySelector("span"), tabLabel(s));
      b.classList.toggle("active", state.screen === s || (s === "more" && state.screen === "audit"));
    });
    var title = state.screen === "audit" ? L(t("audit", "title")) : tabLabel(state.screen);
    txt($("screen-title"), title);
    document.title = "StitchCheck";
    txt($("lang-btn"), state.lang === "en" ? "తె" : "EN");
    var demo = state.session && state.session.is_demo === 1;
    $("demo-pill").hidden = !demo;
    if (demo) { txt($("demo-pill"), L(t("common", "demo_badge"))); }
  }

  function show(name) {
    state.screen = name;
    ["inspect", "dashboard", "report", "more", "audit"].forEach(function (s) {
      $("screen-" + s).hidden = (s !== name);
    });
    if (name === "dashboard") {
      renderDashboard();
      state.dashTimer = setInterval(renderDashboard, 10000);
    } else if (state.dashTimer) {
      clearInterval(state.dashTimer);
      state.dashTimer = 0;
    }
    if (name !== "inspect") { state.pollToken++; }
    if (name === "report") { renderReport(); }
    if (name === "more") { renderMore(); }
    if (name === "audit") { loadAudit(true); }
    if (name === "inspect") { renderInspect(); }
    paintChrome();
    hideFoot();
    if (name === "report") { footReport(); }
    window.scrollTo(0, 0);
  }

  TABS.forEach(function (s) {
    $("tab-" + s).addEventListener("click", function () { show(s); });
  });

  $("lang-btn").addEventListener("click", function () {
    setLang(state.lang === "en" ? "te" : "en");
  });
  window.addEventListener("online", paintOffline);
  window.addEventListener("offline", paintOffline);

  function showFoot(label, fn, spin) {
    var f = $("foot");
    f.hidden = false;
    var b = $("foot-btn");
    b.textContent = "";
    if (spin) {
      var s = el("span", "spin");
      b.appendChild(s);
    }
    b.appendChild(el("span", null, label));
    b.onclick = fn;
    unlock(b);
  }

  function hideFoot() {
    $("foot").hidden = true;
    $("foot-btn").onclick = null;
  }

  function paintSheetTitle() {
    var t = state.sheetBuild.title;
    txt($("sheet-title"), (typeof t === "function") ? t() : t);
  }

  function openSheet(title, build) {
    state.sheetBuild = { title: title, build: build };
    paintSheetTitle();
    var body = $("sheet-body");
    body.textContent = "";
    build(body);
    $("sheet-back").hidden = false;
  }

  function closeSheet() {
    $("sheet-back").hidden = true;
    state.sheetBuild = null;
  }

  function rebuildSheet() {
    if (!state.sheetBuild) { return; }
    paintSheetTitle();
    var body = $("sheet-body");
    body.textContent = "";
    state.sheetBuild.build(body);
  }

  function setLang(lang) {
    state.lang = lang;
    save("stitchcheck_lang", lang);
    document.documentElement.lang = lang === "en" ? "en" : "te";
    txt($("btn-viewer-close"), L(t("extra", "close")));
    paintChrome();
    paintOffline();
    rebuildSheet();
    show(state.screen);
  }

  function seg(options, current, onPick) {
    var box = el("div", "seg");
    options.forEach(function (o) {
      var b = el("button", null, o.label);
      b.type = "button";
      if (o.value === current) { b.classList.add("on"); }
      if (o.disabled) { b.disabled = true; }
      b.addEventListener("click", function () { onPick(o.value); });
      box.appendChild(b);
    });
    return box;
  }

  /* ---------- start day sheet ---------- */

  function figLine(b) {
    return t("extra", "fig_majors", { pct: (Number(b.major_recall) * 100).toFixed(1) });
  }

  function openStartSheet() {
    openSheet(function () { return L(t("start_day", "title")); }, buildStartSheet);
  }

  function openEntry() {
    openSheet(function () { return L(t("common", "app_name")); }, function (body) {
      var err = el("div", "error");
      err.hidden = true;
      var stored = load("stitchcheck");
      if (stored && stored.sessionId) {
        var re = el("button", "tinted", L(t("extra", "resume")));
        re.type = "button";
        re.style.marginTop = "8px";
        re.addEventListener("click", function () {
          lock(re);
          api("GET", "/sessions/" + stored.sessionId).then(function (s) {
            state.session = s;
            state.bundleId = stored.bundleId;
            state.bundleNo = stored.bundleNo || 1;
            state.garmentId = stored.garmentId;
            state.garmentNo = stored.garmentNo || 0;
            state.reportId = stored.reportId;
            state.brain = s.brain || state.brain;
            paintChrome();
            closeSheet();
            show("inspect");
          }).catch(function (e2) {
            showError(err, e2, function () { re.click(); });
          }).then(function () { unlock(re); });
        });
        body.appendChild(re);
      }
      var demo = el("button", "primary", L(t("extra", "try_demo")));
      demo.type = "button";
      demo.style.marginTop = "8px";
      demo.addEventListener("click", function () { demoSession(demo, err); });
      body.appendChild(demo);
      var start = el("button", "tinted", L(t("extra", "start_day_btn")));
      start.type = "button";
      start.addEventListener("click", openStartSheet);
      body.appendChild(start);
      body.appendChild(err);
    });
  }

  function buildStartSheet(body) {
    var f = state.form;
    body.appendChild(el("div", "seclabel", L(t("start_day", "fabric"))));
    var fopts = ["A", "B", "C"].map(function (v) {
      return { value: v, label: v };
    });
    fopts.push({ value: "other", label: L(t("extra", "other")) });
    body.appendChild(seg(fopts, f.fabric, function (v) {
      f.fabric = v;
      rebuildSheet();
    }));
    if (f.fabric === "other") {
      var inp = el("input");
      inp.type = "text";
      inp.value = f.fabricCustom;
      inp.addEventListener("input", function () { f.fabricCustom = inp.value; });
      body.appendChild(inp);
    }
    body.appendChild(el("div", "seclabel", L(t("start_day", "lot_size"))));
    var lot = el("input");
    lot.type = "number";
    lot.min = "2";
    lot.inputMode = "numeric";
    lot.placeholder = "2400";
    lot.value = f.lot;
    lot.addEventListener("input", function () { f.lot = lot.value; });
    body.appendChild(lot);

    var setup = el("button", "chev");
    setup.type = "button";
    setup.appendChild(el("span", null, L(t("extra", "setup"))));
    var ch = el("span", "c", state.setupOpen ? "▾" : "▸");
    setup.appendChild(ch);
    setup.addEventListener("click", function () {
      state.setupOpen = !state.setupOpen;
      rebuildSheet();
    });
    body.appendChild(setup);
    if (state.setupOpen) {
      buildSetup(body);
    }

    var err = el("div", "error");
    err.hidden = true;
    body.appendChild(err);
    var start = el("button", "primary", L(t("start_day", "start")));
    start.type = "button";
    start.style.marginTop = "16px";
    start.addEventListener("click", function () { createSession(start, err); });
    body.appendChild(start);
    var demo = el("button", "tertiary", L(t("extra", "try_demo")));
    demo.type = "button";
    demo.addEventListener("click", function () { demoSession(demo, err); });
    body.appendChild(demo);
  }

  function buildSetup(body) {
    var f = state.form;
    var dl = el("div", "seclabel", L(t("start_day", "date")));
    body.appendChild(dl);
    var dt = el("input");
    dt.type = "date";
    dt.value = f.date;
    dt.addEventListener("input", function () { f.date = dt.value; });
    body.appendChild(dt);
    body.appendChild(el("div", "seclabel", L(t("start_day", "style"))));
    var st = el("input");
    st.type = "text";
    st.value = f.style;
    st.autocomplete = "off";
    st.addEventListener("input", function () { f.style = st.value; });
    body.appendChild(st);

    body.appendChild(el("div", "seclabel", L(t("start_day", "critical_parts"))));
    var chips = el("div", "chips");
    PARTS.forEach(function (p) {
      var c = el("button", "chipbtn" + (f.crit.indexOf(p) >= 0 ? " on" : ""), partName(p));
      c.type = "button";
      c.addEventListener("click", function () {
        var i = f.crit.indexOf(p);
        if (i >= 0) { f.crit.splice(i, 1); } else { f.crit.push(p); }
        rebuildSheet();
      });
      chips.appendChild(c);
    });
    body.appendChild(chips);

    body.appendChild(el("div", "seclabel", L(t("start_day", "line_plan"))));
    var plan = el("div");
    f.plan.forEach(function (row, i) {
      var div = el("div", "plan-row");
      var sel = el("select");
      PARTS.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p;
        o.textContent = partName(p);
        if (p === row.part) { o.selected = true; }
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () { row.part = sel.value; });
      var op = el("input");
      op.value = row.operator_id || "";
      op.placeholder = "OP07";
      op.setAttribute("aria-label", L(t("inspect", "operator")));
      op.addEventListener("input", function () { row.operator_id = op.value; });
      var mc = el("input");
      mc.value = row.machine_id || "";
      mc.placeholder = "7";
      mc.inputMode = "numeric";
      mc.setAttribute("aria-label", L(t("inspect", "machine")));
      mc.addEventListener("input", function () { row.machine_id = mc.value; });
      var del = el("button", null, "×");
      del.type = "button";
      del.addEventListener("click", function () {
        f.plan.splice(i, 1);
        rebuildSheet();
      });
      div.appendChild(sel);
      div.appendChild(op);
      div.appendChild(mc);
      div.appendChild(del);
      plan.appendChild(div);
    });
    var add = el("button", "tertiary", "+ " + L(t("extra", "add")));
    add.type = "button";
    add.addEventListener("click", function () {
      f.plan.push({ part: "hem", operator_id: "", machine_id: "" });
      rebuildSheet();
    });
    plan.appendChild(add);
    body.appendChild(plan);

    body.appendChild(el("div", "seclabel", L(t("start_day", "ai_mode"))));
    var bopts = state.brains.map(function (b) {
      return {
        value: b.brain,
        label: b.brain === "spark" ? L(t("extra", "seg_low")) : L(t("extra", "seg_high")),
        disabled: !b.available
      };
    });
    body.appendChild(seg(bopts, state.brain, function (v) {
      state.brain = v;
      rebuildSheet();
    }));
    var cur = null;
    state.brains.forEach(function (b) {
      if (b.brain === state.brain) { cur = b; }
    });
    if (cur) {
      body.appendChild(el("div", "tiny pad", L(figLine(cur))));
    }
    if (!state.brains.length) {
      loadBrains(rebuildSheet);
    }
  }

  function unlockUnit(pin, btn, pst) {
    lock(btn);
    pst.hidden = true;
    api("POST", "/auth/pin", { pin: pin }).then(function () {
      state.unlocked = true;
      rebuildSheet();
    }).catch(function (err) {
      pst.hidden = false;
      pst.textContent = "";
      if (err && (err.status === 403 || err.status === 404)) {
        txt(pst, L(t("extra", "wrong_pin")));
      } else if (err && err.status === 429) {
        txt(pst, L(t("extra", "pin_wait")));
      } else {
        var pair = userPair(err);
        txt(pst, pair ? L(pair) : ((err && err.detail) || ""));
      }
    }).then(function () { unlock(btn); });
  }

  function fabricValue() {
    var f = state.form;
    return f.fabric === "other" ? (f.fabricCustom || "X") : f.fabric;
  }

  function validSession() {
    var lot = parseInt(state.form.lot, 10);
    if (!(lot >= 2)) { return t("extra", "val_lot"); }
    var seen = {};
    for (var i = 0; i < state.form.plan.length; i++) {
      var r = state.form.plan[i];
      if (seen[r.part]) { return t("extra", "val_dup"); }
      seen[r.part] = true;
      var m = parseInt(r.machine_id, 10);
      if (!r.operator_id || !(m >= 1 && m <= 25)) { return t("extra", "val_row"); }
    }
    var crit = state.form.crit.length ? state.form.crit : ["collar", "placket"];
    for (var j = 0; j < crit.length; j++) {
      if (!seen[crit[j]]) { return t("extra", "val_crit"); }
    }
    return null;
  }

  function createSession(btn, errBox) {
    var bad = validSession();
    if (bad) {
      errBox.hidden = false;
      errBox.textContent = "";
      txt(errBox, L(bad));
      return;
    }
    errBox.hidden = true;
    lock(btn);
    var f = state.form;
    var crit = f.crit.length ? f.crit : ["collar", "placket"];
    var payload = {
      date: f.date,
      style: f.style || "",
      fabric: fabricValue(),
      lot_size: parseInt(f.lot, 10),
      critical_parts: crit,
      spot_check_rate: 0.1,
      line_plan: f.plan.map(function (r) {
        return { part: r.part, operator_id: r.operator_id, machine_id: String(r.machine_id) };
      }),
      is_demo: false,
      brain: state.brain
    };
    var key = idem();
    function go() {
      api("POST", "/sessions", payload, { key: key }).then(function (s) {
        save("stitchcheck_plan", f.plan);
        enterSession(s, 1);
        closeSheet();
      }).catch(function (err) {
        showError(errBox, err, go);
      }).then(function () { unlock(btn); });
    }
    go();
  }

  function enterSession(s, bundleNo) {
    state.session = s;
    state.brain = s.brain || state.brain;
    state.bundleNo = bundleNo || 1;
    return api("POST", "/sessions/" + s.id + "/bundles", { bundle_no: state.bundleNo }).then(function (b) {
      state.bundleId = b.id;
      state.bundleNo = b.bundle_no;
      state.garmentId = null;
      state.garmentNo = 0;
      state.tasks = [];
      state.inspection = null;
      state.alertBanner = [];
      state.garmentComplete = false;
      state.auditMode = false;
      saveSess();
      show("inspect");
    });
  }

  function demoSession(btn, errBox) {
    lock(btn);
    var key = idem();
    function go() {
      api("POST", "/demo/session", undefined, { key: key }).then(function (d) {
      return api("GET", "/sessions/" + d.session_id).then(function (s) {
        state.session = s;
        state.bundleId = d.bundle_id;
        state.bundleNo = 1;
        state.garmentId = null;
        state.garmentNo = 0;
        state.tasks = [];
        state.inspection = null;
        state.alertBanner = [];
        state.garmentComplete = false;
        state.auditMode = false;
        saveSess();
        closeSheet();
        show("inspect");
      });
    }).catch(function (err) {
      if (err && err.status === 404) {
        errBox.hidden = false;
        errBox.textContent = "";
        txt(errBox, L(t("extra", "demo_hidden")));
      } else {
        showError(errBox, err, go);
      }
    }).then(function () { unlock(btn); });
    }
    go();
  }

  function loadBrains(done) {
    api("GET", "/brains").then(function (data) {
      state.brains = data || [];
      var ok = state.brains.some(function (b) { return b.brain === state.brain && b.available; });
      if (!ok) {
        state.brains.forEach(function (b) {
          if (b.available && !ok) { state.brain = b.brain; ok = true; }
        });
      }
      if (done) { done(); }
    }).catch(function () { /* brains retry from the sheet */ });
  }

  /* ---------- inspect ---------- */

  function emptyState(icon, msg, btnLabel, fn) {
    var box = el("div", "empty");
    var ic = el("div");
    ic.innerHTML = ICONS[icon] || "";
    box.appendChild(ic);
    box.appendChild(el("div", "msg", msg));
    var b = el("button", "tinted", btnLabel);
    b.type = "button";
    b.addEventListener("click", fn);
    box.appendChild(b);
    return box;
  }

  function renderInspect() {
    var root = $("screen-inspect");
    root.textContent = "";
    hideFoot();
    if (!state.session) {
      root.appendChild(emptyState("camera", L(t("extra", "no_day")), L(t("extra", "start_day_btn")), openEntry));
      return;
    }
    (state.alertBanner || []).forEach(function (a) {
      var banner = el("div", "notice");
      banner.appendChild(el("div", null, L(t("inspect", "machine")) + " " + a.machine_id));
      banner.appendChild(el("div", "tiny", labelName(a.defect) + " ×" + (a.garment_ids || []).length));
      root.appendChild(banner);
    });
    if (!state.garmentId) {
      var card = el("div", "card");
      card.style.textAlign = "center";
      card.style.padding = "32px 12px";
      card.appendChild(el("h2", null, L(t("inspect", "garment", { n: state.garmentNo + 1 }))));
      var go = el("button", "primary", L(t("extra", "start_garment")));
      go.type = "button";
      go.addEventListener("click", nextGarment);
      card.appendChild(go);
      root.appendChild(card);
      var err0 = el("div", "error");
      err0.id = "inspect-error";
      err0.hidden = true;
      root.appendChild(err0);
      if (state.auditMode) {
        root.appendChild(backToAuditBtn());
      }
      return;
    }
    var prog = el("div", "tiny pad");
    txt(prog, "B" + state.bundleNo + " · G" + state.garmentNo + " · DHU " + state.dhu);
    root.appendChild(prog);

    var chips = el("div", "seamchips");
    state.tasks.forEach(function (tk, i) {
      var c = el("button", "chipbtn" + (i === state.activeTask ? " cur" : "") + (tk.decision !== "pending" ? " done" : ""));
      c.type = "button";
      var nm = partName(tk.part);
      if (tk.decision !== "pending") { nm = "✓ " + nm; }
      txt(c, nm);
      c.title = tk.reason;
      c.addEventListener("click", function () {
        state.activeTask = i;
        renderInspect();
      });
      chips.appendChild(c);
    });
    root.appendChild(chips);

    var err = el("div", "error");
    err.id = "inspect-error";
    err.hidden = true;
    root.appendChild(err);

    if (!state.inspection) {
      var hero = el("button", "camhero");
      hero.type = "button";
      hero.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-2h6l2 2h3v11H4z"/><circle cx="12" cy="13" r="3.2"/></svg>';
      hero.appendChild(el("span", null, L(t("extra", "photo"))));
      hero.addEventListener("click", function () { $("file-shot").click(); });
      root.appendChild(hero);
      var sus = el("button", "micbtn", "+");
      sus.type = "button";
      sus.style.width = "52px";
      sus.style.height = "52px";
      sus.style.marginTop = "8px";
      sus.setAttribute("aria-label", "Add seam");
      sus.addEventListener("click", openSuspicious);
      root.appendChild(sus);
    } else {
      root.appendChild(buildPhotoCard());
    }

    if (state.auditMode) {
      root.appendChild(backToAuditBtn());
    }
    updateFoot();
  }

  function backToAuditBtn() {
    var b = el("button", "tertiary", L(t("common", "back")));
    b.type = "button";
    b.addEventListener("click", function () {
      state.auditMode = false;
      show("audit");
    });
    return b;
  }

  function openSuspicious() {
    openSheet(function () { return L(t("extra", "add_seam")); }, function (body) {
      PARTS.forEach(function (p) {
        var b = el("button", "tinted", partName(p));
        b.type = "button";
        b.style.marginTop = "8px";
        b.addEventListener("click", function () {
          state.tasks.push({ part: p, reason: "suspicious", inspection: null, decision: "pending" });
          state.activeTask = state.tasks.length - 1;
          closeSheet();
          renderInspect();
        });
        body.appendChild(b);
      });
    });
  }

  function nextGarment() {
    if (!state.session || !state.bundleId) { return; }
    var no = state.garmentNo + 1;
    var key = idem();
    function go() {
      api("POST", "/bundles/" + state.bundleId + "/garments", { garment_no: no }, { key: key }).then(function (g) {
        setGarment(g);
      }).catch(function (err) {
        showError($("inspect-error"), err, go);
      });
    }
    go();
  }

  function setGarment(g) {
    state.garmentId = g.id;
    state.garmentNo = g.garment_no;
    state.tasks = (g.photograph || []).map(function (p) {
      return {
        part: p,
        reason: (g.spot_check_part && p === g.spot_check_part) ? "spot_check" : "critical",
        inspection: null,
        decision: "pending"
      };
    });
    if (state.auditMode) {
      state.tasks.forEach(function (tk) { tk.reason = "audit"; });
    }
    state.activeTask = firstPending();
    state.inspection = null;
    state.slowReview = false;
    state.garmentComplete = false;
    state.alertBanner = [];
    saveSess();
    refreshDhu();
    renderInspect();
  }

  function firstPending() {
    for (var i = 0; i < state.tasks.length; i++) {
      if (state.tasks[i].decision === "pending") { return i; }
    }
    return -1;
  }

  function refreshDhu() {
    if (!state.session) { return; }
    api("GET", "/sessions/" + state.session.id + "/dashboard").then(function (d) {
      state.dhu = fmt1(d.dhu);
      if (state.screen === "inspect") { renderInspect(); }
    }).catch(function () { /* dashboard shows the error */ });
  }

  function updateFoot() {
    if (state.garmentComplete) {
      showFoot(L(t("inspect", "next_garment")), function () {
        state.garmentComplete = false;
        nextGarment();
      });
      return;
    }
    var ready = state.tasks.length > 0 && state.tasks.every(function (tk) {
      return tk.decision !== "pending";
    });
    if (ready) {
      showFoot(L(t("inspect", "garment_done")), completeGarment);
    } else {
      hideFoot();
    }
  }

  function completeGarment() {
    if (!state.garmentId) { return; }
    var btn = $("foot-btn");
    lock(btn);
    var key = idem();
    function go() {
      api("POST", "/garments/" + state.garmentId + "/complete", undefined, { key: key }).then(function (res) {
        buzz();
        state.garmentComplete = true;
        state.alertBanner = res.new_alerts || [];
        renderInspect();
        refreshDhu();
        if (state.auditMode) { loadAudit(true); }
      }).catch(function (err) {
        showError($("inspect-error"), err, go);
      }).then(function () { unlock(btn); });
    }
    go();
  }

  /* upload: full resolution, re-encode only over 14 MB */

  function fitPhoto(file) {
    if (file.size <= 14 * 1024 * 1024) { return Promise.resolve(file); }
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var c = document.createElement("canvas");
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        c.toBlob(function (b) { resolve(b || file); }, "image/jpeg", 0.9);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  $("file-shot").addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!f || !state.session || !state.garmentId) { return; }
    var idx = state.activeTask >= 0 ? state.activeTask : firstPending();
    if (idx < 0) { return; }
    state.activeTask = idx;
    var task = state.tasks[idx];
    var box = $("inspect-error");
    if (box) { box.hidden = true; }
    state.inspection = { uploading: true, preview: URL.createObjectURL(f) };
    renderInspect();
    fitPhoto(f).then(function (file) {
      var fd = new FormData();
      fd.append("image", file, file.name || "seam.jpg");
      fd.append("part", task.part);
      fd.append("reason", state.auditMode ? "audit" : task.reason);
      return api("POST", "/garments/" + state.garmentId + "/photos", fd, { long: true });
    }).then(function (res) {
      if (res && res.retake) {
        state.inspection = null;
        renderInspect();
        var errBox = $("inspect-error");
        errBox.hidden = false;
        errBox.textContent = "";
        txt(errBox, res.retake);
      } else if (res && res.inspection_id) {
        task.inspection = res.inspection_id;
        state.inspection = res;
        renderInspect();
        pollInspection(task, res.inspection_id);
      }
    }).catch(function (err) {
      state.inspection = null;
      renderInspect();
      showError($("inspect-error"), err, function () { $("file-shot").click(); });
    });
  });

  function pollInspection(task, id) {
    var token = ++state.pollToken;
    var started = Date.now();
    var slowShown = false;
    function tick() {
      if (token !== state.pollToken || state.screen !== "inspect") { return; }
      api("GET", "/inspections/" + id).then(function (insp) {
        if (token !== state.pollToken) { return; }
        if (insp.review_status === "done") {
          state.inspection = insp;
          renderInspect();
          return;
        }
        if (!slowShown && Date.now() - started > 30000) {
          slowShown = true;
          state.inspection = insp;
          state.slowReview = true;
          renderInspect();
          return;
        }
        setTimeout(tick, 1000);
      }).catch(function (err) {
        if (token !== state.pollToken) { return; }
        showError($("inspect-error"), err, function () { pollInspection(task, id); });
      });
    }
    setTimeout(tick, 1000);
  }

  function buildPhotoCard() {
    var insp = state.inspection;
    var task = state.tasks[state.activeTask] || {};
    var card = el("div", "card");
    if (insp.uploading) {
      var img = el("img", "crop");
      img.src = insp.preview;
      img.alt = "";
      card.appendChild(img);
      var spin = el("div", "grey pad", L(t("extra", "uploading")));
      card.appendChild(spin);
      return card;
    }
    if (insp.review_status !== "done") {
      if (insp.crop_url) {
        var c0 = el("img", "crop");
        c0.src = mediaUrl(insp.crop_url);
        c0.alt = "";
        card.appendChild(c0);
      }
      card.appendChild(el("div", "grey pad", L(t("extra", "checking"))));
      return card;
    }
    var shown = insp.overlay_url || insp.crop_url;
    if (shown) {
      var cimg = el("img", "crop");
      cimg.src = mediaUrl(shown);
      cimg.alt = "";
      cimg.addEventListener("click", function () {
        if (insp.photo_url) {
          $("viewer-img").src = mediaUrl(insp.photo_url);
          txt($("btn-viewer-close"), L(t("extra", "close")));
          $("viewer").hidden = false;
        }
      });
      card.appendChild(cimg);
    }
    var head = el("div");
    var lab = el("span", "verdict-label", labelName(insp.proposed_label));
    head.appendChild(lab);
    head.appendChild(el("span", "pill " + sevPill(insp.proposed_severity), pillWord(insp.proposed_severity)));
    card.appendChild(head);
    if (insp.spark_verdict && insp.spark_verdict.reason) {
      card.appendChild(el("div", "grey", insp.spark_verdict.reason));
    }
    var brain = insp.spark_verdict ? insp.spark_verdict.brain : null;
    if (brain) {
      var bl = el("div");
      bl.appendChild(el("span", "pill grey", brain === "spark" ? L(t("inspect", "brain_spark")) : L(t("extra", "brain_open_short"))));
      card.appendChild(bl);
    }
    if (insp.needs_checker === 1) {
      var yc = el("div");
      yc.appendChild(el("span", "pill amber", L(t("inspect", "your_call"))));
      card.appendChild(yc);
    }
    var meta = el("div", "tiny pad");
    txt(meta, L(t("inspect", "machine")) + " " + (insp.machine_id || "") + " · " + partName(task.part || insp.part_chip || ""));
    card.appendChild(meta);

    var agree = el("button", "primary agreebtn", L(t("inspect", "agree")));
    agree.type = "button";
    agree.addEventListener("click", function () {
      buzz();
      sendDecision(task, insp.id, { action: "agree", via: "tap" }, agree);
    });
    card.appendChild(agree);
    var grid = el("div", "dgrid");
    [[L(t("inspect", "change")), function () { openChange(task, insp.id); }],
     [L(t("extra", "not_defect")), function () { sendDecision(task, insp.id, { action: "not_a_defect", via: "tap" }); }],
     [L(t("inspect", "retake")), function () { sendDecision(task, insp.id, { action: "retake", via: "tap" }); }]
    ].forEach(function (pair) {
      var b = el("button", "tinted", pair[0]);
      b.type = "button";
      b.addEventListener("click", pair[1]);
      grid.appendChild(b);
    });
    card.appendChild(grid);
    var microw = el("div", "microw");
    var mic = el("button", "micbtn");
    mic.type = "button";
    mic.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
    mic.setAttribute("aria-label", L(t("voice", "hold_to_speak")));
    mic.addEventListener("click", function () { openVoice(insp.id); });
    microw.appendChild(mic);
    card.appendChild(microw);

    if (state.slowReview) {
      card.appendChild(el("div", "grey pad", L(t("extra", "still_checking"))));
      agree.disabled = true;
    }
    if (insp.agent_run_id) {
      card.appendChild(traceRow(insp.agent_run_id));
    }
    return card;
  }

  function traceRow(runId) {
    var wrap = el("div");
    var det = el("button", "tertiary");
    det.type = "button";
    det.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>';
    det.appendChild(el("span", null, L(t("extra", "trace"))));
    var tbox = el("div");
    tbox.hidden = true;
    det.addEventListener("click", function () {
      tbox.hidden = !tbox.hidden;
      if (!tbox.hidden && !tbox.children.length) { renderTrace(tbox, runId); }
    });
    wrap.appendChild(det);
    wrap.appendChild(tbox);
    return wrap;
  }

  function sevPill(sev) {
    if (sev === "critical" || sev === "major") { return "red"; }
    if (sev === "minor") { return "amber"; }
    return "green";
  }

  function pillWord(sev) {
    if (!sev || sev === "none") { return L(t("extra", "ok_word")); }
    return sevName(sev);
  }

  function renderTrace(box, runId) {
    var loading = el("div", "grey", L(t("common", "loading")));
    box.appendChild(loading);
    api("GET", "/agent-runs/" + runId).then(function (run) {
      box.textContent = "";
      var steps = run.steps || [];
      steps.forEach(function (step) {
        var d = el("div", "trace-step");
        var name = el("div", null, (step.call && step.call.name) || "");
        name.style.fontWeight = "600";
        d.appendChild(name);
        var args = el("pre");
        args.textContent = JSON.stringify((step.call && step.call.arguments) || {});
        d.appendChild(args);
        var out = el("pre");
        out.textContent = typeof step.output === "string" ? step.output : JSON.stringify(step.output);
        d.appendChild(out);
        box.appendChild(d);
      });
      if (typeof run.final === "string" && run.final) {
        var fin = el("pre");
        fin.textContent = run.final;
        box.appendChild(fin);
      }
      var meta = el("div", "tiny");
      var bits = [run.brain || ""];
      if (run.fell_back) { bits.push("fell back"); }
      if (run.latency_ms !== undefined) { bits.push(run.latency_ms + "ms"); }
      if (run.tokens_in !== undefined || run.tokens_out !== undefined) {
        bits.push((run.tokens_in || 0) + "/" + (run.tokens_out || 0));
      }
      if (run.cost_usd !== undefined) { bits.push("$" + run.cost_usd); }
      txt(meta, bits.join(" "));
      box.appendChild(meta);
    }).catch(function (err) {
      box.textContent = "";
      showError(box, err, function () { renderTrace(box, runId); });
    });
  }

  function sendDecision(task, inspId, body, btn) {
    if (btn) { lock(btn); }
    var key = idem();
    function go() {
      api("POST", "/inspections/" + inspId + "/decision", body, { key: key }).then(function (insp) {
        task.decision = insp.decision === "retake" ? "pending" : insp.decision;
        if (insp.decision === "retake") {
          state.inspection = null;
        } else {
          state.inspection = insp;
        }
        state.slowReview = false;
        renderInspect();
        refreshDhu();
      }).catch(function (err) {
        showError($("inspect-error"), err, go);
      }).then(function () { if (btn) { unlock(btn); } });
    }
    go();
  }

  /* ---------- change sheet ---------- */

  function fillSelect(sel, items, current) {
    sel.textContent = "";
    items.forEach(function (it) {
      var o = document.createElement("option");
      o.value = it.value;
      o.textContent = it.text;
      if (it.value === current) { o.selected = true; }
      sel.appendChild(o);
    });
  }

  function labeledPick(label, sel) {
    var lab = el("label");
    lab.style.display = "block";
    lab.style.margin = "12px 0";
    lab.appendChild(el("div", "seclabel", label));
    lab.appendChild(sel);
    return lab;
  }

  function openChange(task, inspId) {
    var proposed = state.inspection ? state.inspection.proposed_label : LABELS[0];
    openSheet(function () { return L(t("inspect", "change")); }, function (body) {
      var ls = el("select");
      fillSelect(ls, LABELS.map(function (l) {
        return { value: l, text: labelName(l) };
      }), proposed);
      body.appendChild(labeledPick(L(t("extra", "f_label")), ls));
      var ps = el("select");
      fillSelect(ps, [{ value: "", text: "-" }].concat(PARTS.map(function (p) {
        return { value: p, text: partName(p) };
      })), "");
      body.appendChild(labeledPick(L(t("extra", "f_part")), ps));
      var ss = el("select");
      fillSelect(ss, [{ value: "", text: "-" }].concat(SEVS.map(function (s) {
        return { value: s, text: sevName(s) };
      })), "");
      body.appendChild(labeledPick(L(t("extra", "f_severity")), ss));
      var err = el("div", "error");
      err.hidden = true;
      body.appendChild(err);
      var save = el("button", "primary", L(t("extra", "save_change")));
      save.type = "button";
      save.style.marginTop = "12px";
      var changeKey = idem();
      function sendChange() {
        lock(save);
        var payload = { action: "change", label: ls.value, via: "tap" };
        if (ps.value) { payload.part = ps.value; }
        if (ss.value) { payload.severity = ss.value; }
        api("POST", "/inspections/" + inspId + "/decision", payload, { key: changeKey }).then(function (insp) {
          task.decision = insp.decision;
          state.inspection = insp;
          state.slowReview = false;
          closeSheet();
          renderInspect();
          refreshDhu();
        }).catch(function (e2) {
          showError(err, e2, sendChange);
        }).then(function () { unlock(save); });
      }
      save.addEventListener("click", sendChange);
      body.appendChild(save);
      var cancel = el("button", "tertiary", L(t("common", "cancel")));
      cancel.type = "button";
      cancel.addEventListener("click", closeSheet);
      body.appendChild(cancel);
    });
  }

  /* ---------- voice sheet ---------- */

  function pickMime() {
    var cands = ["audio/webm", "audio/mp4", "audio/webm;codecs=opus", "audio/wav"];
    for (var i = 0; i < cands.length; i++) {
      try {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(cands[i])) { return cands[i]; }
      } catch (e) { /* try next */ }
    }
    return "";
  }

  function openVoice(inspId) {
    state.voice = { inspId: inspId, rec: null, chunks: [], timer: 0, inputs: null, ambiguous: [] };
    openSheet(function () { return L(t("extra", "correct")); }, function (body) {
      var mic = el("button", "micbtn");
      mic.type = "button";
      mic.id = "btn-mic";
      mic.setAttribute("aria-label", L(t("voice", "hold_to_speak")));
      mic.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
      var wrap = el("div", "microw");
      wrap.appendChild(mic);
      body.appendChild(wrap);
      var rs = el("div", "grey pad");
      rs.id = "rec-state";
      rs.hidden = true;
      body.appendChild(rs);
      var verr = el("div", "error");
      verr.id = "voice-error";
      verr.hidden = true;
      body.appendChild(verr);
      body.appendChild(el("h4", null, L(t("voice", "transcript"))));
      var tr = el("p");
      tr.id = "voice-transcript";
      body.appendChild(tr);
      body.appendChild(el("h4", null, L(t("voice", "check_fields"))));
      var vf = el("div");
      vf.id = "voice-fields";
      body.appendChild(vf);
      body.appendChild(el("h4", null, L(t("extra", "f_label"))));
      var vc = el("div");
      vc.id = "voice-candidates";
      body.appendChild(vc);
      var confirm = el("button", "primary", L(t("voice", "confirm")));
      confirm.type = "button";
      confirm.id = "btn-voice-confirm";
      confirm.disabled = true;
      confirm.style.marginTop = "12px";
      confirm.addEventListener("click", confirmVoice);
      body.appendChild(confirm);
      var held = false;
      mic.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        held = true;
        startRec(mic, rs, verr);
      });
      ["pointerup", "pointerleave", "pointercancel"].forEach(function (evn) {
        mic.addEventListener(evn, function () {
          if (held) { held = false; stopRec(); }
        });
      });
    });
  }

  function voiceFail(msg) {
    var box = $("voice-error");
    if (!box) { return; }
    box.hidden = false;
    box.textContent = "";
    txt(box, msg);
  }

  function stopRec() {
    var v = state.voice;
    if (v && v.rec && v.rec.state !== "inactive") {
      try { v.rec.stop(); } catch (e) { /* already stopped */ }
    }
    if (v && v.timer) { clearInterval(v.timer); v.timer = 0; }
    var rs = $("rec-state");
    if (rs) { rs.hidden = true; }
    var mb = $("btn-mic");
    if (mb) { mb.classList.remove("rec"); }
  }

  function startRec(mic, rs, verr) {
    var v = state.voice;
    if (!v) { return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      voiceFail(L(t("extra", "mic_denied")));
      return;
    }
    verr.hidden = true;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (!state.voice) {
        stream.getTracks().forEach(function (tr) { tr.stop(); });
        return;
      }
      var mime = pickMime();
      var rec;
      try {
        rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      } catch (e) {
        voiceFail(L(t("extra", "mic_denied")));
        return;
      }
      v.rec = rec;
      v.chunks = [];
      rec.ondataavailable = function (e) { if (e.data && e.data.size) { v.chunks.push(e.data); } };
      rec.onstop = function () {
        stream.getTracks().forEach(function (tr) { tr.stop(); });
        sendVoice(v);
      };
      rec.start();
      mic.classList.add("rec");
      var left = 30;
      rs.hidden = false;
      txt(rs, L(t("extra", "recording")) + " " + left + "s");
      v.timer = setInterval(function () {
        left -= 1;
        if (left <= 0) { stopRec(); return; }
        txt(rs, L(t("extra", "recording")) + " " + left + "s");
      }, 1000);
      setTimeout(function () { stopRec(); }, 30000);
    }).catch(function () {
      voiceFail(L(t("extra", "mic_denied")));
    });
  }

  function sendVoice(v) {
    var blob = new Blob(v.chunks, { type: (v.rec && v.rec.mimeType) || "audio/webm" });
    var key = idem();
    function go() {
      var fd = new FormData();
      var ext = blob.type.indexOf("mp4") >= 0 ? "mp4" : "webm";
      fd.append("audio", blob, "note." + ext);
      api("POST", "/inspections/" + v.inspId + "/voice", fd, { long: true, key: key }).then(function (res) {
        v.proposed = res.proposed || {};
        v.ambiguous = res.ambiguous || [];
        v.candidates = res.label_candidates || [];
        var tr = $("voice-transcript");
        if (tr) { txt(tr, res.transcript || ""); }
        renderVoiceFields(v);
      }).catch(function (err) {
        showError($("voice-error"), err, go);
      });
    }
    go();
  }

  function voiceField(label, input, need) {
    var lab = el("label");
    lab.style.display = "block";
    lab.style.margin = "10px 0";
    lab.appendChild(el("div", "seclabel", label));
    if (need) { input.style.borderColor = "var(--red)"; input.style.background = "var(--red-bg)"; }
    lab.appendChild(input);
    return lab;
  }

  function renderVoiceFields(v) {
    var box = $("voice-fields");
    if (!box) { return; }
    box.textContent = "";
    v.inputs = {};
    [["label", LABELS, function (o) { return labelName(o); }, L(t("extra", "f_label"))],
     ["part", PARTS, function (o) { return partName(o); }, L(t("extra", "f_part"))],
     ["severity", SEVS, function (o) { return sevName(o); }, L(t("extra", "f_severity"))]
    ].forEach(function (spec) {
      var key = spec[0];
      var cur = v.proposed[key] || "";
      var seen = {};
      var opts = [];
      if (cur) { opts.push(cur); seen[cur] = true; }
      spec[1].forEach(function (o) { if (!seen[o]) { opts.push(o); } });
      var sel = el("select");
      sel.style.minHeight = "48px";
      opts.forEach(function (o) {
        var op = document.createElement("option");
        op.value = o;
        op.textContent = spec[2](o);
        if (o === cur) { op.selected = true; }
        sel.appendChild(op);
      });
      v.inputs[key] = sel;
      box.appendChild(voiceField(spec[3], sel, v.ambiguous.indexOf(key) >= 0));
    });
    [["operator_id", L(t("inspect", "operator"))], ["machine_id", L(t("inspect", "machine"))]].forEach(function (spec) {
      var inp = el("input");
      inp.type = "text";
      inp.value = v.proposed[spec[0]] || "";
      v.inputs[spec[0]] = inp;
      box.appendChild(voiceField(spec[1], inp, v.ambiguous.indexOf(spec[0]) >= 0));
    });
    var cbox = $("voice-candidates");
    cbox.textContent = "";
    (v.candidates || []).forEach(function (c) {
      var b = el("button", "chipbtn", labelName(c));
      b.type = "button";
      b.addEventListener("click", function () {
        v.inputs.label.value = c;
      });
      cbox.appendChild(b);
    });
    $("btn-voice-confirm").disabled = false;
  }

  function confirmVoice() {
    var v = state.voice;
    if (!v || !v.inputs) { return; }
    for (var i = 0; i < v.ambiguous.length; i++) {
      var k = v.ambiguous[i];
      if (!v.inputs[k] || !v.inputs[k].value) {
        voiceFail(L(t("extra", "ambiguous_fill")));
        return;
      }
    }
    var btn = $("btn-voice-confirm");
    lock(btn);
    var key = idem();
    function go() {
      var body = {
        action: "change",
        label: v.inputs.label.value,
        part: v.inputs.part.value || undefined,
        severity: v.inputs.severity.value || undefined,
        operator_id: v.inputs.operator_id.value || undefined,
        machine_id: v.inputs.machine_id.value || undefined,
        via: "voice"
      };
      var task = state.tasks[state.activeTask] || { part: body.part || "collar", decision: "pending" };
      api("POST", "/inspections/" + v.inspId + "/decision", body, { key: key }).then(function (insp) {
        task.decision = insp.decision;
        state.inspection = insp;
        closeSheet();
        state.voice = null;
        renderInspect();
        refreshDhu();
      }).catch(function (err) {
        showError($("voice-error"), err, go);
      }).then(function () { unlock(btn); });
    }
    go();
  }

  $("btn-viewer-close").addEventListener("click", function () {
    $("viewer").hidden = true;
    $("viewer-img").src = "";
  });

  /* ---------- dashboard ---------- */

  function bars(box, obj, fmt, key) {
    var keys = Object.keys(obj || {});
    if (!keys.length) { return 0; }
    keys.sort(function (a, b) { return (obj[b].defects || 0) - (obj[a].defects || 0); });
    var show = state.showAll[key] ? keys : keys.slice(0, 5);
    var max = 1;
    keys.forEach(function (k) { max = Math.max(max, obj[k].defects || 0); });
    show.forEach(function (k) {
      var row = el("div", "bar-row");
      row.appendChild(el("span", "lbl", fmt(k)));
      var bar = el("div", "bar");
      var fill = el("i");
      fill.style.width = Math.round(((obj[k].defects || 0) / max) * 100) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("span", "n", String(obj[k].defects || 0)));
      box.appendChild(row);
    });
    if (keys.length > 5) {
      var more = el("button", "tertiary", state.showAll[key] ? L(t("extra", "show_less")) : L(t("extra", "show_all")));
      more.type = "button";
      more.addEventListener("click", function () {
        state.showAll[key] = !state.showAll[key];
        renderDashboard();
      });
      box.appendChild(more);
    }
    return keys.length;
  }

  function renderDashboard() {
    var root = $("screen-dashboard");
    root.textContent = "";
    if (!state.session) {
      root.appendChild(emptyState("chart", L(t("extra", "no_data")), L(t("extra", "start_day_btn")), function () {
        show("inspect");
        openEntry();
      }));
      return;
    }
    var err = el("div", "error");
    err.id = "dash-error";
    err.hidden = true;
    root.appendChild(err);
    var wrap = el("div");
    wrap.id = "dash-body";
    root.appendChild(wrap);
    api("GET", "/sessions/" + state.session.id + "/dashboard").then(function (d) {
      if (state.screen !== "dashboard") { return; }
      state.dhu = fmt1(d.dhu);
      wrap.textContent = "";
      var nums = el("div", "bignums");
      var n1 = el("div", "bignum");
      n1.appendChild(el("div", "v", fmt1(d.dhu)));
      n1.appendChild(el("div", "k", "DHU"));
      var n2 = el("div", "bignum");
      n2.appendChild(el("div", "v", fmt1(d.defective_pct) + "%"));
      n2.appendChild(el("div", "k", L(t("extra", "defective_short"))));
      nums.appendChild(n1);
      nums.appendChild(n2);
      wrap.appendChild(nums);

      var mcard = el("div", "card");
      mcard.appendChild(el("div", "seclabel", L(t("dashboard", "by_machine"))));
      if (!bars(mcard, (d.by || {}).machine, function (k) {
        return L(t("inspect", "machine")) + " " + k;
      }, "machine")) {
        mcard.appendChild(el("div", "tiny", L(t("extra", "no_data"))));
      }
      wrap.appendChild(mcard);

      var all = el("button", "chev");
      all.type = "button";
      all.appendChild(el("span", null, L(t("extra", "all"))));
      var allc = el("span", "c", "▸");
      all.appendChild(allc);
      var allBody = el("div");
      allBody.hidden = true;
      all.addEventListener("click", function () {
        allBody.hidden = !allBody.hidden;
        txt(allc, allBody.hidden ? "▸" : "▾");
        if (!allBody.hidden && !allBody.children.length) {
          [["part", (d.by || {}).part, partName, L(t("dashboard", "by_part"))],
           ["defect", (d.by || {}).defect, labelName, L(t("dashboard", "by_defect"))]
          ].forEach(function (spec) {
            var c2 = el("div", "card");
            c2.appendChild(el("div", "seclabel", spec[3]));
            bars(c2, spec[1], spec[2], spec[0]);
            allBody.appendChild(c2);
          });
          if ((d.top_issues || []).length) {
            var tc = el("div", "card");
            tc.appendChild(el("div", "seclabel", L(t("dashboard", "top_issues"))));
            d.top_issues.forEach(function (row) {
              var p = el("div", "tiny");
              txt(p, L(t("inspect", "machine")) + " " + row[0] + " · " + partName(row[1]) + " · " + labelName(row[2]) + " · " + row[3]);
              tc.appendChild(p);
            });
            allBody.appendChild(tc);
          }
        }
      });
      wrap.appendChild(all);
      wrap.appendChild(allBody);

      var ap = d.audit_pass_chance || {};
      var pc = el("button", "card");
      pc.type = "button";
      pc.style.width = "100%";
      pc.style.textAlign = "left";
      pc.style.cursor = "pointer";
      pc.appendChild(el("div", "seclabel", L(t("extra", "audit_pass"))));
      var pv = el("div");
      pv.style.fontSize = "30px";
      pv.style.fontWeight = "600";
      txt(pv, ap.both === undefined ? "-" : (Number(ap.both) * 100).toFixed(1) + "%");
      pc.appendChild(pv);
      pc.addEventListener("click", function () { show("audit"); });
      wrap.appendChild(pc);

      (d.alerts || []).forEach(function (a) {
        var acard = el("button", "card");
        acard.type = "button";
        acard.style.width = "100%";
        acard.style.textAlign = "left";
        acard.style.cursor = "pointer";
        acard.appendChild(el("div", null, L(t("inspect", "machine")) + " " + a.machine_id));
        acard.appendChild(el("div", "grey", labelName(a.defect)));
        acard.addEventListener("click", function () { openAlert(a); });
        wrap.appendChild(acard);
      });
    }).catch(function (e2) {
      showError(err, e2, renderDashboard);
    });
  }

  function openAlert(a) {
    openSheet(function () { return L(t("inspect", "machine")) + " " + a.machine_id; }, function (body) {
      body.appendChild(el("div", "grey", labelName(a.defect)));
      var woText = state.lang === "te" ? (a.body_te || a.body_md) : (a.body_en || a.body_md);
      if (woText) {
        body.appendChild(el("div", "seclabel", L(t("dashboard", "work_order"))));
        body.appendChild(md(woText));
      } else {
        body.appendChild(el("div", "grey pad", L(t("extra", "work_order_pending"))));
      }
      if (a.agent_run_id) {
        body.appendChild(traceRow(a.agent_run_id));
      }
    });
  }

  /* ---------- report ---------- */

  function footReport() {
    if (!state.session) { return; }
    showFoot(L(t("extra", "generate")), genReport);
  }

  function renderReport() {
    var root = $("screen-report");
    root.textContent = "";
    if (!state.session) {
      root.appendChild(emptyState("doc", L(t("extra", "no_data")), L(t("extra", "start_day_btn")), function () {
        show("inspect");
        openEntry();
      }));
      return;
    }
    var err = el("div", "error");
    err.id = "report-error";
    err.hidden = true;
    root.appendChild(err);
    var body = el("article", "report");
    body.id = "report-body";
    root.appendChild(body);
    if (state.reportId) { openReport(state.reportId, body, err); }
  }

  function genReport() {
    var btn = $("foot-btn");
    lock(btn);
    showFoot(L(t("extra", "writing")), function () {}, true);
    var key = idem();
    function go() {
      api("POST", "/sessions/" + state.session.id + "/report", undefined, { long: true, key: key }).then(function (r) {
        state.reportId = r.id;
        saveSess();
        renderReportView(r);
        footReport();
      }).catch(function (err) {
        footReport();
        showError($("report-error"), err, go);
      });
    }
    go();
  }

  function renderReportView(r) {
    var root = $("screen-report");
    var err = $("report-error");
    var body = $("report-body");
    if (!body) { return; }
    body.textContent = "";
    if (r.validator_ok === 0) {
      var w = el("div", "warn");
      txt(w, L(t("report", "validator_warning")));
      root.insertBefore(w, body);
    }
    body.appendChild(md(r.body_md || ""));
    var row = el("div");
    var pr = el("button", "iconbtn");
    pr.type = "button";
    pr.setAttribute("aria-label", L(t("extra", "print_short")));
    pr.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 8V3h10v5M7 17H4v-7h16v7h-3M7 14h10v7H7z"/></svg>';
    pr.addEventListener("click", function () { window.print(); });
    row.appendChild(pr);
    body.appendChild(row);
    if (r.agent_run_id) {
      body.appendChild(traceRow(r.agent_run_id));
    }
  }

  function openReport(id, body, err) {
    api("GET", "/reports/" + id).then(function (r) {
      if (state.screen === "report") { renderReportView(r); }
    }).catch(function (e2) {
      showError(err, e2, function () { openReport(id, body, err); });
    });
  }

  /* ---------- more ---------- */

  function rowBtn(label, right, fn) {
    var b = el("button", "row");
    b.type = "button";
    b.appendChild(el("span", null, label));
    if (right !== null && right !== undefined) { b.appendChild(el("span", "r", right)); }
    b.addEventListener("click", fn);
    return b;
  }

  function renderMore() {
    var root = $("screen-more");
    root.textContent = "";
    var list = el("div", "rows");
    var brainName = state.brain === "spark" ? "Muse Spark" : L(t("extra", "brain_open_short"));
    list.appendChild(rowBtn(L(t("start_day", "ai_mode")), brainName, openBrainChoice));
    list.appendChild(rowBtn(L(t("extra", "unlock_unit")), "›", openPinSheet));
    if (state.session) {
      list.appendChild(rowBtn(L(t("extra", "new_bundle")), "›", openNewBundle));
    }
    list.appendChild(rowBtn(L(t("extra", "sup_short")), "›", openSupervisor));
    if (state.gallery && state.gallery.length) {
      list.appendChild(rowBtn(L(t("extra", "demo_gallery_row")), "›", openGallery));
    }
    if (state.session && state.session.is_demo === 1) {
      list.appendChild(rowBtn(L(t("demo", "reset")), "", resetDemo));
    }
    root.appendChild(list);
    var about = el("div", "tiny pad");
    txt(about, L(t("extra", "about_data")));
    root.appendChild(about);
    if (state.gallery === null) { loadGallery(); }
  }

  function openPinSheet() {
    openSheet(function () { return L(t("extra", "unlock_unit")); }, function (body) {
      if (state.unlocked) {
        body.appendChild(el("div", "notice", L(t("extra", "unit_unlocked"))));
        return;
      }
      body.appendChild(el("div", "seclabel", L(t("extra", "unit_pin"))));
      var pin = el("input");
      pin.id = "f-pin";
      pin.type = "text";
      pin.inputMode = "numeric";
      pin.maxLength = 6;
      pin.autocomplete = "off";
      body.appendChild(pin);
      var pst = el("div", "pad");
      pst.hidden = true;
      body.appendChild(pst);
      var un = el("button", "primary", L(t("extra", "unlock")));
      un.type = "button";
      un.style.marginTop = "12px";
      un.addEventListener("click", function () { unlockUnit(pin.value, un, pst); });
      body.appendChild(un);
    });
  }

  function openSupervisor() {
    openSheet(function () { return L(t("extra", "sup_short")); }, function (body) {
      if (!state.session) {
        body.appendChild(el("div", "grey pad", L(t("extra", "no_data"))));
        return;
      }
      var loading = el("div", "grey pad", L(t("common", "loading")));
      body.appendChild(loading);
      api("GET", "/sessions/" + state.session.id + "/dashboard").then(function (d) {
        loading.textContent = "";
        var ops = ((d.supervisor_only || {}).by_operator) || {};
        var keys = Object.keys(ops);
        if (!keys.length) {
          txt(loading, L(t("extra", "no_data")));
          return;
        }
        keys.forEach(function (k) {
          var p = el("div", "tiny pad");
          txt(p, k + ": " + (ops[k].defects || 0));
          body.appendChild(p);
        });
      }).catch(function (err) {
        loading.textContent = "";
        showError(loading, err, function () {
          closeSheet();
          openSupervisor();
        });
      });
    });
  }

  function openBrainChoice() {
    openSheet(function () { return L(t("start_day", "ai_mode")); }, function (body) {
      if (!state.brains.length) {
        body.appendChild(el("div", "grey", L(t("common", "loading"))));
        loadBrains(function () { rebuildSheet(); });
        return;
      }
      state.brains.forEach(function (b) {
        var label = b.brain === "spark" ? L(t("extra", "seg_low")) : L(t("extra", "seg_high"));
        var opt = el("button", "tinted", label + (state.brain === b.brain ? " ✓" : ""));
        opt.type = "button";
        opt.style.marginTop = "8px";
        opt.disabled = !b.available;
        opt.addEventListener("click", function () { pickBrain(b.brain, body); });
        body.appendChild(opt);
        body.appendChild(el("div", "tiny", L(figLine(b))));
      });
    });
  }

  function pickBrain(v, body) {
    state.brain = v;
    if (!state.session) {
      closeSheet();
      renderMore();
      return;
    }
    var key = idem();
    function go() {
      api("POST", "/sessions/" + state.session.id + "/brain", { brain: v }, { key: key }).then(function (s) {
        state.session = s;
        saveSess();
        paintChrome();
        closeSheet();
        renderMore();
      }).catch(function (err) {
        var ebox = el("div", "error");
        showError(ebox, err, go);
        body.appendChild(ebox);
      });
    }
    go();
  }

  function loadGallery() {
    api("GET", "/demo/gallery").then(function (items) {
      state.gallery = items || [];
      if (state.screen === "more") { renderMore(); }
    }).catch(function (err) {
      state.gallery = (err && err.status === 404) ? [] : null;
      if (state.screen === "more" && state.gallery !== null) { renderMore(); }
    });
  }

  function openGallery() {
    openSheet(function () { return L(t("extra", "demo_gallery_row")); }, function (body) {
      var g = el("div", "gallery");
      (state.gallery || []).forEach(function (it) {
        var b = el("button");
        b.type = "button";
        if (it.thumb_url) {
          var img = el("img");
          img.src = mediaUrl(it.thumb_url);
          img.alt = "";
          b.appendChild(img);
        }
        var cap = el("div", "cap");
        txt(cap, state.lang === "en" ? (it.title_en || "") : (it.title_te || ""));
        b.appendChild(cap);
        b.addEventListener("click", function () { demoTry(it); });
        g.appendChild(b);
      });
      body.appendChild(g);
    });
  }

  function demoTry(item) {
    if (!state.session) { return; }
    function go() {
      var part = state.tasks.length ? state.tasks[0].part : "collar";
      api("POST", "/demo/gallery/" + item.id, { garment_id: state.garmentId, part: part }).then(function (res) {
        closeSheet();
        show("inspect");
        var task = state.tasks[0];
        if (!task) {
          task = { part: part, reason: "critical", inspection: null, decision: "pending" };
          state.tasks = [task];
          state.activeTask = 0;
        }
        task.inspection = res.inspection_id;
        state.inspection = res;
        renderInspect();
        pollInspection(task, res.inspection_id);
      }).catch(function () { /* gallery errors stay silent in the sheet */ });
    }
    if (!state.garmentId) {
      api("POST", "/bundles/" + state.bundleId + "/garments", { garment_no: state.garmentNo + 1 }).then(function (g) {
        setGarment(g);
        go();
      }).catch(function () { /* silent */ });
    } else {
      go();
    }
  }

  function openNewBundle() {
    openSheet(function () { return L(t("extra", "new_bundle")); }, function (body) {
      body.appendChild(el("div", "seclabel", L(t("extra", "bundle_no"))));
      var num = el("input");
      num.type = "number";
      num.min = "1";
      num.inputMode = "numeric";
      num.value = state.bundleNo + 1;
      body.appendChild(num);
      var err = el("div", "error");
      err.hidden = true;
      body.appendChild(err);
      var go = el("button", "primary", L(t("common", "save")));
      go.type = "button";
      go.style.marginTop = "12px";
      var key = idem();
      function send() {
        lock(go);
        api("POST", "/sessions/" + state.session.id + "/bundles", {
          bundle_no: parseInt(num.value, 10)
        }, { key: key }).then(function (b) {
          state.bundleId = b.id;
          state.bundleNo = b.bundle_no;
          state.garmentId = null;
          state.garmentNo = 0;
          state.tasks = [];
          state.inspection = null;
          state.alertBanner = [];
          state.garmentComplete = false;
          saveSess();
          closeSheet();
          show("inspect");
        }).catch(function (e2) {
          showError(err, e2, send);
        }).then(function () { unlock(go); });
      }
      go.addEventListener("click", send);
      body.appendChild(go);
    });
  }

  function resetDemo() {
    api("POST", "/demo/reset").then(function (d) {
      return api("GET", "/sessions/" + d.session_id).then(function (s) {
        state.session = s;
        state.bundleId = d.bundle_id;
        state.bundleNo = 1;
        state.garmentId = null;
        state.garmentNo = 0;
        state.tasks = [];
        state.inspection = null;
        state.alertBanner = [];
        state.garmentComplete = false;
        saveSess();
        paintChrome();
        renderMore();
      });
    }).catch(function () { /* silent */ });
  }

  /* ---------- audit ---------- */

  function loadAudit(showErr) {
    if (!state.session) {
      var root = $("screen-audit");
      root.textContent = "";
      root.appendChild(emptyState("doc", L(t("extra", "no_data")), L(t("extra", "start_day_btn")), function () {
        show("inspect");
        openEntry();
      }));
      return;
    }
    api("GET", "/sessions/" + state.session.id + "/audit").then(function (a) {
      if (state.screen === "audit") { renderAudit(a); }
    }).catch(function (err) {
      if (err && err.status === 404) {
        renderAudit(null);
        return;
      }
      if (showErr) {
        var root2 = $("screen-audit");
        root2.textContent = "";
        var ebox = el("div", "error");
        root2.appendChild(ebox);
        showError(ebox, err, function () { loadAudit(true); });
      }
    });
  }

  function auditStart() {
    var key = idem();
    function go() {
      api("POST", "/sessions/" + state.session.id + "/audit", undefined, { key: key }).then(function () {
        loadAudit(true);
      }).catch(function (err) {
        var root = $("screen-audit");
        var ebox = el("div", "error");
        root.appendChild(ebox);
        showError(ebox, err, go);
      });
    }
    go();
  }

  function planNums(plan, sev) {
    var p = (plan || {})[sev];
    if (!p) { return null; }
    return { sample: p.sample_size, ac: p.ac, re: p.re };
  }

  function renderAudit(a) {
    var root = $("screen-audit");
    root.textContent = "";
    var back = el("button", "tertiary", L(t("common", "back")));
    back.type = "button";
    back.addEventListener("click", function () { show("dashboard"); });
    root.appendChild(back);
    if (!a) {
      root.appendChild(emptyState("doc", L(t("extra", "audit_none")), L(t("audit", "start")), auditStart));
      return;
    }
    state.auditBundleId = a.audit_bundle_id;
    var badge = el("span", "pill " + (a.status === "accept" ? "green" : (a.status === "reject" ? "red" : "grey")));
    txt(badge, a.status === "accept" ? L(t("audit", "accept")) : (a.status === "reject" ? L(t("audit", "reject")) : L(t("audit", "in_progress"))));
    root.appendChild(badge);
    ["major", "minor"].forEach(function (sev) {
      var n = planNums(a.plan, sev);
      if (!n) { return; }
      var card = el("div", "card");
      card.appendChild(el("div", "seclabel", sev === "major" ? L(t("severity", "major")) : L(t("severity", "minor"))));
      var nums = el("div", "bignums");
      [[n.sample, L(t("extra", "sample_short"))], [n.ac, L(t("extra", "accept_upto"))], [n.re, L(t("extra", "reject_at"))]].forEach(function (pair) {
        var b = el("div", "bignum");
        b.appendChild(el("div", "v", String(pair[0])));
        b.appendChild(el("div", "k", pair[1]));
        nums.appendChild(b);
      });
      card.appendChild(nums);
      root.appendChild(card);
    });
    var c = a.counts || {};
    var counts = el("div", "tiny pad");
    txt(counts, "major " + (c.major || 0) + " · minor " + (c.minor || 0) + " · critical " + (c.critical || 0));
    root.appendChild(counts);
    root.appendChild(el("div", "seclabel", L(t("audit", "sample"))));
    var grid = el("div", "audit-grid");
    (a.sampled_garment_nos || []).forEach(function (n) {
      var b = el("button", (a.inspected_garment_nos || []).indexOf(n) >= 0 ? "hit" : null, String(n));
      b.type = "button";
      if ((a.inspected_garment_nos || []).indexOf(n) >= 0) { b.textContent = "✓ " + n; }
      b.addEventListener("click", function () { auditInspect(n); });
      grid.appendChild(b);
    });
    root.appendChild(grid);
  }

  function auditInspect(n) {
    if (!state.auditBundleId) { return; }
    api("POST", "/bundles/" + state.auditBundleId + "/garments", { garment_no: n }).then(function (g) {
      state.auditMode = true;
      setGarment(g);
      show("inspect");
    }).catch(function (err) {
      var root = $("screen-audit");
      var ebox = el("div", "error");
      root.appendChild(ebox);
      showError(ebox, err, function () { auditInspect(n); });
    });
  }

  /* ---------- service worker banner + boot ---------- */

  function paintOffline() {
    var off = false;
    try { off = !navigator.onLine; } catch (e) { off = false; }
    var bar = $("offline-bar");
    txt(bar, L(t("extra", "offline_short")));
    bar.hidden = !off;
  }

  function watchSw() {
    if (!("serviceWorker" in navigator)) { return; }
    navigator.serviceWorker.register("./sw.js").catch(function () { /* offline first load */ });
    var reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (!reloading) { reloading = true; window.location.reload(); }
    });
  }

  function boot() {
    var saved = load("stitchcheck_lang");
    if (saved === "en" || saved === "te") {
      state.lang = saved;
    } else if (navigator.language && navigator.language.indexOf("en") === 0) {
      state.lang = "en";
    } else {
      state.lang = "te";
    }
    txt($("btn-viewer-close"), L(t("extra", "close")));
    document.documentElement.lang = state.lang === "en" ? "en" : "te";
    var plan = load("stitchcheck_plan");
    if (plan && plan.length) { state.form.plan = plan; }
    paintChrome();
    paintOffline();
    loadBrains(function () {
      if (state.screen === "more") { renderMore(); }
    });
    watchSw();
    show("inspect");
    openEntry();
    $("sheet-back").addEventListener("click", function (e) {
      if (e.target === $("sheet-back")) { closeSheet(); }
    });
  }

  boot();
})();
