<?php
/**
 * Front controller per l'hosting Altervista (PHP+FTP, niente Cloudflare Worker).
 * Porting di worker.js: stessa logica di gate ad accesso riservato (token +
 * cookie, max 2 visite/1 giorno, scadenza assoluta), poi serve il file
 * statico Hugo corrispondente da questa stessa cartella (public/ caricata
 * qui via FTP). Tutte le richieste passano di qui grazie a .htaccess.
 */

$configFile = __DIR__ . '/config.local.php';
if (!file_exists($configFile)) {
    http_response_code(500);
    die('Configurazione mancante: copia config.local.example.php in config.local.php e imposta i valori reali (vedi commenti nel file).');
}
require $configFile;

const PREVIEW_COOKIE = 'ivds_preview';
const PREVIEW_DURATION_SEC = 86400; // 1 giorno per ogni concessione del link
const PREVIEW_MAX_VISITS = 2;
const MAINTENANCE_BG = '/immagini/sito_off_line.jpg';

// Estensione -> MIME esplicito: mime_content_type() su Altervista restituisce
// text/plain per .css/.js/.svg, il browser rifiuta poi CSS/JS con MIME sbagliato.
// FIX: deve stare PRIMA della chiamata a serveStaticFile() (riga piu' in basso) —
// un const a livello globale non e' "hoistato" come le funzioni, va eseguito prima.
const MIME_TYPES = [
    'css' => 'text/css',
    'js' => 'application/javascript',
    'mjs' => 'application/javascript',
    'json' => 'application/json',
    'svg' => 'image/svg+xml',
    'xml' => 'application/xml',
    'html' => 'text/html; charset=utf-8',
    'webmanifest' => 'application/manifest+json',
];

function maintenancePage(): string {
    $bg = MAINTENANCE_BG;
    return <<<HTML
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Il Volo della Sera</title>
<meta name="robots" content="noindex, nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { height: 100%; margin: 0; }
  body {
    font-family: system-ui, sans-serif; text-align: center; color: #f0e8d6;
    display: flex; align-items: center; justify-content: center; min-height: 100vh;
    padding: 1.5rem;
    background: linear-gradient(rgba(8,9,15,0.72), rgba(8,9,15,0.72)), url('{$bg}') center/cover no-repeat;
  }
  .box { max-width: 34rem; }
  h1 { font-size: 1.6rem; }
  p { opacity: 0.85; }
</style>
</head>
<body>
<div class="box">
<h1>Sito temporaneamente non disponibile</h1>
<p>In attesa di autorizzazione dei contenuti da parte degli aventi diritto.</p>
</div>
</body>
</html>
HTML;
}

// Legge/valida il cookie di preview: "<token>.<visite>.<scadenzaUnix>"
function parsePreviewCookie(): ?array {
    if (empty($_COOKIE[PREVIEW_COOKIE])) return null;
    $parts = explode('.', $_COOKIE[PREVIEW_COOKIE]);
    if (count($parts) !== 3) return null;
    [$token, $visitsStr, $expiryStr] = $parts;
    if (!defined('PREVIEW_TOKEN') || $token !== PREVIEW_TOKEN) return null;
    $visits = (int)$visitsStr;
    $expiry = (int)$expiryStr;
    if ($expiry <= 0 || time() > $expiry) return null;
    return ['visits' => $visits, 'expiry' => $expiry];
}

function setPreviewCookie(int $visits, int $expiry): void {
    $value = PREVIEW_TOKEN . '.' . $visits . '.' . $expiry;
    setcookie(PREVIEW_COOKIE, $value, [
        'expires' => $expiry, 'path' => '/', 'httponly' => true,
        'secure' => true, 'samesite' => 'Lax',
    ]);
}

// Una "visita" si conta solo sulla navigazione di pagina (non asset), come in worker.js.
function isNavigationRequest(string $path): bool {
    $accept = $_SERVER['HTTP_ACCEPT'] ?? '';
    if (str_contains($accept, 'text/html')) return true;
    return !preg_match('/\.[a-zA-Z0-9]+$/', $path);
}

$requestPath = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?: '/';

// Endpoint dinamico: il form "Condividi un ricordo" (stesso path del worker.js).
if ($requestPath === '/api/ricordo' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    require __DIR__ . '/api/ricordo.php';
    exit;
}

// Concessione di un nuovo blocco di visite tramite ?preview=<token>.
if (
    isset($_GET['preview']) && defined('PREVIEW_TOKEN') && $_GET['preview'] === PREVIEW_TOKEN
    && time() < TOKEN_ABSOLUTE_EXPIRY
) {
    $expiry = time() + PREVIEW_DURATION_SEC;
    setPreviewCookie(0, $expiry);
    $qs = $_GET;
    unset($qs['preview']);
    $redirect = $requestPath . (count($qs) ? '?' . http_build_query($qs) : '');
    header('Location: ' . $redirect, true, 302);
    exit;
}

$preview = parsePreviewCookie();
$previewOk = $preview && $preview['visits'] < PREVIEW_MAX_VISITS;

if (MAINTENANCE && !$previewOk) {
    if ($requestPath === MAINTENANCE_BG) {
        serveStaticFile($requestPath);
        exit;
    }
    http_response_code(503);
    header('Content-Type: text/html; charset=utf-8');
    header('X-Robots-Tag: noindex');
    echo maintenancePage();
    exit;
}

if ($preview && $preview['visits'] < PREVIEW_MAX_VISITS && isNavigationRequest($requestPath)) {
    setPreviewCookie($preview['visits'] + 1, $preview['expiry']);
}

serveStaticFile($requestPath);

/** Serve il file statico Hugo corrispondente al path richiesto, come farebbe env.ASSETS.fetch(). */
function serveStaticFile(string $path): void {
    $root = __DIR__;
    $candidati = [$path, rtrim($path, '/') . '/index.html', '/index.html'];
    foreach ($candidati as $rel) {
        $file = realpath($root . $rel);
        if ($file && str_starts_with($file, $root) && is_file($file)) {
            $ext = strtolower(pathinfo($file, PATHINFO_EXTENSION));
            $mime = MIME_TYPES[$ext] ?? (mime_content_type($file) ?: 'application/octet-stream');
            header('Content-Type: ' . $mime);
            readfile($file);
            return;
        }
    }
    http_response_code(404);
    $notFound = realpath($root . '/404.html');
    if ($notFound) { readfile($notFound); } else { echo '404 - Pagina non trovata'; }
}
