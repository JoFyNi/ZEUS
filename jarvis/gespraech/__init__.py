"""Das Gespraech: der Agent und alles, was zu einer Antwort gehoert.

`agent.py` war mit 2 100 Zeilen die zweitgroesste Datei des Projekts und
enthielt sechs Sachgebiete nebeneinander: den Prompt, die Werkzeugschleife,
den Tatsachenbericht, die Ordnerwahl, den Schreibweg und das Herauslesen
eines Aufrufs aus Modellprosa. Die Aufteilung aendert am Verhalten nichts -
dieselben Methoden, dieselbe Klasse, dieselben Namen.

Wo was steht
------------

    antwort.py       ToolCallRecord, Answer, FolderDecision
    texte.py         Muster und Konstanten - jedes aus einem echten Fehlfall
    aufrufe.py       Einen Werkzeugaufruf aus Prosa herauslesen
    prompt.py        Was das Modell zu lesen bekommt, samt Budget
    werkzeuglauf.py  Vom Modellwunsch zum echten Werkzeugaufruf
    bericht.py       Was WIRKLICH passiert ist - gemessen, nicht behauptet
    ordner.py        In welchen Ordner eine Notiz gehoert
    schreibweg.py    Die Bruecke zum deterministischen Planer
    agent.py         JarvisAgent: Aufbau und der Weg einer Frage

`jarvis/agent.py` bleibt als Weiterleitung bestehen.
"""
from .agent import JarvisAgent
from .antwort import Answer, FolderDecision, ToolCallRecord
from .aufrufe import (_argumente_lesen, _aufruf_aus_text,
                      _fertigkeit_json_aus_text, _parse_action)
from .texte import (AUTOMATIK_BEHAUPTUNG, ERFOLGS_BEHAUPTUNG,
                    GEDAECHTNIS_ANTEIL, VERAENDERNDE_WERKZEUGE,
                    _behauptet_erfolg)

__all__ = [
    "AUTOMATIK_BEHAUPTUNG", "Answer", "ERFOLGS_BEHAUPTUNG",
    "FolderDecision", "GEDAECHTNIS_ANTEIL", "JarvisAgent", "ToolCallRecord",
    "VERAENDERNDE_WERKZEUGE", "_argumente_lesen", "_aufruf_aus_text",
    "_behauptet_erfolg", "_fertigkeit_json_aus_text", "_parse_action",
]
