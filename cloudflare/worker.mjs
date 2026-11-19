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

// O túnel/Cloudflare devolve páginas HTML nos erros de ligação (530/1033 túnel desligado, 524 mais de 100 s
// sem resposta) e o FastAPI devolve texto simples nos erros não tratados. A app só lê JSON: converter aqui
// para uma mensagem concreta em vez de «o servidor não respondeu como esperado».
function upstreamError(status) {
  if ([504, 522, 524].includes(status)) {
    return errorResponse("O servidor demorou demasiado a responder (limite de 100 segundos do túnel). Se era uma pergunta ao assistente, torne-a mais concreta (loja, fornecedor ou período) e volte a tentar.", 504, "backend_timeout");
  }
  if ([502, 503, 520, 521, 523, 530].includes(status)) {
    return errorResponse("O serviço de dados não está disponível. Confirme que o PC, a API e o túnel estão ligados e que BACKEND_ORIGIN aponta para o endereço atual do túnel.", 502, "backend_unavailable");
  }
  return errorResponse(`O servidor encontrou um erro interno (código ${status}). Tente novamente; se persistir, consulte os registos da API.`, status >= 500 ? status : 502, "backend_error");
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
    const type = upstream.headers.get("Content-Type") || "";
    if (upstream.status >= 500 && !type.includes("json")) {
      await upstream.body?.cancel();
      return protect(upstreamError(upstream.status));
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
