#!/usr/bin/env bash
# Autocommit incondizionato dei dati che il K16 NON copre (data/riferimenti,
# data/pillole, data/playlist) - creato 2026-07-20 per sostituire
# sync_snapshot_data.ps1 (HP14), che falliva per due motivi reali risolti in
# quella sessione (working tree sporco, encoding log) piu' un terzo mai
# risolvibile su un portatile: la tarea Windows ha
# DisallowStartIfOnBatteries/StopIfGoingOnBatteries, quindi se l'HP14 gira a
# batteria semplicemente non parte. L'OMV e' sempre acceso e sempre a
# corrente, nessuno di questi problemi si applica qui.
#
# NON tocca data/trascrizioni ne' data/frammenti: quelle le committa gia' il
# K16 (ilvolo-autocommit.timer, ogni 20 min) - farlo anche qui duplicherebbe
# gli autocommit delle stesse cartelle da due macchine diverse.
#
# Stesso pattern gia' testato in scripts/linux/autocommit_dati.sh (K16):
# commit PRIMA di pullare, cosi' un working tree dirty non blocca mai il
# pull --rebase con "hai modifiche non salvate".
#
# Uso: bash scripts/linux/autocommit_dati_omv.sh (lanciato da cron)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

LOG="logs/autocommit_dati_omv.log"
mkdir -p logs

ts() { date --iso-8601=seconds; }

git add data/riferimenti data/pillole data/playlist 2>/dev/null

if ! git diff --cached --quiet; then
  N=$(git diff --cached --name-only | wc -l)
  git commit -m "Autocommit OMV: dati riferimenti/pillole/playlist ($N file)" --quiet
else
  N=0
fi

git pull --rebase --quiet 2>>"$LOG"
if [[ $? -ne 0 ]]; then
  echo "$(ts) ERRORE: git pull --rebase fallito, salto il push (probabile conflitto vero, va risolto a mano)." | tee -a "$LOG"
  exit 1
fi

if [[ "$N" -eq 0 ]]; then
  echo "$(ts) Nessuna modifica da committare." >> "$LOG"
  exit 0
fi

if git push --quiet 2>>"$LOG"; then
  echo "$(ts) Committati e pushati $N file." | tee -a "$LOG"
else
  echo "$(ts) ERRORE: git push fallito ($N file committati in locale, da ripushare al prossimo giro)." | tee -a "$LOG"
  exit 1
fi
