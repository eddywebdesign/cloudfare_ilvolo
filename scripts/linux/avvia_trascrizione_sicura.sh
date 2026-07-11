#!/usr/bin/env bash
# Equivalente Linux di avvia_trascrizione_sicura.ps1. Essendo il K16 un mini PC
# dedicato senza coperchio, non serve gestire la sospensione ad ogni lancio:
# va disattivata UNA VOLA in fase di setup con:
#   sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
# Questo script si limita a: controllare i sensori, avviare il logger termico in
# background, lanciare la trascrizione di UNA puntata e attendere, scrivendo sempre
# l'esito in logs/ultima_esecuzione.log (niente popup, e' headless).
#
# USO:
#   bash scripts/linux/avvia_trascrizione_sicura.sh [cartella] [--da YYYYMMDD] [--limit N]
# Default: cartella 2016, riprende da dove si e' fermato (--da 20160120), --limit 1.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

CARTELLA="${1:-D:/Docs/il_volo_del_mattino/Volo del mattino/audio/2016}"
DA="20160120"
LIMIT=1

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --da) DA="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

mkdir -p logs

echo "Controllo sensori disponibili..."
if ! python3 scripts/linux/sensori_temp.py > /dev/null 2>&1; then
  echo "ATTENZIONE: sensori non disponibili (lm-sensors non installato o sensors-detect non eseguito)." | tee -a logs/ultima_esecuzione.log
  echo "Procedo comunque, ma senza monitoraggio termico." | tee -a logs/ultima_esecuzione.log
  SENSORI_OK=0
else
  SENSORI_OK=1
fi

LOGGER_PID=""
if [[ "$SENSORI_OK" -eq 1 ]]; then
  echo "Avvio il logger termico in background..."
  python3 scripts/linux/sensori_temp.py --loop 60 logs/trascrizioni_log_termico.csv &
  LOGGER_PID=$!
fi

cleanup() {
  if [[ -n "$LOGGER_PID" ]]; then
    kill "$LOGGER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "=== Avvio trascrizione: $LIMIT puntata/e da $CARTELLA (da $DA) ===" | tee -a logs/ultima_esecuzione.log
python3 scripts/trascrivi_locale_episodi.py "$CARTELLA" --da "$DA" --limit "$LIMIT" --threads 8
EXIT_CODE=$?

TS="$(date --iso-8601=seconds)"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "$TS Trascrizione completata senza errori." | tee -a logs/ultima_esecuzione.log
else
  echo "$TS ATTENZIONE: trascrizione fermata con codice $EXIT_CODE." | tee -a logs/ultima_esecuzione.log
fi

exit "$EXIT_CODE"
