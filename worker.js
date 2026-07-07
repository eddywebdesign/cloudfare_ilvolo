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

const MAINTENANCE_BG = "/immagini/sito_off_line.jpg";

const MAINTENANCE_PAGE = `<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Il Volo della Sera</title>
<meta name="robots" content="noindex, nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { height: 100%; margin: 0; }
  body {
    font-family: system-ui, sans-serif;
    text-align: center;
    color: #f0e8d6;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 1.5rem;
    background: linear-gradient(rgba(8,9,15,0.72), rgba(8,9,15,0.72)), url('${MAINTENANCE_BG}') center/cover no-repeat;
  }
  .box { max-width: 34rem; }
  h1 { font-size: 1.6rem; }
  p { opacity: 0.85; }
</style>
</head>
<body>
<div class="box">
<h1>Sito temporaneamente non disponibile</h1>
<p>In attesa di autorizzazione dei contenuti da parte degli aventi diritto.</p>
</div>
</body>
</html>`;

const PREVIEW_COOKIE = "ivds_preview";
const PREVIEW_DURATION_SEC = 86400; // 1 giorno
const PREVIEW_MAX_VISITS = 2; // oltre le 2 visite, il cookie non basta piu'

// Il cookie codifica token, numero di visite gia' consumate e scadenza
// assoluta: "<token>.<visite>.<scadenzaUnix>". La scadenza si fissa alla
// prima entrata e non si allunga alle visite successive.
function parsePreviewCookie(request, env) {
  const cookie = request.headers.get("Cookie") || "";
  const match = cookie.match(new RegExp(`${PREVIEW_COOKIE}=([^;]+)`));
  if (!match) return null;
  const [token, visitsStr, expiryStr] = decodeURIComponent(match[1]).split(".");
  const visits = parseInt(visitsStr, 10);
  const expiry = parseInt(expiryStr, 10);
  if (!env.PREVIEW_TOKEN || token !== env.PREVIEW_TOKEN) return null;
  if (!Number.isFinite(visits) || !Number.isFinite(expiry)) return null;
  if (Date.now() / 1000 > expiry) return null;
  return { visits, expiry };
}

function setPreviewCookie(env, visits, expiry) {
  return `${PREVIEW_COOKIE}=${env.PREVIEW_TOKEN}.${visits}.${expiry}; Path=/; Expires=${new Date(expiry * 1000).toUTCString()}; HttpOnly; Secure; SameSite=Lax`;
}

// Una "visita" si conta solo sulla navigazione di pagina (non sui singoli
// asset tipo CSS/JS/immagini), altrimenti un solo caricamento di pagina
// esaurirebbe subito le 2 visite consentite.
function isNavigationRequest(request, url) {
  const mode = request.headers.get("Sec-Fetch-Mode");
  if (mode) return mode === "navigate";
  return request.method === "GET" && !/\.[a-zA-Z0-9]+$/.test(url.pathname);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/ricordo" && request.method === "POST") {
      return handleRicordo(request, env);
    }

    // Accesso riservato: /?preview=<token> concede un nuovo blocco di
    // PREVIEW_MAX_VISITS visite valide per PREVIEW_DURATION_SEC. Il token
    // vero vive SOLO nel secret PREVIEW_TOKEN (wrangler/API), mai nel codice.
    const previewParam = url.searchParams.get("preview");
    if (previewParam && env.PREVIEW_TOKEN && previewParam === env.PREVIEW_TOKEN) {
      url.searchParams.delete("preview");
      const expiry = Math.floor(Date.now() / 1000) + PREVIEW_DURATION_SEC;
      return new Response(null, {
        status: 302,
        headers: {
          Location: url.pathname + url.search,
          "Set-Cookie": setPreviewCookie(env, 1, expiry),
        },
      });
    }

    const preview = parsePreviewCookie(request, env);
    const previewOk = preview && preview.visits <= PREVIEW_MAX_VISITS;

    if (MAINTENANCE && !previewOk) {
      // l'immagine di sfondo della pagina di manutenzione deve restare
      // raggiungibile anche col sito "chiuso", altrimenti il browser
      // richiederebbe di nuovo questa stessa funzione fetch() in loop
      // e riceverebbe 503 invece dell'immagine.
      if (url.pathname === MAINTENANCE_BG) {
        return env.ASSETS.fetch(request);
      }
      return new Response(MAINTENANCE_PAGE, {
        status: 503,
        headers: { "content-type": "text/html; charset=utf-8", "X-Robots-Tag": "noindex" },
      });
    }

    const response = await env.ASSETS.fetch(request);

    // Solo le navigazioni di pagina consumano una visita, e solo se il
    // limite non e' ancora stato raggiunto in questa richiesta.
    if (preview && preview.visits < PREVIEW_MAX_VISITS && isNavigationRequest(request, url)) {
      const newResponse = new Response(response.body, response);
      newResponse.headers.append("Set-Cookie", setPreviewCookie(env, preview.visits + 1, preview.expiry));
      return newResponse;
    }

    return response;
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
