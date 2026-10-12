"""Werkzeuge: der Schreibzugriff des Agenten auf die Vault.

Alles, was den Vault-Inhalt veraendert, laeuft durch `VaultTools` - und zwar
so, dass es sich rueckgaengig machen laesst:

* Jede Aenderung sichert die betroffenen Dateien vorher nach `.jarvis/backups/`.
* Jede Aenderung wird in `.jarvis/journal.jsonl` protokolliert.
* `rueckgaengig()` stellt den Stand vor der letzten Aktion wieder her.
* Geloescht wird nie wirklich - Notizen wandern in den Papierkorb-Ordner.

Pfade werden hart auf die Vault begrenzt: `..` oder absolute Pfade ausserhalb
der Vault werden abgelehnt.

Wo was steht
------------

    ergebnis.py      ToolResult, WriteError - was ein Werkzeug zurueckgibt
    hilfen.py        Textarbeit ohne Zustand: Abschnitte, Frontmatter, PDF
    schemas.py       Die Werkzeugliste und alles, was daraus abgeleitet wird
    basis.py         Unterbau: Pfade, Namen, Schreiben, Sicherung, Dispatcher
    lesen.py         Suchen, Lesen, Struktur, verwandte Notizen
    notizen.py       Anlegen, Aendern, Verschieben, Loeschen, Zurueckholen
    listen.py        Listenpunkte einreihen und abhaken
    abschnitte.py    Gezielt an EINER Stelle aendern
    verknuepfen.py   Links, Uebersichten, Zusammenfuehren, Aufteilen
    code.py          Selbstinspektion - nur lesend
    einlesen.py      Fremde Dateien und Ordner importieren
    wartung.py       Die Vault als Ganzes pruefen
    zeichnen.py      Ablaeufe zeichnen
    zusammenbau.py   Setzt daraus VaultTools zusammen

`jarvis/tools.py` bleibt als Weiterleitung bestehen, damit vorhandene Importe
weiterlaufen.
"""
from .ergebnis import ToolResult, WriteError, _Transaktion
from .hilfen import (IMPORT_ENDUNGEN, PROSA_ENDUNGEN, SPRACH_ENDUNGEN,
                     safe_path_fragment)
from .schemas import (NUR_LESENDE_WERKZEUGE, PARAM_SYNONYME,
                      SCHREIBENDE_WERKZEUGE, TOOL_NAMES, TOOL_PARAMS,
                      TOOL_REQUIRED, TOOL_SCHEMAS, ollama_tool_definitions,
                      tool_reference)
from .zusammenbau import VaultTools

__all__ = [
    "IMPORT_ENDUNGEN", "NUR_LESENDE_WERKZEUGE", "PARAM_SYNONYME",
    "PROSA_ENDUNGEN", "SCHREIBENDE_WERKZEUGE", "SPRACH_ENDUNGEN", "TOOL_NAMES",
    "TOOL_PARAMS", "TOOL_REQUIRED", "TOOL_SCHEMAS", "ToolResult", "VaultTools",
    "WriteError", "_Transaktion", "ollama_tool_definitions",
    "safe_path_fragment", "tool_reference",
]
