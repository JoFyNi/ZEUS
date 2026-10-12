"""Die zwei Datentypen, die Planer und Fachagenten gemeinsam brauchen.

Sie stehen in einer eigenen Datei, weil sie sonst einen Ring bauen: Der
Zerleger (`planung.py`) fragt die Fachagenten (`agenten.py`), und die
Fachagenten liefern Planschritte - also genau den Typ, der im Zerleger
definiert waere. `agenten` importiert `planung`, `planung` importiert
`agenten`, und die Architekturpruefung meldet zu Recht einen Zyklus.

Ein Ring waere hier nicht nur haesslich. Er macht die Importreihenfolge zum
Zufall: Wer zuerst geladen wird, sieht den anderen halb fertig, und der
Fehler zeigt sich an einer Stelle, die mit der Ursache nichts zu tun hat.

Die Regel, die daraus folgt und die dieses Projekt ohnehin schon zweimal
gezogen hat (siehe `werkzeuge/ergebnis.py` mit demselben Kopf): **Ein Typ,
den zwei Module brauchen, gehoert keinem von beiden.** Er kommt in eine
Datei ohne eigene Abhaengigkeiten, und beide holen ihn sich dort.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Planschritt:
    """Ein Schritt, wie ihn ein Zerleger liefert - noch ohne Datenbank.

    `braucht` nennt die Nummern der Schritte, deren Ergebnis dieser Schritt
    benoetigt - 1-basiert, damit die Nummern dieselben sind, die ein Mensch
    in der Anzeige liest. Wer hier 0-basiert zaehlt, erzeugt Plaene, die um
    eins verschoben sind, und das faellt erst beim dritten Schritt auf.
    """
    beschreibung: str
    werkzeug: str = ""
    parameter: dict = field(default_factory=dict)
    erwartung: str = ""
    braucht: List[int] = field(default_factory=list)

    def als_daten(self, nummer: int) -> dict:
        """Was davon in die Datenbank geht - das, was der Laeufer braucht."""
        return {"nummer": nummer, "werkzeug": self.werkzeug,
                "parameter": dict(self.parameter),
                "erwartung": self.erwartung}


@dataclass
class Zerlegung:
    """Das Ergebnis einer Zerlegung: geprueft, aber noch nicht gespeichert.

    `quelle` sagt, WER zerlegt hat - 'vorgabe', 'agent:recherche', 'modell'
    oder 'rueckfall'. Das ist kein Schmuck: Wenn ein Plan schiefgeht, ist
    die erste Frage, ob ihn ein Fachagent oder das Modell aufgestellt hat,
    und ohne diese Angabe laesst sie sich nicht beantworten.
    """
    schritte: List[Planschritt] = field(default_factory=list)
    begruendung: str = ""
    quelle: str = "modell"

    @property
    def leer(self) -> bool:
        return not self.schritte
