const errorResponse = (detail, status, code) => Response.json({ detail, code }, {
  status,
  headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
});

function backendOrigin(value, publicOrigin) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/" || url.origin === publicOrigin) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function protect(response) {
  const headers = new Headers(response.headers);
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("X-Frame-Options", "DENY");
  headers.set("Permissions-Policy", "camera=(self), microphone=(), geolocation=()");
  headers.set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api" && !url.pathname.startsWith("/api/")) {
      return protect(await env.ASSETS.fetch(request));
    }
    const origin = backendOrigin(env.BACKEND_ORIGIN, url.origin);
    if (!origin) {
      return protect(errorResponse("O serviço de dados ainda não está configurado. Contacte o administrador.", 503, "backend_not_configured"));
    }
    const target = new URL(origin);
    target.pathname = url.pathname;
    target.search = url.search;
    const headers = new Headers(request.headers);
    for (const name of ["host", "connection", "keep-alive", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]) headers.delete(name);
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", url.protocol.slice(0, -1));
    // Preserve Origin and session cookies so the API keeps its CSRF and tenant checks.
    const forwarded = new Request(target, new Request(request, { headers, redirect: "manual" }));
    let upstream;
    try {
      // Never retry a payment or another write automatically after a network failure.
      upstream = await fetch(forwarded, { cf: { cacheTtl: 0, cacheEverything: false } });
    } catch {
      return protect(errorResponse("Não foi possível contactar o serviço de dados. Verifique a ligação antes de repetir a operação.", 502, "backend_unavailable"));
    }
    const resultHeaders = new Headers(upstream.headers);
    resultHeaders.set("Cache-Control", "no-store");
    const location = resultHeaders.get("Location");
    if (location) {
      const redirect = new URL(location, target);
      if (redirect.origin === origin) {
        resultHeaders.set("Location", url.origin + redirect.pathname + redirect.search + redirect.hash);
      }
    }
    return protect(new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: resultHeaders }));
  },
};
