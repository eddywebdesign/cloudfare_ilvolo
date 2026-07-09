#!/usr/bin/env node
/**
 * Scarica i metadata pubblici (titolo/testo/copertina) dei "video letture"
 * segnalati manualmente in data/video_letture_sources.json e li salva in
 * data/video_letture/<data>.json (stesso pattern derivato/compatto/committato
 * di data/playlist/, data/pillole/, data/frammenti/).
 *
 * Perche' l'input e' manuale: non esiste un indice pubblico crawlabile per
 * i singoli video di YouTube/Facebook/Instagram (vedi memoria progetto,
 * quadro di fattibilita' 2026-07-09) — solo i permalink dei singoli video
 * espongono metadata pubblici, la loro "scoperta" richiede WebSearch mirata
 * o segnalazione diretta, e l'abbinamento a una data episodio richiede
 * verifica umana (non automatizzabile in modo affidabile).
 *
 * Fonti supportate: YouTube (oEmbed ufficiale), Facebook e Instagram
 * (Open Graph meta tag pubblici sui permalink dei singoli post/video).
 * NESSUN login/automazione browser: solo endpoint pubblici, coerente con i
 * ToS delle piattaforme.
 *
 * Uso:
 *   node scripts/genera_video_letture.mjs
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const SOURCES_FILE = path.join(ROOT, "data", "video_letture_sources.json");
const OUTPUT_DIR = path.join(ROOT, "data", "video_letture");
const UA = "Mozilla/5.0 (compatible; IlVoloDellaSeraArchivioBot/1.0)";

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

async function fetchText(url) {
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`HTTP ${res.status} su ${url}`);
    return res.text();
}

/** Decodifica generica delle entita' HTML (nominative + numeriche/esadecimali). */
function decodeHtmlEntities(str) {
    return str
        .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
        .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
        .replace(/&quot;/g, '"')
        .replace(/&#039;|&apos;/g, "'")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&nbsp;/g, " ");
}

/** Estrae un singolo <meta property="og:X" content="..."> da un HTML. */
function extractOgTag(html, prop) {
    const re = new RegExp(`<meta property="og:${prop}"[^>]*content="([^"]*)"`, "i");
    const m = html.match(re);
    if (!m) return null;
    return decodeHtmlEntities(m[1]).trim();
}

/** YouTube: usa l'endpoint oEmbed ufficiale (nessun bot-detection, nessun login). */
async function fetchYouTube(url) {
    const oembedUrl = `https://www.youtube.com/oembed?url=${encodeURIComponent(url)}&format=json`;
    const json = JSON.parse(await fetchText(oembedUrl));
    return {
        piattaforma: "youtube",
        titolo: json.title || null,
        testo: null,
        cover: json.thumbnail_url || null,
        embedHtml: json.html || null,
        url,
    };
}

/** Facebook/Instagram: Open Graph meta tag pubblici sul permalink del post. */
async function fetchOpenGraph(url, piattaforma) {
    const html = await fetchText(url);
    return {
        piattaforma,
        titolo: extractOgTag(html, "title"),
        testo: extractOgTag(html, "description"),
        cover: extractOgTag(html, "image"),
        embedHtml: null,
        url: extractOgTag(html, "url") || url,
    };
}

async function fetchMetadata(url) {
    if (url.includes("youtube.com") || url.includes("youtu.be")) {
        return fetchYouTube(url);
    }
    if (url.includes("instagram.com")) {
        return fetchOpenGraph(url, "instagram");
    }
    if (url.includes("facebook.com")) {
        return fetchOpenGraph(url, "facebook");
    }
    throw new Error(`Piattaforma non riconosciuta per URL: ${url}`);
}

async function main() {
    if (!existsSync(SOURCES_FILE)) {
        console.log(`Nessun file ${SOURCES_FILE}, niente da fare.`);
        return;
    }
    const sources = JSON.parse(await readFile(SOURCES_FILE, "utf8"));
    if (!existsSync(OUTPUT_DIR)) await mkdir(OUTPUT_DIR, { recursive: true });

    console.log(`Elaboro ${sources.length} video letture segnalati...`);
    let aggiunti = 0;
    let saltati = 0;
    let errori = 0;

    for (const { data, url } of sources) {
        const destFile = path.join(OUTPUT_DIR, `${data}.json`);
        let esistenti = [];
        if (existsSync(destFile)) {
            esistenti = JSON.parse(await readFile(destFile, "utf8"));
        }
        if (esistenti.some((v) => v.url === url)) {
            console.log(`  ${data}: ${url} gia' presente, salto`);
            saltati++;
            continue;
        }

        try {
            const meta = await fetchMetadata(url);
            esistenti.push(meta);
            await writeFile(destFile, JSON.stringify(esistenti, null, 2) + "\n", "utf8");
            console.log(`  ${data}: [${meta.piattaforma}] ${meta.titolo || meta.testo || url}`);
            aggiunti++;
        } catch (err) {
            // Un errore di rete/parsing su un singolo link non deve fermare
            // gli altri (stessa lezione imparata con genera_playlist.py).
            console.warn(`  ${data}: errore su ${url} (${err.message}), salto`);
            errori++;
        }
        await sleep(500);
    }

    console.log(`\nFatto. Aggiunti: ${aggiunti} · Gia' presenti: ${saltati} · Errori: ${errori}`);
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
