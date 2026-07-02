#!/usr/bin/env node
/**
 * Recupera episodi storici di "Il Volo del Mattino" dalle capture storiche
 * (Wayback Machine) del feed RSS ufficiale di deejay.it. Il feed live conserva
 * solo ~2-3 mesi di episodi, ma le vecchie capture del feed contengono link
 * a media.deejay.it che spesso sono ancora raggiungibili (il file resta sul
 * bucket anche se esce dalla finestra del feed).
 *
 * Crea i file markdown mancanti in content/episodi/, nello stesso formato di
 * fetch-episodi.mjs. Idempotente: salta le date gia' presenti.
 *
 * Uso:
 *   node scripts/backfill-wayback.mjs
 */

import { mkdir, readdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EPISODI_DIR = path.join(ROOT, "content", "episodi");

const FEED_URL = "https://www.deejay.it/api/pub/v2/all/rss/itunes/33";
const CDX_URL = `http://web.archive.org/cdx/search/cdx?url=${encodeURIComponent(FEED_URL)}&output=json&collapse=timestamp:8`;
const UA = "Mozilla/5.0 (compatible; IlVoloDellaSeraArchivioBot/1.0; +https://ilvolodelmattino.github.io/)";

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

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

/** Interroga la Wayback Machine per tutte le capture del feed RSS. */
async function fetchSnapshots() {
    const raw = await fetchText(CDX_URL);
    const rows = JSON.parse(raw);
    if (!rows.length) return [];
    const [, ...items] = rows; // la prima riga e' l'header delle colonne
    return items
        .filter((r) => r[4] === "200")
        .map((r) => ({ timestamp: r[1], waybackUrl: `http://web.archive.org/web/${r[1]}/${r[2]}` }));
}

/** Estrae { iso, titolo, audioUrl } da ogni <item> di un feed RSS (parsing regex, come fetch-episodi.mjs). */
function parseFeedItems(xml) {
    const items = [];
    const itemRe = /<item>([\s\S]*?)<\/item>/g;
    let m;
    while ((m = itemRe.exec(xml))) {
        const block = m[1];
        const titleM = block.match(/<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?<\/title>/);
        const pubDateM = block.match(/<pubDate>(.*?)<\/pubDate>/);
        const audioM = block.match(/<enclosure url="([^"]+\.mp3)"/);
        if (!titleM || !pubDateM || !audioM) continue;
        const d = new Date(pubDateM[1]);
        if (isNaN(d)) continue;
        const iso = d.toISOString().slice(0, 10);
        items.push({ iso, titolo: titleM[1].trim(), audioUrl: audioM[1] });
    }
    return items;
}

function buildFrontMatter({ iso, titolo, audioUrl }) {
    const [yyyy, mm, dd] = iso.split("-");
    const isPuntata = /^Puntata del/i.test(titolo);
    const title = isPuntata ? `Puntata del ${dd}/${mm}/${yyyy}` : titolo;
    const lines = [
        "---",
        `title: "${title.replace(/"/g, '\\"')}"`,
        `date: ${iso}`,
        "draft: false",
        `resumen: "Puntata di Il Volo del Mattino del ${dd}/${mm}/${yyyy}, condotta da Fabio Volo su Radio Deejay. Recuperata da una cattura storica del feed (Wayback Machine)."`,
        `audio: "${audioUrl}"`,
        `fonte: "https://web.archive.org/web/2020/${FEED_URL}"`,
    ];
    if (!isPuntata) lines.push(`temi: [${JSON.stringify(titolo)}]`);
    lines.push("---", "");
    return lines.join("\n");
}

async function main() {
    if (!existsSync(EPISODI_DIR)) await mkdir(EPISODI_DIR, { recursive: true });
    const existingFiles = await readdir(EPISODI_DIR, { recursive: true });
    const existing = new Set(existingFiles.filter((f) => f.endsWith(".md")).map((f) => path.basename(f, ".md")));

    console.log("Cerco le capture storiche del feed su web.archive.org...");
    const snapshots = await fetchSnapshots();
    console.log(`Trovate ${snapshots.length} capture del feed.`);

    const byDate = new Map(); // iso -> {titolo, audioUrl}
    for (const snap of snapshots) {
        await sleep(200);
        let xml;
        try {
            xml = await fetchText(snap.waybackUrl);
        } catch (err) {
            console.warn(`⚠️  cattura ${snap.timestamp}: impossibile scaricare (${err.message})`);
            continue;
        }
        const items = parseFeedItems(xml);
        for (const item of items) {
            if (!byDate.has(item.iso)) byDate.set(item.iso, item);
        }
        console.log(`  cattura ${snap.timestamp}: ${items.length} episodi trovati (totale unici finora: ${byDate.size})`);
    }

    const dates = [...byDate.keys()].sort();
    console.log(`\n${dates.length} date uniche trovate nelle capture, dal ${dates[0]} al ${dates[dates.length - 1]}.`);

    let created = 0, skippedExisting = 0, skippedNoAudio = 0;
    for (const iso of dates) {
        if (existing.has(iso)) { skippedExisting++; continue; }
        const item = byDate.get(iso);
        await sleep(150);
        if (!(await urlIsReachable(item.audioUrl))) {
            console.warn(`⚠️  ${iso}: audio non piu' raggiungibile (${item.audioUrl}), salto.`);
            skippedNoAudio++;
            continue;
        }
        const frontMatter = buildFrontMatter({ iso, ...item });
        const filePath = path.join(EPISODI_DIR, `${iso}.md`);
        await writeFile(filePath, frontMatter, "utf8");
        console.log(`✅ Creato content/episodi/${iso}.md`);
        created++;
    }

    console.log(
        `\nFatto. Creati: ${created} · Gia' presenti: ${skippedExisting} · Audio non raggiungibile: ${skippedNoAudio}`
    );
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
