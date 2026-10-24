// Secrets set with `wrangler secret put` are not in wrangler.jsonc, so `wrangler types` can't see them.
interface Env {
  TURNSTILE_SECRET_KEY?: string;
}
