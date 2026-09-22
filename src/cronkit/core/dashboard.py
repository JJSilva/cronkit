"""The dashboard: one page showing every tool, its logs, and a run button.

Served from the daemon itself, so there is nothing else to deploy. The page is a
single self-contained document — no CDN, no build step, no external requests —
which keeps it working on a locked-down network and means the whole UI ships with
the container.

It is guarded by ``CRONKIT_DASHBOARD_PASSWORD``. With that unset the routes still
exist but refuse everything, the same way the API does without a token.
"""

import html

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cronkit</title>
<style>
{css}
.login {{ max-width: 22rem; margin: 15vh auto; }}
.login h1 {{ margin-bottom: 0.25rem; }}
.login p {{ color: var(--dim); margin-top: 0; margin-bottom: 1.5rem; }}
.login input {{ width: 100%; box-sizing: border-box; margin-bottom: 0.75rem; }}
.login button {{ width: 100%; }}
.error {{ color: var(--bad); font-size: 0.875rem; margin-bottom: 0.75rem; }}
</style>
</head>
<body>
<main class="login">
  <h1>cronkit</h1>
  <p>Scheduled tools</p>
  <form method="post" action="/login">
    {error}
    <input type="password" name="password" placeholder="Password" autofocus required
           autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
</main>
</body>
</html>
"""

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --panel: #ffffff; --line: #e4e4e1; --ink: #1c1c1a;
  --dim: #6b6b66; --accent: #2f6f4f; --bad: #b3261e; --warn: #9a6700;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a; --panel: #1f2023; --line: #303236; --ink: #e8e8e6;
    --dim: #9a9a95; --accent: #6fbf8f; --bad: #f2837b; --warn: #e0b341;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
}
h1 { font-size: 1.35rem; margin: 0; letter-spacing: -0.01em; }
button, input {
  font: inherit; border-radius: 7px; border: 1px solid var(--line);
  padding: 0.5rem 0.85rem; background: var(--panel); color: var(--ink);
}
button { cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--dim); }
button:disabled { opacity: 0.5; cursor: default; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cronkit</title>
<style>
{css}
.wrap {{ max-width: 60rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }}
header {{ display: flex; align-items: baseline; gap: 0.75rem; margin-bottom: 1.5rem; }}
header .sub {{ color: var(--dim); font-size: 0.875rem; }}
header form {{ margin-left: auto; }}
.tool {{
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.1rem; margin-bottom: 0.85rem;
}}
.tool-head {{ display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }}
.tool-name {{ font-weight: 600; }}
.tool-head .actions {{ margin-left: auto; display: flex; gap: 0.4rem; }}
.pill {{
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 0.15rem 0.5rem; border-radius: 999px; border: 1px solid var(--line);
  color: var(--dim);
}}
.pill.scheduled {{ color: var(--accent); border-color: var(--accent); }}
.pill.unconfigured {{ color: var(--warn); border-color: var(--warn); }}
.pill.failing {{ color: var(--bad); border-color: var(--bad); }}
.summary {{ color: var(--dim); font-size: 0.875rem; margin-top: 0.5rem; }}
.summary code {{ font-family: var(--mono); font-size: 0.82rem; }}
.meta {{
  display: flex; gap: 1.25rem; flex-wrap: wrap;
  font-size: 0.8rem; color: var(--dim); margin-top: 0.6rem;
}}
.logs {{
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  margin-top: 1.75rem; overflow: hidden;
}}
.logs-head {{
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.7rem 1.1rem; border-bottom: 1px solid var(--line);
}}
.logs-head strong {{ font-size: 0.9rem; }}
.logs-head .actions {{ margin-left: auto; display: flex; gap: 0.5rem; align-items: center; }}
.logs-head select {{ font: inherit; font-size: 0.85rem; }}
pre#log {{
  margin: 0; padding: 0.85rem 1.1rem; max-height: 26rem; overflow: auto;
  font-family: var(--mono); font-size: 0.78rem; line-height: 1.55; white-space: pre-wrap;
  word-break: break-word;
}}
#log .t {{ color: var(--dim); }}
#log .ERROR, #log .CRITICAL {{ color: var(--bad); }}
#log .WARNING {{ color: var(--warn); }}
.empty {{ color: var(--dim); font-style: italic; }}
@media (max-width: 34rem) {{ .tool-head .actions {{ width: 100%; margin-left: 0; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>cronkit</h1>
    <span class="sub" id="sub"></span>
    <form method="post" action="/logout"><button type="submit">Sign out</button></form>
  </header>

  <div id="tools"></div>

  <section class="logs">
    <div class="logs-head">
      <strong>Logs</strong>
      <div class="actions">
        <select id="level">
          <option value="">All</option>
          <option value="INFO">Info</option>
          <option value="WARNING">Warnings</option>
          <option value="ERROR">Errors</option>
        </select>
        <label style="font-size:.85rem;color:var(--dim)">
          <input type="checkbox" id="follow" checked style="vertical-align:-2px"> Follow
        </label>
      </div>
    </div>
    <pre id="log"></pre>
  </section>
</div>

<script>
const CSRF = "{csrf}";
const $ = (id) => document.getElementById(id);

function ago(iso) {{
  if (!iso) return "never";
  const secs = Math.round((Date.now() - new Date(iso)) / 1000);
  const abs = Math.abs(secs);
  const [n, unit] = abs < 60 ? [abs, "sec"] : abs < 3600 ? [Math.round(abs/60), "min"]
                  : abs < 86400 ? [Math.round(abs/3600), "hr"] : [Math.round(abs/86400), "day"];
  const plural = n === 1 ? "" : "s";
  return secs >= 0 ? `${{n}} ${{unit}}${{plural}} ago` : `in ${{n}} ${{unit}}${{plural}}`;
}}

function pill(tool) {{
  if (tool.state === "unconfigured") return ["unconfigured", "unconfigured"];
  if (tool.last_error || (tool.last_result && !tool.last_result.ok)) return ["failing", "failing"];
  return [tool.state, tool.state];
}}

function render(data) {{
  $("sub").textContent = `${{data.tools.length}} tool${{data.tools.length === 1 ? "" : "s"}}`;
  $("tools").innerHTML = data.tools.map((t) => {{
    const [cls, label] = pill(t);
    const result = t.last_result;
    const detail = t.load_error ? `<div class="summary">${{esc(t.load_error)}}</div>`
      : result ? `<div class="summary"><code>${{esc(result.summary)}}</code></div>`
      : t.last_error ? `<div class="summary">${{esc(t.last_error)}}</div>`
      : `<div class="summary empty">Not run yet</div>`;
    return `<article class="tool">
      <div class="tool-head">
        <span class="tool-name">${{esc(t.name)}}</span>
        <span class="pill ${{cls}}">${{esc(label)}}</span>
        <span class="actions">
          <button data-run="${{esc(t.name)}}" data-dry="1" ${{t.state === "unconfigured" ? "disabled" : ""}}>Dry run</button>
          <button class="primary" data-run="${{esc(t.name)}}" ${{t.state === "unconfigured" ? "disabled" : ""}}>Run now</button>
        </span>
      </div>
      <div class="summary">${{esc(t.summary)}}</div>
      ${{detail}}
      <div class="meta">
        <span>${{t.schedule ? esc(t.schedule.label) : "no schedule"}}</span>
        <span>last: ${{ago(t.last_run_at)}}</span>
        <span>next: ${{ago(t.next_run_at)}}</span>
        <span>${{t.runs}} run${{t.runs === 1 ? "" : "s"}}, ${{t.failures}} failed</span>
      </div>
    </article>`;
  }}).join("");
}}

function esc(s) {{
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }})[c]);
}}

async function refresh() {{
  const [status, logs] = await Promise.all([
    fetch("/status").then((r) => r.json()),
    fetch("/api/logs?limit=400&level=" + encodeURIComponent($("level").value)).then((r) => r.json()),
  ]);
  render(status);
  const pre = $("log");
  const stuck = $("follow").checked;
  pre.innerHTML = logs.records.map((r) =>
    `<span class="t">${{esc(r.at.slice(11, 19))}}</span> <span class="${{esc(r.level)}}">${{esc(r.level.padEnd(7))}}</span> ${{esc(r.message)}}`
  ).join("\\n") || '<span class="empty">No log records yet.</span>';
  if (stuck) pre.scrollTop = pre.scrollHeight;
}}

document.addEventListener("click", async (event) => {{
  const button = event.target.closest("[data-run]");
  if (!button) return;
  const dry = button.dataset.dry === "1";
  const label = button.textContent;
  button.disabled = true;
  button.textContent = dry ? "Checking…" : "Running…";
  try {{
    await fetch(`/tools/${{encodeURIComponent(button.dataset.run)}}/run${{dry ? "?dry_run=1" : ""}}`, {{
      method: "POST", headers: {{ "X-CSRF-Token": CSRF }},
    }});
  }} finally {{
    button.disabled = false;
    button.textContent = label;
    refresh();
  }}
}});

$("level").addEventListener("change", refresh);
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def login_page(error: str | None = None) -> str:
    banner = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return LOGIN_PAGE.format(css=CSS, error=banner)


def dashboard_page(csrf: str) -> str:
    return PAGE.format(css=CSS, csrf=html.escape(csrf, quote=True))
