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

## 6. Timer di controllo salute batch
```bash
mkdir -p ~/.config/systemd/user
cp ~/ilvolodelmattino/scripts/linux/ilvolo-batch-health.service ~/.config/systemd/user/
cp ~/ilvolodelmattino/scripts/linux/ilvolo-batch-health.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ilvolo-batch-health.timer
systemctl --user status ilvolo-batch-health.timer
```

## 7. Primo run di prova
```bash
cd ~/ilvolodelmattino
bash scripts/linux/avvia_trascrizione_sicura.sh
```
Osserva `logs/trascrizioni_log_termico.csv` durante il run: se la temperatura resta sotto ~80°C senza `throttling=SI`, `--threads 8` (già il default nello script) va bene. Se sale troppo, abbassalo modificando `--threads 8` nello script.
