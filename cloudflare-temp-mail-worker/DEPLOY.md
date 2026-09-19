# Cloudflare TempMail Worker — Deployment Guide

This guide explains how to deploy your own private, headless disposable email API worker on Cloudflare Workers + D1 database + Cloudflare Email Routing. This worker is fully compatible with the `cloudflare` email provider in `grok-register`.

---

## Architecture Overview

```
[ Inbound Verification Emails (e.g. xAI / Grok) ]
                   │
                   ▼
  Cloudflare Email Routing (Catch-all *@yourdomain.com)
                   │
                   ▼
       Cloudflare Worker (`temp-mail-worker`)
                   │
                   ▼
       Cloudflare D1 Database (`temp-mail-db`)
                   ▲
                   │ HTTP API (JWT / Admin Auth)
                   │
  grok-register Pipeline (Hermes Agent / Local Host)
```

---

## Prerequisites

1. A Cloudflare account with an active custom domain (e.g., `yourdomain.com`).
2. Node.js (v18+) and npm installed.
3. Cloudflare Wrangler CLI.

---

## Step-by-Step Deployment

### 1. Install & Authenticate Wrangler

```bash
npm install -g wrangler
wrangler login
```

### 2. Create the D1 Database

Inside the `cloudflare-temp-mail-worker` directory:

```bash
cd cloudflare-temp-mail-worker
wrangler d1 create temp-mail-db
```

Output will display configuration details including `database_id`:
```text
[[d1_databases]]
binding = "DB"
database_name = "temp-mail-db"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Copy the `database_id` and update `wrangler.toml` (copy `wrangler.toml.example` to `wrangler.toml` first if not present):
```toml
[[d1_databases]]
binding = "DB"
database_name = "temp-mail-db"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

### 3. Initialize D1 Schema

Execute the table migrations on your remote D1 database:

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

### 4. Configure Secrets & Environment Variables

Set secure authentication secrets:

```bash
# Random secret used for signing message JWT tokens
wrangler secret put JWT_SECRET

# Master admin key used by grok-register (x-admin-auth header)
wrangler secret put ADMIN_KEY
```

Configure your allowed domains in `wrangler.toml` under `[vars]`:
```toml
[vars]
DOMAINS = "yourdomain.com"
```

### 5. Deploy the Worker

```bash
wrangler deploy
```

Upon completion, Wrangler will output your live worker URL:
```text
https://temp-mail-worker.<your-subdomain>.workers.dev
```

---

## Configure Cloudflare Email Routing

1. In the Cloudflare Dashboard, select your domain (`yourdomain.com`).
2. Navigate to **Email** > **Email Routing**.
3. If not already enabled, click **Enable Email Routing** and let Cloudflare add the recommended MX and SPF DNS records automatically.
4. Go to the **Routing Rules** tab:
   - Under **Catch-all rule**, select **Edit**.
   - Set Action to **Send to a Worker**.
   - Select your deployed worker: `temp-mail-worker`.
   - Save rule.

All emails sent to `*@yourdomain.com` will now be routed directly to your worker and stored in D1.

---

## Verification & Smoke Test

### 1. List Supported Domains
```bash
curl -s https://temp-mail-worker.<your-subdomain>.workers.dev/api/domains
```
Expected output:
```json
["yourdomain.com"]
```

### 2. Create a Mailbox via Admin Key
```bash
curl -s -X POST https://temp-mail-worker.<your-subdomain>.workers.dev/admin/new_address \
  -H "Content-Type: application/json" \
  -H "x-admin-auth: <YOUR_ADMIN_KEY>" \
  -d '{"name": "testprobe", "domain": "yourdomain.com"}'
```
Expected output:
```json
{
  "address": "testprobe@yourdomain.com",
  "jwt": "eyJhbGciOi..."
}
```

### 3. Send a Test Email & Poll Messages
Send an email to `testprobe@yourdomain.com` from your personal mail client, then poll:
```bash
curl -s -H "Authorization: Bearer <JWT_FROM_STEP_2>" \
  "https://temp-mail-worker.<your-subdomain>.workers.dev/api/mails?limit=10"
```

---

## grok-register Integration

Update `config.json` in the root `grok-register` directory:

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
  "defaultDomains": "yourdomain.com"
}
```

