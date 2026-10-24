import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, describeFailure, getJson, hostOf } from "../src/client/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function respond(status: number, body: unknown, type = "application/json") {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "content-type": type } });
}

describe("getJson", () => {
  it("returns parsed JSON and shares one request per URL", async () => {
    const fetch = vi.fn(async () => respond(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetch);
    const url = "https://api.example.dev/v1/one";
    expect(await getJson(url)).toEqual({ ok: 1 });
    expect(await getJson(url)).toEqual({ ok: 1 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("turns problem+json into an ApiError with the slug", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(
          404,
          { type: "https://climatedata.dev/docs/errors#unknown-dataset", title: "Unknown dataset", status: 404, detail: 'There is no dataset "x".' },
          "application/problem+json",
        ),
      ),
    );
    const err = await getJson("https://api.example.dev/v1/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("http");
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).slug).toBe("unknown-dataset");
    expect(describeFailure(err)).toMatch(/hasn't been published yet/);
  });

  it("reports network failures and forgets them so a retry refetches", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", fetch);
    const url = "https://api.example.dev/v1/down";
    const err = await getJson(url).catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("network");
    expect(describeFailure(err, "api.example.dev")).toBe(
      "Couldn't reach the API (api.example.dev). It may be down, or your connection may be offline.",
    );
    await getJson(url).catch(() => undefined);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("reports timeouts, bad bodies, rate limits and other HTTP errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("The operation timed out.", "TimeoutError");
      }),
    );
    expect(describeFailure(await getJson("https://a.dev/t").catch((e: unknown) => e), "a.dev")).toBe("The API (a.dev) took too long to answer.");

    vi.stubGlobal("fetch", vi.fn(async () => respond(200, "<html>", "text/html")));
    expect((await getJson("https://a.dev/p").catch((e: unknown) => e)) as ApiError).toMatchObject({ kind: "parse" });

    vi.stubGlobal("fetch", vi.fn(async () => respond(429, { title: "Too many requests", status: 429 }, "application/problem+json")));
    expect(describeFailure(await getJson("https://a.dev/r").catch((e: unknown) => e))).toMatch(/Too many requests/);

    vi.stubGlobal("fetch", vi.fn(async () => respond(502, "")));
    expect(describeFailure(await getJson("https://a.dev/b").catch((e: unknown) => e), "a.dev")).toBe("The API (a.dev) answered with HTTP 502.");
    expect(describeFailure(new Error("x"))).toMatch(/Something went wrong/);
  });
});

describe("hostOf", () => {
  it("returns the host of a URL", () => {
    expect(hostOf("http://localhost:8787")).toBe("localhost:8787");
    expect(hostOf("nope")).toBe("nope");
  });
});
