import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { escapeHtml } from "../src/page";

interface Row {
  sql: string;
  args: unknown[];
}

function fakeDb(rows: Row[]) {
  return {
    prepare(sql: string) {
      return {
        bind(...args: unknown[]) {
          return {
            async run() {
              rows.push({ sql, args });
              return { success: true };
            },
          };
        },
      };
    },
  } as unknown as D1Database;
}

function env(rows: Row[], overrides: Partial<Env> = {}): Env {
  return {
    DB: fakeDb(rows),
    ENV: "production",
    TURNSTILE_SITE_KEY: "test-site-key",
    TURNSTILE_SECRET_KEY: "test-secret",
    ...overrides,
  } as Env;
}

function call(url: string, init: RequestInit | undefined, e: Env) {
  return worker.fetch(new Request(url, init) as Request<unknown, IncomingRequestCfProperties>, e);
}

function signupRequest(fields: Record<string, string>, host = "https://climatedata.dev") {
  return [`${host}/signup`, { method: "POST", body: new URLSearchParams(fields) }] as const;
}

afterEach(() => vi.unstubAllGlobals());

describe("coming-soon pages", () => {
  it("renders the page for the requested domain", async () => {
    const res = await call("https://waterdata.dev/", undefined, env([]));
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("Water Data is coming soon");
    expect(body).toContain('data-sitekey="test-site-key"');
    expect(body).toContain('action="/signup"');
    expect(body).toContain('href="https://climatedata.dev/"'); // sibling links
    expect(body).not.toContain('href="https://waterdata.dev/"');
  });

  it("uses ?site= on localhost and keeps it in links", async () => {
    const res = await call("http://localhost:8788/?site=electiondata", undefined, env([]));
    const body = await res.text();
    expect(body).toContain("Election Data is coming soon");
    expect(body).toContain('action="/signup?site=electiondata"');
  });

  it("honors ?site= in local development even though wrangler dev rewrites the host", async () => {
    const res = await call("https://airdata.dev/?site=electiondata", undefined, env([], { ENV: "development" }));
    const body = await res.text();
    expect(body).toContain("Election Data is coming soon");
    expect(body).toContain('action="/signup?site=electiondata"');
  });

  it("ignores ?site= in production", async () => {
    const res = await call("https://airdata.dev/?site=electiondata", undefined, env([]));
    expect(await res.text()).toContain("Air Data is coming soon");
  });

  it("returns 404 for unknown hosts", async () => {
    expect((await call("https://example.com/", undefined, env([]))).status).toBe(404);
  });

  it("serves privacy, favicon and robots", async () => {
    expect(await (await call("https://airdata.dev/privacy", undefined, env([]))).text()).toContain("hello@airdata.dev");
    expect((await call("https://airdata.dev/favicon.svg", undefined, env([]))).headers.get("content-type")).toBe("image/svg+xml");
    expect(await (await call("https://airdata.dev/robots.txt", undefined, env([]))).text()).toContain("Allow: /");
  });

  it("redirects other paths to the home page", async () => {
    const res = await call("https://airdata.dev/anything", undefined, env([]));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://airdata.dev/");
  });
});

describe("signup", () => {
  it("stores a verified signup once per email and site", async () => {
    const siteverify = vi.fn(async () => Response.json({ success: true }));
    vi.stubGlobal("fetch", siteverify);
    const rows: Row[] = [];
    const res = await call(...signupRequest({ email: "  Ada@Example.com ", "cf-turnstile-response": "tok" }), env(rows));
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("ada@example.com");
    expect(siteverify).toHaveBeenCalledOnce();
    expect(rows).toHaveLength(1);
    expect(rows[0]!.sql).toContain("ON CONFLICT (email, site) DO NOTHING");
    expect(rows[0]!.args.slice(0, 2)).toEqual(["ada@example.com", "climatedata"]);
  });

  it("rejects invalid email addresses without calling Turnstile", async () => {
    const siteverify = vi.fn();
    vi.stubGlobal("fetch", siteverify);
    const rows: Row[] = [];
    const res = await call(...signupRequest({ email: "not-an-email" }), env(rows));
    expect(res.status).toBe(400);
    expect(siteverify).not.toHaveBeenCalled();
    expect(rows).toHaveLength(0);
  });

  it("rejects a failed Turnstile check", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ success: false })));
    const rows: Row[] = [];
    const res = await call(...signupRequest({ email: "a@b.co", "cf-turnstile-response": "bad" }), env(rows));
    expect(res.status).toBe(400);
    expect(rows).toHaveLength(0);
  });

  it("quietly drops honeypot submissions", async () => {
    const rows: Row[] = [];
    const res = await call(...signupRequest({ email: "bot@spam.co", website: "http://spam" }), env(rows));
    expect(res.status).toBe(200);
    expect(rows).toHaveLength(0);
  });

  it("refuses to skip Turnstile in production when the secret is missing", async () => {
    const res = call(...signupRequest({ email: "a@b.co" }), env([], { TURNSTILE_SECRET_KEY: undefined }));
    await expect(res).rejects.toThrow("TURNSTILE_SECRET_KEY");
  });

  it("allows skipping Turnstile in development", async () => {
    const rows: Row[] = [];
    const res = await call(
      ...signupRequest({ email: "dev@example.com" }, "http://localhost:8788"),
      env(rows, { TURNSTILE_SECRET_KEY: undefined, ENV: "development" }),
    );
    // localhost without ?site= has no site
    expect(res.status).toBe(404);
    const ok = await call(
      "http://localhost:8788/signup?site=airdata",
      { method: "POST", body: new URLSearchParams({ email: "dev@example.com" }) },
      env(rows, { TURNSTILE_SECRET_KEY: undefined, ENV: "development" }),
    );
    expect(ok.status).toBe(200);
    expect(rows).toHaveLength(1);
  });
});

describe("escapeHtml", () => {
  it("escapes markup characters", () => {
    expect(escapeHtml(`<a href="x">'&'</a>`)).toBe("&#60;a href=&#34;x&#34;&#62;&#39;&#38;&#39;&#60;/a&#62;");
  });
});
