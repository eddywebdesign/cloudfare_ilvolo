#!/usr/bin/env node
/**
 * Recoge le puntate di "Il Volo del Mattino" da deejay.it e crea i file
 * markdown mancanti in content/episodi/. Idempotente: salta le puntate
 * il cui file esiste già (aggiorna solo i tag se mancanti), così può
 * girare ogni giorno senza duplicare nulla.
 *
 * Fonti:
 *   - /puntate/        → audio + data di ogni puntata completa
 *   - /highlights/      → frammenti tematici con titolo reale, collegati
 *                          per data alla puntata completa → usati come tag
 *
 * Uso:
 *   node scripts/fetch-episodi.mjs
 */

import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EPISODI_DIR = path.join(ROOT, "content", "episodi");

const LISTING_URL = "https://www.deejay.it/programmi/il-volo-del-mattino/puntate/";
const HIGHLIGHTS_BASE = "https://www.deejay.it/programmi/il-volo-del-mattino/highlights/";
const HIGHLIGHTS_MAX_PAGES = 500; // scrape storico completo: la paginazione si ferma da sola alla prima pagina vuota
const UA =
    "Mozilla/5.0 (compatible; IlVoloDellaSeraArchivioBot/1.0; +https://ilvolodelmattino.github.io/)";

const MESI_IT = {
    gennaio: "01", febbraio: "02", marzo: "03", aprile: "04",
    maggio: "05", giugno: "06", luglio: "07", agosto: "08",
    settembre: "09", ottobre: "10", novembre: "11", dicembre: "12",
};

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

/** "5 giugno 2026" → "2026-06-05" */
function parseDataItaliana(testo) {
    const m = testo.trim().match(/(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})/i);
    if (!m) return null;
    const [, giorno, mese, anno] = m;
    const mm = MESI_IT[mese.toLowerCase()];
    if (!mm) return null;
    return `${anno}-${mm}-${giorno.padStart(2, "0")}`;
}

/** Estrae { titolo, dataIso, url, durata } da ogni pillola di una pagina /highlights/. */
function parseHighlightsPage(html) {
    const items = [];
    const re = /<h1 class="title small red"><a href="([^"]*)">([^<]+)<\/a><\/h1>\s*<div class="service-title text small">\s*<strong>dalla puntata del:<\/strong>\s*([^<]+?)<br\s*\/>\s*<strong>durata:<\/strong>\s*([0-9:]+)min/g;
    let match;
    while ((match = re.exec(html))) {
        const [, url, titoloRaw, dataRaw, durata] = match;
        const dataIso = parseDataItaliana(dataRaw);
        if (dataIso) items.push({ titolo: titoloRaw.trim(), dataIso, url, durata });
    }
    return items;
}

/** Costruisce una mappa dataIso → [{titolo,url,durata}] scorrendo le pagine /highlights/. */
async function fetchHighlightsMap() {
    const map = new Map();
    for (let page = 1; page <= HIGHLIGHTS_MAX_PAGES; page++) {
        const url = page === 1 ? HIGHLIGHTS_BASE : `${HIGHLIGHTS_BASE}page/${page}/`;
        await sleep(250);
        let html;
        try {
            html = await fetchText(url);
        } catch {
            break; // pagina inesistente o errore di rete → fine paginazione
        }
        const items = parseHighlightsPage(html);
        if (items.length === 0) break;
        for (const pillola of items) {
            if (!map.has(pillola.dataIso)) map.set(pillola.dataIso, []);
            const lista = map.get(pillola.dataIso);
            if (!lista.some((p) => p.url === pillola.url)) lista.push(pillola);
        }
    }
    return map;
}

/** Scrive data/pillole/<dataIso>.json per ogni data della mappa (stesso pattern
 *  idempotente/compatto/committato di data/playlist/ e data/frammenti/): non
 *  sovrascrive un file gia' presente, cosi' un rilancio non perde eventuali
 *  arricchimenti manuali futuri. */
async function scriviPilloleData(map) {
    const PILLOLE_DIR = path.join(ROOT, "data", "pillole");
    if (!existsSync(PILLOLE_DIR)) await mkdir(PILLOLE_DIR, { recursive: true });
    let scritti = 0;
    let saltati = 0;
    for (const [dataIso, pillole] of map.entries()) {
        const filePath = path.join(PILLOLE_DIR, `${dataIso}.json`);
        if (existsSync(filePath)) {
            saltati++;
            continue;
        }
        const payload = pillole.map(({ titolo, url, durata }) => ({ titolo, url, durata }));
        await writeFile(filePath, JSON.stringify(payload, null, 2) + "\n", "utf8");
        scritti++;
    }
    console.log(`Pillole: ${scritti} file data/pillole/*.json scritti, ${saltati} già presenti.`);
}

function yamlStringArray(arr) {
    return `[${arr.map((t) => JSON.stringify(t)).join(", ")}]`;
}

function buildFrontMatter({ iso, dd, mm, yyyy, audioUrl, fonte, durataMin, tags }) {
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
        `fonte: "${fonte}"`
    );
    if (tags && tags.length) lines.push(`temi: ${yamlStringArray(tags)}`);
    lines.push("---", "");
    return lines.join("\n");
}

/** Inserisce una riga `tags:` in un file esistente che non ce l'ha ancora. */
async function aggiornaTagsSeMancanti(filePath, tags) {
    const testo = await readFile(filePath, "utf8");
    if (/^temi:/m.test(testo)) return false; // già presenti, non si sovrascrive
    const rigaTags = `temi: ${yamlStringArray(tags)}\n`;
    const parts = testo.split(/^---\s*$/m);
    if (parts.length < 3) return false; // formato inatteso, non tocchiamo il file
    const nuovoTesto = `---${parts[1]}${rigaTags}---${parts.slice(2).join("---")}`;
    await writeFile(filePath, nuovoTesto, "utf8");
    return true;
}

async function main() {
    if (!existsSync(EPISODI_DIR)) await mkdir(EPISODI_DIR, { recursive: true });
    const existingFiles = await readdir(EPISODI_DIR);
    const existing = new Set(existingFiles.map((f) => f.replace(/\.md$/, "")));

    console.log(`Scarico indice puntate: ${LISTING_URL}`);
    const listingHtml = await fetchText(LISTING_URL);
    const episodes = parseListingDates(listingHtml).sort((a, b) =>
        b.iso.localeCompare(a.iso)
    );

    console.log("Scarico frammenti tematici (highlights) per i tag...");
    const highlightsMap = await fetchHighlightsMap();
    console.log(`Trovati frammenti per ${highlightsMap.size} date diverse.`);
    await scriviPilloleData(highlightsMap);

    console.log(`Trovate ${episodes.length} puntate nell'indice, ${existing.size} già archiviate.`);

    let created = 0;
    let tagsAggiornati = 0;
    let skippedExisting = 0;
    let skippedNoAudio = 0;

    for (const ep of episodes) {
        if (existing.has(ep.iso)) {
            const tags = (highlightsMap.get(ep.iso) || []).map((p) => p.titolo);
            if (tags.length) {
                const filePath = path.join(EPISODI_DIR, `${ep.iso}.md`);
                const aggiornato = await aggiornaTagsSeMancanti(filePath, tags);
                if (aggiornato) {
                    console.log(`🏷️  Aggiunti tag a content/episodi/${ep.iso}.md`);
                    tagsAggiornati++;
                }
            }
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
        const tags = (highlightsMap.get(ep.iso) || []).map((p) => p.titolo);
        const frontMatter = buildFrontMatter({ ...ep, audioUrl, fonte, durataMin, tags });
        const filePath = path.join(EPISODI_DIR, `${ep.iso}.md`);
        await writeFile(filePath, frontMatter, "utf8");
        console.log(`✅ Creato content/episodi/${ep.iso}.md${tags.length ? ` (tag: ${tags.join(", ")})` : ""}`);
        created++;
    }

    console.log(
        `\nFatto. Creati: ${created} · Tag aggiunti a esistenti: ${tagsAggiornati} · Già presenti: ${skippedExisting} · Senza audio valido: ${skippedNoAudio}`
    );
}

main().catch((err) => {
    console.error("Errore fatale:", err);
    process.exit(1);
});
