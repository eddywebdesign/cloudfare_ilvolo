# Sincronizza uno snapshot dei dati dal share centrale OMV
# (\\192.168.8.80\Media\ilvolodellasera\data\, vedi dati_root.py) dentro al
# repo locale (data\) e lo committa/pusha su git.
#
# Perche' serve: dal 2026-07-17 i dati (frammenti/pillole/riferimenti/playlist/
# trascrizioni) vivono SOLO sul share OMV, non piu' in data\ del repo. Il
# runner di GitHub Actions (fetch-episodi.yml, cron 09:30 UTC) pero' non ha
# accesso alla LAN/OMV — quindi prima di ogni build serve uno snapshot
# committato in git. Questo script va eseguito su HP14 (unica macchina sempre
# disponibile) via Task Scheduler, PRIMA delle 09:30 UTC.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\sync_snapshot_data.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\sync_snapshot_data.log"
$ShareData = "\\192.168.8.80\Media\ilvolodellasera\data"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

function Scrivi($msg) {
    "$ts $msg" | Out-File -FilePath $Log -Append -Encoding utf8
}

function Push-ConReintento($n) {
    git push --quiet 2>>$Log
    if ($LASTEXITCODE -eq 0) { return $true }

    Scrivi "AVVISO: git push fallito al primo tentativo, provo pull --rebase + repush..."
    git pull --rebase --quiet 2>>$Log
    if ($LASTEXITCODE -ne 0) {
        Scrivi "ERRORE: pull --rebase di recupero fallito. $n file committati in locale, NON pushati."
        return $false
    }
    git push --quiet 2>>$Log
    if ($LASTEXITCODE -eq 0) {
        Scrivi "Committati e pushati $n file (tras reintento)."
        return $true
    }
    Scrivi "ERRORE: git push fallito tras reintento ($n file committati en local, NO pushati)."
    return $false
}

if (-not (Test-Path $ShareData)) {
    Scrivi "ERRORE: share OMV non raggiungibile ($ShareData), salto questo giro."
    exit 1
}

git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: git pull --rebase fallito, salto questo giro."
    exit 1
}

# Mirror SOLO delle 5 sottocartelle di contenuto gestite dal share centrale —
# MAI l'intera data\, che contiene anche file locali non centralizzati
# (admin.json, config.json con OMDB key, contribuitori.json,
# estado_clasificacion.json, letture_fabio/, video_letture/): un /MIR sulla
# radice li cancellerebbe, perche' non esistono sul share (verificato con un
# dry-run /L prima di scrivere questa versione — vedi mai fidarsi di /MIR
# senza controllare prima cosa segnala come "EXTRA").
$rcMax = 0
foreach ($sub in @("trascrizioni", "frammenti", "pillole", "riferimenti", "playlist")) {
    Scrivi "Avvio robocopy mirror $ShareData\$sub -> data\$sub..."
    robocopy "$ShareData\$sub" "data\$sub" /MIR /R:2 /W:5 /NFL /NDL /NP /LOG+:$Log
    if ($LASTEXITCODE -gt $rcMax) { $rcMax = $LASTEXITCODE }
}
# robocopy: exit code < 8 = successo (0-7 sono tutti "ok" con vari dettagli, vedi doc MS)
if ($rcMax -ge 8) {
    Scrivi "ERRORE: robocopy fallito (exit code $rcMax), salto il commit."
    exit 1
}
Scrivi "robocopy completato (exit code max $rcMax)."

git add data
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Scrivi "Nessuna modifica nello snapshot da committare."
    exit 0
}

$n = (git diff --cached --name-only | Measure-Object -Line).Lines
git commit -m "Snapshot automatico data/ dal share OMV ($n file)" --quiet
Scrivi "Committati $n file."

if (Push-ConReintento $n) {
    exit 0
} else {
    exit 1
}
