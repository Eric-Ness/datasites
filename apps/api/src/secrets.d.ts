// Secrets set with `wrangler secret put` are not in wrangler.jsonc, so `wrangler types` can't see them.
interface Env {
  /** healthchecks.io ping URL for the freshness check. Optional. */
  HEALTHCHECK_URL?: string;
  /** Stripe secret key (sk_live_... or rk_live_... with meter-event write access). Without it, no usage is reported. */
  STRIPE_SECRET_KEY?: string;
  /** Signing secret of the Stripe webhook endpoint (whsec_...). Without it, POST /billing/webhook answers 404. */
  STRIPE_WEBHOOK_SECRET?: string;
}
