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

LOG="logs/clasificacion_omv.log"
mkdir -p logs
ts() { date --iso-8601=seconds; }

echo "$(ts) Avvio riclassifica_frammenti.py..." >> "$LOG"
python3 -u scripts/riclassifica_frammenti.py >> "$LOG" 2>&1
rc1=$?
echo "$(ts) riclassifica_frammenti.py terminato (exit $rc1)." >> "$LOG"

echo "$(ts) Avvio verifica_frammenti.py..." >> "$LOG"
python3 -u scripts/verifica_frammenti.py >> "$LOG" 2>&1
rc2=$?
echo "$(ts) verifica_frammenti.py terminato (exit $rc2)." >> "$LOG"
