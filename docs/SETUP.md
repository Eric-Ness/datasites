# Setup: the steps that need your accounts

Everything in the repo builds and runs locally without these steps. They're what it takes to put the sites on the internet. Work through them in order. Each step says how to check it worked.

Commands assume PowerShell in the repo root (`C:\Users\ericn\source\repos\datasites`).

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
- Optional: **Email → Email Routing**, to forward `hello@<domain>` to your inbox.

### votingdata.net redirect
1. In the votingdata.net zone, add two **proxied** DNS records: `A @ 192.0.2.1` and `AAAA www 100::`. They are placeholders; the redirect answers before any server is contacted.
2. Go to **Rules → Redirect Rules → Create rule (Single Redirect)** and set:
   - **When:** `(http.host eq "votingdata.net") or (http.host eq "www.votingdata.net")`
   - **Then:** dynamic `concat("https://electiondata.dev", http.request.uri.path)`
   - **Status 302**, with "preserve query string" on.
   - Use 302, not 301: browsers cache 301s almost forever, and this domain may become its own site later.

### www for the .dev sites (optional)
Same pattern for each .dev domain:
1. Add a proxied `AAAA www 100::` record.
2. Add a Single Redirect from `www.<domain>` to `https://<domain>`, 301, preserving path and query string.

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
5. **Create the Turnstile widget:**
   - Go to **Turnstile → Add widget**, mode **Managed**, and add the nine .dev domains as hostnames.
   - Put the **site key** in `apps/coming-soon/wrangler.jsonc` as `vars.TURNSTILE_SITE_KEY`. It's public, so it's fine in git. Commit that change.
   - Keep the **secret key** for step 5. It must not go in git.
6. **Create an R2 API token for the pipelines:**
   - Go to **R2 → Manage API tokens → Create API token**.
   - Permission: **Object Read & Write**, limited to `datasites-data` and `datasites-data-staging`.
   - Note the **Access Key ID**, the **Secret Access Key** and the **endpoint** (`https://<account-id>.r2.cloudflarestorage.com`).
7. **Create a deploy token:**
   - Go to **My Profile → API Tokens → Create Token** and use the **"Edit Cloudflare Workers"** template.
   - Add **Account → D1 → Edit**, and include your zones.

---

## 3. GitHub settings

The repo is private, so GitHub Actions minutes count against your plan: 2,000 a month on Free, 3,000 on Pro. CI takes about 2–3 minutes per push, and the daily ingest about 1 minute.

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

## 4. Monitoring (healthchecks.io, free plan)

Create two checks:

| Check | Period | Grace | Where its ping URL goes |
|---|---|---|---|
| `ingest-climate` | 1 day | 2 hours | `gh secret set HC_INGEST_CLIMATE --env production` |
| `api-freshness` | 15 minutes | 30 minutes | `pnpm --filter @datasites/api exec wrangler secret put HEALTHCHECK_URL` (after the first deploy) |

Then add email or phone-push integrations to the project so a failure reaches you.

---

## 5. First deploy

Deploy only when the zones in the Workers' `routes` lists are **Active**. If some aren't yet, remove them from `apps/coming-soon/wrangler.jsonc` and add them back later.

1. Merge the `phase-0/scaffold` pull request. With `DEPLOY_ENABLED=true`, the **deploy** workflow then:
   - runs the tests,
   - applies the D1 migration,
   - deploys both Workers,
   - smoke-tests `https://api.climatedata.dev/v1/status`.

   To deploy from your machine instead:
   ```powershell
   pnpm --filter @datasites/coming-soon exec wrangler d1 migrations apply datasites-signups --remote
   pnpm --filter @datasites/coming-soon exec wrangler deploy
   pnpm --filter @datasites/api exec wrangler deploy
   ```
2. Set the Turnstile secret:
   ```powershell
   pnpm --filter @datasites/coming-soon exec wrangler secret put TURNSTILE_SECRET_KEY
   ```
   Until it's set, signups in production return an error rather than skipping the bot check.
3. Run the first ingest: **Actions → ingest-climate → Run workflow**.

**Check it worked:**
```powershell
pnpm verify:live
```
This checks everything in one go:
- all ten domains on Cloudflare nameservers and answering HTTPS;
- the nine coming-soon pages;
- the votingdata.net redirect;
- `api.climatedata.dev/v1/status` reporting every dataset ok.

It prints `LIVE DNS OK` and `LIVE PROD OK` when everything is up. Otherwise it lists exactly what isn't up yet, so you can also run it partway through these steps to see what's left.

---

## Reference: where every secret lives

| Secret | Stored in | Used by |
|---|---|---|
| R2 access key and secret | GitHub environment `production` | Python pipelines |
| Cloudflare deploy token | GitHub environment `production` | deploy workflow |
| Turnstile secret key | `wrangler secret put` on `datasites-coming-soon` | signup form |
| healthchecks ping URLs | GitHub secret, `wrangler secret put` on `datasites-api` | ingest workflow, freshness cron |
| Local development | `apps/*/.dev.vars` (gitignored; copy from `.dev.vars.example`) | `wrangler dev` |
