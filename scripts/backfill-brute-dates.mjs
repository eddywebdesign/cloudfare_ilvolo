#!/usr/bin/env node
/**
 * Recupera episodi storici provando direttamente la pagina di ogni giorno
 * feriale (lun-ven) di deejay.it dal 30/09/2013 (rilancio del programma) a
 * oggi, indipendentemente dal fatto che compaiano nella listing o nel feed.
 * La pagina, quando esiste, contiene sempre il link audio reale (schema
 * "legacy" o moderno che sia) — a differenza di provare a indovinare
 * direttamente l'URL del file, che si e' rivelato inaffidabile.
 *
 * Idempotente: salta le date gia' presenti in content/episodi/. Rispettoso:
 * pausa tra le richieste.
 *
 * Uso:
 *   node scripts/backfill-brute-dates.mjs [--from 2013-09-30] [--to oggi]
 */

import { mkdir, readdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EPISODI_DIR = path.join(ROOT, "content", "episodi");
const UA = "Mozilla/5.0 (compatible; IlVoloDellaSeraArchivioBot/1.0; +https://ilvolodelmattino.github.io/)";

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

async function fetchText(url, retries = 3) {
    for (let attempt = 1; attempt <= retries; attempt++) {
        try {
            const res = await fetch(url, { headers: { "User-Agent": UA } });
            if (!res.ok) return null;
            return await res.text();
        } catch (err) {
            if (attempt === retries) {
                console.warn(`⚠️  errore di rete su ${url} dopo ${retries} tentativi: ${err.message}`);
                return null;
            }
            await sleep(2000 * attempt);
        }
    }
    return null;
}

async function urlIsReachable(url) {
    try {
        const res = await fetch(url, { method: "GET", headers: { "User-Agent": UA, Range: "bytes=0-0" } });
        return res.ok || res.status === 206;
    } catch {
        return false;
    }
}

function extractAudioUrl(html) {
    const m = html.match(/"(https:\/\/media\.deejay\.it\/[^"]+\.mp3)"/);
    return m ? m[1] : null;
}

function extractDurataMin(html) {
    const m = html.match(/"trt_lenght_sec":"([0-9.]+)"/);
    if (!m) return null;
    return Math.round(parseFloat(m[1]) / 60);
}

function buildFrontMatter({ iso, dd, mm, yyyy, audioUrl, fonte, durataMin }) {
    const titleDate = `${dd}/${mm}/${yyyy}`;
    const lines = [
        "---",
        `title: "Puntata del ${titleDate}"`,
        `date: ${iso}`,
        "draft: false",
    ];
    if (durataMin) lines.push(`durata: "${durataMin} min"`);
    lines.push(
        `resumen: "Puntata di Il Volo del Mattino del ${titleDate}, condotta da Fabio Volo su Radio Deejay. Recuperata provando direttamente la pagina della puntata."`,
        `audio: "${audioUrl}"`,
        `fonte: "${fonte}"`,
        "---", ""
    );
    return lines.join("\n");
}

function* weekdaysBetween(from, to) {
    const d = new Date(from);
    while (d <= to) {
        const day = d.getUTCDay(); // 0=dom, 6=sab
        if (day !== 0 && day !== 6) yield new Date(d);
        d.setUTCDate(d.getUTCDate() + 1);
    }
}

async function main() {
    const args = Object.fromEntries(
        process.argv.slice(2).map((a) => {
            const [k, v] = a.replace(/^--/, "").split("=");
            return [k, v ?? true];
        })
    );
    const from = new Date(args.from || "2013-09-30T00:00:00Z");
    const to = args.to ? new Date(args.to) : new Date();

    if (!existsSync(EPISODI_DIR)) await mkdir(EPISODI_DIR, { recursive: true });
    const existingFiles = await readdir(EPISODI_DIR, { recursive: true });
    const existing = new Set(existingFiles.filter((f) => f.endsWith(".md")).map((f) => path.basename(f, ".md")));

    const dates = [...weekdaysBetween(from, to)];
    console.log(`${dates.length} giorni feriali da controllare, dal ${from.toISOString().slice(0, 10)} al ${to.toISOString().slice(0, 10)}.`);

    let created = 0, skippedExisting = 0, notFound = 0, noAudio = 0;
    for (const d of dates) {
        const iso = d.toISOString().slice(0, 10);
        if (existing.has(iso)) { skippedExisting++; continue; }

        try {
            const [yyyy, mm, dd] = iso.split("-");
            const slug = `il-volo-del-mattino-del-${dd}-${mm}-${yyyy}`;
            const fonte = `https://www.deejay.it/programmi/il-volo-del-mattino/puntate/${slug}/`;

            await sleep(300);
            const html = await fetchText(fonte);
            if (!html) { notFound++; continue; }

            const audioUrl = extractAudioUrl(html);
            if (!audioUrl) { noAudio++; continue; }

            await sleep(100);
            if (!(await urlIsReachable(audioUrl))) { noAudio++; continue; }

            const durataMin = extractDurataMin(html);
            const frontMatter = buildFrontMatter({ iso, dd, mm, yyyy, audioUrl, fonte, durataMin });
            await writeFile(path.join(EPISODI_DIR, `${iso}.md`), frontMatter, "utf8");
            console.log(`✅ Creato content/episodi/${iso}.md`);
            created++;
        } catch (err) {
            console.warn(`⚠️  ${iso}: errore imprevisto (${err.message}), salto.`);
        }
    }

    console.log(
        `\nFatto. Creati: ${created} · Gia' presenti: ${skippedExisting} · Pagina inesistente: ${notFound} · Senza audio valido: ${noAudio}`
    );
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
