"""CLI : analyse un ou plusieurs .eml et écrit la proposition JSON."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import DEFAULT_MODEL, analyser_mail
from .divalto import DivaltoWebServiceClient, MockDivalto
from .eml_parser import parse_eml


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="devis-ia")
    p.add_argument("eml", nargs="+", help="fichiers .eml")
    p.add_argument("--divalto", choices=["mock", "ws"], default="mock")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--out", default="out", help="dossier de sortie JSON")
    a = p.parse_args(argv)

    divalto = MockDivalto() if a.divalto == "mock" else DivaltoWebServiceClient()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in a.eml:
        mail = parse_eml(f)
        res = analyser_mail(mail, divalto, model=a.model)
        dest = out / (Path(f).stem + ".json")
        dest.write_text(json.dumps({"mail": mail.subject, "proposition": res.proposition, "alertes": res.alertes,
                                    "usage": res.usage, "trace": res.trace}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{Path(f).name}: {'OK' if res.proposition else 'ECHEC'} alertes={len(res.alertes)} -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
