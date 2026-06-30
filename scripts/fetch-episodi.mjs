#!/usr/bin/env node
/**
 * Recoge le puntate di "Il Volo del Mattino" da deejay.it e crea i file
 * markdown mancanti in content/episodi/. Idempotente: salta le puntate
 * il cui file esiste già, così può girare ogni giorno senza duplicare nulla.
 *
 * Uso:
 *   node scripts/fetch-episodi.mjs
 */

import { mkdir, readdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EPISODI_DIR = path.join(ROOT, "content", "episodi");

const LISTING_URL = "https://www.deejay.it/programmi/il-volo-del-mattino/puntate/";
const UA =
    "Mozilla/5.0 (compatible; IlVoloDellaSeraArchivioBot/1.0; +https://ilvolodelmattino.github.io/)";

async function fetchText(url) {
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`HTTP ${res.status} su ${url}`);
    return res.text();
}

async function urlIsReachable(url) {
    try {
        const res = await fetch(url, { method: "GET", headers: { "User-Agent": UA, Range: "bytes=0-0" } });
        return res.ok || res.status === 206;
    } catch {
        return false;
    }
}

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

/** Estrae le date delle puntate elencate nella pagina indice (DD-MM-YYYY → {y,m,d}). */
function parseListingDates(html) {
    const re = /il-volo-del-mattino-del-(\d{2})-(\d{2})-(\d{4})/g;
    const seen = new Map();
    let match;
    while ((match = re.exec(html))) {
        const [, dd, mm, yyyy] = match;
        const key = `${yyyy}-${mm}-${dd}`;
        if (!seen.has(key)) seen.set(key, { dd, mm, yyyy });
    }
    return [...seen.entries()].map(([iso, parts]) => ({ iso, ...parts }));
}

/** Estrae il primo URL .mp3 di media.deejay.it trovato nella pagina della puntata. */
function extractAudioUrl(html) {
    const m = html.match(/"(https:\/\/media\.deejay\.it\/[^"]+\.mp3)"/);
    return m ? m[1] : null;
}

/** Durata in minuti, se disponibile nei metadati Omny embeddati nella pagina. */
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
        `resumen: "Puntata di Il Volo del Mattino del ${titleDate}, condotta da Fabio Volo su Radio Deejay."`,
        `audio: "${audioUrl}"`,
        `fonte: "${fonte}"`,
        "---",
        ""
    );
    return lines.join("\n");
}

async function main() {
    if (!existsSync(EPISODI_DIR)) await mkdir(EPISODI_DIR, { recursive: true });
    const existing = new Set(
        (await readdir(EPISODI_DIR)).map((f) => f.replace(/\.md$/, ""))
    );

    console.log(`Scarico indice puntate: ${LISTING_URL}`);
    const listingHtml = await fetchText(LISTING_URL);
    const episodes = parseListingDates(listingHtml).sort((a, b) =>
        b.iso.localeCompare(a.iso)
    );

    console.log(`Trovate ${episodes.length} puntate nell'indice, ${existing.size} già archiviate.`);

    let created = 0;
    let skippedExisting = 0;
    let skippedNoAudio = 0;

    for (const ep of episodes) {
        if (existing.has(ep.iso)) {
            skippedExisting++;
            continue;
        }

        const slug = `il-volo-del-mattino-del-${ep.dd}-${ep.mm}-${ep.yyyy}`;
        const fonte = `https://www.deejay.it/programmi/il-volo-del-mattino/puntate/${slug}/`;

        await sleep(300); // rispetto verso il server di deejay.it
        let html;
        try {
            html = await fetchText(fonte);
        } catch (err) {
            console.warn(`⚠️  ${ep.iso}: impossibile scaricare la pagina (${err.message})`);
            continue;
        }

        const audioUrl = extractAudioUrl(html);
        if (!audioUrl) {
            console.warn(`⚠️  ${ep.iso}: nessun audio_url trovato, salto.`);
            skippedNoAudio++;
            continue;
        }
        if (!(await urlIsReachable(audioUrl))) {
            console.warn(`⚠️  ${ep.iso}: audio non raggiungibile (${audioUrl}), salto.`);
            skippedNoAudio++;
            continue;
        }

        const durataMin = extractDurataMin(html);
        const frontMatter = buildFrontMatter({ ...ep, audioUrl, fonte, durataMin });
        const filePath = path.join(EPISODI_DIR, `${ep.iso}.md`);
        await writeFile(filePath, frontMatter, "utf8");
        console.log(`✅ Creato content/episodi/${ep.iso}.md`);
        created++;
    }

    console.log(
        `\nFatto. Creati: ${created} · Già presenti: ${skippedExisting} · Senza audio valido: ${skippedNoAudio}`
    );
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
