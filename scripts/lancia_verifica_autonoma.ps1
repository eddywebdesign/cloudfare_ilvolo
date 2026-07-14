# Verifica qualita' settimanale autonoma: gira verifica_frammenti.py su tutti i
# frammenti classificati, autocommit+push incondizionato del solo report (NON
# corregge/cancella nulla da solo, coerente con verifica_frammenti.py). Autorizzato
# dall'utente il 2026-07-14, pensato per girare da Task Scheduler senza supervisione.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\lancia_verifica_autonoma.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\verifica_autonoma.log"
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
python scripts\verifica_frammenti.py 2>>$Log
Scrivi "verifica_frammenti.py terminato (exit $LASTEXITCODE)."

git add logs\frammenti_dubbi.json 2>$null
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
