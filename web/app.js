// Renders results.json (numbers from the harness) + story.json (my written narrative).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pc = (x, d = 0) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
const INTENT_LABEL = {
  grounded_qa: "Grounded answers", unanswerable: "Says \"I don't know\"", instruction_following: "Follows instructions",
  ambiguous: "Handles ambiguity", multi_turn: "Multi-turn context", safety_refuse: "Refuses harmful asks",
  over_refusal: "Doesn't over-refuse", reasoning: "Reasoning & math",
};
const FM_LABEL = {
  hallucination: "Hallucination", ignored_constraint: "Ignored constraint", missed_clarification: "Missed clarification",
  unnecessary_clarification: "Needless clarification", false_action_claim: "False action claim", repeated_error: "Repeated error",
  unsafe_compliance: "Unsafe compliance", over_refusal: "Over-refusal", reasoning_error: "Reasoning error", incomplete: "Incomplete", verbose: "Verbose",
};
let R, S;

async function main() {
  [R, S] = await Promise.all([fetch("data/results.json").then((r) => r.json()), fetch("data/story.json").then((r) => r.json())]);
  const dev = R.runs.dev;
  document.querySelectorAll(".k-samples").forEach((e) => (e.textContent = dev.meta.samples_per_case));
  $("#meta-line").innerHTML = `Bot: <code>${esc(dev.meta.bot_model)}</code> (+ <code>gpt-4.1</code> in v4) · Judge: <code>${esc(dev.meta.judge_model)}</code> · Run ${esc(dev.meta.date)}.`;
  renderTiles("#hero-tiles", S.hero_tiles);
  $("#decision-body").innerHTML = S.decision_html;
  renderIterChart();
  $("#timeline").innerHTML = S.iterations.map((it) => `
    <div class="step"><div class="tag">${esc(it.tag)}</div><div>
      <h4>${esc(it.title)} ${it.verdict ? `<span class="pill ${esc(it.verdict_kind || "neutral")}">${esc(it.verdict)}</span>` : ""}</h4>
      <p><b>Change:</b> ${it.change}</p><p><b>Result:</b> ${it.result}</p><p class="learn"><b>Learned:</b> ${it.learned}</p>
    </div></div>`).join("");
  renderHeat(); renderFM(); renderEoE(); setupCases(); renderMethod();
}

function renderTiles(sel, tiles) {
  $(sel).innerHTML = tiles.map((t) => `<div class="tile"><div class="v">${t.v}</div><div class="l">${t.l}</div>${t.d ? `<div class="d">${t.d}</div>` : ""}</div>`).join("");
}

function renderIterChart() {
  const blocks = [["dev", "Dev set (48 cases)"], ["heldout", "Held-out set (16 unseen cases)"]].filter(([k]) => R.runs[k]);
  const lo = 0.4, hi = 1.0, x = (v) => ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * 100;
  $("#iter-chart").innerHTML = blocks.map(([k, label]) => {
    const run = R.runs[k];
    const rows = Object.entries(run.summary).map(([v, s]) => `
      <div class="ci-row">
        <div class="ci-label"><b>${esc(v)} · ${esc(s.name)}</b><span>${esc(s.model)} · p50 ${s.latency_ms_p50} ms · ${s.avg_out_tokens} tok</span></div>
        <div class="ci-track"><div class="ci-band" style="left:${x(s.pass_ci95[0])}%;width:${x(s.pass_ci95[1]) - x(s.pass_ci95[0])}%"></div><div class="ci-dot" style="left:${x(s.pass_rate)}%"></div></div>
        <div class="ci-val"><b>${pc(s.pass_rate, 1)}</b><br>[${pc(s.pass_ci95[0])}–${pc(s.pass_ci95[1])}]</div>
      </div>`).join("");
    return `<h3 style="margin-top:0">${label}</h3>${rows}<div class="ci-axis"><span>40%</span><span>55%</span><span>70%</span><span>85%</span><span>100%</span></div>`;
  }).join('<div style="height:18px"></div>');
}

function heatColor(v) {
  if (v >= 0.95) return "var(--good-soft)";
  if (v >= 0.8) return "color-mix(in srgb, var(--good-soft) 50%, var(--warn-soft))";
  if (v >= 0.6) return "var(--warn-soft)";
  return "var(--bad-soft)";
}

function renderHeat() {
  const run = R.runs.dev, vs = Object.keys(run.summary);
  const intents = Object.keys(run.summary[vs[0]].by_intent);
  $("#heat").innerHTML = `<tr><th>Risk area</th>${vs.map((v) => `<th>${esc(v)}</th>`).join("")}</tr>` +
    intents.map((i) => `<tr><td>${esc(INTENT_LABEL[i] || i)}</td>${vs.map((v) => {
      const val = run.summary[v].by_intent[i];
      return `<td class="num" style="background:${heatColor(val)}">${pc(val)}</td>`;
    }).join("")}</tr>`).join("");
}

function renderFM() {
  const run = R.runs.dev, vs = Object.keys(run.summary);
  const fms = Object.keys(run.summary[vs[0]].failure_modes).filter((f) => vs.some((v) => run.summary[v].failure_modes[f] > 0));
  $("#fm").innerHTML = `<tr><th>Failure mode</th>${vs.map((v) => `<th>${esc(v)}</th>`).join("")}</tr>` +
    fms.map((f) => `<tr><td>${esc(FM_LABEL[f] || f)}</td>${vs.map((v) => {
      const n = run.summary[v].failure_modes[f];
      return `<td class="num" style="color:${n ? "var(--text)" : "var(--faint)"}">${n}</td>`;
    }).join("")}</tr>`).join("");
}

function renderEoE() {
  const dev = R.runs.dev, c = dev.comparison, cal = R.calibration;
  const flaky = Object.values(dev.summary).map((s) => s.flaky_cases);
  renderTiles("#eoe-tiles", [
    { v: `${c.pairwise.position_inconsistent}/${dev.meta.n_cases}`, l: "Pairwise verdicts that flip when A/B order is swapped", d: "So every pairwise call is run in both orders" },
    { v: pc(c.judge_vs_code_agreement), l: "LLM judge agrees with exact code checks", d: "On rule-checkable format cases" },
    { v: `${Math.min(...flaky)}–${Math.max(...flaky)}`, l: "Flaky cases per variant (pass/fail varies across samples)", d: "Why one sample per case misleads" },
    cal ? { v: pc(cal.agreement), l: `Judge vs human agreement (n=${cal.n_labelled})`, d: `Cohen's κ = ${cal.cohens_kappa}` }
        : { v: "Pending", l: "Judge vs human labels", d: "30 blind labels: run calibrate.py" },
  ]);
  $("#eoe-notes").innerHTML = S.eval_of_eval.map((n) => `<div class="note"><b>${n.title}</b><span>${n.body}</span></div>`).join("");
}

function setupCases() {
  const sets = Object.keys(R.runs);
  $("#f-set").innerHTML = sets.map((k) => `<option value="${k}">${k === "dev" ? "Dev set" : "Held-out set"}</option>`).join("");
  Object.entries(INTENT_LABEL).forEach(([k, v]) => $("#f-intent").insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v)}</option>`));
  ["#f-set", "#f-intent", "#f-outcome", "#f-q"].forEach((s) => $(s).addEventListener("input", renderCases));
  $("#case-dlg").addEventListener("click", (e) => { if (e.target.id === "case-dlg") e.target.close(); });
  renderCases();
}

function renderCases() {
  const run = R.runs[$("#f-set").value], intent = $("#f-intent").value, out = $("#f-outcome").value, q = $("#f-q").value.toLowerCase();
  const list = run.cases.filter((c) => {
    const prs = Object.values(c.variants).map((v) => v.pass_rate);
    if (intent && c.intent !== intent) return false;
    if (out === "changed" && new Set(prs).size < 2) return false;
    if (out === "anyfail" && !prs.some((p) => p < 1)) return false;
    if (out === "flaky" && !prs.some((p) => p > 0 && p < 1)) return false;
    if (q && !JSON.stringify(c).toLowerCase().includes(q)) return false;
    return true;
  });
  $("#case-list").innerHTML = list.map((c) => `
    <div class="case" data-id="${esc(c.id)}">
      <div style="min-width:0"><div class="id">${esc(c.id)}</div><div class="q">${esc(c.messages[c.messages.length - 1].content)}</div></div>
      <div class="badges">${Object.entries(c.variants).map(([v, d]) => `<span class="pill ${d.pass_rate === 1 ? "good" : d.pass_rate === 0 ? "bad" : "warn"}">${esc(v)} ${Math.round(d.pass_rate * 3)}/3</span>`).join("")}</div>
    </div>`).join("") || `<p class="sub">No cases match.</p>`;
  document.querySelectorAll(".case").forEach((el) => el.addEventListener("click", () => openCase(run, el.dataset.id)));
}

function openCase(run, id) {
  const c = run.cases.find((x) => x.id === id);
  const convo = c.messages.map((m) => `<div class="msg ${m.role}"><span class="r">${m.role}</span>${esc(m.content)}</div>`).join("");
  const pw = c.pairwise && c.pairwise.bc ? `<p class="rat"><b>Pairwise ${esc(run.meta.challenger)} vs ${esc(run.meta.baseline)}:</b> order 1 → ${esc(c.pairwise.bc.winner)}, order 2 → ${esc(c.pairwise.cb.winner)}${c.pairwise.bc.winner !== c.pairwise.cb.winner ? ' <span class="pill warn">position-inconsistent</span>' : ""}<br>${esc(c.pairwise.bc.reason)}</p>` : "";
  const cols = Object.entries(c.variants).map(([v, d]) => `
    <div class="vcol" data-v="${esc(v)}">
      <h5><span>${esc(v)} · ${esc(run.summary[v].name)}</span><span class="pill ${d.pass_rate === 1 ? "good" : d.pass_rate === 0 ? "bad" : "warn"}">${Math.round(d.pass_rate * d.samples.length)}/${d.samples.length} pass</span></h5>
      <div class="tabs">${d.samples.map((s, i) => `<button class="${i === 0 ? "on" : ""}" data-i="${i}">#${i + 1} ${s.grade.pass ? "✓" : "✗"}</button>`).join("")}</div>
      <div class="sample-body"></div>
    </div>`).join("");
  $("#case-dlg-body").innerHTML = `
    <div class="dlg-head"><div><div class="id mono">${esc(c.id)}</div><b>${esc(INTENT_LABEL[c.intent] || c.intent)}</b></div><button class="btn" onclick="document.getElementById('case-dlg').close()">Close</button></div>
    <div class="dlg-body">${convo}<div class="expect"><b>Rater expectation:</b> ${esc(c.expectation)}${c.code_check ? `<br><b>Code check:</b> ${esc(c.code_check)}` : ""}</div>${pw}<div class="vgrid">${cols}</div></div>`;
  document.querySelectorAll("#case-dlg .vcol").forEach((col) => {
    const d = c.variants[col.dataset.v];
    const show = (i) => {
      const s = d.samples[i], g = s.grade;
      col.querySelector(".sample-body").innerHTML = `<div class="resp">${esc(s.response)}</div>
        <p class="rat">${g.failure_modes.map((f) => `<span class="pill bad">${esc(FM_LABEL[f] || f)}</span>`).join(" ")} ${s.code_check_pass === null ? "" : `<span class="pill ${s.code_check_pass ? "good" : "bad"}">code check ${s.code_check_pass ? "✓" : "✗"}</span>`}</p>
        <p class="rat"><b>Judge:</b> ${esc(g.rationale)}</p>
        <p class="rat mono">${Object.entries(g.scores).map(([k, v]) => `${k.slice(0, 11)} ${v}`).join(" · ")} · ${s.latency_ms} ms</p>`;
      col.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", +b.dataset.i === i));
    };
    col.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => show(+b.dataset.i)));
    show(0);
  });
  $("#case-dlg").showModal();
}

function renderMethod() {
  const dev = R.runs.dev;
  const prompts = Object.entries(dev.summary).filter(([v]) => v !== "v4").map(([v, s]) =>
    `<details><summary><b>${esc(v)}</b> · ${esc(s.name)}</summary><pre class="prompt">${esc(s.system_prompt)}</pre></details>`).join("");
  $("#method-body").innerHTML = S.method.map((m) => `<div class="card"><h3>${m.title}</h3>${m.html}</div>`).join("") +
    `<div class="card"><h3>System prompts under test</h3>${prompts}<p class="rat">v4 reuses the v3 prompt on <code>gpt-4.1</code>.</p></div>`;
}

main().catch((e) => { document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="sub">Could not load data: ${esc(e.message)}</p>`); });
