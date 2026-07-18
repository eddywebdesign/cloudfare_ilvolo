# Workflow admin per correggere/confermare la classificazione automatica (Groq)
# dei frammenti in data/frammenti/, usato dal pannello admin di /frammenti-recenti/?admin=1
#
# Uso:
#   python scripts/modifica_frammento.py --id 2016-02-01-002 --tipo citazione --titolo "Amore e assenza" --tema "amore,assenza"
#   python scripts/modifica_frammento.py --id 2016-02-01-002 --reset
#   python scripts/modifica_frammento.py  (senza argomenti: mostra i frammenti classificati, per un controllo rapido)

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"
# Se ILVOLO_DATA_DIR e' impostata, i dati veri vivono sullo share OMV, fuori
# dal repo git: "git add data/frammenti/" (path del repo locale) non vedrebbe
# mai la modifica appena scritta. In quel caso il commit lo fa gia'
# sync_snapshot_data.ps1 (mirror giornaliero OMV->repo), quindi qui va solo
# saltato con un avviso invece di finire per committare un path stantio.
SCRIVE_SU_SHARE_ESTERNO = FRAMMENTI_DIR != ROOT / "data" / "frammenti"


def load_json(path: Path) -> list:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def trova_frammento(fid: str) -> tuple[Path, list, int]:
    """Restituisce (file_path, records, indice) per un dato ID tipo '2016-02-01-002'."""
    data_str = "-".join(fid.split("-")[:3])  # "2016-02-01"
    dest = FRAMMENTI_DIR / f"{data_str}.json"
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
    if SCRIVE_SU_SHARE_ESTERNO:
        print(f"(scritto su {FRAMMENTI_DIR}, share condiviso: il commit lo fara' "
              "il prossimo snapshot automatico, non questo script)")
        return
    subprocess.run(["git", "-C", str(ROOT), "add", "data/frammenti/"], check=True)
    # Niente da salvare (es. --reset su un frammento gia' non classificato):
    # evita il crash di "git commit" quando non c'e' nulla in staging.
    staged = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"]
    ).returncode
    if staged == 0:
        print("(nessuna modifica reale: il valore era gia' quello, niente da committare)")
        return
    subprocess.run(["git", "-C", str(ROOT), "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(ROOT), "push"], check=True)


def mostra_classificati() -> None:
    """Elenca tutti i frammenti gia' classificati da Groq, per un controllo rapido."""
    trovati = []
    for f in sorted(FRAMMENTI_DIR.glob("*.json")):
        for r in load_json(f):
            if r.get("tipo"):
                trovati.append(r)
    if not trovati:
        print("Nessun frammento classificato.")
        return
    print(f"\n{'─'*60}")
    print(f"  {len(trovati)} FRAMMENTI CLASSIFICATI\n")
    for r in trovati:
        print(f"  ID:    {r['id']}")
        print(f"  Tipo:  {r.get('tipo','')}  Titolo: {r.get('titolo','')}  Tema: {', '.join(r.get('tema') or [])}")
        print()
    print(f"{'─'*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Corregge/conferma un frammento classificato da Groq in data/frammenti/")
    parser.add_argument("--id",     help="ID del frammento (es. 2016-02-01-002)")
    parser.add_argument("--tipo",   help="Tipo: aneddoto|riflessione|citazione|riferimento_libro|riferimento_musica|riferimento_film|...")
    parser.add_argument("--titolo", help="Titolo assegnato al frammento")
    parser.add_argument("--tema",   help="Temi separati da virgola, es. 'amore,assenza'")
    parser.add_argument("--reset",  action="store_true", help="Svuota la classificazione (tipo/titolo/tema), lo riporta a non classificato")
    args = parser.parse_args()

    if not args.id:
        mostra_classificati()
        return

    dest, records, idx = trova_frammento(args.id)
    r = records[idx]

    if args.reset:
        r["tipo"] = ""
        r["titolo"] = ""
        r["tema"] = []
        save_json(dest, records)
        print(f"✓ Frammento {args.id} riportato a non classificato.")
        git_commit_push(f"chore: reset classificazione frammento {args.id}")
        print("✓ Commit e push effettuati.")
        return

    if args.tipo is not None:
        r["tipo"] = args.tipo
    if args.titolo is not None:
        r["titolo"] = args.titolo
    if args.tema is not None:
        r["tema"] = [t.strip() for t in args.tema.split(",") if t.strip()]

    save_json(dest, records)
    print(f"✓ Frammento aggiornato: [{r.get('tipo','')}] {r.get('titolo','')}")
    git_commit_push(f"chore: correggi classificazione frammento {args.id}")
    print("✓ Commit e push effettuati.")


if __name__ == "__main__":
    main()
