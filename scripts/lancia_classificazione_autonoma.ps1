# Classificazione notturna autonoma: pull dati nuovi dal K16, classifica i
# frammenti non ancora titolati, autocommit+push incondizionato dei risultati.
# Autorizzato esplicitamente dall'utente il 2026-07-14 per rendere la pipeline
# indipendente da Claude/abbonamento Pro (in scadenza il 16). Nessuna conferma
# richiesta: pensato per girare da Task Scheduler senza nessuno collegato.
#
# Uso: powershell -ExecutionPolicy Bypass -File "scripts\lancia_classificazione_autonoma.ps1"

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo
$Log = "logs\classificazione_autonoma.log"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

function Scrivi($msg) {
    "$ts $msg" | Out-File -FilePath $Log -Append -Encoding utf8
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
    exit 1
}

Scrivi "Avvio riclassifica_frammenti.py..."
python scripts\riclassifica_frammenti.py 2>>$Log
Scrivi "riclassifica_frammenti.py terminato (exit $LASTEXITCODE)."

git add data\frammenti data\riferimenti 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Scrivi "Nessuna modifica da committare."
    exit 0
}

$n = (git diff --cached --name-only | Measure-Object -Line).Lines
git commit -m "Autocommit classificazione notturna ($n file)" --quiet
git push --quiet 2>>$Log
if ($LASTEXITCODE -eq 0) {
    Scrivi "Committati e pushati $n file."
    exit 0
}

# Push rifiutato (remoto avanzato nel frattempo, es. autocommit del K16): un
# solo tentativo di pull --rebase + repush prima di arrendersi. Prima (fino al
# 2026-07-16) un push fallito veniva solo loggato ma lo script usciva con
# successo comunque - Task Scheduler non segnalava mai il problema.
Scrivi "AVVISO: git push fallito al primo tentativo, provo pull --rebase + repush..."
git pull --rebase --quiet 2>>$Log
if ($LASTEXITCODE -ne 0) {
    Scrivi "ERRORE: pull --rebase di recupero fallito. $n file committati in locale, NON pushati."
    exit 1
}
git push --quiet 2>>$Log
if ($LASTEXITCODE -eq 0) {
    Scrivi "Committati e pushati $n file (tras reintento)."
    exit 0
} else {
    Scrivi "ERRORE: git push fallito tras reintento ($n file committati en local, NO pushati)."
    exit 1
}
