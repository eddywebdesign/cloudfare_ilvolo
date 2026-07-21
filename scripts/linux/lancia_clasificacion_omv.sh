#!/usr/bin/env bash
# Clasificación/verificación autónoma corriendo EN EL PROPIO servidor OMV
# (creado 2026-07-17, punto 4 del plan de centralización). A diferencia de
# lancia_classificazione_autonoma.ps1 (HP14), este script NO hace git
# add/commit/push de datos: el OMV ES la fuente central del share
# (\\192.168.8.80\Media\ilvolodellasera\data\), no necesita subir nada a git
# para que HP14/K16 lo vean — solo escribe directo al share local.
#
# Uso: bash scripts/linux/lancia_clasificacion_omv.sh (lanzado por cron)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
source .venv/bin/activate

export ILVOLO_DATA_DIR="/srv/dev-disk-by-uuid-ea50b37b-a1a7-42a4-b88c-eace6e82365f/ilvolodellasera/data"
export GROQ_API_KEY="$(cat ~/'API GROQ IA.txt')"
export CEREBRAS_API_KEY="$(cat ~/'API Cerebras.txt')"
export GEMINI_API_KEY="$(cat ~/'API_google_AI.txt')"

LOG="logs/clasificacion_omv.log"
mkdir -p logs
ts() { date --iso-8601=seconds; }

# Aggiunto 2026-07-18: K16 gira con --skip-classify, quindi NON estrae mai i
# riferimenti (libri/film/musica) dei nuovi episodi trascritti. Senza questo
# passo il buco cresce di un episodio ogni volta che K16 ne finisce uno.
# Va PRIMA di riclassifica_frammenti.py cosi' i riferimenti nuovi entrano
# subito anche nel giro di verifica_riferimenti.py dello stesso run.
echo "$(ts) Avvio estrai_riferimenti_nuovi.py..." >> "$LOG"
python3 -u scripts/estrai_riferimenti_nuovi.py >> "$LOG" 2>&1
rc0=$?
echo "$(ts) estrai_riferimenti_nuovi.py terminato (exit $rc0)." >> "$LOG"

echo "$(ts) Avvio riclassifica_frammenti.py..." >> "$LOG"
python3 -u scripts/riclassifica_frammenti.py >> "$LOG" 2>&1
rc1=$?
echo "$(ts) riclassifica_frammenti.py terminato (exit $rc1)." >> "$LOG"

echo "$(ts) Avvio verifica_frammenti.py..." >> "$LOG"
python3 -u scripts/verifica_frammenti.py >> "$LOG" 2>&1
rc2=$?
echo "$(ts) verifica_frammenti.py terminato (exit $rc2)." >> "$LOG"

# Aggiunto 2026-07-21: Hugo legge il badge "da rivedere" SOLO da
# data/frammenti_dubbi.json (.Site.Data.frammenti_dubbi), mai da
# logs/frammenti_dubbi.json (dove scrive davvero verifica_frammenti.py, sullo
# share). Prima di oggi questa copia la faceva lancia_verifica_autonoma.ps1 su
# HP14 - macchina uscita dal processo automatico la sera del 20/07, quindi la
# copia si era fermata e il badge era tornato stantio senza che nessuno se ne
# accorgesse. Ora la fa direttamente questo script, cosi' arriva anche in git
# tramite autocommit_dati_omv.sh (git add data/, ogni 20 min).
cp "$(dirname "$ILVOLO_DATA_DIR")/logs/frammenti_dubbi.json" data/frammenti_dubbi.json 2>>"$LOG"
echo "$(ts) data/frammenti_dubbi.json aggiornato per Hugo." >> "$LOG"

# Aggiunto 2026-07-18: verifica anche i riferimenti storici (libri/film/musica),
# mai riprocessati dopo il fix di ancoraggio del 17/07. Solo segnalazione
# (logs/riferimenti_dubbi.json), non cancella/modifica nulla da solo.
echo "$(ts) Avvio verifica_riferimenti.py..." >> "$LOG"
python3 -u scripts/verifica_riferimenti.py >> "$LOG" 2>&1
rc3=$?
echo "$(ts) verifica_riferimenti.py terminato (exit $rc3)." >> "$LOG"

# Aggiunto 2026-07-18: completa il reprocessamento dei riferimenti storici non
# ancorati (vedi logs/riferimenti_non_ancorati.json, generato una tantum da
# controlla_ancoraggio_riferimenti.py). Idempotente: salta da solo le date già
# sistemate, riprende da dove si era fermato per budget l'ultima volta.
echo "$(ts) Avvio reprocessa_riferimenti_dubbi.py..." >> "$LOG"
python3 -u scripts/reprocessa_riferimenti_dubbi.py >> "$LOG" 2>&1
rc4=$?
echo "$(ts) reprocessa_riferimenti_dubbi.py terminato (exit $rc4)." >> "$LOG"

# Aggiunto 2026-07-18: lancia_classificazione_autonoma.ps1 (HP14) scriveva
# logs/estado_clasificacion.json ad ogni run - il pannello K16 lo legge per
# mostrare "ultima esecuzione". Da quando la classificazione gira qui (OMV),
# nessuno lo aggiornava piu': il pannello mostrava per sempre l'ultimo errore
# di HP14 (git pull fallito, ore prima del passaggio a OMV). Riscritto qui
# senza BOM (python open() di default, a differenza di PowerShell Out-File
# che lo aggiungeva e rompeva il parser JSON di Hugo altrove nel progetto).
python3 -c "
import json
from pathlib import Path
frammenti_dir = Path('$ILVOLO_DATA_DIR') / 'frammenti'
logs_dir = Path('$ILVOLO_DATA_DIR').parent / 'logs'
classificati = sum(
    1 for f in frammenti_dir.glob('*.json')
    for x in json.loads(f.read_text(encoding='utf-8'))
    if x.get('tipo')
)
resultato = 'ok' if $rc1 == 0 and $rc2 == 0 else 'error'
json.dump({
    'resultado': resultato,
    'archivos_clasificados': classificati,
    'ultima_ejecucion': '$(ts)',
    'mensaje': 'estrai_riferimenti_nuovi=$rc0 riclassifica=$rc1 verifica_frammenti=$rc2 verifica_riferimenti=$rc3 reprocessa_riferimenti=$rc4',
}, open(logs_dir / 'estado_clasificacion.json', 'w', encoding='utf-8'))
"
echo "$(ts) estado_clasificacion.json aggiornato." >> "$LOG"
