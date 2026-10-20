import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "../cloudflare/worker.mjs";

const app = "https://gpgest.example.com";
const origin = "https://api.example.com";

test("frontend and SPA routes use static assets", async () => {
  for (const path of ["/", "/plataforma", "/assets/app.js", "/api-example"]) {
    const request = new Request(app + path);
    const response = await worker.fetch(request, { ASSETS: { fetch: incoming => {
      assert.equal(incoming, request);
      return new Response("frontend");
    } } });
    assert.equal(await response.text(), "frontend");
  }
});

test("public legal routes block third-party scripts and frames", async () => {
  const response = await worker.fetch(new Request(app + "/legal/cookies"), { ASSETS: { fetch: () => new Response("legal page") } });
  assert.equal(await response.text(), "legal page");
  assert.equal(response.headers.get("Referrer-Policy"), "no-referrer");
  const policy = response.headers.get("Content-Security-Policy");
  assert.ok(policy.includes("script-src 'self'"));
  assert.ok(policy.includes("frame-src 'self' blob:"));
  assert.ok(!policy.includes("google.com"));
  assert.equal(response.headers.get("Permissions-Policy"), "camera=(self), microphone=(), geolocation=()");
});

test("API routes never return the SPA when the backend is missing or invalid", async () => {
  for (const value of [undefined, "", "http://api.example.com", origin + "/api", origin + "?secret=example", "https://user:example@api.example.com", app]) {
    for (const path of ["/api", "/api/bootstrap", "/api/documents/attachments/example/view"]) {
      const response = await worker.fetch(new Request(app + path), { BACKEND_ORIGIN: value });
      assert.equal(response.status, 503);
      assert.equal((await response.json()).code, "backend_not_configured");
      assert.equal(response.headers.get("Cache-Control"), "no-store");
    }
  }
});

test("JSON writes preserve authentication, origin, path and query", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (request, options) => {
    calls++;
    assert.equal(request.url, origin + "/api/liquidity/settle/example?store_id=demo");
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("Origin"), app);
    assert.equal(request.headers.get("Cookie"), "gpgest_session=demo-session");
    assert.equal(request.headers.get("X-Forwarded-Host"), "gpgest.example.com");
    assert.equal(request.headers.get("X-Forwarded-Proto"), "https");
    assert.equal(request.redirect, "manual");
    assert.deepEqual(await request.json(), { request_key: "demo", amount_cents: 100 });
    assert.equal(options.cf.cacheTtl, 0);
    return Response.json({ confirmed: true });
  });
  const response = await worker.fetch(new Request(app + "/api/liquidity/settle/example?store_id=demo", {
    method: "POST", headers: { "Content-Type": "application/json", Origin: app, Cookie: "gpgest_session=demo-session" },
    body: JSON.stringify({ request_key: "demo", amount_cents: 100 }),
  }), { BACKEND_ORIGIN: origin });
  assert.deepEqual(await response.json(), { confirmed: true });
  assert.equal(calls, 1);
});

test("multipart PDF uploads reach the backend without changing their content", async t => {
  const form = new FormData();
  form.append("file", new Blob(["%PDF-demo"], { type: "application/pdf" }), "demo.pdf");
  t.mock.method(globalThis, "fetch", async request => {
    const incoming = await request.formData();
    assert.equal(incoming.get("file").name, "demo.pdf");
    assert.equal(await incoming.get("file").text(), "%PDF-demo");
    return Response.json({ saved: true });
  });
  assert.equal((await worker.fetch(new Request(app + "/api/scans", { method: "POST", body: form }), { BACKEND_ORIGIN: origin })).status, 200);
});

test("cookies and protected PDF responses remain intact and are not cached", async t => {
  const headers = new Headers({ "Content-Type": "application/pdf", "Content-Disposition": 'inline; filename="demo.pdf"', "Cache-Control": "public, max-age=3600" });
  headers.append("Set-Cookie", "gpgest_session=demo; HttpOnly; Secure; SameSite=Strict; Path=/");
  headers.append("Set-Cookie", "gpgest_company=demo; HttpOnly; Secure; SameSite=Strict; Path=/");
  t.mock.method(globalThis, "fetch", async () => new Response("%PDF-demo", { headers }));
  const response = await worker.fetch(new Request(app + "/api/documents/attachments/example/view"), { BACKEND_ORIGIN: origin });
  assert.equal(await response.text(), "%PDF-demo");
  assert.equal(response.headers.get("Content-Type"), "application/pdf");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(response.headers.getSetCookie(), headers.getSetCookie());
});

test("backend validation and authentication failures are preserved", async t => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "Origem do pedido não permitida." }, { status: 403 }));
  const response = await worker.fetch(new Request(app + "/api/auth/login", { method: "POST", headers: { Origin: "https://other.example.com" } }), { BACKEND_ORIGIN: origin });
  assert.equal(response.status, 403);
  assert.equal((await response.json()).detail, "Origem do pedido não permitida.");
});

test("same-backend redirects return to the public host without following them", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 307, headers: { Location: origin + "/api/example/?id=demo" } }));
  const response = await worker.fetch(new Request(app + "/api/example?id=demo"), { BACKEND_ORIGIN: origin });
  assert.equal(response.status, 307);
  assert.equal(response.headers.get("Location"), app + "/api/example/?id=demo");
});

test("network failure returns a clear error and never retries a write", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("private upstream error"); });
  const response = await worker.fetch(new Request(app + "/api/payments", { method: "POST", body: "demo" }), { BACKEND_ORIGIN: origin });
  assert.equal(response.status, 502);
  assert.equal((await response.json()).code, "backend_unavailable");
  assert.equal(calls, 1);
});
