"""Muster und Konstanten - alles, was erkannt statt gerechnet wird.

Jeder regulaere Ausdruck hier ist aus einem konkreten Fehlverhalten
entstanden, und ueber jedem steht, aus welchem. Wer einen lockert, sollte
den Fall darueber lesen: Er kommt zurueck.
"""
from __future__ import annotations

import re

from ..tools import SCHREIBENDE_WERKZEUGE



STRUCTURE_PATTERNS = re.compile(
    r"\b(ordner|verzeichnis|struktur|baum|übersicht|uebersicht|wie viele notizen|"
    r"welche notizen|welche dateien|was liegt in|inhaltsverzeichnis|vault)\b",
    re.IGNORECASE,
)
FOLDER_HINT_RE = re.compile(r"@([\wÄÖÜäöüß /\-]+?)(?=\s|$)")

# Werkzeuge, die die Vault verändern - nur deren Ergebnis muss belegt werden.
# Kommt aus tools.py, damit Prüfung und Probelauf dieselbe Liste benutzen.
VERAENDERNDE_WERKZEUGE = set(SCHREIBENDE_WERKZEUGE)

# Anteil des Promptbudgets, den sich Lektionen, Sitzungsfakten und die
# Anknüpfung an die letzte Sitzung TEILEN. Bei NUM_CTX=8192 sind das rund
# 2 800 von 14 000 Zeichen; der Rest bleibt für Struktur, Werkzeuge, Regeln
# und vor allem den Notizkontext. Zwei getrennte Kontingente hätten hier
# unweigerlich den Notizkontext aufgefressen - siehe _gedaechtnisblock().
GEDAECHTNIS_ANTEIL = 0.20

# Eine Fertigkeit ANLEGEN heißt nicht, sie AUSZUFÜHREN. Das Modell hat aus
# "Fertigkeit angelegt" schon "die Ergänzungen wurden durchgeführt" gemacht
# und Ergebnisse berichtet, die nie entstanden sind.
NUR_DEFINIERENDE_WERKZEUGE = {"fertigkeit_definieren", "verhalten_definieren"}

# Behauptungen über wiederkehrende Ausführung. Es gibt keinen Scheduler -
# "wird nun regelmäßig ausgeführt" ist immer falsch.
AUTOMATIK_BEHAUPTUNG = re.compile(
    r"\b(regelmäßig|regelmaessig|automatisch|selbstständig|selbststaendig|"
    r"ab\s+jetzt|von\s+nun\s+an|periodisch|im\s+Hintergrund|zyklisch|"
    r"regularly|automatically|from\s+now\s+on)\b"
    r"[^.]{0,60}\b(ausgeführt|ausgefuehrt|laufen|läuft|laeuft|angewendet|"
    r"aufgerufen|executed|run)\b",
    re.IGNORECASE,
)

# Formulierungen, mit denen ein Modell Erfolg behauptet
ERFOLGS_BEHAUPTUNG = re.compile(
    r"\b(erfolgreich|wurde\s+(?:verschoben|angelegt|erstellt|gelöscht|geloescht|"
    r"ergänzt|ergaenzt|aktualisiert|gespeichert)|habe\s+(?:ich\s+)?"
    r"(?:verschoben|angelegt|erstellt|gelöscht|geloescht|ergänzt|ergaenzt)|"
    r"ist\s+(?:jetzt|nun)\s+(?:in|unter)|erledigt|fertig|"
    r"successfully|has\s+been\s+(?:moved|created|deleted|updated))\b",
    re.IGNORECASE,
)


# Wenn ein Werkzeug wiederholt falsch benutzt wird, sagt die Sperre nicht nur
# "geht nicht", sondern welches Werkzeug gemeint war. Genau daran ist der Lauf
# vom 22.08. gescheitert: Das Modell wollte tote Links entfernen, griff zu
# notizen_verknuepfen(nach="") und probierte es fünfmal.
_WERKZEUG_HINWEISE = {
    "notizen_verknuepfen": "Zum ENTFERNEN toter Links: links_bereinigen(name=…). "
                           "Zum Umbiegen auf eine ähnliche Notiz: "
                           "links_pruefen(reparieren=true).",
    "notiz_ergaenzen": "Gibt es die Notiz nicht, zuerst notiz_erstellen.",
    "notiz_verschieben": "Zielordner muss als Text angegeben werden, nicht als Liste.",
    "notizen_zusammenfuehren": "quellen ist eine LISTE von Notiznamen, titel ist das Ziel.",
}


def _behauptet_erfolg(text: str) -> bool:
    return bool(ERFOLGS_BEHAUPTUNG.search(text or ""))
ACTION_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


FEHLERFRAGE = re.compile(
    r"\b(warum|wieso|weshalb)\b[^?]{0,80}\b(nicht|fehl\w*|schief|problem\w*)\b"
    r"|\bnicht\s+(?:geschafft|geklappt|funktioniert|gelungen)\b"
    r"|\b(?:fehlgeschlagen|fehlschlag|fehler)\b"
    r"|\b(?:schaffst|kannst|konntest)\s+du\b[^?]{0,60}?\bnicht\b",
    re.IGNORECASE,
)


def _fragt_nach_fehlern(text: str) -> bool:
    return bool(FEHLERFRAGE.search(text or ""))


def _ist_wiederholung(text: str) -> bool:
    return bool(re.search(
        r"\b(nochmal|noch mal|erneut|wiederhol\w*|versuch\w+\s+es|try again|retry)\b",
        text or "", re.IGNORECASE,
    ))


# Kurz UND ohne eigenständiges Objekt - "dann erstelle sie", "ja, mach es",
# "leg sie an", "tu es", "los", "ok" beziehen sich auf die VORIGE Runde, nicht
# auf etwas Neues. Absichtlich eng: "erstelle eine Notiz über Katzen" ist
# länger und hat ein eigenes Objekt, soll also NICHT hier durchfallen.
_KURZE_BESTAETIGUNG_RE = re.compile(
    r"^\s*(?:dann\s+|ja[,.]?\s*|bitte\s+)*"
    r"(?:mach(?:e)?(?:\s+(?:es|das|sie|ihn))?|leg(?:e)?\s+(?:sie|es|das|ihn)\s*an|"
    r"erstell\w*\s+(?:sie|es|das|ihn)|tu(?:e)?\s+es|los\s*geht'?s?|los|ok(?:ay)?|bitte)"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _ist_kurze_bestaetigung(text: str) -> bool:
    """Eine knappe Zustimmung/Anweisung ohne eigenes Thema - sie übernimmt das
    Thema der vorigen Runde, statt eines zu benennen."""
    text = (text or "").strip()
    return bool(text) and len(text) <= 60 and bool(_KURZE_BESTAETIGUNG_RE.match(text))


def _extract_context(system_prompt: str) -> str:
    marker = "KONTEXT AUS DEN NOTIZEN:\n"
    if marker in system_prompt:
        return system_prompt.split(marker, 1)[1]
    return ""
