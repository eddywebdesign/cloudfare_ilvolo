/**
 * Worker che serve il sito statico (Hugo build in /public) e gestisce
 * l'unico endpoint dinamico: POST /api/ricordo, il form "Condividi il
 * tuo ricordo" della sezione Contribuisci. Richiede il secret
 * RESEND_API_KEY (wrangler secret put RESEND_API_KEY).
 */
const DEST_EMAIL = "ilvolodellasera.web@gmail.com";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/ricordo" && request.method === "POST") {
      return handleRicordo(request, env);
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
    }),
  });

  if (!resp.ok) {
    return json({ ok: false, error: "Invio fallito" }, 502);
  }
  return json({ ok: true });
}
