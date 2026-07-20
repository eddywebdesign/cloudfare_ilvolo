#!/usr/bin/env bash
# Autocommit incondizionato di TUTTA data/ - riscritto 2026-07-20 sera dopo aver
# trovato il bug reale: prima di questa versione, sia questo script sia
# autocommit_dati.sh (K16) committavano dalla copia LOCALE del clone git
# (~/ilvolodelmattino/data/), MAI dal share reale dove scrivono davvero la
# trascrizione/classificazione (ILVOLO_DATA_DIR) - due cartelle fisicamente
# separate. Risultato verificato: gli ultimi 20 "Autocommit K16: batch
# trascrizione" toccavano SOLO logs/trascrizioni_log_termico.csv, mai un file
# vero di trascrizione/frammenti, nonostante il messaggio del commit.
#
# Fix reale (non un altro patch sopra il sintomo): data/ del clone git
# sull'OMV ora e' un bind mount della cartella vera dello share
# (/srv/dev-disk-by-uuid-.../ilvolodellasera/data), fatto UNA TANTA a mano
# (vedi memoria project_ilvolodelmattino_pipeline_infra.md). Da quel momento
# "il clone git" e "il share" sono la STESSA cartella fisica - questo script
# non ha piu' bisogno di sapere quali sottocartelle toccare, ne' di fare
# nessuna copia: cio' che classifica_frammenti/estrai_riferimenti scrivono
# e' gia' li'.
#
# Sostituisce sia sync_snapshot_data.ps1 (HP14, disabilitato) sia
# autocommit_dati.sh (K16, disabilitato) - questo e' l'UNICO punto di
# commit/push dei dati di tutto il progetto.
#
# Stesso pattern gia' testato: commit PRIMA di pullare (un working tree
# dirty non deve mai bloccare il pull --rebase).
#
# Uso: bash scripts/linux/autocommit_dati_omv.sh (lanciato da cron)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

LOG="logs/autocommit_dati_omv.log"
mkdir -p logs

ts() { date --iso-8601=seconds; }

git add data/ 2>/dev/null

if ! git diff --cached --quiet; then
  N=$(git diff --cached --name-only | wc -l)
  git commit -m "Autocommit OMV: dati aggiornati ($N file)" --quiet
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
