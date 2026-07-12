# Lancia il batch di trascrizione locale (WhisperX CPU + classificazione Groq free-tier)
# impedendo la sospensione del PC SOLO per la durata dell'attivita', e ripristina SEMPRE
# i settaggi originali (coperchio chiuso -> sospensione -> spegnimento, comportamento
# normale del laptop) alla fine, anche se lo interrompi a mano con Ctrl+C.
#
# USO (lancio manuale, doppio click o da terminale):
#   powershell -ExecutionPolicy Bypass -File "scripts\avvia_trascrizione_sicura.ps1"
# Parametri opzionali:
#   -Cartella "D:\...\audio\2015"   (default: 2016)
#   -Da 20160120                     (default: riprende da dove si e' fermato, salta le date gia' fatte)
#   -Limit 1                         (default: 1 = UNA puntata a lancio, poi si ferma da solo e avvisa;
#                                      metti 0 per elaborare tutte le puntate rimanenti in un colpo solo)
#
# Cosa fa, in ordine:
#   1. Salva i valori ATTUALI di sospensione AC/DC (per ripristinarli esattamente, non a caso).
#   2. Disattiva la sospensione (solo per la durata dello script).
#   3. Controlla che HWiNFO64 esponga i sensori (finestra Sensori aperta) — se no, ARRESTA lo
#      script (ripristinando prima la sospensione): senza sensori nessuna soglia di emergenza
#      puo' rilevare/fermare un surriscaldamento, quindi la trascrizione non deve partire.
#   4. Avvia il logger termico in background (logs/trascrizioni_log_termico.csv) CON soglia di
#      emergenza: se la CPU resta >=93C per 2 letture di fila (non una sola, evita falsi allarmi
#      da glitch del sensore), il logger stesso uccide whisperx + trascrivi_locale_episodi.py e
#      scrive logs/OVERHEAT_STOP.flag.
#   5. Avvia la trascrizione di UNA puntata (scripts/trascrivi_locale_episodi.py --limit 1) e ASPETTA che finisca.
#   6. Alla fine ripristina SEMPRE la sospensione com'era prima, poi mostra in terminale (niente
#      popup Windows, "msg" non funziona su questo PC): allarme rosso + beep se fermato per
#      surriscaldamento (logs/OVERHEAT_STOP.flag), altrimenti "completata" o "errore/interrotta".
#
# Il controllo periodico del processo (ogni 15 min, CPU/temperatura/JSON validi) e' gia'
# gestito in automatico dal task Windows "IlVoloBatchHealthCheck" (indipendente da questo
# script, resta attivo comunque).

param(
    [string]$Cartella = "D:\Docs\il_volo_del_mattino\Volo del mattino\audio\2016",
    [string]$Da = "20160120",
    [int]$Limit = 1
)

$Repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $Repo

$SogliaEmergenzaCPU = 93  # C, per 2 letture consecutive (KILL_CONSECUTIVE in hwinfo_temp.py) - vedi decisione utente 2026-07-12
$OverheatFlag = "logs\OVERHEAT_STOP.flag"
Remove-Item $OverheatFlag -ErrorAction SilentlyContinue

function Get-StandbyIndici {
    $out = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
    $indici = $out | Select-String -Pattern "actual[a-z]*:\s*(0x[0-9A-Fa-f]+)" -AllMatches |
        ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value }
    if ($indici.Count -lt 2) {
        Write-Warning "Non sono riuscito a leggere gli indici di sospensione attuali, uso 0x00000384 (900s) come fallback per il ripristino."
        return @{ AC = "0x00000384"; DC = "0x00000384" }
    }
    return @{ AC = $indici[0]; DC = $indici[1] }
}

$originali = Get-StandbyIndici
Write-Host "Valori sospensione ATTUALI salvati (verranno ripristinati alla fine): AC=$($originali.AC) DC=$($originali.DC)"

Write-Host "`nDisattivo la sospensione per la durata della trascrizione..."
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-dc 0 | Out-Null

# Controllo HWiNFO — BLOCCANTE: senza sensori raggiungibili non esiste alcun modo di rilevare
# ne' fermare un surriscaldamento, quindi la trascrizione NON deve partire (decisione utente 2026-07-12).
Write-Host "`nControllo che HWiNFO64 esponga i sensori..."
$hwinfoOk = $true
try {
    $risultato = & python scripts\hwinfo_temp.py 2>&1
    if ($LASTEXITCODE -ne 0) { $hwinfoOk = $false }
} catch {
    $hwinfoOk = $false
}
if (-not $hwinfoOk) {
    Write-Warning "HWiNFO64 non espone i sensori (finestra Sensori chiusa o non attiva)."
    Write-Warning "ARRESTO: senza sensori nessuna soglia di emergenza puo' rilevare/fermare un surriscaldamento."
    Write-Host "`nRipristino i settaggi di sospensione originali (AC=$($originali.AC) DC=$($originali.DC))..."
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $originali.AC | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $originali.DC | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Write-Host "Apri la finestra Sensori in HWiNFO64 (Compatibilita' con memoria condivisa attiva) e rilancia lo script." -ForegroundColor Yellow
    exit 1
}
Write-Host "OK, sensori HWiNFO64 raggiungibili."

# Logger termico in background
Write-Host "Avvio il logger termico in background..."
$loggerJob = Start-Job -ScriptBlock {
    param($repo, $soglia)
    Set-Location $repo
    python scripts\hwinfo_temp.py --loop 60 logs\trascrizioni_log_termico.csv --kill-cpu $soglia
} -ArgumentList $Repo, $SogliaEmergenzaCPU

$exitCode = 1
try {
    $limitArgs = @()
    if ($Limit -gt 0) { $limitArgs = @("--limit", "$Limit") }
    $puntataMsg = if ($Limit -eq 1) { "UNA puntata" } elseif ($Limit -gt 1) { "$Limit puntate" } else { "TUTTE le puntate rimanenti" }
    Write-Host "`n=== Avvio trascrizione: $puntataMsg da $Cartella (da $Da) ===`n"
    python scripts\trascrivi_locale_episodi.py "$Cartella" --da $Da @limitArgs
    $exitCode = $LASTEXITCODE
    Write-Host "`n=== Trascrizione terminata, codice uscita: $exitCode ===`n"
}
finally {
    Write-Host "`nRipristino i settaggi di sospensione originali (AC=$($originali.AC) DC=$($originali.DC))..."
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $originali.AC | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $originali.DC | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null

    if ($loggerJob) {
        Stop-Job $loggerJob -ErrorAction SilentlyContinue | Out-Null
        Remove-Job $loggerJob -ErrorAction SilentlyContinue | Out-Null
    }

    Write-Host "Fatto. Il PC tornera' a sospendersi/spegnersi normalmente (coperchio, inattivita')."

    if (Test-Path $OverheatFlag) {
        $dettaglio = Get-Content $OverheatFlag -Raw
        Write-Host "`n$('!' * 60)" -ForegroundColor Red
        Write-Host "ALLARME TEMPERATURA: trascrizione FERMATA automaticamente per sicurezza." -ForegroundColor Red
        Write-Host $dettaglio -ForegroundColor Red
        Write-Host "$('!' * 60)`n" -ForegroundColor Red
        try { [console]::beep(1000, 400); [console]::beep(1000, 400); [console]::beep(1000, 400) } catch {}
        Remove-Item $OverheatFlag -ErrorAction SilentlyContinue
    } elseif ($exitCode -eq 0) {
        Write-Host "Trascrizione completata senza errori ($puntataMsg). Sospensione automatica ripristinata." -ForegroundColor Green
    } else {
        Write-Host "ATTENZIONE: la trascrizione si e' fermata con un errore o e' stata interrotta (codice $exitCode). Controlla l'output prima di rilanciare. Sospensione automatica comunque ripristinata." -ForegroundColor Yellow
    }
}
