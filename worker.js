/**
 * Worker che serve il sito statico (Hugo build in /public) e gestisce
 * l'unico endpoint dinamico: POST /api/ricordo, il form "Condividi il
 * tuo ricordo" della sezione Contribuisci. Richiede il secret
 * RESEND_API_KEY (wrangler secret put RESEND_API_KEY).
 */
// NB: Resend e' in sandbox finche' non si verifica un dominio proprio
// (resend.com/domains) — puo' consegnare solo alla mail del titolare
// dell'account. Cambiare qui in ilvolodellasera.web@gmail.com dopo la
// verifica del dominio.
const DEST_EMAIL = "eddywebdesign2.0@gmail.com";
// re-trigger deploy 2026-07-07

// Sito momentaneamente chiuso al pubblico: in attesa di autorizzazione dei
// contenuti audio da Radio Deejay/Fabio Volo. Rimettere a false per riaprire.
const MAINTENANCE = true;

const MAINTENANCE_PAGE = `<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Il Volo della Sera</title>
<meta name="robots" content="noindex, nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, sans-serif; text-align: center; padding: 4rem 1.5rem; color: #333; }
  h1 { font-size: 1.4rem; }
</style>
</head>
<body>
<h1>Sito temporaneamente non disponibile</h1>
<p>In attesa di autorizzazione dei contenuti da parte degli aventi diritto.</p>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/ricordo" && request.method === "POST") {
      return handleRicordo(request, env);
    }

    if (MAINTENANCE) {
      return new Response(MAINTENANCE_PAGE, {
        status: 503,
        headers: { "content-type": "text/html; charset=utf-8", "X-Robots-Tag": "noindex" },
      });
    }

    return env.ASSETS.fetch(request);
  },
};

async function handleRicordo(request, env) {
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });

  let data;
  try {
    data = await request.json();
  } catch {
    return json({ ok: false, error: "Richiesta non valida" }, 400);
  }

  const nome = String(data.nome || "").slice(0, 200).trim();
  const email = String(data.email || "").slice(0, 200).trim();
  const messaggio = String(data.messaggio || "").slice(0, 5000).trim();

  if (!messaggio) {
    return json({ ok: false, error: "Messaggio mancante" }, 400);
  }
  if (!env.RESEND_API_KEY) {
    return json({ ok: false, error: "Servizio non configurato" }, 500);
  }

  // Allegato opzionale: foto o audio, gia' in base64 lato client. Limite
  // 8MB originali ≈ 11MB di stringa base64 — margine di sicurezza incluso.
  let attachments;
  const allegato = data.allegato;
  if (allegato && typeof allegato.data === "string" && allegato.data.length < 11 * 1024 * 1024) {
    attachments = [
      {
        filename: String(allegato.filename || "allegato").slice(0, 200),
        content: allegato.data,
      },
    ];
  }

  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "Il Volo della Sera <onboarding@resend.dev>",
      to: [DEST_EMAIL],
      reply_to: email || undefined,
      subject: `Nuovo ricordo condiviso da ${nome || "un ascoltatore"}`,
      text: `Nome: ${nome || "(non indicato)"}\nEmail: ${email || "(non indicata)"}\n\n${messaggio}`,
      attachments,
    }),
  });

  if (!resp.ok) {
    return json({ ok: false, error: "Invio fallito" }, 502);
  }
  return json({ ok: true });
}
