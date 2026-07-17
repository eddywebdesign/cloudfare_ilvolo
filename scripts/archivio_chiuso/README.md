# Archivio chiuso — pipeline archive.org (2026-07-09)

Script della vecchia pipeline di sincronizzazione/upload audio su archive.org,
**chiusa definitivamente il 2026-07-09** per problemi di copyright (vedi
richiesta di cancellazione di ~1950 item inviata ad archive.org). Non più in
uso, tenuti solo come riferimento storico.

- `sync_archive.py` — scarica audio da deejay.it, trascrive, carica su archive.org,
  aggiorna il front matter. Le funzioni `transcribe`/`load_lines`/`HF_TOKEN_FILE`
  sono state estratte in `scripts/transcribe_utils.py` perché ancora usate dal
  pipeline di trascrizione attivo (`trascrivi_locale_episodi.py`) — questa copia
  qui NON è più eseguibile as-is (l'import di `transcribe_utils` presuppone che
  sia su `sys.path`, cosa vera solo per script dentro `scripts/`, non qui dentro).
- `upload_local_archive.py` — variante che caricava audio già presente in locale.

Container Docker correlato sul server OMV (`ilvolo-sync`, immagine
`ilvolo-sync-img:with-deps`) è fermo dal 2026-07-08/09, non rimosso.
