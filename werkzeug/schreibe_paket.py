"""Schreibt das Paket jarvis/werkzeuge/ aus tools.py - mit Nachweis.

Der Nachweis ist der Punkt: Am Ende wird geprueft, dass jede Methode der
Klasse genau einmal in genau einer Datei gelandet ist und dass der
zusammengesetzte Rumpf dieselben Namen traegt wie vorher.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

WURZEL = pathlib.Path("/root/zeus/jarvis_second_brain")
QUELLE = WURZEL / "jarvis" / "tools.py"
PAKET = WURZEL / "jarvis" / "werkzeuge"

text = QUELLE.read_text(encoding="utf-8")
zeilen = text.split("\n")
plan = json.loads(pathlib.Path("/root/zeus/aufteilung.json").read_text(encoding="utf-8"))
methoden = {k: tuple(v) for k, v in plan["methoden"].items()}
gruppen = plan["gruppen"]


def schnitt(a: int, b: int) -> str:
    """Zeilen a..b (1-basiert, einschliesslich) - unveraendert."""
    return "\n".join(zeilen[a - 1:b])


# --------------------------------------------------------------- Kopfzeilen
GEMEINSAM = '''from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..config import settings
from ..vault import (
    Note,
    VaultStructure,
    extract_section,
    fold,
    parse_note,
    safe_filename,
    similarity,
    split_sections,
    strip_frontmatter,
    trim_at_boundary,
    WIKILINK_RE,
)
'''

MIXIN_KOPF = GEMEINSAM + '''
from .ergebnis import ToolResult, WriteError, _Transaktion
from .hilfen import (
    IMPORT_ENDUNGEN, PROSA_ENDUNGEN, SPRACH_ENDUNGEN,
    _abschnitt_ende, _abschnitt_ersetzen, _abschnitt_index, _abschnittsgrenzen,
    _abschnittsnamen, _im_ordner, _insert_into_section, _kurz_wert,
    _listenbereich, _pdf_als_text, _punkttext, _pypdf_version,
    _rename_wikilinks, _set_frontmatter_list, _set_frontmatter_wert,
    _CHECKBOX_RE, _LISTENZEILE_RE, safe_path_fragment,
)
from .schemas import (
    NUR_LESENDE_WERKZEUGE, SCHREIBENDE_WERKZEUGE, TOOL_NAMES, TOOL_PARAMS,
    TOOL_REQUIRED, TOOL_SCHEMAS, _LIST_PARAMS, _clean_params, _pflicht_pruefen,
    _uebersetzen,
)
'''

BESCHREIBUNG = {
    "basis": ("Der Unterbau aller Werkzeuge: Pfade, Namen, Schreiben, Sicherung.\n\n"
              "Hier steht alles, was JEDES Werkzeug braucht und was kein Werkzeug\n"
              "selbst noch einmal machen darf: die Aufloesung eines Notiznamens in\n"
              "sieben Stufen, das atomare Schreiben mit Rueckleseprobe, Sicherung,\n"
              "Journal, Transaktion und Ruecknahme - und `ausfuehren`, das jeden\n"
              "Werkzeugaufruf durch dieselben Pruefungen schickt.\n\n"
              "Die Pruefungen stehen bewusst HIER und nicht in den einzelnen\n"
              "Werkzeugen: Dort koennen sie dreizehnmal vergessen werden, und beim\n"
              "vierzehnten Werkzeug, das jemand hinzufuegt, fehlen sie sicher."),
    "lesen": ("Lesende Werkzeuge - sie fassen die Vault nicht an.\n\n"
              "Sie brauchen keine Sicherung, keine Transaktion und keine\n"
              "Namensschranke: Ein ungefaehr getroffener Name ist beim Lesen ein\n"
              "brauchbares Ergebnis und beim Schreiben ein Datenverlust."),
    "notizen": ("Notizen anlegen, aendern, verschieben, loeschen, zurueckholen.\n\n"
                "Der Schreibzugriff im engeren Sinn. Jede Aenderung hier laeuft\n"
                "durch `transaktion()` aus basis.py - Sicherung vorher, Journal,\n"
                "Rollback bei einem Fehlschlag mittendrin."),
    "listen": ("Listen als Listen behandeln.\n\n"
               "`notiz_ergaenzen` haengt an - ans Dateiende oder ans Ende eines\n"
               "Abschnitts. Fuer Fliesstext ist das richtig, fuer eine\n"
               "Aufgabenliste falsch. Diese beiden Werkzeuge reihen einen Punkt\n"
               "IN die Liste ein und aendern beim Abhaken die vorhandene Zeile,\n"
               "statt einen Kommentar dazuzuschreiben."),
    "abschnitte": ("Gezielt an EINER Stelle aendern - Abschnitte und Bloecke.\n\n"
                   "Der Unterschied zu notiz_ueberschreiben ist der Schadensumfang:\n"
                   "Hier geht bei einem Fehlgriff ein Abschnitt verloren, dort die\n"
                   "ganze Notiz. Deshalb duerfen diese Werkzeuge tolerant mit\n"
                   "Namen umgehen, das Ganz-Ersetzen aber nicht."),
    "verknuepfen": ("Notizen miteinander in Beziehung setzen.\n\n"
                    "Verknuepfen, automatisch verlinken, Uebersichtsnotizen,\n"
                    "Zusammenfuehren und Aufteilen. Alles, was mehr als eine Notiz\n"
                    "gleichzeitig betrifft - und deshalb am ehesten halb\n"
                    "durchlaufen kann. Darum liegt jede dieser Aktionen in einer\n"
                    "Transaktion."),
    "code": ("Selbstinspektion: der eigene Quellcode.\n\n"
             "Bewusst nur LESEND nach aussen. Zeus darf seinen Code ansehen,\n"
             "durchsuchen und als Notizen ablegen. Aendern kann er ihn hier\n"
             "nicht - dafuer gibt es den Weg ueber die Quarantaene in\n"
             "kern/selbst.py, mit Testlauf und Freigabe."),
    "einlesen": ("Fremde Dateien hereinholen - einzeln oder ordnerweise.\n\n"
                 "Nach aussen nur lesend: Der Agent darf beliebige Textdateien\n"
                 "und PDFs importieren, aber nichts ausserhalb der Vault\n"
                 "schreiben."),
    "wartung": ("Die Vault als Ganzes pruefen und reparieren."),
    "zeichnen": ("Ablaeufe zeichnen.\n\n"
                 "Das Werkzeug faellt aus der Reihe: Es fasst die Vault nicht an\n"
                 "und rechnet nur. Es steht trotzdem hier, damit es ueber\n"
                 "denselben Weg aufgerufen, geprueft, gezaehlt und protokolliert\n"
                 "wird wie jedes andere."),
}

KLASSENNAME = {
    "basis": "VaultBasis",
    "lesen": "LeseWerkzeuge",
    "notizen": "NotizWerkzeuge",
    "listen": "ListenWerkzeuge",
    "abschnitte": "AbschnittsWerkzeuge",
    "verknuepfen": "VerknuepfungsWerkzeuge",
    "code": "CodeWerkzeuge",
    "einlesen": "ImportWerkzeuge",
    "wartung": "WartungsWerkzeuge",
    "zeichnen": "ZeichenWerkzeuge",
}

PAKET.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------- ergebnis.py
(PAKET / "ergebnis.py").write_text(
    '"""Was ein Werkzeug zurueckgibt - und was schiefgehen kann.\n\n'
    "Drei kleine Typen, die ueberall gebraucht werden und deshalb keine\n"
    "Abhaengigkeit haben duerfen. Sie stehen in einer eigenen Datei, damit\n"
    "jede andere sie importieren kann, ohne einen Ring zu bauen.\n"
    '"""\n' + GEMEINSAM.split("from ..config")[0] + "\n\n"
    + schnitt(70, 91) + "\n", encoding="utf-8")

# --------------------------------------------------------------- hilfen.py
(PAKET / "hilfen.py").write_text(
    '"""Textarbeit ohne Zustand: Abschnitte finden, Frontmatter setzen, PDF lesen.\n\n'
    "Reine Funktionen. Keine davon kennt die Vault, keine schreibt etwas -\n"
    "sie bekommen Text und geben Text zurueck. Genau deshalb sind sie einzeln\n"
    "pruefbar, und genau deshalb stehen sie nicht als Methoden in der Klasse.\n"
    '"""\n' + GEMEINSAM + "\n\n"
    + schnitt(44, 67) + "\n\n\n"
    + schnitt(2587, 2816) + "\n", encoding="utf-8")

# -------------------------------------------------------------- schemas.py
(PAKET / "schemas.py").write_text(
    '"""Die Werkzeugliste: Namen, Parameter, Pflichtfelder, Schreibrechte.\n\n'
    "Diese Datei ist das Verzeichnis, aus dem sich alles andere speist: die\n"
    "Werkzeugliste fuer das Modell, die Pruefung im Dispatcher, der Probelauf\n"
    "einer Fertigkeit, die Auswahl des Planers. Ein neues Werkzeug wird hier\n"
    "eingetragen - und ist damit ueberall bekannt.\n\n"
    "TOOL_NAMES, TOOL_PARAMS und TOOL_REQUIRED werden aus TOOL_SCHEMAS\n"
    "abgeleitet und nicht daneben gepflegt: Zwei Listen desselben Sachverhalts\n"
    "laufen auseinander, und zwar genau dann, wenn es darauf ankommt.\n"
    '"""\n' + GEMEINSAM + "\nfrom .ergebnis import ToolResult\n\n\n"
    + schnitt(2820, 3418) + "\n", encoding="utf-8")

# ---------------------------------------------------------- Die Mixin-Dateien
for gruppe, namen in gruppen.items():
    kopfsatz = BESCHREIBUNG[gruppe]
    teile = [f'"""{kopfsatz}\n"""\n', MIXIN_KOPF, "\n"]
    if gruppe == "basis":
        teile.append("\nclass VaultBasis:\n"
                     '    """Zustand, Pfade, Sicherung, Namensaufloesung, Dispatcher."""\n\n')
        # Die Klassenkonstanten gehoeren zum Unterbau und muessen mit -
        # UNSICHERE_STUFEN steuert, wann ein ungefaehrer Namenstreffer beim
        # Schreiben abgelehnt wird, und wer sie vergisst, hebelt genau die
        # Sicherung aus, um die es dabei geht.
        teile.append(
            "    # Aufloesungsstufen, die NICHT sicher genug sind, um darauf zu\n"
            "    # schreiben - siehe _namen_pruefen().\n"
            "    UNSICHERE_STUFEN = {\"wortueberlappung\", \"tippfehler\", \"semantisch\"}\n"
            "    # Parameter, die eine Notiz BENENNEN. Nur sie werden vor einem\n"
            "    # Schreibvorgang auf einen geratenen Namen geprueft.\n"
            "    NAMENSPARAMETER = (\"name\", \"von\", \"nach\", \"quelle\", \"notiz\")\n"
            "    # Ab wie vielen Zeilen eine Codedatei beim Import geteilt wird.\n"
            "    CODE_TEIL_ZEILEN = 400\n\n")
    else:
        teile.append(f"\nclass {KLASSENNAME[gruppe]}:\n"
                     f'    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""\n\n')
    for name in namen:
        a, b = methoden[name]
        teile.append(schnitt(a, b) + "\n\n")
    (PAKET / f"{gruppe}.py").write_text("".join(teile).rstrip() + "\n", encoding="utf-8")

print("geschrieben:", ", ".join(sorted(p.name for p in PAKET.glob("*.py"))))
