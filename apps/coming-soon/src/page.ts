import { sites, type Site } from "@datasites/registry";

/** One accent hue per site so the placeholders don't all look identical. */
const ACCENT: Record<string, string> = {
  airdata: "#2f7fd1",
  carbondata: "#3b8a5a",
  climatedata: "#c4532d",
  economicdata: "#6a4fc4",
  electiondata: "#b23a6b",
  healthdata: "#c0577f",
  housingdata: "#6b7a1f",
  justicedata: "#4a5a8c",
  satellitedata: "#2b6f8f",
  visualizedata: "#b5761f",
  waterdata: "#1f8a9a",
  wildfiredata: "#c2362b",
};

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
}

const STYLE = `
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#5d5b55;--line:#dedbd2;--card:#ffffff}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a19a;--line:#33322f;--card:#1c1c1a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:100vh;display:flex;flex-direction:column}
main{flex:1;width:100%;max-width:640px;margin:0 auto;padding:12vh 20px 48px}
.eyebrow{font:600 13px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.04em;color:var(--accent);margin:0 0 18px}
h1{font-size:clamp(32px,6vw,44px);line-height:1.1;margin:0 0 14px;letter-spacing:-.02em}
.tagline{font-size:20px;margin:0 0 18px}
.lede{color:var(--muted);margin:0 0 32px}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
label{display:block;font-weight:600;margin-bottom:10px}
.row{display:flex;gap:8px;flex-wrap:wrap}
input[type=email]{flex:1 1 220px;min-width:0;padding:11px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
button{padding:11px 18px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:600 15px/1 inherit;cursor:pointer}
button:hover{filter:brightness(1.08)}
.cf-turnstile{margin-top:14px}
.hp{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}
.fine{font-size:14px;color:var(--muted);margin-top:14px}
.notice{border-left:3px solid var(--accent);padding:4px 0 4px 14px;margin:0 0 24px}
.error{border-left-color:#c2362b}
a{color:var(--accent)}
footer{border-top:1px solid var(--line);padding:20px;font-size:13px;color:var(--muted);text-align:center}
footer a{color:var(--muted);margin:0 6px;white-space:nowrap}
`;

function layout(site: Site, title: string, body: string, head = ""): string {
  const accent = ACCENT[site.id] ?? "#c4532d";
  const siblings = sites
    .filter((s) => s.id !== site.id)
    .map((s) => `<a href="https://${s.domain}/">${escapeHtml(s.domain)}</a>`)
    .join(" ");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<meta name="description" content="${escapeHtml(site.tagline)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>:root{--accent:${accent}}${STYLE}</style>
${head}
</head>
<body>
<main>
${body}
</main>
<footer>Part of a family of open data sites: ${siblings}</footer>
</body>
</html>`;
}

export interface PageOptions {
  turnstileSiteKey: string;
  /** "?site=climatedata" in local development, "" in production. */
  query: string;
  notice?: { kind: "ok" | "error"; text: string };
  email?: string;
}

export function comingSoonPage(site: Site, opts: PageOptions): string {
  const notice = opts.notice
    ? `<p class="notice${opts.notice.kind === "error" ? " error" : ""}" role="status">${escapeHtml(opts.notice.text)}</p>`
    : "";
  const form =
    opts.notice?.kind === "ok"
      ? ""
      : `<form method="post" action="/signup${opts.query}">
  <label for="email">Get one email when it launches</label>
  <div class="row">
    <input id="email" name="email" type="email" required maxlength="254" autocomplete="email" placeholder="you@example.com" value="${escapeHtml(opts.email ?? "")}">
    <button type="submit">Notify me</button>
  </div>
  <input class="hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
  <div class="cf-turnstile" data-sitekey="${escapeHtml(opts.turnstileSiteKey)}"></div>
</form>
<p class="fine">One email at launch, nothing else, never shared. <a href="/privacy${opts.query}">Privacy</a></p>`;
  const body = `<p class="eyebrow">${escapeHtml(site.domain)}</p>
<h1>${escapeHtml(site.name)} is coming soon</h1>
<p class="tagline">${escapeHtml(site.tagline)}</p>
<p class="lede">${escapeHtml(site.description)}</p>
${notice}
${form}`;
  // No integrity hash: Cloudflare updates api.js in place and says not to pin or self-host it.
  const head = opts.notice?.kind === "ok" ? "" : `<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>`;
  return layout(site, `${site.name} (${site.domain}): coming soon`, body, head);
}

export function privacyPage(site: Site, query: string): string {
  const body = `<p class="eyebrow">${escapeHtml(site.domain)}</p>
<h1>Privacy</h1>
<p>If you sign up for a launch email, we store your email address, which site you signed up on, and when. We use it to send one email when the site launches, and we never sell or share it.</p>
<p>The signup form uses Cloudflare Turnstile to keep bots out. Turnstile checks your browser without tracking you across sites.</p>
<p>We don't use analytics cookies or advertising trackers on this page.</p>
<p>To have your address removed, email <a href="mailto:hello@${escapeHtml(site.domain)}">hello@${escapeHtml(site.domain)}</a>.</p>
<p><a href="/${query}">Back</a></p>`;
  return layout(site, `Privacy: ${site.domain}`, body);
}

export function favicon(siteId: string): string {
  const accent = ACCENT[siteId] ?? "#c4532d";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="${accent}"/><path d="M8 22V14M14 22V9M20 22V17M26 22V12" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`;
}
