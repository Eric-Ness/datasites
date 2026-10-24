// Secrets set with `wrangler secret put` are not in wrangler.jsonc, so `wrangler types` can't see them.
interface Env {
  /** healthchecks.io ping URL for the freshness check. Optional. */
  HEALTHCHECK_URL?: string;
}
