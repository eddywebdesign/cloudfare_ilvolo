#!/usr/bin/env bash
# Equivalente Linux di avvia_trascrizione_sicura.ps1. Essendo il K16 un mini PC
# dedicato senza coperchio, non serve gestire la sospensione ad ogni lancio: va
# disattivata UNA VOLTA in fase di setup con:
#   sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
#
# A differenza della versione Windows, questo script lavora su TUTTO l'archivio:
# scorre in ordine ogni sottocartella anno (2012, 2013, ...) dentro la cartella
# radice e passa ciascuna a trascrivi_locale_episodi.py, che gia' salta da solo
# le date completate (vedi data/trascrizioni|frammenti) e sposta ogni mp3 finito
# in <anno>/gia_trascritti/ (stesso meccanismo di Windows).
#
# Sicurezza termica: se lm-sensors non espone temperature, lo script si FERMA
# PRIMA di iniziare (niente rete di sicurezza possibile senza sensori — qui
# ancora piu' importante che su Windows, il K16 gira headless). Se durante il
# run la CPU resta >=93C per 2 letture consecutive, sensori_temp.py stesso
# termina la trascrizione e scrive logs/OVERHEAT_STOP.flag: questo script lo
# rileva e si ferma anche lui, senza passare alla cartella anno successiva.
#
# USO:
#   bash scripts/linux/avvia_trascrizione_sicura.sh [cartella_radice] [--da YYYYMMDD] [--limit N]
# Default: cartella radice da $ILVOLO_AUDIO_ROOT (variabile d'ambiente) o
# /mnt/ilvolo-audio-backup (mount point CIFS del NAS \\192.168.8.80\Media\ilvolo-audio-backup,
# vedi SETUP.md) — adattalo al tuo mount point reale se diverso.
# --da 20160101 (default), --limit 0 (default = tutte le puntate rimanenti di
# OGNI cartella anno prima di passare alla successiva; metti un numero per
# fermarti dopo N puntate PER CARTELLA anno).

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

export ILVOLO_DATA_DIR="${ILVOLO_DATA_DIR:-/mnt/ilvolodellasera-data}"

ROOT="${1:-${ILVOLO_AUDIO_ROOT:-/mnt/ilvolo-audio-backup}}"
DA="20160101"
LIMIT=0
SKIP_CLASSIFY=0

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --da) DA="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --skip-classify) SKIP_CLASSIFY=1; shift ;;
    *) shift ;;
  esac
done

EXTRA_ARGS=()
if [[ "$SKIP_CLASSIFY" -eq 1 ]]; then
  EXTRA_ARGS+=(--skip-classify)
fi

mkdir -p logs

echo "Controllo sensori disponibili..."
if ! python3 scripts/linux/sensori_temp.py > /dev/null 2>&1; then
  echo "ERRORE: sensori non disponibili (lm-sensors non installato o 'sudo sensors-detect --auto' mai eseguito)." | tee -a logs/ultima_esecuzione.log
  echo "ARRESTO: senza sensori nessuna soglia di emergenza puo' rilevare/fermare un surriscaldamento." | tee -a logs/ultima_esecuzione.log
  exit 1
fi

if [[ ! -d "$ROOT" ]]; then
  echo "ERRORE: cartella radice '$ROOT' non trovata (NAS montato? controlla il mount point CIFS)." | tee -a logs/ultima_esecuzione.log
  exit 1
fi

SOGLIA_EMERGENZA_CPU=93
rm -f logs/OVERHEAT_STOP.flag

echo "Avvio il logger termico in background (soglia di emergenza ${SOGLIA_EMERGENZA_CPU}C)..."
python3 scripts/linux/sensori_temp.py --loop 60 logs/trascrizioni_log_termico.csv --kill-cpu "$SOGLIA_EMERGENZA_CPU" &
LOGGER_PID=$!

cleanup() {
  kill "$LOGGER_PID" 2>/dev/null || true
}
trap cleanup EXIT

EXIT_CODE=0
for anno_dir in "$ROOT"/*/; do
  anno="$(basename "$anno_dir")"
  [[ "$anno" =~ ^[0-9]{4}$ ]] || continue  # salta sottocartelle che non sono un anno

  echo "=== Cartella $anno ($anno_dir) ===" | tee -a logs/ultima_esecuzione.log
  python3 -u scripts/trascrivi_locale_episodi.py "$anno_dir" --da "$DA" --limit "$LIMIT" --threads 8 "${EXTRA_ARGS[@]}"
  EXIT_CODE=$?

  if [[ -f logs/OVERHEAT_STOP.flag ]]; then
    echo "ALLARME TEMPERATURA: fermato per surriscaldamento durante $anno." | tee -a logs/ultima_esecuzione.log
    break
  fi
  if [[ "$EXIT_CODE" -ne 0 ]]; then
    echo "ATTENZIONE: cartella $anno terminata con codice $EXIT_CODE, continuo comunque con l'anno successivo." | tee -a logs/ultima_esecuzione.log
  fi
done

TS="$(date --iso-8601=seconds)"
if [[ -f logs/OVERHEAT_STOP.flag ]]; then
  echo "$TS FERMATO per surriscaldamento — vedi logs/OVERHEAT_STOP.flag prima di rilanciare." | tee -a logs/ultima_esecuzione.log
  exit 1
elif [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "$TS Trascrizione completata senza errori (tutte le cartelle anno elaborate)." | tee -a logs/ultima_esecuzione.log
else
  echo "$TS ATTENZIONE: l'ultima cartella elaborata e' terminata con codice $EXIT_CODE." | tee -a logs/ultima_esecuzione.log
fi

exit "$EXIT_CODE"
