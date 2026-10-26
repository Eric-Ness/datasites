// Redirects for hostnames that serve no site of their own:
// - votingdata.net and www.votingdata.net go to the same path and query on electiondata.dev (302: the domain
//   may become its own site later, and browsers cache a 301 almost forever);
// - www.<site>.dev goes to the same path and query on <site>.dev (301: the bare domain is the canonical one).

export const VOTINGDATA_TARGET = "https://electiondata.dev";

/** Where a request should go, and with which status; null for a host this Worker doesn't handle. */
export function redirectFor(url: URL): { location: string; status: 301 | 302 } | null {
  const host = url.hostname.toLowerCase();
  const rest = `${url.pathname}${url.search}`;
  if (host === "votingdata.net" || host === "www.votingdata.net") return { location: `${VOTINGDATA_TARGET}${rest}`, status: 302 };
  const www = /^www\.([a-z0-9-]+\.dev)$/.exec(host);
  if (www) return { location: `https://${www[1]}${rest}`, status: 301 };
  return null;
}

export default {
  fetch(request: Request): Response {
    const target = redirectFor(new URL(request.url));
    if (!target) return new Response("Not found", { status: 404 });
    return new Response(null, {
      status: target.status,
      headers: { location: target.location, "cache-control": "public, max-age=3600" },
    });
  },
};
