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
# Stesso share/cartella di logs_root() (vedi dati_root.py) dove OMV scrive
# estado_clasificacion.json: il pannello di K16 gia' lo legge da li' via
# ILVOLO_LOGS_DIR, quindi scrivere qui lo stato del push lo rende visibile a
# K16 senza bisogno di SSH ne' di accesso diretto al disco locale di HP14.
$ShareLogs = "\\192.168.8.80\Media\ilvolodellasera\logs"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

function Scrivi($msg) {
    "$ts $msg" | Out-File -FilePath $Log -Append -Encoding utf8
}

function Scrivi-EstadoPush($resultado, $mensaje, $n) {
    $obj = [ordered]@{
        resultado = $resultado
        ultima_ejecucion = $ts
        archivos = $n
        mensaje = $mensaje
    }
    try {
        $obj | ConvertTo-Json -Compress | Out-File -FilePath "$ShareLogs\estado_push.json" -Encoding utf8 -Force
    } catch {
        Scrivi "AVVISO: impossibile scrivere estado_push.json su share OMV: $_"
    }
}

function Push-ConReintento($n) {
    git push --quiet 2>>$Log
    if ($LASTEXITCODE -eq 0) {
        # Successo silenzioso finora: nessuna riga di log distingueva "pushato
        # bene" da "script mai arrivato fin qui" — necessario per la card
        # commit/push del pannello K16 (panel_control.py), che legge
        # estado_push.json per sapere se l'ultimo giro e' andato a buon fine.
        Scrivi "PUSH OK: $n file pushati su GitHub."
        Scrivi-EstadoPush "ok" "$n file pushati su GitHub." $n
        return $true
    }

    Scrivi "AVVISO: git push fallito al primo tentativo, provo pull --rebase + repush..."
    git pull --rebase --quiet 2>>$Log
    if ($LASTEXITCODE -ne 0) {
        Scrivi "ERRORE: pull --rebase di recupero fallito. $n file committati in locale, NON pushati."
        Scrivi-EstadoPush "error" "pull --rebase di recupero fallito, $n file committati in locale NON pushati." $n
        return $false
    }
    git push --quiet 2>>$Log
    if ($LASTEXITCODE -eq 0) {
        Scrivi "PUSH OK: $n file pushati su GitHub (dopo reintento)."
        Scrivi-EstadoPush "ok" "$n file pushati su GitHub (dopo reintento)." $n
        return $true
    }
    Scrivi "ERRORE: git push fallito tras reintento ($n file committati en local, NO pushati)."
    Scrivi-EstadoPush "error" "push fallito dopo reintento, $n file committati in locale NON pushati." $n
    return $false
}

if (-not (Test-Path $ShareData)) {
    Scrivi "ERRORE: share OMV non raggiungibile ($ShareData), salto questo giro."
    Scrivi-EstadoPush "error" "share OMV non raggiungibile, giro saltato." 0
    exit 1
}

# HP14 e' anche dove giro le sessioni Claude Code: e' normale trovare file
# locali sporchi (log, snapshot manuali di data\ per anteprima, ecc.) quando
# questo script scatta. Senza lo stash, un solo file dirty blocca per sempre
# ogni pull successivo (bug reale, 2026-07-17 e 2026-07-19: il push si e'
# fermato al primo comando, niente e' arrivato su GitHub per giorni).
$stashOutput = git stash push --include-untracked --quiet --message "sync_snapshot_data auto-stash" 2>>$Log
$haStash = $LASTEXITCODE -eq 0 -and $stashOutput -notmatch "No local changes to save"

git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: git pull --rebase fallito, salto questo giro."
    Scrivi-EstadoPush "error" "git pull --rebase fallito, giro saltato." 0
    if ($haStash) {
        git stash pop --quiet 2>>$Log
        if ($LASTEXITCODE -ne 0) {
            Scrivi "ATTENZIONE: git stash pop fallito dopo pull fallito, stash lasciato intatto (mai perso lavoro locale)."
        }
    }
    exit 1
}

function Esci-ConStashPop($code) {
    if ($haStash) {
        git stash pop --quiet 2>>$Log
        if ($LASTEXITCODE -ne 0) {
            Scrivi "ATTENZIONE: git stash pop fallito, stash lasciato intatto (mai perso lavoro locale) — controllare 'git stash list' su HP14."
        }
    }
    exit $code
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
    Scrivi-EstadoPush "error" "robocopy fallito (exit code $rcMax), commit saltato." 0
    Esci-ConStashPop 1
}
Scrivi "robocopy completato (exit code max $rcMax)."

git add data
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Scrivi "Nessuna modifica nello snapshot da committare."
    Scrivi-EstadoPush "ok" "nessuna modifica da sincronizzare." 0
    Esci-ConStashPop 0
}

$n = (git diff --cached --name-only | Measure-Object -Line).Lines
git commit -m "Snapshot automatico data/ dal share OMV ($n file)" --quiet
Scrivi "Committati $n file."

if (Push-ConReintento $n) {
    Esci-ConStashPop 0
} else {
    Esci-ConStashPop 1
}
