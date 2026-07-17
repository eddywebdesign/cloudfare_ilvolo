# Workflow admin per approvare/cestinare frammenti da /da-ricostruire/
#
# Uso:
#   python scripts/approva.py --id 2016-01-07-film-clip-0003 --cat film --titolo "The Hateful Eight" --autore "Tarantino" --anno 2015
#   python scripts/approva.py --id 2016-01-07-film-clip-0003 --cestina
#   python scripts/approva.py  (senza argomenti: mostra frammenti non identificati)

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RIF_DIR = dati_root(ROOT) / "riferimenti"
CONTRIB_FILE = ROOT / "data" / "contribuitori.json"


def load_json(path: Path) -> list:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def trova_frammento(fid: str) -> tuple[Path, list, int]:
    """Restituisce (file_path, records, indice) per un dato ID."""
    data_str = "-".join(fid.split("-")[:3])  # "2016-01-07"
    dest = RIF_DIR / f"{data_str}.json"
    if not dest.exists():
        print(f"File non trovato: {dest}")
        sys.exit(1)
    records = load_json(dest)
    for i, r in enumerate(records):
        if r.get("id") == fid:
            return dest, records, i
    print(f"ID '{fid}' non trovato in {dest}")
    sys.exit(1)


def git_commit_push(msg: str) -> None:
    subprocess.run(["git", "-C", str(ROOT), "add", "data/riferimenti/", "data/contribuitori.json"], check=True)
    # Niente da salvare: evita il crash di "git commit" quando non c'e' nulla in staging.
    staged = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"]
    ).returncode
    if staged == 0:
        print("(nessuna modifica reale: il valore era gia' quello, niente da committare)")
        return
    subprocess.run(["git", "-C", str(ROOT), "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(ROOT), "push"], check=True)


def aggiungi_contribuitore(nome: str) -> None:
    """Aggiunge nome a contribuitori.json se non già presente."""
    contrib = load_json(CONTRIB_FILE)
    nomi = [c.get("nome", "").lower() for c in contrib]
    if nome.lower() not in nomi:
        contrib.append({"nome": nome, "n": 1})
    else:
        for c in contrib:
            if c.get("nome", "").lower() == nome.lower():
                c["n"] = c.get("n", 0) + 1
    save_json(CONTRIB_FILE, contrib)


def mostra_pendenti() -> None:
    """Elenca tutti i frammenti non identificati da tutti i file JSON."""
    pendenti = []
    for f in sorted(RIF_DIR.glob("*.json")):
        for r in load_json(f):
            if not r.get("titolo") or r.get("note") == "non identificato":
                pendenti.append(r)
    if not pendenti:
        print("Nessun frammento da identificare.")
        return
    print(f"\n{'─'*60}")
    print(f"  {len(pendenti)} FRAMMENTI IN ATTESA\n")
    for r in pendenti:
        parziali = " · ".join(filter(None, [r.get("autore"), r.get("anno"), r.get("note")]))
        print(f"  ID:  {r['id']}")
        print(f"  Ep:  {r.get('episodio_data','?')}  {r.get('categoria','?').upper()}")
        if parziali and parziali != "non identificato":
            print(f"  Indizi: {parziali}")
        print()
    print(f"{'─'*60}")
    print("\nComando approvazione:")
    print('  python scripts/approva.py --id ID --cat CATEGORIA --titolo "Titolo" --autore "Autore" --anno ANNO')
    print("\nComando cestina:")
    print("  python scripts/approva.py --id ID --cestina\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approva o cestina frammenti da /da-ricostruire/")
    parser.add_argument("--id",     help="ID del frammento (es. 2016-01-07-film-clip-0003)")
    parser.add_argument("--cat",      help="Categoria: film | libro | musica")
    parser.add_argument("--subcat",   help="Sotto-categoria: romanzo|poesia|saggio|citazione|lettura_volo|documentario")
    parser.add_argument("--titolo",   help="Titolo del riferimento")
    parser.add_argument("--autore",   help="Autore / regista / artista")
    parser.add_argument("--anno",     help="Anno di uscita/pubblicazione")
    parser.add_argument("--note",     help="Note aggiuntive")
    parser.add_argument("--cestina", action="store_true", help="Marca come cestinato (rimuove dalla cernita)")
    parser.add_argument("--nome",   help="Nome contribuitore (da aggiungere all'elenco)")
    args = parser.parse_args()

    if not args.id:
        mostra_pendenti()
        return

    dest, records, idx = trova_frammento(args.id)
    r = records[idx]

    if args.cestina:
        r["note"] = "cestinato"
        r["titolo"] = r.get("titolo") or ""
        save_json(dest, records)
        print(f"✓ Frammento {args.id} segnato come cestinato.")
        git_commit_push(f"chore: cestina frammento {args.id}")
        print("✓ Commit e push effettuati.")
        return

    # Approvazione
    if not args.titolo:
        print("Errore: --titolo è obbligatorio per l'approvazione.")
        sys.exit(1)

    r["titolo"]        = args.titolo
    r["autore"]        = args.autore  or r.get("autore", "")
    r["anno"]          = args.anno    or r.get("anno", "")
    r["note"]          = args.note    or ""
    if args.cat:
        r["categoria"] = args.cat
    if args.subcat is not None:
        r["sottocategoria"] = args.subcat

    save_json(dest, records)

    if args.nome:
        aggiungi_contribuitore(args.nome)
        print(f"✓ Contribuitore '{args.nome}' aggiunto.")

    print(f"✓ Frammento approvato: [{r['categoria'].upper()}] {r['titolo']}")
    commit_msg = f"feat: identifica {args.id} → {r['titolo']} ({r['categoria']})"
    git_commit_push(commit_msg)
    print("✓ Commit e push effettuati — il sito si aggiorna in ~1 minuto su Cloudflare.")


if __name__ == "__main__":
    main()
