# Genera i frammenti (turni di parola) da una trascrizione WhisperX.
#
# Input:  data/trascrizioni/<data>.json  (output WhisperX: segments con
#         "start"/"end"/"text"/"speaker")
# Output: data/frammenti/<data>.json     (lista di frammenti, uno per
#         ogni sequenza di segmenti consecutivi con lo stesso speaker)
#
# I campi titolo/tema/tipo/video_url restano vuoti: sono da compilare
# a mano (o dalla community) dopo la generazione automatica. Rilanciare
# lo script su un file gia' arricchito NON sovrascrive questi campi se
# gia' presenti nel file di output esistente.
#
# Merge per SOVRAPPOSIZIONE TEMPORALE + similarita' testo, non per indice
# posizionale (fix 2026-07-24): un merge per id (`data-000`, `data-001`...)
# si rompe silenziosamente se una ritrascrizione cambia il numero/ordine dei
# segmenti (es. nuova config whisperx senza initial_prompt/con min_speakers -
# osservato un cambio da 203 a 227 segmenti sullo STESSO episodio) - l'id
# 5 di prima puo' finire per puntare a un pezzo di testo completamente
# diverso, attaccandogli titolo/tema di un altro frammento senza errori.
#
# Uso: python scripts/genera_frammenti.py [data1 data2 ...]
#      senza argomenti processa tutti i file in data/trascrizioni/.

import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

SOVRAPPOSIZIONE_MIN_FRAZIONE = 0.3  # della durata del frammento esistente
SIMILARITA_TESTO_MIN = 0.5  # difflib ratio, controllo di sicurezza aggiuntivo

ROOT = Path(__file__).resolve().parent.parent
DATI = dati_root(ROOT)
TRASCRIZIONI_DIR = DATI / "trascrizioni"
FRAMMENTI_DIR = DATI / "frammenti"


def raggruppa_per_speaker(segments):
    """Unisce segmenti WhisperX consecutivi con lo stesso speaker in un frammento."""
    frammenti = []
    corrente = None
    for seg in segments:
        speaker = seg.get("speaker", "SCONOSCIUTO")
        testo = seg.get("text", "").strip()
        if corrente and corrente["speaker_raw"] == speaker:
            corrente["end"] = seg["end"]
            corrente["testo"] += " " + testo
        else:
            if corrente:
                frammenti.append(corrente)
            corrente = {
                "start": seg["start"],
                "end": seg["end"],
                "speaker_raw": speaker,
                "testo": testo,
            }
    if corrente:
        frammenti.append(corrente)
    return frammenti


def genera(data_str):
    src = TRASCRIZIONI_DIR / f"{data_str}.json"
    if not src.exists():
        print(f"  manca {src}, salto")
        return

    trascrizione = json.loads(src.read_text(encoding="utf-8"))
    grezzi = raggruppa_per_speaker(trascrizione["segments"])

    dest = FRAMMENTI_DIR / f"{data_str}.json"
    esistenti = []
    if dest.exists():
        esistenti = [f for f in json.loads(dest.read_text(encoding="utf-8")) if f.get("titolo")]

    def _trova_corrispondenza(nuovo_start: float, nuovo_end: float, nuovo_testo: str) -> dict:
        """Trova il frammento esistente (gia' classificato) che si sovrappone di piu'
        nel tempo con il nuovo frammento, con una verifica extra di similarita' testo
        per evitare falsi positivi su segmenti che si toccano solo ai bordi."""
        migliore, migliore_overlap = None, 0.0
        for f in esistenti:
            overlap = min(nuovo_end, f["end"]) - max(nuovo_start, f["start"])
            if overlap <= 0:
                continue
            durata_esistente = f["end"] - f["start"]
            if durata_esistente <= 0:
                continue
            frazione = overlap / durata_esistente
            if frazione > migliore_overlap:
                migliore_overlap = frazione
                migliore = f
        if migliore is None or migliore_overlap < SOVRAPPOSIZIONE_MIN_FRAZIONE:
            return {}
        similarita = difflib.SequenceMatcher(None, migliore["testo"], nuovo_testo).ratio()
        if similarita < SIMILARITA_TESTO_MIN:
            return {}
        return migliore

    frammenti = []
    for i, g in enumerate(grezzi):
        fid = f"{data_str}-{i:03d}"
        testo = g["testo"].strip()
        prec = _trova_corrispondenza(g["start"], g["end"], testo)
        frammenti.append({
            "id": fid,
            "start": round(g["start"], 2),
            "end": round(g["end"], 2),
            "speaker_raw": g["speaker_raw"],
            "testo": testo,
            "titolo": prec.get("titolo", ""),
            "tema": prec.get("tema", []),
            "tipo": prec.get("tipo", ""),
            "video_url": prec.get("video_url", ""),
        })

    FRAMMENTI_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {data_str}: {len(frammenti)} frammenti -> {dest}")


def main():
    if len(sys.argv) > 1:
        date_list = sys.argv[1:]
    else:
        date_list = sorted(p.stem for p in TRASCRIZIONI_DIR.glob("*.json"))

    print(f"Genero frammenti per {len(date_list)} puntate...")
    for d in date_list:
        genera(d)


if __name__ == "__main__":
    main()
