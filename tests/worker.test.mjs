// Run with: node --test tests/worker.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import worker from "../tracker/worker.js";

function memoryKV() {
  const store = new Map();
  return {
    store,
    async put(key, value, opts) {
      store.set(key, { value, metadata: opts && opts.metadata });
    },
    async list({ prefix, cursor, limit }) {
      const keys = [...store.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = keys.slice(start, start + limit);
      const done = start + limit >= keys.length;
      return {
        keys: page.map((name) => ({ name, metadata: store.get(name).metadata })),
        list_complete: done,
        cursor: done ? undefined : String(start + limit),
      };
    },
  };
}

function setup() {
  const env = { OPENS: memoryKV(), API_SECRET: "s3cret-value" };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = async (path, headers = {}) => {
    const res = await worker.fetch(new Request(`https://tracker.example.workers.dev${path}`, { headers }), env, ctx);
    await Promise.all(pending);
    return res;
  };
  return { env, call };
}

test("pixel returns a gif and records the open", async () => {
  const { env, call } = setup();
  const res = await call("/p/abcDEF123_-xyz.gif", { "user-agent": "GoogleImageProxy" });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "image/gif");
  assert.match(res.headers.get("cache-control"), /no-store/);
  const bytes = new Uint8Array(await res.arrayBuffer());
  assert.equal(new TextDecoder().decode(bytes.slice(0, 6)), "GIF89a");
  assert.equal(env.OPENS.store.size, 1);
  const [key] = env.OPENS.store.keys();
  assert.match(key, /^o:\d+:abcDEF123_-xyz:[0-9a-f-]{8}$/);
});

test("bad tokens are not recorded", async () => {
  const { env, call } = setup();
  assert.equal((await call("/p/short.gif")).status, 404);
  assert.equal((await call("/p/has%20space1234.gif")).status, 404);
  assert.equal(env.OPENS.store.size, 0);
});

test("api requires the secret", async () => {
  const { call } = setup();
  assert.equal((await call("/api/opens")).status, 401);
  assert.equal((await call("/api/opens", { authorization: "Bearer wrong" })).status, 401);
});

test("api lists opens with token, time and user agent, paging with cursor", async () => {
  const { env, call } = setup();
  for (let i = 0; i < 3; i++) await call(`/p/token${i}abcdefgh.gif`, { "user-agent": `UA${i}` });
  const res = await call("/api/opens", { authorization: "Bearer s3cret-value" });
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.opens.length, 3);
  assert.equal(data.cursor, null);
  const tokens = data.opens.map((o) => o.token).sort();
  assert.deepEqual(tokens, ["token0abcdefgh", "token1abcdefgh", "token2abcdefgh"]);
  assert.ok(data.opens.every((o) => Number.isFinite(o.t) && o.ua.startsWith("UA")));
  assert.equal(env.OPENS.store.size, 3);
});

test("no secret configured means the api stays closed", async () => {
  const { env, call } = setup();
  env.API_SECRET = undefined;
  assert.equal((await call("/api/opens", { authorization: "Bearer " })).status, 401);
});
