#!/usr/bin/env bash
# Autocommit incondizionato dei SOLI dati di batch (trascrizioni/frammenti grezzi
# prodotti dal K16), autorizzato esplicitamente dall'utente il 2026-07-14 per
# rendere l'intera pipeline autonoma senza intervento umano/Claude (fine abbonamento
# Pro). Eccezione gia' concessa in precedenza restava condizionata a conferma manuale
# per ogni lancio — ora e' incondizionata: nessuna conferma richiesta, gira da un
# timer systemd. Tocca SOLO data/trascrizioni, data/frammenti, logs/ (mai codice,
# mai contenuti pubblicati sul sito).
#
# Uso: bash scripts/linux/autocommit_dati.sh (lanciato da un timer periodico)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

LOG="logs/autocommit_dati.log"
mkdir -p logs

ts() { date --iso-8601=seconds; }

# Pull prima di committare: il portatile scrive sugli stessi file (classificazione),
# --rebase evita merge commit inutili su un repo di soli dati.
git pull --rebase --quiet 2>>"$LOG"
if [[ $? -ne 0 ]]; then
  echo "$(ts) ERRORE: git pull --rebase fallito, salto questo giro (probabile conflitto, va risolto a mano)." | tee -a "$LOG"
  exit 1
fi

git add data/trascrizioni data/frammenti logs/trascrizioni_log_termico.csv 2>/dev/null

if git diff --cached --quiet; then
  echo "$(ts) Nessuna modifica da committare." >> "$LOG"
  exit 0
fi

N=$(git diff --cached --name-only | wc -l)
git commit -m "Autocommit K16: batch trascrizione ($N file)" --quiet
if git push --quiet 2>>"$LOG"; then
  echo "$(ts) Committati e pushati $N file." | tee -a "$LOG"
else
  echo "$(ts) ERRORE: git push fallito ($N file committati in locale, da ripushare al prossimo giro)." | tee -a "$LOG"
  exit 1
fi
