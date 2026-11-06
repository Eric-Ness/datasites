# Setup: the steps that need your accounts

Everything in the repo builds and runs locally without these steps. They're what it takes to put the sites on the internet. Work through them in order. Each step says how to check it worked.

Commands assume PowerShell in the repo root.

---

## 1. Move the domains to Cloudflare DNS

Do this for each domain once DirectNIC shows it in your account. The nine .dev names were still processing on 2026-10-24.

1. **Cloudflare dashboard → Add a domain.** Enter the domain and choose the **Free** plan. When asked about DNS records, pick **enter manually**, not import.
2. **Note the two nameservers** Cloudflare assigns.
3. **In DirectNIC,** confirm **DNSSEC is off** for the domain. Then go to **Name Servers → Edit** and replace the DirectNIC nameservers with Cloudflare's two.
4. **Wait for the zone to show Active** in Cloudflare. This takes minutes to 24 hours.

**Check it worked:**
```powershell
Resolve-DnsName climatedata.dev -Type NS -Server 1.1.1.1
```
It should list `*.ns.cloudflare.com`.

**Settings for each zone:**
- Leave **Bot Fight Mode off**, because it blocks API clients.
- Turn on **SSL/TLS → Edge Certificates → Always Use HTTPS**.
- **Email → Email Routing**, to forward `hello@<domain>` to your inbox. Needed for every launched site (section 5).

### votingdata.net redirect
Nothing to do by hand once the zone is Active. The **deploy** workflow deploys the `apps/redirect` Worker on `votingdata.net` and `www.votingdata.net`, and its custom domains create the DNS records. Every request answers **302** to the same path and query string on electiondata.dev. It uses 302, not 301, because browsers cache 301s almost forever and this domain may become its own site later.

### www for the .dev sites
Nothing to do by hand. The same `apps/redirect` Worker answers `www.<domain>` for every launched site with a **301** to `https://<domain>`, keeping the path and query string. `scripts/verify/workflows.mjs` fails if a site in `WEB_SITES` has no `www` route, and the deploy smoke test checks each one.

---

## 2. Cloudflare account setup

1. **Subscribe to Workers Paid** ($5/month) under **Workers & Pages → Plans**.
2. **Log Wrangler in** (opens your browser):
   ```powershell
   pnpm --filter @datasites/api exec wrangler login
   ```
3. **Create the storage buckets:**
   ```powershell
   pnpm --filter @datasites/api exec wrangler r2 bucket create datasites-data
   pnpm --filter @datasites/api exec wrangler r2 bucket create datasites-data-staging
   ```
4. **Create the signups database:**
   ```powershell
   pnpm --filter @datasites/coming-soon exec wrangler d1 create datasites-signups
   ```
   Copy the `database_id` it prints into `apps/coming-soon/wrangler.jsonc`, replacing the zeros. Commit that change.
5. **Create the Turnstile widget (only for coming-soon placeholder pages; the real websites don't need it):**
   - Go to **Turnstile → Add widget**, mode **Managed**, and add the nine .dev domains as hostnames.
   - Put the **site key** in `apps/coming-soon/wrangler.jsonc` as `vars.TURNSTILE_SITE_KEY`. It's public, so it's fine in git. Commit that change.
   - Keep the **secret key** for section 5. It must not go in git.
6. **Create an R2 API token for the pipelines:**
   - Go to **R2 → Manage API tokens → Create API token**.
   - Permission: **Object Read & Write**, limited to `datasites-data` and `datasites-data-staging`.
   - Note the **Access Key ID**, the **Secret Access Key** and the **endpoint** (`https://<account-id>.r2.cloudflarestorage.com`).
7. **Create a deploy token:**
   - Go to **My Profile → API Tokens → Create Token** and use the **"Edit Cloudflare Workers"** template.
   - Add **Account → D1 → Edit**, and include your zones.

---

## 3. GitHub settings

The repo is private, so GitHub Actions minutes count against your plan: 2,000 a month on Free, 3,000 on Pro. GitHub rounds each job up to a whole minute. Measured on 2026-10-24:

- **CI:** about 2 billed minutes per push.
- **Ingest:** about 1,380 minutes a month across the nine schedules. The biggest are wildfire every 3 hours (480), water (330) and air and climate (210 each). `node scripts/verify/workflows.mjs` keeps the schedules under 1,500 and prints the breakdown.
- **Water needs its API key** (section 6) while the repo is private: without one, USGS throttling can stretch a run to its 60-minute limit, about 1,800 minutes a month on its own.

Making the repository public removes the limit: Actions minutes are free for public repositories.

1. Create an Actions **environment** named `production`: **Settings → Environments → New environment**.
2. In it, add the **variables** and **secrets**:
   ```powershell
   gh variable set R2_ENDPOINT --env production --body "https://<account-id>.r2.cloudflarestorage.com"
   gh variable set R2_BUCKET --env production --body "datasites-data"
   gh secret set R2_ACCESS_KEY_ID --env production
   gh secret set R2_SECRET_ACCESS_KEY --env production
   gh secret set CLOUDFLARE_API_TOKEN --env production
   ```
   `gh secret set` prompts you to paste each value, so it never lands in your shell history.
3. Add the **repository variables**. The two `*_ENABLED` switches turn the scheduled workflows on.
   ```powershell
   gh variable set CLOUDFLARE_ACCOUNT_ID --body "<account-id>"
   gh variable set DEPLOY_ENABLED --body true
   gh variable set INGEST_ENABLED --body true
   ```

---

## 4. Monitoring (tickharbor.net)

Alerts go through [tickharbor.net](https://tickharbor.net), our own cron monitor. It takes healthchecks.io-style pings:
- `https://ping.tickharbor.net/<uuid>` when a run succeeds;
- `/start` when a run begins;
- `/fail` or `/<exit code>` with the result.

Each ingest workflow pings `$HC_URL/start` and then `$HC_URL/0` or `$HC_URL/1`. The API's freshness cron pings `HEALTHCHECK_URL` (or `/fail`) every 15 minutes. If a secret is missing, that ping is skipped.

**Setting up the checks.** Create one check per row in the tickharbor dashboard, with the cron schedule (UTC) and grace below. The schedules copy the workflows' `cron:` lines. Each grace covers GitHub's scheduling delay, which has reached 1 h 41 min, plus the run's own length. Then paste each check's ping URL where the last column says.

| Check | Schedule (cron, UTC) | Grace | Where its ping URL goes |
|---|---|---|---|
| `datasites-ingest-air` | `19 16 * * *` | 3 hours | `gh secret set HC_INGEST_AIR --env production` |
| `datasites-ingest-carbon` | `41 6 * * 2` | 6 hours | `gh secret set HC_INGEST_CARBON --env production` |
| `datasites-ingest-climate` | `23 14 * * *` | 3 hours | `gh secret set HC_INGEST_CLIMATE --env production` |
| `datasites-ingest-economic` | `47 13 * * *` | 3 hours | `gh secret set HC_INGEST_ECONOMIC --env production` |
| `datasites-ingest-election` | `41 15 * * 1` | 6 hours | `gh secret set HC_INGEST_ELECTION --env production` |
| `datasites-ingest-housing` | `29 10 * * 3` | 6 hours | `gh secret set HC_INGEST_HOUSING --env production` |
| `datasites-ingest-satellite` | `19 3 * * *` | 3 hours | `gh secret set HC_INGEST_SATELLITE --env production` |
| `datasites-ingest-visualize` | `53 5 * * 4` | 6 hours | `gh secret set HC_INGEST_VISUALIZE --env production` |
| `datasites-ingest-water` | `41 11 * * *` | 3 hours | `gh secret set HC_INGEST_WATER --env production` |
| `datasites-ingest-wildfire` | `37 */3 * * *` | 2 hours | `gh secret set HC_INGEST_WILDFIRE --env production` |
| `datasites-api-freshness` | `*/15 * * * *` | 30 minutes | `pnpm --filter @datasites/api exec wrangler secret put HEALTHCHECK_URL` |

**Optional settings:**
- A run time limit for the slow-run alert. Water can take up to 60 minutes; the others take under 10.
- A `datasites` tag on the checks, so `https://ping.tickharbor.net/api/v1/status?tag=datasites` shows them together.

The project's email and ntfy channels deliver the alerts.

`gh secret set` asks for the value, so the URL never lands in your shell history. The ingest secrets live in the `production` environment, so manual runs against `staging` don't ping.

---

## Optional: a staging API before the domains are ready

The API's staging environment deploys to a free `workers.dev` address, so you can see the real API serve real data while DirectNIC finishes. It needs sections 2 and 3, but not section 1.

1. **Create a GitHub environment named `staging`** (Settings → Environments) with the same secrets as `production`, but pointing at the staging bucket:
   ```powershell
   gh variable set R2_ENDPOINT --env staging --body "https://<account-id>.r2.cloudflarestorage.com"
   gh variable set R2_BUCKET --env staging --body "datasites-data-staging"
   gh secret set R2_ACCESS_KEY_ID --env staging
   gh secret set R2_SECRET_ACCESS_KEY --env staging
   gh secret set CLOUDFLARE_API_TOKEN --env staging
   ```
2. **Turn on the staging deploy:** `gh variable set STAGING_ENABLED --body true`, then **Actions → deploy-staging → Run workflow**. The run's summary prints the address, for example `https://datasites-api-staging.<your-subdomain>.workers.dev`. After this, every change to the API on `main` redeploys staging.
3. **Fill the staging bucket:** **Actions → ingest-climate → Run workflow**, with target `staging`. Repeat for the other sites you want to see. Scheduled runs only write to production, so staging data stays as of the last manual run.
4. **Try it:** `https://datasites-api-staging.<your-subdomain>.workers.dev/v1/catalog?site=climatedata`. Add `?site=<id>` to every request, since the address doesn't name a site; links in the responses keep it for you.

Staging serves everyone anonymously: it has no API-key namespace until you add one (see the comment in `apps/api/wrangler.jsonc`).

---

## 5. Launching sites

Each site launches straight to its real website: the static site on the apex domain (`web-<id>` Worker) and the API at `api.<domain>`. The **deploy** workflow deploys the API Worker and every site listed in `WEB_SITES` at the top of `.github/workflows/deploy.yml`. `climatedata` and `electiondata` are listed first.

1. **Wait for the zones to be Active** in Cloudflare (each domain's overview page). A deploy that names a zone still pending fails.
   Then turn on **Email → Email Routing** for each launched domain and forward `hello@<domain>` to your inbox. The privacy page gives that address, and the pipelines send `hello@climatedata.dev` in their User-Agent, so it is how data providers would reach you about a problem.
2. **Fill the production bucket** for those sites, so the pages have data from the first minute: **Actions → ingest-climate → Run workflow** (target `production`), then the same for **ingest-election**. Manual runs work while `INGEST_ENABLED` is off.
3. **Turn deploys on and deploy:**
   ```powershell
   gh variable set DEPLOY_ENABLED --body true
   gh workflow run deploy.yml
   ```
   The workflow runs the tests, deploys the API Worker, builds and deploys each website, and smoke-tests every launched domain and its API. New custom domains can take a minute or two to get certificates; the smoke test waits for them.
4. **Check it worked:**
   ```powershell
   node scripts/verify/live.mjs dns --sites climatedata,electiondata
   node scripts/verify/live.mjs prod --sites climatedata,electiondata
   ```
   They print `LIVE DNS OK` and `LIVE PROD OK`, or list exactly what isn't up yet.
5. **Keep the data fresh:** `gh variable set INGEST_ENABLED --body true` starts every ingest schedule (about 1,400 Actions minutes a month; see section 3). Set up the tickharbor checks in section 4 first, so a failing schedule reaches you.

**Launching another site**, once its zone is Active: in one pull request, add its id to `WEB_SITES` in `deploy.yml` and its `api.<domain>` route to `apps/api/wrangler.jsonc`, and remove its route from `apps/coming-soon/wrangler.jsonc`. `node scripts/verify/workflows.mjs` fails if the two lists disagree. Fill its data with a manual ingest run, then merge.

When every site is launched, `pnpm verify:live` (no `--sites`) also checks the votingdata.net redirect.

The coming-soon Worker (`apps/coming-soon`) is not deployed by the workflow. To put a placeholder with signups on a domain that isn't ready, deploy it by hand with only that domain in its routes. It needs a Turnstile widget (section 2, step 5) and `pnpm --filter @datasites/coming-soon exec wrangler secret put TURNSTILE_SECRET_KEY`.

---

## 6. Credentials and notices for specific datasets

Every dataset works without these except the ones named. Until a dataset's credentials exist, its runs report `needs-credentials`, CI stays green and the status page shows it as pending.

| What | Needed for | How |
|---|---|---|
| Census API key (free) | economicdata `census-acs-income` | Sign up at https://api.census.gov/data/key_signup.html, then `gh secret set CENSUS_API_KEY --env production` |
| Space-Track account (free; one per person, never shared) | satellitedata `gp-current` | Register at https://www.space-track.org, then `gh secret set SPACETRACK_USERNAME --env production` and `gh secret set SPACETRACK_PASSWORD --env production` |
| USGS Water Data API key (free; needed while the repo is private) | waterdata `stream-gages`, `streamflow-daily` | Works without it, but the anonymous rate limit can stretch a daily run to an hour of Actions minutes (see section 3). It is an api.data.gov key: sign up at https://api.data.gov/signup/, then `gh secret set USGS_WATERDATA_API_KEY --env production` |
| MIT Election Lab files | electiondata `county-president`, `house-results` | Done 2026-10-26: V20 and V15 are committed in `data/sources/medsl/`. When MEDSL publishes a new version the weekly run stops with an error; download the files again in your browser (Dataverse guestbook) as that folder's README describes |

**API keys and billing (Phases 3 and 6):**

1. The production KV namespace for keys, `datasites-api-keys`, exists (created 2026-10-25) and is in `apps/api/wrangler.jsonc`. Staging has none, so it serves everyone anonymously. To test keys there, create one and add it to `env.staging` (see the comment in that file):
   ```powershell
   pnpm --filter @datasites/api exec wrangler kv namespace create datasites-api-keys-staging
   ```
2. Issue a key: `node apps/api/scripts/keys.mjs issue <account> --plan free --remote`. It prints the key once.
3. For Stripe:
   - Create a Billing Meter named `api_requests` (customer key `stripe_customer_id`, value key `value`, sum aggregation).
   - Create a metered Price and a Pro product.
   - Set `client_reference_id` to the account id in Checkout.
   - Register a webhook to `https://api.<domain>/billing/webhook` for `checkout.session.completed`, `customer.subscription.updated` and `customer.subscription.deleted`.
   - Then run:
     ```powershell
     pnpm --filter @datasites/api exec wrangler secret put STRIPE_SECRET_KEY
     pnpm --filter @datasites/api exec wrangler secret put STRIPE_WEBHOOK_SECRET
     ```
   - Without these, billing stays inert.

**Notices the sources ask for.** Send these yourself; nothing here sends email:

- **AirNow** (airdata's preliminary data): their Data Exchange Guidelines ask users to return a signed form to dmc@airnowtech.org, keep contact details current, and tell them about derived products.
- **NASA POWER** (climatedata `world-capitals`): asks to be told when its data is redistributed: larc-power-project@mail.nasa.gov.

---

## Reference: where every secret lives

| Secret | Stored in | Used by |
|---|---|---|
| R2 access key and secret | GitHub environment `production` | Python pipelines |
| Cloudflare deploy token | GitHub environment `production` | deploy workflow |
| Turnstile secret key | `wrangler secret put` on `datasites-coming-soon` | signup form |
| tickharbor ping URLs (`HC_INGEST_*`, `HEALTHCHECK_URL`) | GitHub environment `production`, `wrangler secret put` on `datasites-api` | ingest workflows, freshness cron |
| `CENSUS_API_KEY`, `SPACETRACK_USERNAME`, `SPACETRACK_PASSWORD`, `USGS_WATERDATA_API_KEY` | GitHub environment `production` | economicdata, satellitedata and waterdata pipelines |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | `wrangler secret put` on `datasites-api` | billing webhook and nightly meter report |
| Local development | `apps/*/.dev.vars` (gitignored; copy from `.dev.vars.example`) | `wrangler dev` |
