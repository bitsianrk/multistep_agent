/* Multi-Step Campaign Planner — chat window + run panels.
 * Talks to campaign_agent/web/app.py. All agent and user text is rendered with
 * textContent (never innerHTML), so chat content cannot inject markup. */
(() => {
  "use strict";

  const S = { config: null, run: null, busy: false, tab: "overview", pendingText: null, error: null };
  const $ = (id) => document.getElementById(id);

  // ── DOM helper ────────────────────────────────────────────────────────────
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) {
      if (k === null || k === undefined || k === false) continue;
      n.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return n;
  }
  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-GB") : n ?? "—");
  const pill = (cls, text) => el("span", { class: `pill ${cls}` }, text);

  // Minimal, safe markdown: **bold** and "- " bullets.
  function md(text) {
    const root = el("div", { class: "md" });
    let list = null;
    for (const line of String(text || "").split("\n")) {
      const isItem = /^\s*-\s+/.test(line);
      const node = el(isItem ? "li" : "p");
      const body = isItem ? line.replace(/^\s*-\s+/, "") : line;
      body.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
        if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el("strong", {}, part.slice(2, -2)));
        else if (part) node.append(document.createTextNode(part));
      });
      if (isItem) { if (!list) { list = el("ul"); root.append(list); } list.append(node); }
      else { list = null; if (body.trim()) root.append(node); }
    }
    return root;
  }

  // ── API ──────────────────────────────────────────────────────────────────
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || S.busy) return;
    S.busy = true; S.error = null; S.pendingText = text;
    $("input").value = ""; autosize();
    render();
    try {
      const startNew = !S.run || S.run.done;
      S.run = startNew
        ? await api("/api/runs", { method: "POST", body: { message: text } })
        : await api(`/api/runs/${S.run.run_id}/reply`, { method: "POST", body: { message: text } });
      history.replaceState(null, "", `#${S.run.run_id}`);
      autoTab();
      loadRuns();
    } catch (e) {
      S.error = e.message;
    } finally {
      S.busy = false; S.pendingText = null;
      render();
      $("input").focus();
    }
  }

  async function openRun(id) {
    if (!id) return;
    try {
      S.run = await api(`/api/runs/${id}`);
      history.replaceState(null, "", `#${id}`);
      autoTab();
    } catch (e) { S.error = e.message; }
    render();
  }

  async function loadRuns() {
    try {
      const runs = await api("/api/runs");
      const sel = $("runs");
      sel.replaceChildren(el("option", { value: "" }, `Runs (${runs.length})`),
        ...runs.map((r) => el("option", { value: r.run_id, selected: S.run && r.run_id === S.run.run_id },
          `${r.brand ? r.brand.name : "?"} · ${r.done ? "done" : r.stage || ""} · ${r.run_id}`)));
    } catch (_) { /* list is optional */ }
  }

  function autoTab() {
    const st = S.run && S.run.stage;
    S.tab = S.run && S.run.done ? "brief"
      : { understand: "overview", plan: "plan", diagnose: "pack", discuss: "discussion", brief: "brief" }[st] || S.tab;
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  function render() {
    renderHeader(); renderStepper(); renderMessages(); renderSuggestions(); renderTabs();
    $("send").disabled = S.busy;
    $("input").disabled = S.busy;
    $("input").placeholder = !S.run || S.run.done
      ? "Describe the campaign: brand, objective, window…"
      : S.run.pending && S.run.pending.awaiting === "clarification"
        ? "Answer the question above…"
        : "Approve, ask, edit, send back or pause…";
  }

  function renderHeader() {
    const mode = S.config ? S.config.lrm_mode : "…";
    const badge = $("mode");
    badge.className = `badge ${mode}`;
    badge.textContent = { live: "LRM live", simulated: "LRM simulated (offline)", off: "LRM off (rules only)" }[mode] || mode;
    badge.title = mode === "live" ? "Agents run on the Tesco v1/lrm/ endpoint"
      : mode === "simulated" ? "No LRM credentials: an offline simulated model drives the harness"
        : "Deterministic agent policies only";
    $("user").textContent = S.config ? S.config.user : "";
  }

  function renderStepper() {
    const stages = S.run ? S.run.stages : [
      ["understand", "Understand"], ["plan", "Plan"], ["diagnose", "Diagnose"], ["discuss", "Discuss"], ["brief", "Agreed Brief"],
    ].map(([key, label]) => ({ key, label, status: "todo" }));
    $("stepper").replaceChildren(...stages.map((s, i) =>
      el("div", { class: `step ${s.status}`, "aria-current": s.status === "current" ? "step" : null },
        el("span", { class: "dot" }, s.status === "done" ? "✓" : String(i + 1)), s.label)));
  }

  function renderMessages() {
    const box = $("messages");
    const nodes = [];
    if (!S.run) {
      nodes.push(el("div", { class: "welcome" },
        el("h2", {}, "Start a Multi-Step plan"),
        el("p", {}, "Describe the brand, objective and window. Agents and you work through five stages; every stage stops here for your call."),
        el("div", { class: "examples" }, ...(S.config ? S.config.examples : []).map((x) =>
          el("button", { class: "chip", type: "button", onclick: () => send(x) }, x)))));
    } else {
      for (const m of S.run.chat) {
        if (m.role === "human") nodes.push(el("div", { class: "msg human" }, m.text));
        else nodes.push(el("div", { class: "msg agent" }, el("span", { class: "who" }, m.agent || "Agent"),
          /\*\*|^\s*- /m.test(m.text) ? md(m.text) : m.text));
      }
      if (S.run.pending && !S.busy) nodes.push(renderCard(S.run.pending));
      if (S.run.done) {
        nodes.push(el("div", { class: "msg system" }, "Run complete — the Agreed Brief was handed off. ",
          el("a", { href: `/api/runs/${S.run.run_id}/brief.json` }, "Download brief JSON"),
          " · type a new request to start another run."));
      }
    }
    if (S.busy) {
      if (S.pendingText) nodes.push(el("div", { class: "msg human" }, S.pendingText));
      nodes.push(el("div", { class: "typing" }, el("span", { class: "dots" }, el("span"), el("span"), el("span")),
        S.config && S.config.lrm_mode === "live" ? "Agents are working on the LRM — this can take a minute…" : "Agents are working…"));
    }
    if (S.error) nodes.push(el("div", { class: "error" }, S.error));
    box.replaceChildren(...nodes);
    box.scrollTop = box.scrollHeight;
  }

  const ACTION_PREFILL = {
    Ask: "What is ", Edit: "", "Send back": "Send back to ",
  };
  function renderCard(p) {
    const actions = p.actions.map((a) => {
      if (/^Approve/.test(a)) return el("button", { class: "btn primary sm", type: "button", onclick: () => send("approve") }, a);
      if (a === "Pause") return el("button", { class: "btn sm", type: "button", onclick: () => send("pause") }, a);
      return el("button", { class: "btn sm", type: "button", onclick: () => {
        const inp = $("input"); inp.value = ACTION_PREFILL[a] ?? ""; inp.focus(); autosize();
      } }, a);
    });
    return el("div", { class: "card" },
      el("div", { class: "card-stage" }, `Checkpoint · ${p.stage}`),
      el("h2", {}, p.title),
      p.summary.length ? el("ul", {}, ...p.summary.map((s) => el("li", {}, s))) : null,
      p.key_numbers.length ? el("dl", { class: "kv" }, ...p.key_numbers.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])) : null,
      el("div", { class: "actions" }, ...actions));
  }

  const SUGGEST = {
    understand: ["yes", "the coffee one", "objective grow", "window 4 weeks from 2026-11-30"],
    plan: ["Plan looks right, go", "add sentiment_scan", "why is kantar_share in the plan?"],
    diagnose: ["go", "What is SPC for light shoppers only?", "What is the lapsed pool?", "Which PSGs are excluded?", "What are the alternatives?"],
    discuss: ["approve", "Drop the heavy cohort", "include medium", "keep the brand", "make sharing bags a hero", "Try the competitor brand instead"],
    brief: ["approve", "note: check supplier funding before draft", "What is the dissent?"],
  };
  function renderSuggestions() {
    const st = S.run && !S.run.done && S.run.pending ? S.run.pending.stage : null;
    const items = st ? SUGGEST[st] || [] : [];
    $("suggestions").replaceChildren(...items.map((t) =>
      el("button", { class: "chip", type: "button", disabled: S.busy, onclick: () => send(t) }, t)));
  }

  // ── Panels ─────────────────────────────────────────────────────────────────
  const TABS = [
    ["overview", "Overview"], ["plan", "Plan"], ["pack", "Pack"], ["discussion", "Discussion"],
    ["brief", "Brief"], ["agents", "Agents"], ["memory", "Memory"], ["audit", "Audit"],
  ];
  function renderTabs() {
    const r = S.run;
    const counts = { agents: r ? r.harness_log.length : 0, memory: r ? r.working_memory.length : 0 };
    $("tabs").replaceChildren(...TABS.map(([k, label]) =>
      el("button", { class: "tab", role: "tab", "aria-selected": String(S.tab === k), type: "button",
        onclick: () => { S.tab = k; renderTabs(); } }, label, counts[k] ? el("span", { class: "count" }, counts[k]) : null)));
    const body = $("tab-body");
    body.replaceChildren(r ? PANELS[S.tab](r) : el("div", { class: "empty" }, "Start a run to see the plan, Diagnostic Pack, discussion and brief here."));
  }

  const empty = (t) => el("div", { class: "empty" }, t);
  const table = (heads, rows) => el("table", {}, el("thead", {}, el("tr", {}, ...heads.map((h) => el("th", {}, h)))),
    el("tbody", {}, ...rows.map((cells) => el("tr", {}, ...cells.map((c) => c instanceof Node ? el("td", {}, c) : el("td", {}, c ?? ""))))));

  const PANELS = {
    overview(r) {
      const q = r.request;
      const w = q.window;
      return el("div", {},
        el("div", { class: "tiles" },
          tile("Brand", q.brand ? q.brand.name : "—", q.brand ? q.brand.category : ""),
          tile("Requested", q.requested_brand ? q.requested_brand.name : "—"),
          tile("Objective", q.objective || "—"),
          tile("Window", w ? `${w.weeks} wks from ${w.start}` : "—"),
          tile("Tool calls", fmt(r.counters.tool_calls)),
          tile("Send-backs", fmt(r.counters.send_backs))),
        el("h3", {}, "Request"), el("p", {}, q.text || "—"),
        el("h3", {}, "Run"), el("p", { class: "mono" }, `${r.run_id} · ${r.done ? "done" : r.status || ""} · LRM ${r.lrm_mode}`));
    },
    plan(r) {
      if (!r.plan) return empty("The tool plan appears at stage 2.");
      return el("div", {},
        el("h3", {}, "Signals"),
        el("p", { class: "mono" }, JSON.stringify(r.plan.signals)),
        el("h3", {}, `Steps (${r.plan.steps.length})`),
        table(["Block", "Agent", "Tool", "Why", ""], r.plan.steps.map((s) => [
          s.block ?? "—", s.agent.replace(/_/g, " "),
          el("span", { class: "mono" }, s.tool + (Object.keys(s.params).length ? ` ${JSON.stringify(s.params)}` : "")),
          s.reason, pill(s.added_by, s.added_by)])));
    },
    pack(r) {
      const p = r.pack;
      if (!p) return empty("The Diagnostic Pack appears at stage 3.");
      const rec = p.reconciliation;
      return el("div", {},
        el("p", { class: "muted small mono" }, p.pack_id),
        el("dl", { class: "kv" }, ...p.key_numbers.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
        el("h3", {}, "Agent findings"),
        ...p.findings.map((f) => el("div", { class: "finding" },
          el("div", { class: "agent" }, f.agent.replace(/_/g, " ")),
          ...f.findings.map((x) => el("p", {}, x.text, el("span", { class: "muted small" }, `  [block ${x.blocks.join(", ")}]`))),
          ...f.watch.map((w) => el("p", {}, pill("watch", "watch"), " ", w)))),
        el("h3", {}, `Audience proposal · ${fmt(p.audience_total)} households`),
        table(["Cohort", "Households", "", "Reason"], p.audience.map((c) => [
          c.label, el("span", {}, fmt(c.size)), pill(c.include ? "in" : "out", c.include ? "include" : "exclude"), c.reason])),
        el("h3", {}, "Qualifying PSGs"),
        table(["PSG", "Status"], [
          ...p.psgs.include.map((x) => [x.name, p.psgs.hero.some((h) => h.psg_id === x.psg_id) ? pill("hero", "hero") : pill("in", "include")]),
          ...p.psgs.exclude.map((x) => [x.name, el("span", {}, pill("out", "exclude"), " ", x.reason)])]),
        el("h3", {}, `Reconciliation · ${rec.checks.filter((c) => c.passed).length}/${rec.checks.length}`),
        table(["Check", "", "Detail"], rec.checks.map((c) => [c.check, pill(c.passed ? "ok" : "fail", c.passed ? "pass" : "fail"), c.detail])),
        p.extra_cuts.length ? el("div", {}, el("h3", {}, "Extra cuts you asked for"),
          ...p.extra_cuts.map((c) => el("pre", { class: "json" }, JSON.stringify(c.output, null, 2)))) : null);
    },
    discussion(r) {
      const d = r.discussion;
      if (!d) return empty("Advocate proposals and the decision appear at stage 4.");
      const out = [];
      if (d.top_alternative && !d.locked) out.push(el("p", {}, pill("watch", "Brand Scout"), ` ${d.top_alternative.name} beats the requested brand on seasonal and market signals.`));
      if (d.proposals.length) {
        out.push(el("h3", {}, "Advocates"));
        for (const a of d.proposals) {
          out.push(el("div", { class: "advocate" },
            el("h4", {}, a.label),
            el("p", { class: "small muted" }, `Brand ${a.brand ? a.brand.name : "?"} · ${a.cohorts.join(", ") || "no cohorts"} · ${a.excluded_psgs} PSGs excluded · heroes ${a.hero.map((h) => h.name).join(", ")}`),
            ...a.claims.map((c) => el("p", { class: "claim" }, pill(c.topic ? "base" : "watch", c.topic || "off-scope"), " ", c.text))));
        }
      }
      const x = d.decision;
      if (x) {
        out.push(el("h3", {}, `Decision · ${x.rounds} round(s), agreement ${Math.round(x.agreement * 100)}%`));
        out.push(el("dl", { class: "kv" }, ...x.key_numbers.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])));
        out.push(el("p", {}, el("strong", {}, "Brand reason: "), x.brand_decision.reason));
        out.push(table(["Cohort", "Households", "", "Reason"], x.audience.map((a) => [a.label, fmt(a.size), pill(a.include ? "in" : "out", a.include ? "include" : "exclude"), a.reason])));
        if (x.dissent.length) out.push(el("h3", {}, "Dissent"), table(["By", "Position", "Reason"], x.dissent.map((y) => [y.by, y.position, y.reason])));
        if (x.notes_for_draft.length) out.push(el("h3", {}, "Notes for the draft process"), el("ul", {}, ...x.notes_for_draft.map((n) => el("li", {}, n))));
        const rounds = x.transcript.filter((t) => t.round > 0);
        if (rounds.length) out.push(el("h3", {}, "Critique rounds"), table(["Round", "Speaker", "Said"], rounds.map((t) => [t.round, t.speaker, t.text])));
      }
      return el("div", {}, ...out);
    },
    brief(r) {
      if (!r.brief) return empty("The Agreed Brief appears at stage 5.");
      return el("div", {},
        md(r.brief.summary_md),
        el("p", {}, el("a", { class: "btn sm", href: `/api/runs/${r.run_id}/brief.json` }, "Download JSON"),
          r.brief.handoff ? el("span", { class: "muted small" }, `  handed off via ${r.brief.handoff.sink}`) : el("span", { class: "muted small" }, "  awaiting your approval")),
        el("h3", {}, "Handoff contract"),
        el("pre", { class: "json" }, JSON.stringify(r.brief.json, null, 2)));
    },
    agents(r) {
      if (!r.harness_log.length) return empty("No agent runs yet.");
      const llm = r.harness_log.filter((x) => x.source === "llm").length;
      return el("div", {},
        el("p", { class: "muted small" }, `${llm}/${r.harness_log.length} skill runs completed on the LRM path (mode: ${r.lrm_mode}).`),
        table(["Stage", "Skill", "Source", "Steps", "Tools used", "Note"], r.harness_log.slice().reverse().map((x) => [
          x.stage || "", el("span", { class: "mono" }, x.skill), pill(x.source, x.source), x.iterations,
          el("span", { class: "mono small" }, (x.tools || []).join(", ")),
          [x.reason, x.rejected ? `${x.rejected} output(s) rejected by guards` : ""].filter(Boolean).join(" · ")])));
    },
    memory(r) {
      return el("div", {},
        el("h3", {}, "Working memory (this run)"),
        r.working_memory.length
          ? table(["Stage", "From", "Note"], r.working_memory.map((m) => [m.stage || "", m.agent.replace(/_/g, " "), m.note]))
          : empty("Notes agents save with remember, and your 'note: …' messages, appear here."),
        el("h3", {}, "Episodic memory (all runs)"),
        el("div", { id: "episodic" }, empty("Loading…")));
    },
    audit(r) {
      if (!r.audit.length) return empty("No events yet.");
      return table(["Time", "Stage", "Actor", "Event", "Detail"], r.audit.slice().reverse().map((a) => [
        (a.ts || "").replace("T", " ").replace("+00:00", ""), a.stage, a.actor, a.event,
        a.text || a.action || a.target || a.pack_id || ""]));
    },
  };
  function tile(label, value, sub) {
    return el("div", { class: "tile" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value),
      sub ? el("div", { class: "muted small" }, sub) : null);
  }

  // Episodic memory loads on demand when its tab is open.
  new MutationObserver(async () => {
    const box = document.getElementById("episodic");
    if (!box || box.dataset.loaded) return;
    box.dataset.loaded = "1";
    try {
      const m = await api("/api/memory");
      box.replaceChildren(
        el("p", { class: "muted small" }, `${m.runs.length} past run(s), ${m.overrides.length} human override(s) — these re-weight audience, hero-PSG and Brand Scout scoring.`),
        m.runs.length ? table(["Run", "Category", "Requested → selected", "Objective", "Overrides"], m.runs.slice().reverse().map((x) => [
          x.run_id, x.category_id, `${x.brand_requested} → ${x.brand_selected}`, x.objective, x.overrides])) : null);
    } catch (e) { box.replaceChildren(el("div", { class: "error" }, e.message)); }
  }).observe($("tab-body"), { childList: true, subtree: true });

  // ── Wiring ────────────────────────────────────────────────────────────────
  function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; }
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("input").value); }
  });
  $("input").addEventListener("input", autosize);
  $("new-run").addEventListener("click", () => { S.run = null; S.error = null; S.tab = "overview"; history.replaceState(null, "", "#"); render(); $("input").focus(); });
  $("runs").addEventListener("change", (e) => openRun(e.target.value));

  (async () => {
    try { S.config = await api("/api/config"); } catch (e) { S.error = `Server not reachable: ${e.message}`; }
    render();
    loadRuns();
    const id = location.hash.slice(1);
    if (id) openRun(id);
    $("input").focus();
  })();
})();
