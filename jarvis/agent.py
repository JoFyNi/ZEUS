"""Weiterleitung: Der Agent liegt jetzt im Paket `jarvis/gespraech/`.

Diese Datei war 2 100 Zeilen lang und enthielt sechs Sachgebiete
nebeneinander. Sie ist aufgeteilt worden - dieselben Methoden, dieselbe
Klasse, dieselben Namen. Wo was jetzt steht, sagt
`jarvis/gespraech/__init__.py`.

Wie bei `tools.py` bleibt die Weiterleitung stehen, damit `from .agent import
JarvisAgent` weiterlaeuft: Alle Importe im selben Schritt umzuschreiben waere
eine zweite Aenderung neben der Aufteilung, und wenn danach etwas nicht
laeuft, wuesste niemand, an welcher von beiden es lag.

Neuer Code nimmt `from .gespraech import ...`.
"""
from .gespraech import (  # noqa: F401  (Weiterleitung - hier absichtlich ungenutzt)
    AUTOMATIK_BEHAUPTUNG,
    Answer,
    ERFOLGS_BEHAUPTUNG,
    FolderDecision,
    GEDAECHTNIS_ANTEIL,
    JarvisAgent,
    ToolCallRecord,
    VERAENDERNDE_WERKZEUGE,
    _argumente_lesen,
    _aufruf_aus_text,
    _behauptet_erfolg,
    _fertigkeit_json_aus_text,
    _parse_action,
)

__all__ = [
    "AUTOMATIK_BEHAUPTUNG", "Answer", "ERFOLGS_BEHAUPTUNG", "FolderDecision",
    "GEDAECHTNIS_ANTEIL", "JarvisAgent", "ToolCallRecord",
    "VERAENDERNDE_WERKZEUGE",
]
