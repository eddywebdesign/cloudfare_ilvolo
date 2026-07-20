# Verifica qualita' settimanale autonoma: gira verifica_frammenti.py su tutti i
# frammenti classificati, autocommit+push incondizionato del solo report (NON
# corregge/cancella nulla da solo, coerente con verifica_frammenti.py). Autorizzato
# dall'utente il 2026-07-14, pensato per girare da Task Scheduler senza supervisione.
#
# Dal 2026-07-17 verifica_frammenti.py scrive il report in
# \\192.168.8.80\Media\ilvolodellasera\logs\ (share condiviso, vedi dati_root.py)
# invece che in logs\ locale — per mantenere comunque uno storico leggibile via
# git (utile per vedere QUANDO e' stata segnalata ogni allucinazione), questo
# script copia il report nel repo locale prima di committarlo.
#
# ATTENZIONE bug reale trovato il 2026-07-20: il sito Hugo legge il badge "da
# rivedere" da .Site.Data.frammenti_dubbi, che carica SOLO data\frammenti_dubbi.json
# (cartella dati di Hugo) — MAI logs\frammenti_dubbi.json (Hugo non guarda dentro
# logs\). Il report veniva aggiornato per mesi senza che il sito lo mostrasse mai
# (contatore "0 da rivedere" fisso nonostante centinaia di voci nel report reale).
# Ora la copia va SEMPRE fatta in entrambi i posti.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\lancia_verifica_autonoma.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\verifica_autonoma.log"
if ($env:ILVOLO_DATA_DIR) {
    $ReportRemoto = Join-Path (Split-Path $env:ILVOLO_DATA_DIR -Parent) "logs\frammenti_dubbi.json"
} else {
    $ReportRemoto = "logs\frammenti_dubbi.json"
}
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

function Scrivi($msg) {
    "$ts $msg" | Out-File -FilePath $Log -Append -Encoding utf8
}

git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: git pull --rebase fallito, salto questo giro."
    exit 1
}

Scrivi "Avvio verifica_frammenti.py..."
python -u scripts\verifica_frammenti.py *>> $Log
Scrivi "verifica_frammenti.py terminato (exit $LASTEXITCODE)."

if ((Resolve-Path $ReportRemoto -ErrorAction SilentlyContinue) -and
    ((Resolve-Path $ReportRemoto).Path -ne (Join-Path $Repo "logs\frammenti_dubbi.json"))) {
    Copy-Item $ReportRemoto "logs\frammenti_dubbi.json" -Force
}
Copy-Item "logs\frammenti_dubbi.json" "data\frammenti_dubbi.json" -Force

git add logs\frammenti_dubbi.json data\frammenti_dubbi.json 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Scrivi "Nessuna modifica al report da committare."
    exit 0
}

git commit -m "Autocommit report verifica qualita' settimanale" --quiet
git push --quiet 2>>$Log
if ($LASTEXITCODE -eq 0) {
    Scrivi "Report committato e pushato."
} else {
    Scrivi "ERRORE: git push fallito."
}
