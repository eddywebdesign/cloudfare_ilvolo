<?php
/**
 * Porting di handleRicordo() da worker.js. Invocato da index.php per
 * POST /api/ricordo. Usa mail() nativo di PHP (incluso gratis su
 * Altervista) invece di Resend, per restare a costo zero — nessun
 * servizio esterno, nessuna chiave API da gestire.
 */

header('Content-Type: application/json');

function jsonOut(array $body, int $status = 200): void {
    http_response_code($status);
    echo json_encode($body);
}

$raw = file_get_contents('php://input');
$data = json_decode($raw, true);
if (!is_array($data)) {
    jsonOut(['ok' => false, 'error' => 'Richiesta non valida'], 400);
    return;
}

$nome = mb_substr(trim((string)($data['nome'] ?? '')), 0, 200);
$email = mb_substr(trim((string)($data['email'] ?? '')), 0, 200);
$messaggio = mb_substr(trim((string)($data['messaggio'] ?? '')), 0, 5000);

if ($messaggio === '') {
    jsonOut(['ok' => false, 'error' => 'Messaggio mancante'], 400);
    return;
}
if (!defined('RICORDO_DEST_EMAIL')) {
    jsonOut(['ok' => false, 'error' => 'Servizio non configurato'], 500);
    return;
}

$subject = 'Nuovo ricordo condiviso da ' . ($nome !== '' ? $nome : 'un ascoltatore');
$body = "Nome: " . ($nome !== '' ? $nome : '(non indicato)') . "\n"
      . "Email: " . ($email !== '' ? $email : '(non indicata)') . "\n\n"
      . $messaggio;

// Allegato opzionale (foto/audio in base64 dal client), stesso limite di worker.js.
$allegato = $data['allegato'] ?? null;
$boundary = null;
if (is_array($allegato) && isset($allegato['data']) && is_string($allegato['data']) && strlen($allegato['data']) < 11 * 1024 * 1024) {
    $boundary = md5((string)microtime());
    $filename = mb_substr((string)($allegato['filename'] ?? 'allegato'), 0, 200);
    $headers = "From: Il Volo della Sera <noreply@" . ($_SERVER['SERVER_NAME'] ?? 'localhost') . ">\r\n";
    if ($email !== '') { $headers .= "Reply-To: {$email}\r\n"; }
    $headers .= "MIME-Version: 1.0\r\n";
    $headers .= "Content-Type: multipart/mixed; boundary=\"{$boundary}\"\r\n";

    $msg = "--{$boundary}\r\n";
    $msg .= "Content-Type: text/plain; charset=utf-8\r\n\r\n";
    $msg .= $body . "\r\n";
    $msg .= "--{$boundary}\r\n";
    $msg .= "Content-Type: application/octet-stream; name=\"{$filename}\"\r\n";
    $msg .= "Content-Transfer-Encoding: base64\r\n";
    $msg .= "Content-Disposition: attachment; filename=\"{$filename}\"\r\n\r\n";
    $msg .= chunk_split($allegato['data']) . "\r\n";
    $msg .= "--{$boundary}--";

    $ok = mail(RICORDO_DEST_EMAIL, $subject, $msg, $headers);
} else {
    $headers = "From: Il Volo della Sera <noreply@" . ($_SERVER['SERVER_NAME'] ?? 'localhost') . ">\r\n";
    if ($email !== '') { $headers .= "Reply-To: {$email}\r\n"; }
    $ok = mail(RICORDO_DEST_EMAIL, $subject, $body, $headers);
}

if (!$ok) {
    jsonOut(['ok' => false, 'error' => 'Invio fallito'], 502);
    return;
}
jsonOut(['ok' => true]);
