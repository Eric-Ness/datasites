// Sends votingdata.net and www.votingdata.net to the same path and query on electiondata.dev.

export const TARGET = "https://electiondata.dev";

/** The electiondata.dev URL for a request to votingdata.net: same path and query string. */
export function redirectUrl(url: URL): string {
  return `${TARGET}${url.pathname}${url.search}`;
}

export default {
  fetch(request: Request): Response {
    return new Response(null, {
      status: 302,
      headers: { location: redirectUrl(new URL(request.url)), "cache-control": "public, max-age=3600" },
    });
  },
};
