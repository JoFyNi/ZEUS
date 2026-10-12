"""Zerlegt tools.py in ein Paket - mechanisch, nachpruefbar, verlustfrei.

Der Punkt dieses Skripts ist die Nachpruefbarkeit: Es schneidet nach dem
Syntaxbaum, nicht nach Suchmustern, und es PRUEFT anschliessend, dass jede
Zeile des Klassenrumpfs genau einmal verwendet wurde. Ein Umbau, bei dem
still eine Methode verschwindet, waere genau die Sorte Fehler, die dieses
Projekt sonst ueberall bekaempft.
"""
from __future__ import annotations

import ast
import pathlib
import sys

WURZEL = pathlib.Path("/root/zeus/jarvis_second_brain")
QUELLE = WURZEL / "jarvis" / "tools.py"
PAKET = WURZEL / "jarvis" / "werkzeuge"

text = QUELLE.read_text(encoding="utf-8")
zeilen = text.split("\n")          # 0-basiert; ast zaehlt 1-basiert
baum = ast.parse(text)


def block(knoten) -> tuple:
    """Zeilenbereich eines Knotens - einschliesslich der Kommentare darueber.

    Ein Kommentarblock direkt ueber einer Methode gehoert zu ihr. Wer nur
    nach lineno schneidet, laesst die Begruendung beim alten Nachbarn zurueck
    - und in diesem Projekt steht die Begruendung ueber allem.
    """
    start = knoten.lineno - 1
    if knoten.decorator_list:
        start = min(d.lineno for d in knoten.decorator_list) - 1
    # Nach oben laufen, solange dort Kommentare stehen. Wurde KEINER
    # aufgenommen, bleibt es bei der def-Zeile selbst: 'i + 1' waere sonst die
    # Zeile davor - und die ist im Zweifel 'class JarvisAgent:'. Genau dieser
    # Fehler um eins hat beim ersten Lauf eine zweite, leere Klasse erzeugt,
    # die alle Mixins verdeckte.
    i = start - 1
    genommen = False
    while i >= 0:
        roh = zeilen[i].strip()
        if roh.startswith("#"):
            i -= 1
            genommen = True
            continue
        if not roh and i > 0 and zeilen[i - 1].strip().startswith("#"):
            i -= 1
            continue
        break
    return ((i + 1) if genommen else (start + 1)), knoten.end_lineno


klasse = next(k for k in baum.body
              if isinstance(k, ast.ClassDef) and k.name == "VaultTools")
methoden = {}
for m in klasse.body:
    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
        methoden[m.name] = block(m)

# ------------------------------------------------------------ Die Aufteilung
GRUPPEN = {
    "basis": [
        "__init__", "lauf_zuruecksetzen", "root", "_safe_path", "_notes",
        "_structure", "_zielordner_waehlen", "notiz_exakt", "resolve_note",
        "_gemerkt", "_per_wortueberlappung", "aehnliche_namen", "_log", "_guard",
        "_backup", "_journal", "_snapshot", "_atomic_write", "_write",
        "_remove_from_index", "schritt", "transaktion", "_track", "_rollback",
        "_namen_pruefen", "ausfuehren",
    ],
    "lesen": [
        "notizen_suchen", "notiz_lesen", "struktur_anzeigen", "bloecke_anzeigen",
        "verwandte_finden", "links_pruefen", "letzte_aenderungen",
    ],
    "notizen": [
        "ordner_anlegen", "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten",
        "notiz_ueberschreiben", "notiz_verschieben", "notiz_loeschen",
        "notiz_wiederherstellen", "ordner_umbenennen", "tags_setzen",
        "frontmatter_setzen", "rueckgaengig",
    ],
    "listen": ["listenpunkt_hinzufuegen", "listenpunkt_erledigt"],
    "abschnitte": [
        "block_ersetzen", "abschnitt_ersetzen", "abschnitt_einfuegen",
        "abschnitt_loeschen", "codeblock_hinzufuegen",
    ],
    "verknuepfen": [
        "notizen_verknuepfen", "links_bereinigen", "auto_verlinken",
        "uebersichtsnotiz", "notizen_zusammenfuehren", "notiz_aufteilen",
    ],
    "code": [
        "_code_wurzel_pruefen", "code_dateien", "quellcode_lesen", "code_suchen",
        "codebasis_importieren", "_fingerabdruck", "_code_teile", "_teil_ablegen",
        "code_auslesen",
    ],
    "einlesen": ["datei_importieren", "_ordner_importieren", "_bereits_importiert"],
    "wartung": ["vault_wartung"],
    "zeichnen": ["struktur_zeichnen"],
}

# --- Prüfung 1: jede Methode genau einmal, keine erfunden ---
verteilt = [n for gruppe in GRUPPEN.values() for n in gruppe]
if sorted(verteilt) != sorted(methoden):
    fehlt = sorted(set(methoden) - set(verteilt))
    zuviel = sorted(set(verteilt) - set(methoden))
    doppelt = sorted({n for n in verteilt if verteilt.count(n) > 1})
    print("ABBRUCH – die Aufteilung deckt sich nicht mit der Klasse.")
    print("  nicht zugeordnet:", fehlt)
    print("  gibt es nicht   :", zuviel)
    print("  doppelt         :", doppelt)
    sys.exit(1)

# --- Prüfung 2: keine überlappenden Zeilenbereiche ---
belegt = {}
for name, (a, b) in methoden.items():
    for z in range(a, b + 1):
        if z in belegt:
            print(f"ABBRUCH – Zeile {z} gehoert zu '{belegt[z]}' UND '{name}'.")
            sys.exit(1)
        belegt[z] = name

print(f"{len(methoden)} Methoden, {len(belegt)} Zeilen, keine Ueberschneidung.")
for gruppe, namen in GRUPPEN.items():
    umfang = sum(methoden[n][1] - methoden[n][0] + 1 for n in namen)
    print(f"  {gruppe:<12} {len(namen):>3} Methoden  {umfang:>5} Zeilen")

# Für den nächsten Schritt ablegen
import json
(pathlib.Path("/root/zeus/aufteilung.json")).write_text(json.dumps(
    {"methoden": {k: list(v) for k, v in methoden.items()}, "gruppen": GRUPPEN},
    ensure_ascii=False, indent=1), encoding="utf-8")
print("Aufteilung nach /root/zeus/aufteilung.json geschrieben.")
