# Grok Register & OIDC Minting Pipeline

Automated high-throughput registration, Cloudflare Turnstile handling, verification, and xAI OIDC / Grok-CLI credential minting pipeline. Optimized for headless Linux VPS servers, background daemons, and autonomous agent environments (such as **Hermes Agent**).

---

## Key Features

- **Autonomous Registration**: Full Chromium automation via DrissionPage to handle xAI registration, Turnstile challenges, and SSO session persistence.
- **Private Disposable Mail API**: First-class integration with Cloudflare Workers + D1 database for zero-cost, private catch-all verification email polling.
- **Headless & Agent-Ready**: Native virtual display (`Xvfb`) support with zero manual GUI intervention required.
- **OAuth Device-Auth Minting**: Automatically mints CPA xAI OIDC credentials (`xai-*.json`) compatible with Grok-CLI and CLIProxyAPI.
- **9Router Gateway Ingestion**: Directly syncs minted tokens into local 9Router SQLite databases or remote token pools.

---

## Architecture

```
                                  [ xAI / Grok Signup ]
                                            │
               ┌────────────────────────────┴────────────────────────────┐
               ▼                                                         ▼
    [ Chromium Automation ]                                   [ Inbound Verification ]
      - DrissionPage engine                                      - Catch-all Email Routing
      - Turnstile challenge bypass                               - Cloudflare Worker
      - Headless Xvfb display                                    - Cloudflare D1 Database
               │                                                         │
               └────────────────────────────┬────────────────────────────┘
                                            ▼
                           [ Verification Code Polling ]
                                            │
                                            ▼
                              [ Session SSO Cookie Acquired ]
                                            │
                                            ▼
                            [ OIDC Device-Authorization ]
                                            │
                                            ▼
                       [ Minted Grok-CLI Token (xai-*.json) ]
                                            │
                                            ▼
                       [ Ingestion to 9Router / Token Pool ]
```

---

## Prerequisites (Linux / Hermes Environment)

Before running the registration pipeline on Ubuntu / Debian / Oracle Linux:

```bash
# 1. Install system packages (Python 3.10+, Xvfb, and Chromium)
sudo apt-get update && sudo apt-get install -y \
  python3-venv \
  python3-pip \
  xvfb \
  chromium-browser \
  curl \
  jq

# 2. Clone repository & create virtual environment
git clone <YOUR_REPO_URL> grok-register
cd grok-register
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 1: Deploy Cloudflare TempMail Worker

The pipeline requires an email service to generate throwaway addresses and retrieve 6-digit OTP codes. The included Cloudflare Worker runs completely within Cloudflare's free tier.

Detailed instructions are also in [`cloudflare-temp-mail-worker/DEPLOY.md`](cloudflare-temp-mail-worker/DEPLOY.md).

### 1.1 Install Wrangler & Login

```bash
npm install -g wrangler
wrangler login
```

### 1.2 Create D1 Database

```bash
cd cloudflare-temp-mail-worker
wrangler d1 create temp-mail-db
```

Wrangler will output your `database_id`. Copy `wrangler.toml.example` to `wrangler.toml` and update the database binding:

```toml
name = "temp-mail-worker"
main = "worker.js"
compatibility_date = "2024-12-01"

[[d1_databases]]
binding = "DB"
database_name = "temp-mail-db"
database_id = "PASTE_YOUR_D1_DATABASE_ID_HERE"

[vars]
DOMAINS = "yourdomain.com"
```

### 1.3 Initialize Database Schema

```bash
wrangler d1 execute temp-mail-db --remote --command="
CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  sender TEXT NOT NULL,
  subject TEXT,
  body_text TEXT,
  body_html TEXT,
  raw TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emails_address ON emails(address);
CREATE INDEX IF NOT EXISTS idx_emails_created_at ON emails(created_at);
CREATE TABLE IF NOT EXISTS addresses (
  address TEXT PRIMARY KEY,
  password_hash TEXT,
  created_at INTEGER NOT NULL
);
"
```

### 1.4 Set Authentication Secrets

```bash
# Secret used for JWT generation
wrangler secret put JWT_SECRET
# Master admin key used by grok-register
wrangler secret put ADMIN_KEY
```

### 1.5 Deploy the Worker

```bash
wrangler deploy
```

Note your deployed worker URL, for example:  
`https://temp-mail-worker.<your-subdomain>.workers.dev`

### 1.6 Enable Cloudflare Email Routing

1. In the Cloudflare Dashboard, open your domain settings (`yourdomain.com`).
2. Go to **Email** > **Email Routing** and enable it (Cloudflare will automatically configure DNS MX records).
3. Under **Routing Rules** > **Catch-all rule**:
   - Action: **Send to a Worker**
   - Select: **`temp-mail-worker`**
   - Click **Save**.

### 1.7 Verify TempMail Worker

```bash
# Test endpoint
curl -s https://temp-mail-worker.<your-subdomain>.workers.dev/api/domains

# Test admin email creation
curl -s -X POST https://temp-mail-worker.<your-subdomain>.workers.dev/admin/new_address \
  -H "Content-Type: application/json" \
  -H "x-admin-auth: <YOUR_ADMIN_KEY>" \
  -d '{"name": "testprobe", "domain": "yourdomain.com"}'
```

---

## Step 2: Configure `config.json`

Return to the root `grok-register` directory and create `config.json`:

```bash
cp config.example.json config.json
```

Edit `config.json` with your TempMail Worker details:

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://temp-mail-worker.<your-subdomain>.workers.dev",
  "cloudflare_api_key": "<YOUR_ADMIN_KEY>",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "yourdomain.com",
  
  "browser_hide_mode": "xvfb",
  "enable_nsfw": true,
  "register_count": 10,
  
  "cpa_export_enabled": true,
  "cpa_auth_dir": "./cpa_auths",
  "cpa_push_9router": true,
  "cpa_headless": false,
  "cpa_force_standalone": true,
  "cpa_mint_timeout_sec": 300
}
```

### Key Configuration Fields

| Field | Description |
|---|---|
| `email_provider` | Set to `"cloudflare"`. |
| `cloudflare_api_base` | Your Cloudflare Worker URL. |
| `cloudflare_api_key` | The `ADMIN_KEY` configured in Wrangler secrets. |
| `cloudflare_auth_mode` | `"x-admin-auth"` (bypasses browser captcha when provisioning mailboxes). |
| `defaultDomains` | Your custom domain configured in Cloudflare Email Routing. |
| `browser_hide_mode` | Set to `"xvfb"` for completely headless Linux execution. |
| `cpa_export_enabled` | Set to `true` to mint CPA Grok-CLI OIDC credentials. |
| `cpa_push_9router` | Set to `true` to auto-insert credentials into 9Router SQLite DB. |
| `proxy` | Optional HTTP/SOCKS5 proxy (e.g. `socks5://127.0.0.1:40000`). |

---

## Step 3: Running on Hermes / Headless Linux

### Option A: Quick Single Account Test (CLI)

Test end-to-end registration of a single account:

```bash
source .venv/bin/activate
python grok_register_ttk.py cli
# When prompted, type 'start' and press Enter
```

### Option B: Batch Runner via Tmux (Recommended for Hermes)

When managing batch jobs autonomously via Hermes, run the process in a detached tmux session to avoid shell timeouts:

```bash
# Start background tmux session
tmux new-session -d -s grok-batch '
  cd /path/to/grok-register
  source .venv/bin/activate
  python run_500_batch.py
'

# Monitor logs
tail -f /path/to/grok-register/mint_500.log
```

### Option C: Minting Existing Accounts into 9Router

If you already have registered accounts saved in `accounts.txt` and want to mint Grok-CLI OIDC credentials into 9Router:

```bash
python auto_import_grok_cli.py --limit 50 --sleep 5
```

---

## Troubleshooting & FAQ

### 1. Cloudflare WAF Blocks from Datacenter IPs
If xAI or Cloudflare blocks direct connections from your VPS datacenter ASN (HTTP 403 / Cloudflare Challenge Loop):
- Route traffic through local Cloudflare WARP SOCKS5 proxy (`socks5://127.0.0.1:40000`) or residential proxy.
- Set `"proxy": "socks5://127.0.0.1:40000"` in `config.json`.

### 2. Missing Display / Xvfb Errors
If you see `Cannot open display :99`:
```bash
# Ensure Xvfb is running or let browser_runtime start it automatically
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

### 3. Verification Code Timeout
- Ensure Cloudflare Email Routing Catch-all rule is set to **Send to a Worker** and pointed to `temp-mail-worker`.
- Verify inbound routing by manually sending an email to `anyprefix@yourdomain.com` and querying `/api/mails`.

### 4. Turnstile Retries
The engine automatically detects Turnstile iframes and triggers clicks. Allow up to 30-45 seconds for difficult challenges on clean residential or WARP IPs.

---

## License

MIT License. Educational and authorized testing research purposes only.
