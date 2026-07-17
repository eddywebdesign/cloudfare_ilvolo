# Classificazione notturna autonoma: classifica i frammenti non ancora
# titolati. Autorizzato esplicitamente dall'utente il 2026-07-14 per rendere
# la pipeline indipendente da Claude/abbonamento Pro. Nessuna conferma
# richiesta: pensato per girare da Task Scheduler senza nessuno collegato.
#
# STORIA: fino al 2026-07-17 questo script faceva autocommit+push di
# data\frammenti e data\riferimenti su git, perche' era l'unico modo per far
# arrivare i risultati al K16 (che li leggeva dopo un git pull). Dal
# 2026-07-17 i dati vivono in \\192.168.8.80\Media\ilvolodellasera\ (share
# OMV), montata sia da HP14 che da K16 via CIFS: riclassifica_frammenti.py
# scrive gia' direttamente li' (tramite ILVOLO_DATA_DIR, vedi dati_root.py),
# quindi non c'e' piu' nulla da committare — data\frammenti/data\riferimenti
# locali restano vuoti/invariati, e l'autocommit e' stato rimosso (il vecchio
# `git add $EstadoPath` falliva comunque, perche' EstadoPath punta ormai
# fuori dal repo). Il `git pull` iniziale resta solo per non lavorare su un
# checkout di content/episodi/*.md vecchio rispetto al workflow giornaliero.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\lancia_classificazione_autonoma.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\classificazione_autonoma.log"
if ($env:ILVOLO_DATA_DIR) {
    $EstadoPath = Join-Path (Split-Path $env:ILVOLO_DATA_DIR -Parent) "logs\estado_clasificacion.json"
} else {
    $EstadoPath = "data\estado_clasificacion.json"
}
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

function Scrivi($msg) {
    "$ts $msg" | Out-File -FilePath $Log -Append -Encoding utf8
}

function Escribe-Estado($resultado, $archivos, $mensaje) {
    $estado = @{
        ultima_ejecucion   = $ts
        resultado          = $resultado
        archivos_clasificados = $archivos
        mensaje            = $mensaje
    }
    $dir = Split-Path $EstadoPath -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $estado | ConvertTo-Json | Out-File -FilePath $EstadoPath -Encoding utf8
}

git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: git pull --rebase fallito, salto questo giro."
    Escribe-Estado "error" 0 "git pull --rebase fallito"
    exit 1
}

Scrivi "Avvio riclassifica_frammenti.py..."
python -u scripts\riclassifica_frammenti.py *>> $Log
$exitClassificazione = $LASTEXITCODE
Scrivi "riclassifica_frammenti.py terminato (exit $exitClassificazione)."

$resultado = if ($exitClassificazione -eq 0) { "ok" } else { "error" }
Escribe-Estado $resultado 0 "riclassifica_frammenti.py exit $exitClassificazione"

exit $exitClassificazione
