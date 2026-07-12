#!/usr/bin/env node
/**
 * Le puntate 2013/2014/2019/2021/2026 il cui campo `audio` e' stato riscritto
 * per puntare ad archive.org (poi diventato dark/403, pipeline chiusa) NON sono
 * perse: il campo `fonte` di quei file registra da dove sono state prese la
 * PRIMA volta (pagina deejay.it diretta per backfill-brute-dates.mjs, cattura
 * Wayback del feed RSS per backfill-wayback.mjs). Verificato con test reali che
 * ri-scaricando quella fonte oggi ed estraendo un mp3 fresco con la stessa
 * regex, l'audio e' ancora vivo (solo il path legacy e' cambiato nel tempo).
 *
 * A differenza di backfill-brute-dates.mjs/backfill-wayback.mjs (che SALTANO
 * le date gia' presenti), questo script lavora SOLO su date gia' presenti con
 * `audio` che punta ad archive.org, e ne aggiorna il front matter esistente
 * (non lo ricrea da zero, preserva titolo/temi/resumen/durata gia' compilati).
 *
 * Scarica SOLO in locale (AUDIO_BACKUP_DIR), MAI upload/ri-upload su archive.org.
 *
 * Uso:
 *   $env:AUDIO_BACKUP_DIR = "\\192.168.8.80\Media\ilvolo-audio-backup"
 *   node scripts/recupera_audio_da_fonte.mjs [--limit N]
 */

import { mkdir, readdir, readFile, writeFile, appendFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EPISODI_DIR = path.join(ROOT, "content", "episodi");
const LOGS_DIR = path.join(ROOT, "logs");
const REPORT_PATH = path.join(LOGS_DIR, "recupero_122_report.txt");
const AUDIO_BACKUP_DIR = process.env.AUDIO_BACKUP_DIR;

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

function extractAudioUrl(html) {
    const m = html.match(/"(https:\/\/media\.deejay\.it\/[^"]+\.mp3)"/) || html.match(/(https:\/\/media\.deejay\.it\/[^"'\s]+\.mp3)/);
    return m ? m[1] : null;
}

function parseFeedItems(xml) {
    const items = [];
    const itemRe = /<item>([\s\S]*?)<\/item>/g;
    let m;
    while ((m = itemRe.exec(xml))) {
        const block = m[1];
        const pubDateM = block.match(/<pubDate>(.*?)<\/pubDate>/);
        const audioM = block.match(/<enclosure url="([^"]+\.mp3)"/);
        if (!pubDateM || !audioM) continue;
        const d = new Date(pubDateM[1]);
        if (isNaN(d)) continue;
        items.push({ iso: d.toISOString().slice(0, 10), audioUrl: audioM[1] });
    }
    return items;
}

/** Legge front matter grezzo (regex, nessuna dipendenza YAML) di un file .md. */
async function readFrontMatter(mdPath) {
    const text = await readFile(mdPath, "utf8");
    const audioM = text.match(/^audio:\s*"?([^"\n]+)"?\s*$/m);
    const fonteM = text.match(/^fonte:\s*"?([^"\n]+)"?\s*$/m);
    return { text, audio: audioM ? audioM[1] : null, fonte: fonteM ? fonteM[1] : null };
}

/** Sostituisce la riga 'audio:' e rimuove la riga 'archivio_audio_url:' se presente. */
async function aggiornaFrontMatter(mdPath, text, nuovoAudioUrl) {
    let nuovo = text.replace(/^audio:.*$/m, `audio: "${nuovoAudioUrl}"`);
    nuovo = nuovo.replace(/^archivio_audio_url:.*\n/m, "");
    await writeFile(mdPath, nuovo, "utf8");
}

/** Cerca un file gia' presente per questa data OVUNQUE nell'albero di backup_dir (il
 * backup e' organizzato in sottocartelle per anno) — controllare solo destPath in root
 * fa perdere i file gia' li' dentro e li riscarica come doppioni. */
function trovaEsistente(backupDir, iso) {
    const stack = [backupDir];
    while (stack.length) {
        const dir = stack.pop();
        let entries;
        try {
            entries = readdirSync(dir, { withFileTypes: true });
        } catch {
            continue;
        }
        for (const e of entries) {
            const full = path.join(dir, e.name);
            if (e.isDirectory()) stack.push(full);
            else if (e.name.startsWith(`${iso}_`) || e.name.includes(iso.replace(/-/g, ""))) return full;
        }
    }
    return null;
}

async function scarica(url, destPath, iso, backupDir) {
    const esistente = trovaEsistente(backupDir, iso);
    if (esistente) return `gia' presente altrove nell'albero: ${esistente}`;
    if (existsSync(destPath)) return "gia' presente";
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`download fallito HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    await writeFile(destPath, buf);
    return `scaricato (${(buf.length / 1e6).toFixed(1)} MB)`;
}

async function main() {
    const args = Object.fromEntries(
        process.argv.slice(2).map((a) => {
            const [k, v] = a.replace(/^--/, "").split("=");
            return [k, v ?? true];
        })
    );
    const limit = args.limit ? parseInt(args.limit, 10) : Infinity;

    if (!AUDIO_BACKUP_DIR) {
        console.error("ERRORE: imposta AUDIO_BACKUP_DIR prima di lanciare lo script.");
        process.exit(1);
    }

    const files = (await readdir(EPISODI_DIR, { recursive: true })).filter((f) => f.endsWith(".md"));
    const target = [];
    for (const f of files) {
        const mdPath = path.join(EPISODI_DIR, f);
        const fm = await readFrontMatter(mdPath);
        if (fm.audio && fm.audio.startsWith("https://archive.org/")) {
            target.push({ iso: path.basename(f, ".md"), mdPath, ...fm });
        }
    }
    target.sort((a, b) => a.iso.localeCompare(b.iso));
    console.log(`${target.length} puntate con audio su archive.org trovate, elaboro fino a ${limit === Infinity ? "tutte" : limit}.`);

    const diretti = target.filter((t) => t.fonte && t.fonte.startsWith("https://www.deejay.it/"));
    const waybackTarget = target.filter((t) => t.fonte && t.fonte.startsWith("https://web.archive.org/"));

    // Pre-carica la mappa data->audioUrl dalle capture Wayback UNA sola volta, se serve.
    let waybackByDate = new Map();
    if (waybackTarget.length) {
        console.log(`Cerco le capture storiche del feed su web.archive.org (per ${waybackTarget.length} puntate)...`);
        const rows = JSON.parse(await fetchText(CDX_URL));
        const snapshots = rows.slice(1)
            .filter((r) => r[4] === "200")
            .map((r) => ({ timestamp: r[1], waybackUrl: `http://web.archive.org/web/${r[1]}/${r[2]}` }));
        console.log(`Trovate ${snapshots.length} capture del feed.`);
        for (const snap of snapshots) {
            await sleep(200);
            let xml;
            try {
                xml = await fetchText(snap.waybackUrl);
            } catch {
                continue;
            }
            for (const item of parseFeedItems(xml)) {
                if (!waybackByDate.has(item.iso)) waybackByDate.set(item.iso, item.audioUrl);
            }
        }
        console.log(`Mappate ${waybackByDate.size} date uniche dalle capture.`);
    }

    await mkdir(LOGS_DIR, { recursive: true });
    let recuperate = 0, irrecuperabili = [];
    let processati = 0;

    async function processa(t, audioUrlGrezzo) {
        if (processati >= limit) return;
        processati++;
        console.log(`${t.iso}:`);
        if (!audioUrlGrezzo) {
            console.log("  nessun audio estratto dalla fonte");
            irrecuperabili.push(`${t.iso}: nessun audio estratto`);
            return;
        }
        await sleep(150);
        if (!(await urlIsReachable(audioUrlGrezzo))) {
            console.log(`  audio non raggiungibile: ${audioUrlGrezzo}`);
            irrecuperabili.push(`${t.iso}: audio non raggiungibile (${audioUrlGrezzo})`);
            return;
        }
        const destPath = path.join(AUDIO_BACKUP_DIR, `${t.iso}_${path.basename(audioUrlGrezzo)}`);
        try {
            const esito = await scarica(audioUrlGrezzo, destPath, t.iso, AUDIO_BACKUP_DIR);
            await sleep(8000); // pausa cortese dopo ogni download, nessuna fretta
            console.log(`  ${esito} -> ${destPath}`);
            await aggiornaFrontMatter(t.mdPath, t.text, audioUrlGrezzo);
            console.log("  front matter aggiornato (audio nuovo, archivio_audio_url rimosso)");
            recuperate++;
        } catch (err) {
            console.log(`  ERRORE: ${err.message}`);
            irrecuperabili.push(`${t.iso}: errore download (${err.message})`);
        }
    }

    for (const t of diretti) {
        if (processati >= limit) break;
        await sleep(300);
        let audioUrlGrezzo = null;
        try {
            const html = await fetchText(t.fonte);
            audioUrlGrezzo = extractAudioUrl(html);
        } catch (err) {
            console.log(`${t.iso}: errore fetch fonte (${err.message})`);
        }
        await processa(t, audioUrlGrezzo);
    }
    for (const t of waybackTarget) {
        if (processati >= limit) break;
        await processa(t, waybackByDate.get(t.iso) || null);
    }

    const report = [
        `Recupero eseguito ${new Date().toISOString()}`,
        `Recuperate: ${recuperate}`,
        `Irrecuperabili: ${irrecuperabili.length}`,
        ...irrecuperabili,
        "",
    ].join("\n");
    await appendFile(REPORT_PATH, report, "utf8");
    console.log(`\nFatto. Recuperate: ${recuperate} · Irrecuperabili: ${irrecuperabili.length} (dettagli in ${REPORT_PATH})`);
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
