# Check fattuale, standalone, indipendente da Claude/app aperta.
# Eseguito da Windows Task Scheduler ogni 15 min. Scrive SEMPRE una riga di heartbeat
# in logs/batch_health_log.txt (prova che il check e' girato davvero) e, solo in caso
# di anomalia, scrive anche logs/batch_health_ALERT.txt e mostra un popup visibile.
# NOTA: logs/ e non data/ perche' Hugo tratta OGNI file in data/ come data file da
# parsare (JSON/YAML/TOML/CSV) e un .txt semplice li' rompe l'intero build del sito.

$repo = "D:\Download\CLAUDE FOLDER\ilvolodelmattino"
Set-Location $repo
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$anomalie = @()

# 1) Processi vivi (fattuale: interroga i processi reali)
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
$batch = $procs | Where-Object { $_.CommandLine -match 'trascrivi_locale_episodi' }
$logger = $procs | Where-Object { $_.CommandLine -match 'hwinfo_temp' }
$whisperx = $procs | Where-Object { $_.CommandLine -match 'whisperx' }

if (-not $batch) { $anomalie += "batch trascrivi_locale_episodi.py NON in esecuzione" }
if (-not $logger) { $anomalie += "logger hwinfo_temp.py NON in esecuzione" }

# 2) Progresso reale: CPU time del sottoprocesso whisperx deve crescere
if ($whisperx) {
    $pid1 = $whisperx[0].ProcessId
    try {
        $cpu1 = (Get-Process -Id $pid1 -ErrorAction Stop).CPU
        Start-Sleep -Seconds 8
        $cpu2 = (Get-Process -Id $pid1 -ErrorAction Stop).CPU
        if (($cpu2 - $cpu1) -le 0) { $anomalie += "whisperx PID $pid1 vivo ma CPU ferma (possibile hang)" }
    } catch {
        # Il PID puo' sparire durante gli 8s di attesa solo perche' l'episodio e' finito e
        # ne e' partito uno nuovo con un PID diverso - controllo diretto: c'e' un whisperx
        # vivo ADESSO? Se si', non e' un'anomalia (vedi stesso fix in check_batch_health.py).
        $whisperxOra = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'whisperx' }
        if (-not $whisperxOra) {
            $anomalie += "whisperx PID $pid1 sparito durante il check, nessun nuovo whisperx trovato"
        }
    }
} elseif ($batch) {
    $anomalie += "batch vivo ma nessun sottoprocesso whisperx trovato (tra un episodio e l'altro puo' essere normale per pochi secondi)"
}

# 3) Contenuto trascrizione: JSON valido con segmenti reali
$ultimoJson = Get-ChildItem "data\trascrizioni\2016-*.json" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($ultimoJson) {
    $py = "import json,sys`nd=json.load(open(r'$($ultimoJson.FullName)',encoding='utf-8'))`nprint(len(d.get('segments',[])))"
    $nSegs = & python -c $py 2>$null
    if (-not $nSegs -or [int]$nSegs -eq 0) { $anomalie += "ultimo JSON trascrizione ($($ultimoJson.Name)) senza segmenti validi" }
}

# 4) Temperatura/throttling dal CSV
$csv = "logs\trascrizioni_log_termico.csv"
if (Test-Path $csv) {
    $ultimaRiga = Get-Content $csv -Tail 1
    $campi = $ultimaRiga -split ','
    if ($campi.Length -ge 4) {
        $tempCsv = [datetime]$campi[0]
        if (((Get-Date) - $tempCsv).TotalMinutes -gt 20) { $anomalie += "log termico fermo da oltre 20 min" }
        if ([double]$campi[1] -gt 78) { $anomalie += "CPU a $($campi[1])C, sopra soglia 78C" }
    }
}

# 5) Stop richiesto dall'utente: se il flag esiste E siamo tra un episodio e
# l'altro (nessun whisperx attivo, batch vivo = e' in pausa, non a meta' lavoro),
# fermare il batch ORA senza perdere nulla. Se whisperx e' ancora attivo, non
# fare nulla: ci riprova al prossimo giro (ogni 15 min) finche' non trova la
# finestra sicura.
$stopFlag = "data\STOP_BATCH_AFTER_EPISODE.flag"
$stopEseguito = $false
if ((Test-Path $stopFlag) -and $batch -and -not $whisperx) {
    Stop-Process -Id $batch[0].ProcessId -Force -ErrorAction SilentlyContinue
    Remove-Item $stopFlag -ErrorAction SilentlyContinue
    $stopEseguito = $true
    msg $env:USERNAME /TIME:0 "Batch di trascrizione FERMATO come richiesto (nessun episodio in corso, zero lavoro perso). CPU libera per raffreddarsi."
}

# Heartbeat SEMPRE (prova che il task Windows gira davvero)
$statoRiga = "$ts | batch=$([bool]$batch) logger=$([bool]$logger) whisperx=$([bool]$whisperx) anomalie=$($anomalie.Count) stopEseguito=$stopEseguito"
Add-Content -Path "logs\batch_health_log.txt" -Value $statoRiga

if ($anomalie.Count -gt 0) {
    $msg = "$ts ANOMALIA:`n" + ($anomalie -join "`n")
    Add-Content -Path "logs\batch_health_ALERT.txt" -Value $msg
    msg $env:USERNAME /TIME:0 $msg
}
