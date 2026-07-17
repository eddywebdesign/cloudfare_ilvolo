# Setup GMKtec K16 — Ubuntu Server 24.04 LTS

## 1. Installazione base
Installa Ubuntu Server 24.04 LTS (immagine standard, no GUI). Durante l'installer: crea utente, abilita OpenSSH se vuoi amministrarlo da remoto.

## 2. Pacchetti
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip lm-sensors git
sudo sensors-detect --auto
sensors   # verifica che stampi almeno una temperatura CPU (k10temp)
```

## 3. Disattiva sospensione (mini PC dedicato, sempre acceso)
```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

## 4. Ambiente Python
```bash
python3 -m venv ~/ilvolo-env
source ~/ilvolo-env/bin/activate
pip install faster-whisper whisperx pyannote.audio groq psutil
```

## 5. Repo e segreti
Copia il repo (via Syncthing o `git clone`, da decidere) in `~/ilvolodelmattino`.
Copia manualmente (MAI da git):
- `~/hf_token.txt` — token HuggingFace per diarizzazione pyannote
- `GROQ_API_KEY` come variabile d'ambiente, o `~/API GROQ IA.txt`

## 5bis. Montare il NAS audio via CIFS
Lo script lavora sull'intero archivio `\\192.168.8.80\Media\ilvolo-audio-backup` (organizzato in sottocartelle anno), NON copiato in locale sul SSD — vedi [[project_trascrizione_2012_2016]] per il perche' (CPU-bound, no doppia fonte di verita').
```bash
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/ilvolo-audio-backup
# credenziali NAS in un file protetto, mai in /etc/fstab in chiaro:
sudo nano /root/.smbcredentials   # username=... \n password=...
sudo chmod 600 /root/.smbcredentials
echo "//192.168.8.80/Media/ilvolo-audio-backup /mnt/ilvolo-audio-backup cifs credentials=/root/.smbcredentials,uid=$(id -u),gid=$(id -g),vers=3.0 0 0" | sudo tee -a /etc/fstab
sudo mount -a
ls /mnt/ilvolo-audio-backup   # deve mostrare le cartelle anno (2012, 2013, ...)
```
Se il mount point e' diverso, imposta `export ILVOLO_AUDIO_ROOT=/tuo/mount/point` prima di lanciare lo script (o passalo come primo argomento).

## 6. Timer di controllo salute batch
```bash
mkdir -p ~/.config/systemd/user
cp ~/ilvolodelmattino/scripts/linux/ilvolo-batch-health.service ~/.config/systemd/user/
cp ~/ilvolodelmattino/scripts/linux/ilvolo-batch-health.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ilvolo-batch-health.timer
systemctl --user status ilvolo-batch-health.timer
```

## 6bis. Resumen diario por email
Requiere `~/.config/ilvolo_alert_smtp.conf` ya configurado (mismo SMTP que usan las alertas de `check_batch_health.py`).
```bash
cp ~/ilvolodelmattino/scripts/linux/ilvolo-resumen-diario.service ~/.config/systemd/user/
cp ~/ilvolodelmattino/scripts/linux/ilvolo-resumen-diario.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ilvolo-resumen-diario.timer
systemctl --user status ilvolo-resumen-diario.timer
# Prueba manual inmediata (no espera a las 21:00):
python3 ~/ilvolodelmattino/scripts/linux/resumen_diario.py
```

## 7. Primo run di prova
Il default dello script (`--limit 0`) elabora TUTTE le puntate rimanenti di ogni cartella anno prima di passare alla successiva — per un primo test controllato, limita a una sola puntata:
```bash
cd ~/ilvolodelmattino
bash scripts/linux/avvia_trascrizione_sicura.sh "" --limit 1
```
Osserva `logs/trascrizioni_log_termico.csv` durante il run: se la temperatura resta sotto ~80°C senza `throttling=SI`, `--threads 8` (già il default nello script) va bene. Se sale troppo, abbassalo modificando `--threads 8` nello script.
Una volta verificato che tutto funziona, lancia senza `--limit` (o con `--limit 0`) per l'elaborazione completa e incustodita di tutto l'archivio.
