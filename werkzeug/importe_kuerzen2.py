"""Entfernt ungenutzte Importe - diesmal ueber den Syntaxbaum.

Die erste Fassung hat Importe zeilenweise gelesen und ist an der Form
`from x import (a, b,\n c)` gescheitert - der Klammerauf steht dort nicht am
Zeilenende. Ein Importsatz kann sich beliebig ueber Zeilen verteilen; der
Syntaxbaum weiss, wo er anfaengt und aufhoert, ein regulaerer Ausdruck rät.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

ORDNER = pathlib.Path(sys.argv[1])


def ungenutzte() -> dict:
    lauf = subprocess.run([sys.executable, "-m", "pyflakes"]
                          + [str(p) for p in sorted(ORDNER.glob("*.py"))],
                          capture_output=True, text=True)
    heraus: dict = {}
    for zeile in lauf.stdout.splitlines():
        treffer = re.match(r"^(.+?):(\d+):\d+: '(.+?)' imported but unused$", zeile)
        if treffer:
            datei, _, name = treffer.groups()
            kurz = name.split(" as ")[-1].strip()
            if " as " not in name:
                kurz = kurz.split(".")[-1]
            heraus.setdefault(datei, set()).add(kurz)
    return heraus


def neu_schreiben(knoten, behalten: list, zeilen: list) -> list:
    """Einen Importsatz aus den verbliebenen Namen neu setzen - hoechstens 88 Zeichen."""
    if isinstance(knoten, ast.Import):
        teile = [f"{a.name} as {a.asname}" if a.asname else a.name for a in behalten]
        kopf = "import "
        einzeln = [f"import {t}" for t in teile]
        return einzeln if len("".join(teile)) + 7 > 79 else [kopf + ", ".join(teile)]
    punkte = "." * knoten.level
    ziel = f"{punkte}{knoten.module or ''}"
    teile = [f"{a.name} as {a.asname}" if a.asname else a.name for a in behalten]
    einzeiler = f"from {ziel} import " + ", ".join(teile)
    if len(einzeiler) <= 79:
        return [einzeiler]
    heraus = [f"from {ziel} import ("]
    zeile = "   "
    for i, t in enumerate(teile):
        stueck = t + ("," if i < len(teile) - 1 else ",")
        if len(zeile) + len(stueck) + 1 > 78:
            heraus.append(zeile)
            zeile = "   "
        zeile += " " + stueck
    heraus.append(zeile)
    heraus.append(")")
    return heraus


def kuerzen(pfad: pathlib.Path, namen: set) -> bool:
    text = pfad.read_text(encoding="utf-8")
    zeilen = text.split("\n")
    baum = ast.parse(text)
    ersetzungen = []            # (von, bis, neue Zeilen)
    for knoten in ast.walk(baum):
        if not isinstance(knoten, (ast.Import, ast.ImportFrom)):
            continue
        behalten = [a for a in knoten.names
                    if (a.asname or a.name.split(".")[0] if isinstance(knoten, ast.Import)
                        else (a.asname or a.name)) not in namen]
        if len(behalten) == len(knoten.names):
            continue
        einzug = zeilen[knoten.lineno - 1][:len(zeilen[knoten.lineno - 1])
                                           - len(zeilen[knoten.lineno - 1].lstrip())]
        neu = ([einzug + z for z in neu_schreiben(knoten, behalten, zeilen)]
               if behalten else [])
        ersetzungen.append((knoten.lineno, knoten.end_lineno, neu))
    if not ersetzungen:
        return False
    for von, bis, neu in sorted(ersetzungen, reverse=True):
        zeilen[von - 1:bis] = neu
    pfad.write_text(re.sub(r"\n{4,}", "\n\n\n", "\n".join(zeilen)), encoding="utf-8")
    return True


runde = 0
while runde < 8:
    offen = ungenutzte()
    if not offen:
        break
    runde += 1
    geaendert = False
    for datei, namen in offen.items():
        if kuerzen(pathlib.Path(datei), namen):
            geaendert = True
            print(f"Runde {runde}: {pathlib.Path(datei).name} – {len(namen)} entfernt")
    if not geaendert:
        break

rest = ungenutzte()
print("Uebrig:", sum(len(v) for v in rest.values()))
for datei, namen in rest.items():
    print("  ", pathlib.Path(datei).name, sorted(namen))
