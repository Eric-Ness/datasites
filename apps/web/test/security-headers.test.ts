import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { contentSecurityPolicy, headersFile, inlineScriptHashes, securityTxt } from "../src/lib/security-headers";

const sha = (text: string) => `'sha256-${createHash("sha256").update(text).digest("base64")}'`;

describe("inlineScriptHashes", () => {
  it("hashes inline scripts once each and skips external and data scripts", () => {
    const page = [
      "<script>a()</script>",
      '<script type="module" src="/_astro/x.js"></script>',
      '<script type="application/ld+json">{"@type":"Dataset"}</script>',
      "<script type=module>b()</script>",
    ].join("");
    expect(inlineScriptHashes([page, page])).toEqual([sha("a()"), sha("b()")].sort());
  });

  it("hashes the text as the browser sees it, with CRLF turned into LF", () => {
    expect(inlineScriptHashes(["<script>a()\r\nb()</script>"])).toEqual([sha("a()\nb()")]);
  });
});

describe("contentSecurityPolicy", () => {
  it("allows only this site, its API and the hashed inline scripts", () => {
    const csp = contentSecurityPolicy(["'sha256-x'"], "https://api.airdata.dev");
    expect(csp).toContain("script-src 'self' 'sha256-x' https://static.cloudflareinsights.com;");
    expect(csp).toContain("connect-src 'self' https://api.airdata.dev https://cloudflareinsights.com;");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp.split("; ").find((d) => d.startsWith("script-src"))).not.toContain("unsafe");
    expect(csp).toMatch(/upgrade-insecure-requests$/);
  });

  it("leaves out upgrade-insecure-requests for a local http API", () => {
    const csp = contentSecurityPolicy([], "http://localhost:8787");
    expect(csp).toContain("connect-src 'self' http://localhost:8787 https://cloudflareinsights.com;");
    expect(csp).not.toContain("upgrade-insecure-requests");
  });
});

describe("headersFile", () => {
  it("applies every header to every path", () => {
    const text = headersFile([], "https://api.airdata.dev");
    expect(text.split("\n")[1]).toBe("/*");
    for (const name of ["Strict-Transport-Security", "Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Cross-Origin-Resource-Policy"]) {
      expect(text).toContain(`\n  ${name}: `);
    }
  });
});

describe("securityTxt", () => {
  it("names the contact and expires a year after the build", () => {
    const text = securityTxt("airdata.dev", new Date("2026-11-26T17:00:00Z"));
    expect(text).toBe(
      [
        "Contact: mailto:eric.ness@nulllabs.com",
        "Expires: 2027-11-26T00:00:00Z",
        "Preferred-Languages: en",
        "Canonical: https://airdata.dev/.well-known/security.txt",
        "",
      ].join("\n"),
    );
  });
});
