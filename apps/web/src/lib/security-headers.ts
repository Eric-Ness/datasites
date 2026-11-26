// The security headers for a built site, written to dist/<id>/_headers after each build (astro.config.ts), which
// Cloudflare applies to every file the site's Worker serves. The Content-Security-Policy allows the build's own
// inline scripts by their SHA-256 hash rather than 'unsafe-inline', so a script someone manages to inject has a
// different hash and doesn't run. Inline scripts must be the same on every page for the list to stay short: put
// per-page values in data attributes, not in the script text.
import { createHash } from "node:crypto";

/** Far more distinct inline scripts than this means a per-page value leaked into one. */
export const MAX_INLINE_SCRIPTS = 20;

const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;
const DATA_TYPES = /\btype\s*=\s*["']?(application\/(ld\+)?json|text\/template|text\/plain)/i;

/** The CSP hash source of every inline script the browser would run in these pages, sorted and unique. */
export function inlineScriptHashes(pages: Iterable<string>): string[] {
  const hashes = new Set<string>();
  for (const html of pages) {
    for (const [, attrs = "", body = ""] of html.matchAll(SCRIPT)) {
      if (/\bsrc\s*=/i.test(attrs) || DATA_TYPES.test(attrs)) continue; // external, or data the browser never runs
      // Browsers hash the script text after the HTML parser has turned CRLF and CR into LF.
      const text = body.replace(/\r\n?/g, "\n");
      hashes.add(`'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`);
    }
  }
  return [...hashes].sort();
}

/** The Content-Security-Policy for a site whose pages fetch data from apiBase. */
export function contentSecurityPolicy(scriptHashes: readonly string[], apiBase: string): string {
  const api = new URL(apiBase).origin;
  return [
    "default-src 'self'",
    ["script-src 'self'", ...scriptHashes].join(" "),
    // The charts size and place things with style attributes, which only 'unsafe-inline' allows.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ${api}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    // Only on https: on a local http API it would turn the pages' own requests into failing https ones.
    ...(api.startsWith("https:") ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}

/** Where to report a security problem (RFC 9116). */
export const SECURITY_CONTACT = "mailto:eric.ness@nulllabs.com";

/**
 * /.well-known/security.txt for a domain. RFC 9116 asks for an expiry under a year away, so it is one year after
 * `now` (the build): every deploy renews it, and a site not rebuilt for a year shows a lapsed file.
 */
export function securityTxt(domain: string, now: Date): string {
  const expires = new Date(Date.UTC(now.getUTCFullYear() + 1, now.getUTCMonth(), now.getUTCDate()));
  return [
    `Contact: ${SECURITY_CONTACT}`,
    `Expires: ${expires.toISOString().replace(".000", "")}`,
    "Preferred-Languages: en",
    `Canonical: https://${domain}/.well-known/security.txt`,
    "",
  ].join("\n");
}

/** The whole _headers file. */
export function headersFile(scriptHashes: readonly string[], apiBase: string): string {
  const headers: Record<string, string> = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": contentSecurityPolicy(scriptHashes, apiBase),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
  };
  const lines = Object.entries(headers).map(([name, value]) => `  ${name}: ${value}`);
  return ["# Written by the build (src/lib/security-headers.ts). Do not edit.", "/*", ...lines, ""].join("\n");
}
