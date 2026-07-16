# Classificazione notturna autonoma: pull dati nuovi dal K16, classifica i
# frammenti non ancora titolati, autocommit+push incondizionato dei risultati.
# Autorizzato esplicitamente dall'utente il 2026-07-14 per rendere la pipeline
# indipendente da Claude/abbonamento Pro (in scadenza il 16). Nessuna conferma
# richiesta: pensato per girare da Task Scheduler senza nessuno collegato.
#
# Scrive SEMPRE data\estado_clasificacion.json (trackeado in git, NON in
# logs/) con l'esito dell'ultima esecuzione - a differenza del log (in
# logs/, ignorato da git), questo file arriva al K16 tramite il normale
# flusso git push/pull, cosi' il panel del K16 puo' mostrare anche lo stato
# della classificazione senza connessione diretta tra le due macchine.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\lancia_classificazione_autonoma.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\classificazione_autonoma.log"
$EstadoPath = "data\estado_clasificacion.json"
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
    $estado | ConvertTo-Json | Out-File -FilePath $EstadoPath -Encoding utf8
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

# Committa PRIMA di pullare: mai lasciare file modificati non tracciati (es. da
# un giro precedente interrotto) che bloccherebbero il pull --rebase con "hai
# modifiche non salvate" - stesso fix gia' applicato in autocommit_dati.sh (K16)
# dopo l'incidente del 2026-07-14/15 (backlog di frammenti mai committato che
# bloccava OGNI esecuzione successiva, senza possibilita' di auto-ripararsi).
git add data\frammenti data\riferimenti 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    $nPrevio = (git diff --cached --name-only | Measure-Object -Line).Lines
    git commit -m "Autocommit classificazione notturna, backlog pre-pull ($nPrevio file)" --quiet
    Scrivi "Committati $nPrevio file pendenti di un giro precedente, prima del pull."
}

git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: git pull --rebase fallito, salto questo giro."
    Escribe-Estado "error" 0 "git pull --rebase fallito"
    git add $EstadoPath 2>$null
    git commit -m "Estado: pull fallito" --quiet 2>$null
    Push-ConReintento 1 | Out-Null
    exit 1
}

Scrivi "Avvio riclassifica_frammenti.py..."
python scripts\riclassifica_frammenti.py 2>>$Log
$exitClassificazione = $LASTEXITCODE
Scrivi "riclassifica_frammenti.py terminato (exit $exitClassificazione)."

git add data\frammenti data\riferimenti 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Scrivi "Nessuna modifica da committare."
    $resultado = if ($exitClassificazione -eq 0) { "ok" } else { "error" }
    Escribe-Estado $resultado 0 "Sin cambios nuevos que clasificar"
    git add $EstadoPath 2>$null
    git commit -m "Estado: sin cambios" --quiet 2>$null
    Push-ConReintento 1 | Out-Null
    exit $exitClassificazione
}

$n = (git diff --cached --name-only | Measure-Object -Line).Lines
$resultadoFinal = if ($exitClassificazione -eq 0) { "ok" } else { "error" }
Escribe-Estado $resultadoFinal $n "riclassifica_frammenti.py exit $exitClassificazione"
git add $EstadoPath 2>$null
git commit -m "Autocommit classificazione notturna ($n file)" --quiet

if (Push-ConReintento $n) {
    exit 0
} else {
    exit 1
}
