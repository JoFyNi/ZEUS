"""Ein Auftrag: die kleinste Einheit Arbeit, die der Kern verteilen kann.

Ein Auftrag ist bewusst mehr als eine Funktion in einer Warteschlange. Er
trägt vier Dinge mit sich, die man sonst später schmerzlich vermisst:

* EINEN ZUSTAND. Ohne ihn ist "läuft der noch oder hängt der?" nicht zu
  beantworten - und das ist die Frage, die man bei unbeaufsichtigter Arbeit
  als erste stellt.
* EINEN ABBRUCHSCHALTER. Ein Auftrag, den man nur durch Beenden des Programms
  loswird, ist bei einem System, das dauerhaft im Hintergrund läuft, keine
  Option.
* EINE VERSUCHSZÄHLUNG. Ein Netzfehler zum Ollama-Server ist vorübergehend
  und darf wiederholt werden. Ein Fehler in der Aufgabe selbst nicht - der
  wiederholt sich beliebig oft mit demselben Ergebnis.
* DIE ANGABE, OB ER IN DIE VAULT SCHREIBT. Daran hängt, ob er parallel laufen
  darf. Zwei Aufträge, die gleichzeitig dieselbe Notiz ergänzen, erzeugen
  genau den stillen Datenverlust, gegen den das ganze Werkzeugsystem
  abgesichert ist - nur eine Ebene darüber, wo die Sicherung nicht greift.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

_ZAEHLER = itertools.count(1)


class Auftragszustand(str, Enum):
    WARTET = "wartet"            # in der Warteschlange
    GEPLANT = "geplant"          # wartet auf seinen Zeitpunkt
    LAEUFT = "laeuft"
    FERTIG = "fertig"
    GESCHEITERT = "gescheitert"
    ABGEBROCHEN = "abgebrochen"

    @property
    def endgueltig(self) -> bool:
        return self in (Auftragszustand.FERTIG, Auftragszustand.GESCHEITERT,
                        Auftragszustand.ABGEBROCHEN)

    def __str__(self) -> str:
        return self.value


# Prioritäten. Kleinere Zahl geht zuerst - wie bei nice(1), damit niemand
# raten muss, ob "hoch" nun 0 oder 100 heißt.
SOFORT = 0        # der Nutzer wartet darauf
NORMAL = 50       # angeforderte Arbeit ohne Wartenden
HINTERGRUND = 90  # Wartung, Aufräumen, Nachdenken


class Abgebrochen(Exception):
    """Wird von der Aufgabe geworfen, wenn sie den Abbruch bemerkt."""


@dataclass
class Auftrag:
    art: str                                   # "wartung", "autolauf", "notiz" …
    beschreibung: str
    aufgabe: Callable[["Auftrag"], Any]        # bekommt sich selbst - für abbruch()
    prioritaet: int = NORMAL
    braucht_vault: bool = False                # schreibt er? dann nur einer zugleich
    max_versuche: int = 1
    faellig_ab: float = 0.0                    # 0 = sofort
    wiederholen_alle: float = 0.0              # >0 = danach neu einplanen
    quelle: str = ""                           # wer ihn eingereicht hat

    # --- vom Planer gefüllt ---
    id: int = field(default_factory=lambda: next(_ZAEHLER))
    zustand: Auftragszustand = Auftragszustand.WARTET
    eingereiht: float = field(default_factory=time.time)
    gestartet: float = 0.0
    beendet: float = 0.0
    versuche: int = 0
    ergebnis: Any = None
    fehler: str = ""
    _abbruch: threading.Event = field(default_factory=threading.Event, repr=False)
    _fertig: threading.Event = field(default_factory=threading.Event, repr=False)

    # ------------------------------------------------------------- Abbruch
    def abbrechen(self) -> None:
        """Bittet den Auftrag aufzuhören. Erzwingt nichts.

        Einen laufenden Thread hart abzuschießen gibt es in Python nicht, und
        es wäre auch falsch: Der Auftrag könnte mitten in einem Schreibvorgang
        stehen. Stattdessen wird der Schalter gesetzt; die Aufgabe fragt ihn
        an ihren Zwischenschritten ab. Eine Aufgabe, die nie fragt, läuft zu
        Ende - das ist ehrlicher als ein halb geschriebener Zustand.
        """
        self._abbruch.set()

    @property
    def abgebrochen(self) -> bool:
        return self._abbruch.is_set()

    def haltepunkt(self) -> None:
        """Von der Aufgabe zwischen ihren Schritten aufzurufen."""
        if self._abbruch.is_set():
            raise Abgebrochen(f"Auftrag {self.id} ({self.beschreibung}) abgebrochen.")

    # -------------------------------------------------------------- Warten
    def warten(self, sekunden: Optional[float] = None) -> bool:
        """Blockiert, bis der Auftrag endgültig ist. True, wenn er es wurde."""
        return self._fertig.wait(sekunden)

    def _abschliessen(self, zustand: Auftragszustand) -> None:
        self.zustand = zustand
        self.beendet = time.time()
        self._fertig.set()

    # ------------------------------------------------------------ Auskunft
    @property
    def dauer(self) -> float:
        if not self.gestartet:
            return 0.0
        return (self.beendet or time.time()) - self.gestartet

    def als_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "art": self.art,
            "beschreibung": self.beschreibung,
            "zustand": str(self.zustand),
            "prioritaet": self.prioritaet,
            "braucht_vault": self.braucht_vault,
            "versuche": self.versuche,
            "max_versuche": self.max_versuche,
            "dauer": round(self.dauer, 2),
            "quelle": self.quelle,
            "fehler": self.fehler,
            "wiederholen_alle": self.wiederholen_alle,
        }

    def __lt__(self, andere: "Auftrag") -> bool:
        # Für die Vorrangwarteschlange: erst Priorität, bei Gleichstand die
        # ältere Nummer. Ohne den zweiten Schlüssel vergleicht die
        # Warteschlange bei gleicher Priorität die Aufträge selbst - und käme
        # bei nicht vergleichbaren Feldern ins Straucheln.
        return (self.prioritaet, self.id) < (andere.prioritaet, andere.id)

    def __repr__(self) -> str:
        return f"<Auftrag {self.id} {self.art}/{self.zustand}: {self.beschreibung[:40]}>"
