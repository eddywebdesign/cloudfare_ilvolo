# Equivalente OMV di check_batch_health.py (K16): check fattuale, standalone,
# indipendente da Claude/app aperta. Eseguito da cron ogni 15 min su OMV.
# Scrive SEMPRE una riga di heartbeat in logs/identificazione_health_log.txt
# e, solo in caso di anomalia, anche logs/identificazione_health_ALERT.txt.
#
# Nato il 2026-07-25 dopo aver trovato che il check K16 misurava solo "processo
# vivo" (whisperx esiste, CPU si muove) invece di "progresso reale" -- durante
# un crash-loop di 7 ore ha scritto "anomalie=0" ogni 15 minuti. Sul lato OMV/
# identificazione non esisteva NESSUN controllo indipendente: se un crash-loop
# equivalente fosse successo li' (es. retry ciechi contro un provider LLM
# saturo, gia' visto il 24/07 con Gemini 429), nessuno se ne sarebbe accorto
# finche' l'utente non l'avesse chiesto esplicitamente.
#
# Scope volutamente ristretto (metodologia concordata il 25/07: agenti piccoli,
# una cosa sola, testati al 110% prima di fidarsene): SOLO rilevare uno stallo
# e fermare il processo che sta girando a vuoto -- NESSUN tentativo di
# diagnosticare/riparare la causa reale (a differenza di check_batch_health.py
# su K16, qui non abbiamo ancora un pattern di causa nota codificabile).
#
# Vincolo esplicito dell'utente per qualunque azione su OMV: opera SOLO dentro
# ~/ilvolodelmattino (il progetto ilvolodellasera) -- mai toccare altro sul
# server (Media/HHD_500gb condivisi con altri usi non del progetto). Questo
# script rispetta il vincolo per costruzione: killa solo processi Python di
# QUESTO progetto (pattern espliciti sotto), non cancella mai file.

import datetime
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = REPO / "logs"
FRAMMENTI_DIR = REPO / "data" / "frammenti"
RIFERIMENTI_DIR = REPO / "data" / "riferimenti"
LOCK_FILE = Path("/tmp/lancia_clasificacion_omv.lock")

SOGLIA_PROGRESSO_MIN = 15  # margine ampio: alcuni step (verifica_riferimenti_esterna.py)
# chiamano API esterne (Open Library/TMDB/MusicBrainz) che possono essere lente, non
# solo LLM -- soglia piu' larga di quella K16 (12 min) apposta per questo.

# Nomi dei processi della pipeline di identificazione (wrapper + tutti gli step
# che lancia_clasificacion_omv.sh invoca in sequenza) -- SOLO questi, mai altro.
# riclassifica_frammenti.py resta in elenco pur essendo uscito dal cron il
# 2026-07-26: se qualcuno lo lancia a mano su singole date deve comunque essere
# sorvegliato/fermabile da qui. La sua assenza NON e' un'anomalia (questa lista
# serve a RILEVARE processi vivi, non a pretendere che ci siano).
PATTERN_PROCESSI = (
    "lancia_clasificacion_omv.sh",
    "estrai_riferimenti_nuovi.py",
    "riclassifica_frammenti.py",
    "verifica_frammenti.py",
    "verifica_riferimenti_esterna.py",
    "verifica_riferimenti.py",
    "reprocessa_riferimenti_dubbi.py",
)


def _pattern_sicuro(pattern: str) -> str:
    """Trucco delle parentesi quadre (gia' documentato nel progetto, vedi
    memoria K16): pgrep/pkill -f matcha contro la riga di comando COMPLETA,
    che include il comando pgrep/pkill stesso se il pattern e' testuale puro
    -- '[l]ancia...' invece di 'lancia...' evita che il processo trovi se
    stesso."""
    return f"[{pattern[0]}]{pattern[1:]}"


def trova_processo_identificazione():
    """Ritorna (nome_pattern, prima_riga_cmdline) del primo processo trovato
    tra quelli della pipeline, o (None, None) se nessuno e' attivo -- normale,
    la pipeline non gira sempre (solo su lancio manuale o cron, oggi disattivato)."""
    for pattern in PATTERN_PROCESSI:
        r = subprocess.run(
            ["pgrep", "-af", _pattern_sicuro(pattern)],
            capture_output=True, text=True, timeout=10,
        )
        if r.stdout.strip():
            return pattern, r.stdout.strip().splitlines()[0]
    return None, None


def ultimo_progresso_reale():
    """Timestamp (epoch) del file piu' recente tra data/frammenti/*.json e
    data/riferimenti/*.json -- segnale diretto di lavoro REALE completato,
    non di un processo che esiste soltanto. None se nessun file trovato."""
    mtimes = []
    for cartella in (FRAMMENTI_DIR, RIFERIMENTI_DIR):
        if not cartella.exists():
            continue
        for f in cartella.glob("*.json"):
            try:
                mtimes.append(f.stat().st_mtime)
            except OSError:
                continue
    return max(mtimes) if mtimes else None


def ferma_pipeline_identificazione() -> tuple[bool, list[str]]:
    """Ferma TUTTI i processi della pipeline (pattern espliciti sopra) e
    rimuove il lock file, cosi' un rilancio successivo (manuale o cron) non
    trova il flock ancora occupato da un processo morto. Verifica reale col
    ricontrollo dopo il kill, non assunta."""
    fermati = []
    for pattern in PATTERN_PROCESSI:
        r = subprocess.run(["pkill", "-f", _pattern_sicuro(pattern)], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            fermati.append(pattern)
    LOCK_FILE.unlink(missing_ok=True)
    time.sleep(1)
    ancora_attivo, _ = trova_processo_identificazione()
    return ancora_attivo is None, fermati


def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    anomalie = []

    pattern_attivo, cmdline = trova_processo_identificazione()

    if pattern_attivo:
        ultimo_mtime = ultimo_progresso_reale()
        if ultimo_mtime is not None:
            minuti = (datetime.datetime.now().timestamp() - ultimo_mtime) / 60
            if minuti > SOGLIA_PROGRESSO_MIN:
                anomalie.append(
                    f"STALLO SOSPETTO: processo '{pattern_attivo}' attivo "
                    f"({cmdline[:150]}) ma nessun file in data/frammenti|riferimenti "
                    f"modificato da {minuti:.0f} min (soglia {SOGLIA_PROGRESSO_MIN})"
                )
                # Primo livello di auto-correzione (stesso principio di
                # check_batch_health.py su K16, richiesto dall'utente il
                # 2026-07-25): fermare lo spreco, NON tentare di indovinare/
                # riparare la causa -- qui non abbiamo ancora un pattern noto
                # come il mismatch driver su K16.
                detenuto, fermati = ferma_pipeline_identificazione()
                if detenuto:
                    anomalie.append(
                        f"AUTO-CORREZIONE ESEGUITA: fermati {', '.join(fermati) or '(nessun processo trovato al momento del kill)'}, "
                        f"lock rimosso. Causa non diagnosticata -- serve capire perche' si e' fermato "
                        f"il progresso prima di rilanciare manualmente."
                    )
                else:
                    anomalie.append(
                        "AUTO-CORREZIONE FALLITA: un processo della pipeline e' ancora attivo dopo "
                        "il tentativo di stop -- serve intervento manuale ORA."
                    )
        # else: nessun file mai trovato in data/frammenti|riferimenti -- non
        # dovrebbe succedere in produzione (migliaia di file esistenti), ma se
        # capitasse non trattarlo come anomalia automatica: potrebbe essere un
        # problema di mount/path diverso da diagnosticare a mano, non un
        # crash-loop da fermare alla cieca.

    riga = f"{ts} | processo_attivo={pattern_attivo or 'nessuno'} anomalie={len(anomalie)}"
    with open(LOGS_DIR / "identificazione_health_log.txt", "a", encoding="utf-8") as f:
        f.write(riga + "\n")

    if anomalie:
        msg = f"{ts} ANOMALIA:\n" + "\n".join(anomalie)
        with open(LOGS_DIR / "identificazione_health_ALERT.txt", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        print(msg)


if __name__ == "__main__":
    main()
