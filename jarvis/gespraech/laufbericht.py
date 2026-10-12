"""Was in einer Gespraechsrunde WIRKLICH geschah - gezaehlt, nicht erzaehlt.

Zwei Abnehmer:

1. Die Messung. Ohne Zahlen laesst sich nicht belegen, dass eine Runde mit
   einer Antwort endet, statt mit "Erledigt: ...".
2. Der Weg DIREKT. Fragt der Nutzer "warum hast du mir nicht geantwortet?",
   steht die wahre Antwort hier - welcher Weg gewaehlt wurde, welche Werkzeuge
   mit welchem Ausgang liefen, wie die Runde endete. Laesst man das Modell
   seine letzte Runde nacherzaehlen, erfindet es eine plausible Geschichte;
   dieser Bericht wird aus den Zaehlern gebaut und kann nicht beschoenigen.

Rundenenden (`endete_mit`):

    prosa          Das Modell hat in der Schleife selbst geantwortet.
    schlussrunde   Erst die Runde ohne Werkzeuge brachte die Antwort.
    hinweis        Auch die nicht - der Nutzer bekam den ehrlichen Grund.
    notbremse      Der Protokollauszug `_schlussbericht`. Soll nie vorkommen.
    direkt         Weg DIREKT, ohne Abruf und ohne Werkzeuge beantwortet.
    plan           Der Schreibplaner hat die Runde abgeschlossen.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Tuple

# Wie viele Runden fuer "was hast du gerade gemacht?" im Gedaechtnis bleiben.
LAUFBERICHT_RUNDEN = 3


@dataclass
class Runde:
    eingabe: str
    weg: str = ""
    folge: str = ""                 # zweiter Weg bei kombinierten Auftraegen
    quelle: str = ""                # regel | bewerter | rueckfall
    begruendung: str = ""
    phase: str = ""                 # wer gerade das Modell ruft
    werkzeuge: List[Tuple[str, bool]] = field(default_factory=list)
    abgewiesen: int = 0
    endete_mit: str = ""
    notbremse: int = 0
    # Was das Arbeitsgedaechtnis (kontextwahl.py) und die Pruefung
    # (pruefung.py) beigetragen haben.
    erinnert: int = 0
    bezuege: int = 0
    widersprueche: int = 0
    befunde: int = 0

    @property
    def weg_text(self) -> str:
        return self.weg + (f"+{self.folge}" if self.folge else "")

    def werkzeug_vermerken(self, name: str, ok: bool) -> None:
        self.werkzeuge.append((name, bool(ok)))

    def zeile(self) -> str:
        """Eine Zeile, deterministisch aus den Zaehlern."""
        teile = [f"Eingabe „{self.eingabe[:80]}“"]
        if self.weg:
            teile.append(f"Weg {self.weg_text.upper()} ({self.quelle or '?'})")
        if self.werkzeuge:
            gezaehlt: dict = {}
            for name, ok in self.werkzeuge:
                schluessel = (name, ok)
                gezaehlt[schluessel] = gezaehlt.get(schluessel, 0) + 1
            teile.append("Werkzeuge: " + ", ".join(
                f"{name} {'ok' if ok else 'FEHLER'}" + (f" x{n}" if n > 1 else "")
                for (name, ok), n in gezaehlt.items()))
        else:
            teile.append("keine Werkzeuge")
        if self.abgewiesen:
            teile.append(f"{self.abgewiesen} Aufruf(e) abgewiesen")
        if self.erinnert or self.bezuege or self.widersprueche:
            teile.append(f"Gedächtnis: {self.erinnert} frühere Stelle(n), "
                         f"{self.bezuege} Bezug/Bezüge, {self.widersprueche} Widerspruch/"
                         f"Widersprüche")
        if self.befunde:
            teile.append(f"{self.befunde} Prüfbefund(e)")
        teile.append(f"endete mit: {_ENDEN.get(self.endete_mit, self.endete_mit or '?')}")
        return "; ".join(teile)


_ENDEN = {
    "prosa": "einer Antwort in Prosa",
    "schlussrunde": "einer Antwort aus der Schlussrunde ohne Werkzeuge",
    "hinweis": "dem Hinweis, dass keine Antwort zustande kam",
    "notbremse": "einem Protokollauszug ('Erledigt: ...') statt einer Antwort",
    "direkt": "einer direkten Antwort ohne Abruf",
    "plan": "dem Bericht des Schreibplaners",
    "leer": "gar keinem Text",
    "fehler": "einem Verbindungsfehler",
}


class Laufbericht:
    """Die letzten Runden - fuer den Weg DIREKT und fuer `/kern`."""

    def __init__(self, runden: int = LAUFBERICHT_RUNDEN) -> None:
        self._runden: Deque[Runde] = deque(maxlen=max(1, runden))

    def ablegen(self, runde: Runde) -> None:
        self._runden.append(runde)

    @property
    def runden(self) -> List[Runde]:
        return list(self._runden)

    def text(self) -> str:
        if not self._runden:
            return "Vor dieser Frage gab es in diesem Gespraech noch keine Runde."
        zeilen = []
        gesamt = len(self._runden)
        for i, runde in enumerate(self._runden, 1):
            wann = "letzte Runde" if i == gesamt else f"vor {gesamt - i + 1} Runden"
            zeilen.append(f"- {wann}: {runde.zeile()}")
        return "\n".join(zeilen)
