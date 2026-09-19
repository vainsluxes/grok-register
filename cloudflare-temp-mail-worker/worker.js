/**
 * Cloudflare Worker: Temp Mail API
 * Compatible with grok-register cloudflare email provider
 *
 * Endpoints:
 *   GET  /api/domains          → list available domains
 *   POST /api/new_address      → create anonymous email (returns address + jwt)
 *   POST /admin/new_address    → create email via admin (requires x-admin-auth header)
 *   POST /api/token            → exchange address+password for jwt
 *   GET  /api/mails            → list messages (Bearer auth)
 *   GET  /api/mail/:id         → get message detail (Bearer auth)
 *   POST /api/mails            → receive inbound email (called by Email Routing)
 *
 * Bindings:
 *   DB          — D1 database
 *   ADMIN_KEY   — (optional) env var for admin auth
 *   JWT_SECRET  — (optional) env var for signing JWTs
 *   DOMAINS     — (optional) env var, comma-separated domain list
 */

// ─── Config ──────────────────────────────────────────────────────────────────

function getDomains(env) {
  const raw = env.DOMAINS || "example.com";
  return raw.split(",").map((d) => d.trim()).filter(Boolean);
}

function getJwtSecret(env) {
  return env.JWT_SECRET || "change-me-in-production";
}

function getAdminKey(env) {
  return env.ADMIN_KEY || "";
}

// ─── Base64url helpers (no btoa/atob dependency) ────────────────────────────

const B64CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function b64Encode(buf) {
  const bytes = new Uint8Array(buf);
  let result = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const b1 = bytes[i];
    const b2 = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const b3 = i + 2 < bytes.length ? bytes[i + 2] : 0;
    result += B64CHARS[b1 >> 2];
    result += B64CHARS[((b1 & 3) << 4) | (b2 >> 4)];
    result += i + 1 < bytes.length ? B64CHARS[((b2 & 15) << 2) | (b3 >> 6)] : "=";
    result += i + 2 < bytes.length ? B64CHARS[b3 & 63] : "=";
  }
  return result;
}

function b64urlEncode(buf) {
  return b64Encode(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlEncodeStr(str) {
  return b64urlEncode(new TextEncoder().encode(str));
}

function b64urlDecode(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

// ─── JWT (HMAC-SHA256, minimal) ──────────────────────────────────────────────

async function hmacSign(key, data) {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(data));
  return b64urlEncode(sig);
}

async function signJwt(payload, secret) {
  const header = b64urlEncodeStr(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = b64urlEncodeStr(JSON.stringify(payload));
  const sig = await hmacSign(secret, `${header}.${body}`);
  return `${header}.${body}.${sig}`;
}

async function verifyJwt(token, secret) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const expected = await hmacSign(secret, `${parts[0]}.${parts[1]}`);
  if (expected !== parts[2]) return null;
  try {
    const payloadBytes = b64urlDecode(parts[1]);
    const payload = JSON.parse(new TextDecoder().decode(payloadBytes));
    if (payload.exp && Date.now() / 1000 > payload.exp) return null;
    return payload;
  } catch {
    return null;
  }
}

function extractBearerAuth(request) {
  const auth = request.headers.get("Authorization") || "";
  if (auth.startsWith("Bearer ")) return auth.slice(7).trim();
  return "";
}

function generatePassword() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function generateUsername(len = 10) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => chars[b % chars.length])
    .join("");
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

// ─── D1 Helpers ──────────────────────────────────────────────────────────────

async function ensureTables(db) {
  await db.prepare(`CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    created_at INTEGER DEFAULT (unixepoch())
  )`).run();
  await db.prepare(`CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msgid TEXT UNIQUE NOT NULL,
    address TEXT NOT NULL,
    from_addr TEXT,
    subject TEXT,
    body TEXT,
    html TEXT,
    received_at INTEGER DEFAULT (unixepoch())
  )`).run();
  try {
    await db.prepare(
      "CREATE INDEX IF NOT EXISTS idx_messages_address ON messages(address)"
    ).run();
  } catch {}
}

async function findAccount(db, address) {
  return await db
    .prepare("SELECT * FROM accounts WHERE address = ?")
    .bind(address)
    .first();
}

async function insertAccount(db, address, password) {
  await db
    .prepare("INSERT OR IGNORE INTO accounts (address, password) VALUES (?, ?)")
    .bind(address, password)
    .run();
}

async function insertMessage(db, msgid, address, fromAddr, subject, body, html) {
  await db
    .prepare(
      "INSERT OR IGNORE INTO messages (msgid, address, from_addr, subject, body, html) VALUES (?, ?, ?, ?, ?, ?)"
    )
    .bind(msgid, address, fromAddr, subject, body, html || "")
    .run();
}

async function getMessages(db, address, limit = 20, offset = 0) {
  // Include body/html so clients can extract OTP without a second detail call.
  // AWS SES Message-IDs contain < > @ which break poorly-encoded detail URLs.
  const { results } = await db
    .prepare(
      "SELECT msgid as id, from_addr as \"from\", subject, body, html, body as text, body as raw, body as content, received_at as createdAt FROM messages WHERE address = ? ORDER BY received_at DESC LIMIT ? OFFSET ?"
    )
    .bind(address, limit, offset)
    .all();
  return results || [];
}

function normalizeMsgid(msgid) {
  let id = String(msgid || "");
  // Decode repeatedly in case of double-encoding (%253C -> %3C -> <)
  for (let i = 0; i < 3; i++) {
    try {
      const decoded = decodeURIComponent(id);
      if (decoded === id) break;
      id = decoded;
    } catch {
      break;
    }
  }
  return id;
}

async function getMessageDetail(db, msgid) {
  const id = normalizeMsgid(msgid);
  // Try exact, then common variants for SES Message-IDs.
  const candidates = [id];
  if (id.startsWith("<") && id.endsWith(">")) {
    candidates.push(id.slice(1, -1));
  } else if (id.includes("@") && !id.startsWith("<")) {
    candidates.push(`<${id}>`);
  }
  for (const candidate of candidates) {
    const row = await db
      .prepare("SELECT * FROM messages WHERE msgid = ?")
      .bind(candidate)
      .first();
    if (row) return row;
  }
  return null;
}

// ─── Route Handlers ──────────────────────────────────────────────────────────

async function handleDomains(env) {
  return jsonResponse(getDomains(env));
}

async function handleNewAddress(request, env) {
  const db = env.DB;
  await ensureTables(db);

  let domain = "";
  let name = "";
  try {
    const body = await request.json();
    domain = body.domain || "";
    name = body.name || "";
  } catch {}

  const domains = getDomains(env);
  if (!domain || !domains.includes(domain)) {
    domain = domains[0] || "example.com";
  }

  const username = name || generateUsername(10);
  const address = `${username}@${domain}`;
  const password = generatePassword();

  await insertAccount(db, address, password);

  const secret = getJwtSecret(env);
  const jwt = await signJwt(
    { address, exp: Math.floor(Date.now() / 1000) + 86400 * 30 },
    secret
  );

  return jsonResponse({ address, jwt, password });
}

async function handleAdminNewAddress(request, env) {
  const adminKey = getAdminKey(env);
  if (!adminKey) {
    return jsonResponse({ error: "Admin mode not configured" }, 403);
  }

  const provided =
    request.headers.get("x-admin-auth") ||
    extractBearerAuth(request) ||
    "";
  if (provided !== adminKey) {
    return jsonResponse({ error: "Unauthorized" }, 401);
  }

  const db = env.DB;
  await ensureTables(db);

  let domain = "";
  let name = "";
  let enablePrefix = false;
  try {
    const body = await request.json();
    domain = body.domain || "";
    name = body.name || "";
    enablePrefix = !!body.enablePrefix;
  } catch {}

  const domains = getDomains(env);
  if (!domain || !domains.includes(domain)) {
    domain = domains[0] || "example.com";
  }

  const username = name || generateUsername(10);
  const prefix = enablePrefix ? "a" : "";
  const address = `${prefix}${username}@${domain}`;
  const password = generatePassword();

  await insertAccount(db, address, password);

  const secret = getJwtSecret(env);
  const jwt = await signJwt(
    { address, exp: Math.floor(Date.now() / 1000) + 86400 * 30 },
    secret
  );

  return jsonResponse({ address, jwt, password });
}

async function handleToken(request, env) {
  const db = env.DB;
  await ensureTables(db);

  let address = "";
  let password = "";
  try {
    const body = await request.json();
    address = body.address || "";
    password = body.password || "";
  } catch {}

  if (!address) {
    return jsonResponse({ error: "address required" }, 400);
  }

  const account = await findAccount(db, address);
  if (!account) {
    return jsonResponse({ error: "Account not found" }, 404);
  }

  // Allow token issue even without password match (for anonymous-created accounts)
  if (password && account.password !== password) {
    return jsonResponse({ error: "Invalid credentials" }, 401);
  }

  const secret = getJwtSecret(env);
  const token = await signJwt(
    { address, exp: Math.floor(Date.now() / 1000) + 86400 * 30 },
    secret
  );

  return jsonResponse({ token });
}

async function handleGetMails(request, env) {
  const db = env.DB;
  await ensureTables(db);

  const token = extractBearerAuth(request);
  if (!token) {
    return jsonResponse({ error: "Authorization required" }, 401);
  }

  const payload = await verifyJwt(token, getJwtSecret(env));
  if (!payload || !payload.address) {
    return jsonResponse({ error: "Invalid token" }, 401);
  }

  const url = new URL(request.url);
  const limit = parseInt(url.searchParams.get("limit") || "20", 10);
  const offset = parseInt(url.searchParams.get("offset") || "0", 10);

  const messages = await getMessages(db, payload.address, limit, offset);
  return jsonResponse(messages);
}

async function handleGetMessageDetail(request, env, msgid) {
  const db = env.DB;
  await ensureTables(db);

  const token = extractBearerAuth(request);
  if (!token) {
    return jsonResponse({ error: "Authorization required" }, 401);
  }

  const payload = await verifyJwt(token, getJwtSecret(env));
  if (!payload || !payload.address) {
    return jsonResponse({ error: "Invalid token" }, 401);
  }

  const msg = await getMessageDetail(db, msgid);
  if (!msg) {
    return jsonResponse({ error: "Message not found" }, 404);
  }

  // Verify message belongs to the authenticated user
  if (msg.address !== payload.address) {
    return jsonResponse({ error: "Forbidden" }, 403);
  }

  return jsonResponse(msg);
}

// ─── Email Routing Handler (inbound) ─────────────────────────────────────────

async function handleEmail(message, env) {
  console.log(`[EMAIL] Incoming: from=${message.from} to=${message.to}`);
  const db = env.DB;
  await ensureTables(db);

  const rawBody = await new Response(message.raw).text();
  const address = message.to;
  const msgid = message.headers.get("message-id") || crypto.randomUUID();

  console.log(`[EMAIL] Storing: msgid=${msgid} addr=${address} subject=${message.headers.get("subject")}`);

  await insertMessage(
    db,
    msgid,
    address,
    message.from,
    message.headers.get("subject") || "",
    rawBody,
    rawBody // store raw as html fallback
  );
  console.log(`[EMAIL] Stored OK: ${msgid}`);
}

// ─── Router ──────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method;

    // CORS preflight
    if (method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key, x-admin-auth",
        },
      });
    }

    try {
      // GET /api/domains
      if (method === "GET" && path === "/api/domains") {
        return handleDomains(env);
      }

      // POST /api/new_address
      if (method === "POST" && path === "/api/new_address") {
        return handleNewAddress(request, env);
      }

      // POST /admin/new_address
      if (method === "POST" && path === "/admin/new_address") {
        return handleAdminNewAddress(request, env);
      }

      // POST /api/token
      if (method === "POST" && path === "/api/token") {
        return handleToken(request, env);
      }

      // GET /api/mail?id=... or /api/mails?id=...  (preferred for SES Message-IDs with < > @)
      // Must be checked BEFORE plain list handler.
      if (method === "GET" && (path === "/api/mail" || path === "/api/mails")) {
        const qid = url.searchParams.get("id") || url.searchParams.get("msgid") || "";
        if (qid) {
          return handleGetMessageDetail(request, env, qid);
        }
      }

      // GET /api/mails (list)
      if (method === "GET" && path === "/api/mails") {
        return handleGetMails(request, env);
      }

      // GET /api/mail/:id or /api/mails/:id
      const mailDetailMatch = path.match(/^\/api\/mails?\/(.+)$/);
      if (method === "GET" && mailDetailMatch) {
        return handleGetMessageDetail(request, env, mailDetailMatch[1]);
      }

      return jsonResponse({ error: "Not found" }, 404);
    } catch (err) {
      return jsonResponse({ error: err.message || "Internal error" }, 500);
    }
  },

  async email(message, env) {
    try {
      await handleEmail(message, env);
    } catch (err) {
      console.error("Email handler error:", err);
    }
  },
};
