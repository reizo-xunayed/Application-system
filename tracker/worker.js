// Open tracking for Ausbildung Outreach (Cloudflare Worker, free plan).
//
//   GET /p/<token>.gif   -> records an open, returns an invisible 1x1 image
//   GET /api/opens       -> lists recorded opens (needs "Authorization: Bearer <API_SECRET>")
//
// Bindings: KV namespace OPENS, secret API_SECRET. Nothing but a time and the
// mail program's user agent is stored, and entries delete themselves after 180 days.

const PIXEL = Uint8Array.from(atob("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"), (c) => c.charCodeAt(0));
const KEEP_SECONDS = 60 * 60 * 24 * 180;
const TOKEN = /^\/p\/([A-Za-z0-9_-]{8,64})(?:\.gif)?$/;

function pixel() {
  return new Response(PIXEL, {
    headers: {
      "content-type": "image/gif",
      "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
      pragma: "no-cache",
      expires: "0",
    },
  });
}

function sameSecret(given, expected) {
  if (!expected || typeof given !== "string") return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== "GET") return new Response("method not allowed", { status: 405 });

    const match = url.pathname.match(TOKEN);
    if (match) {
      const key = `o:${Date.now()}:${match[1]}:${crypto.randomUUID().slice(0, 8)}`;
      const ua = (request.headers.get("user-agent") || "").slice(0, 200);
      ctx.waitUntil(env.OPENS.put(key, "", { metadata: { ua }, expirationTtl: KEEP_SECONDS }));
      return pixel();
    }

    if (url.pathname === "/api/opens") {
      const auth = request.headers.get("authorization") || "";
      if (!sameSecret(auth.replace(/^Bearer\s+/i, ""), env.API_SECRET)) {
        return new Response("unauthorized", { status: 401 });
      }
      const list = await env.OPENS.list({ prefix: "o:", cursor: url.searchParams.get("cursor") || undefined, limit: 1000 });
      const opens = list.keys.map((k) => {
        const [, t, token] = k.name.split(":");
        return { key: k.name, token, t: Number(t), ua: (k.metadata && k.metadata.ua) || "" };
      });
      return Response.json({ opens, cursor: list.list_complete ? null : list.cursor });
    }

    if (url.pathname === "/") return new Response("ok");
    return new Response("not found", { status: 404 });
  },
};
