"""Schleifenschutz: Ein Aufruf, dessen Ergebnis schon vorliegt, wird nicht wiederholt.

Der Anlass (claude/PROMPT_Antwortpflicht_und_Triage.md, M3): dreimal
"Identischer Aufruf von notiz_lesen". Die alte Sperre in `_run_tool` griff
erst, NACHDEM der Schritt verbraucht war - drei von acht Schritten gingen fuer
nichts drauf. Und sie verglich den rohen JSON-Text: `"Einkauf"` und
`" einkauf "` galten als verschieden und liefen beide.

Hier wird VOR der Ausfuehrung geprueft, und eine Abweisung kostet keinen
Schritt. Dafuer gibt es einen eigenen Deckel (MAX_ABWEISUNGEN): Sonst waere
die kostenlose Abweisung die naechste Endlosschleife. Ist er erreicht, geht
die Runde direkt in die Schlussrunde (antwortpflicht.py).

Die Abweisung sagt dem Modell nicht nur "identisch", sondern gibt ihm das
Ergebnis zurueck, das schon vorliegt. Meist hatte es genau das verloren -
siehe den Kommentar zu `_schleife_kuerzen`.

Nahezu gleiche Suchen
---------------------
Fuer Suchwerkzeuge reicht Gleichheit nicht: "offene Aufgaben To-do wa steht
an" und "offene Aufgaben To-do wa steht an bitte" sind dieselbe Suche. Sie
werden nach Kleinschreibung und ohne Fuellwoerter als Wortmengen verglichen
(Jaccard). Der Schwellwert SUCH_AEHNLICHKEIT steht in config.py; die
Begruendung mit Beispielen aus dem Testsatz:

    "offene Aufgaben To-do wa steht an"  vs  "... bitte"            1.00  abweisen
    "Allplan 2026 Systemvoraussetzungen" vs  "... Hersteller"        0.75  abweisen
    "wa steht an"                        vs  "offene Aufgaben To-do
                                              wa steht an"           0.40  laufen
    "Sandkisten"                         vs  "Sandkisten Konzept"    0.50  laufen

0.6 trennt "dasselbe anders gesagt" von "enger/weiter gefasst". Darunter
wuerde eine echte Verfeinerung abgewiesen, darueber kaemen Umformulierungen
wieder durch - das Muster vom 21.09. (acht Varianten derselben Websuche).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Set, Tuple

# Welcher Parameter bei einem Suchwerkzeug die Suchanfrage traegt.
SUCHPARAMETER = {"notizen_suchen": "frage", "web_suchen": "frage",
                 "code_suchen": "begriff"}

# Parameter, die einen Notiz- oder Dateinamen tragen: Pfadtrenner und
# Endung werden angeglichen.
_NAMENSPARAMETER = {"name", "titel", "von", "nach", "quelle", "ziel", "datei",
                    "ordner", "pfad"}

STOPPWOERTER = frozenset("""
    der die das den dem des ein eine einen einem einer eines und oder aber
    was wie wo wer wann warum ist sind war waren an in im auf aus zu zum zur
    mit von vom fuer für ueber über bei nach vor bitte mir mich mein meine
    meinen meiner ich du er sie es wir ihr alle alles etwas noch schon auch
    nur mal doch ja nein nicht kein keine the a an of to and or is are
""".split())


def _wert_normalisieren(schluessel: str, wert):
    if isinstance(wert, str):
        text = re.sub(r"\s+", " ", wert.replace("\\", "/")).strip().lower()
        if schluessel in _NAMENSPARAMETER:
            text = text.strip("/")
            if text.endswith(".md"):
                text = text[:-3]
        return text
    if isinstance(wert, list):
        return [_wert_normalisieren(schluessel, w) for w in wert]
    if isinstance(wert, dict):
        return {k: _wert_normalisieren(k, v) for k, v in wert.items()}
    return wert


def aufrufschluessel(name: str, args: dict) -> str:
    """Kanonischer Schluessel: (werkzeug, sortiertes JSON nach Normalisierung)."""
    norm = {str(k): _wert_normalisieren(str(k), v) for k, v in (args or {}).items()
            if v not in (None, "")}
    try:
        fest = json.dumps(norm, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        fest = str(sorted(norm.items()))
    return f"{name}|{fest}"


def suchwoerter(text: str) -> Set[str]:
    woerter = re.findall(r"[\w\-]+", str(text or "").lower())
    return {w for w in woerter if len(w) > 1 and w not in STOPPWOERTER}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def kurzfassung(ergebnis, laenge: int = 400) -> str:
    try:
        text = ergebnis.to_model()
    except Exception:
        text = str(getattr(ergebnis, "meldung", "") or "")
    return text if len(text) <= laenge else text[:laenge] + " …[gekürzt]"


class Schleifenschutz:
    """Merkt sich, was in DIESER Runde schon geholt wurde."""

    def __init__(self, schwelle: float = 0.6, deckel: int = 3) -> None:
        self.schwelle = float(schwelle)
        self.deckel = max(1, int(deckel))
        self.abweisungen = 0
        self._ergebnisse: Dict[str, str] = {}
        self._suchen: List[Tuple[str, Set[str], str, str]] = []

    @property
    def erschoepft(self) -> bool:
        return self.abweisungen >= self.deckel

    def pruefen(self, name: str, args: dict) -> str:
        """Grund fuer eine Abweisung - oder ''. Zaehlt die Abweisung mit."""
        schluessel = aufrufschluessel(name, args)
        if schluessel in self._ergebnisse:
            return self._abweisen(
                f"Diesen Aufruf ({name}) hast du in dieser Runde schon gemacht. "
                f"Das Ergebnis liegt bereits vor: {self._ergebnisse[schluessel]} "
                f"Benutze es, statt es erneut zu holen.")
        param = SUCHPARAMETER.get(name)
        if param:
            woerter = suchwoerter((args or {}).get(param, ""))
            for frueher_name, frueher, anfrage, kurz in self._suchen:
                if frueher_name != name:
                    continue
                aehnlich = jaccard(woerter, frueher)
                if aehnlich >= self.schwelle:
                    return self._abweisen(
                        f"Diese Suche gleicht '{anfrage}' zu {aehnlich:.0%} - "
                        f"die lief in dieser Runde schon. Ergebnis damals: {kurz} "
                        f"Eine Umformulierung findet nichts Neues; arbeite mit "
                        f"dem Ergebnis oder suche nach etwas anderem.")
        return ""

    def vermerken(self, name: str, args: dict, ergebnis) -> None:
        kurz = kurzfassung(ergebnis)
        self._ergebnisse[aufrufschluessel(name, args)] = kurz
        param = SUCHPARAMETER.get(name)
        if param:
            anfrage = str((args or {}).get(param, ""))
            self._suchen.append((name, suchwoerter(anfrage), anfrage[:80], kurz))

    def _abweisen(self, grund: str) -> str:
        self.abweisungen += 1
        return grund
