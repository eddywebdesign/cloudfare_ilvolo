#!/usr/bin/env bash
# Watchdog periodico: rileva se il mount CIFS del NAS e' "fantasma" (montato in
# tabella ma vuoto/irraggiungibile, tipico dopo un riavvio dell'OMV mentre il K16
# resta acceso) e lo ripara da solo, poi rilancia il batch di trascrizione se
# risultava fermo per lo stesso motivo. Richiede la regola sudoers dedicata
# (/etc/sudoers.d/ilvolo-nas-remount) per umount/mount senza password — MAI sudo
# generico, solo quei due comandi specifici.
#
# Uso: bash scripts/linux/watchdog_nas.sh (lanciato da un timer periodico, non a mano)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

ROOT="${ILVOLO_AUDIO_ROOT:-/mnt/ilvolo-audio-backup}"
LOG="logs/watchdog_nas.log"
mkdir -p logs

ts() { date --iso-8601=seconds; }

# Mount "sano" = almeno una cartella anno visibile (4 cifre)
ANNI=$(ls "$ROOT" 2>/dev/null | grep -cE '^[0-9]{4}$')

if [[ "$ANNI" -eq 0 ]]; then
  echo "$(ts) Mount vuoto/irraggiungibile ($ANNI cartelle anno), tento riparazione..." | tee -a "$LOG"
  sudo umount -l "$ROOT" 2>&1 | tee -a "$LOG"
  sudo mount -a 2>&1 | tee -a "$LOG"
  sleep 3
  ANNI=$(ls "$ROOT" 2>/dev/null | grep -cE '^[0-9]{4}$')
  if [[ "$ANNI" -eq 0 ]]; then
    echo "$(ts) ERRORE: mount ancora vuoto dopo il tentativo di riparazione (NAS/OMV forse ancora giu')." | tee -a "$LOG"
    exit 1
  fi
  echo "$(ts) Mount riparato, $ANNI cartelle anno visibili." | tee -a "$LOG"
fi

# Il batch e' vivo se la sessione tmux esiste E un processo whisperx/trascrivi_locale_episodi gira
#
# NOTA IMPORTANTE: l'output del batch (whisperx e' MOLTO verboso: progress bar,
# log pyannote/tqdm) va rediretto su file, MAI lasciato scrivere libero sul pty
# del pane tmux. Nessuno apre mai questa sessione (K16 headless) - se il buffer
# del pty si riempie, QUALSIASI processo che scriva su quella stessa terminale
# (incluso sensori_temp.py, lanciato in background nello stesso pane) si blocca
# in attesa di spazio, indefinitamente. Successo il 2026-07-15: il logger
# termico e' rimasto vivo ma fermo per quasi 3 ore, scatenando false alarme
# "log termico fermo" via email ogni 15 min senza che nulla si fosse rotto
# davvero - solo bloccato su una print() sul pty pieno.
COMANDO_BATCH="cd '$REPO' && source ~/ilvolo-env/bin/activate && export ILVOLO_AUDIO_ROOT='$ROOT' && bash scripts/linux/avvia_trascrizione_sicura.sh '' --skip-classify > logs/consola_batch.log 2>&1"

if ! tmux has-session -t trascrizione 2>/dev/null; then
  echo "$(ts) Sessione tmux 'trascrizione' assente, la ricreo e rilancio il batch." | tee -a "$LOG"
  tmux new-session -d -s trascrizione -n 0 "$COMANDO_BATCH; bash"
  exit 0
fi

if ! pgrep -f "trascrivi_locale_episodi.py|whisperx" > /dev/null; then
  echo "$(ts) Sessione tmux presente ma nessun processo di trascrizione attivo, rilancio il batch." | tee -a "$LOG"
  tmux send-keys -t trascrizione "$COMANDO_BATCH" Enter
fi
