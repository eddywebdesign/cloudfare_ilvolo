<?php
// Copia questo file in "config.local.php" (stesso posto) DIRETTAMENTE sul server
// Altervista via FTP — NON tramite git, il token reale non deve mai finire nel repo
// (stesso principio dei secret Groq/HuggingFace usati negli script Python locali).

// Token per l'accesso riservato durante la manutenzione. Chi visita
// "?preview=<questo-token>" ottiene l'accesso (stesse regole di worker.js:
// 1 giorno, max 2 visite, scadenza assoluta sotto).
define('PREVIEW_TOKEN', 'CAMBIAMI');

// true = sito chiuso al pubblico (pagina di manutenzione), come oggi su Cloudflare.
// false = sito aperto a tutti, nessun gate.
define('MAINTENANCE', true);

// Dopo questa data assoluta (formato ISO 8601, UTC) il link ?preview=<token>
// non concede piu' NUOVI accessi. Le concessioni gia' fatte restano valide
// secondo le loro regole (1 giorno/2 visite), non vengono revocate.
define('TOKEN_ABSOLUTE_EXPIRY', strtotime('2026-12-31T23:59:59Z'));

// Email di destinazione per il form "Condividi un ricordo".
define('RICORDO_DEST_EMAIL', 'eddywebdesign2.0@gmail.com');
