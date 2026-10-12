"""Ereignisbus: wie Module voneinander erfahren, ohne sich zu kennen.

Der Grund, warum das nicht einfach Methodenaufrufe sind: Sobald der Scheduler
das Gedächtnis anruft, wenn ein Auftrag fertig ist, und die Oberfläche
anruft, damit sie es anzeigt, und das Lernwerk anruft, damit es mitzählt,
kennt der Scheduler drei Module und lässt sich ohne sie nicht mehr testen.
Beim vierten Zuhörer wird er wieder angefasst. Über den Bus meldet er
"auftrag.fertig" und weiß nicht, wer zuhört.

Drei Festlegungen, die aus dem Betrieb kommen und nicht aus dem Lehrbuch:

1. ZUSTELLUNG IST SYNCHRON. Ein Ereignis wird sofort an alle Zuhörer gegeben,
   im Thread des Melders. Asynchron wäre eleganter und würde die Reihenfolge
   zerreißen - "Auftrag fertig" träfe womöglich vor "Auftrag gestartet" ein,
   und ein Protokoll, dessen Reihenfolge nicht stimmt, ist wertlos.

2. EIN ZUHÖRER, DER WIRFT, REISST NICHTS MIT. Er wird gezählt und nach zu
   vielen Fehlern abgehängt. Ein kaputtes Anzeigemodul darf keinen Auftrag
   scheitern lassen - das ist dieselbe Regel wie beim Slash-Befehl im
   Terminal, nur eine Ebene tiefer.

3. ES GIBT EINEN VERLAUF. Ein Ringpuffer der letzten Ereignisse. Ohne ihn ist
   die Frage "was ist gerade passiert" nach dem Neustart nicht mehr zu
   beantworten, und genau das ist die Frage, die man stellt.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

VERLAUF_LAENGE = 400
# Nach so vielen Ausnahmen wird ein Zuhörer abgehängt. Einer darf danebengehen
# - wer dauernd wirft, kostet bei jedem Ereignis Zeit und füllt das Protokoll.
FEHLERGRENZE = 5


@dataclass(frozen=True)
class Ereignis:
    name: str                       # "auftrag.fertig", "modul.gestoert", …
    quelle: str = ""                # welches Modul es gemeldet hat
    daten: dict = field(default_factory=dict)
    zeit: float = field(default_factory=time.time)

    def __str__(self) -> str:
        kurz = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(self.daten.items())[:3])
        return f"{self.name}" + (f" ({self.quelle})" if self.quelle else "") \
               + (f" [{kurz}]" if kurz else "")


@dataclass
class _Zuhörer:
    muster: str
    empfaenger: Callable[[Ereignis], None]
    beschreibung: str = ""
    fehler: int = 0
    abgehaengt: bool = False


class Bus:
    """Sehr kleiner Ereignisbus - synchron, threadsicher, mit Verlauf."""

    def __init__(self, verlauf_laenge: int = VERLAUF_LAENGE) -> None:
        self._sperre = threading.RLock()
        self._zuhoerer: List[_Zuhörer] = []
        self._verlauf: Deque[Ereignis] = deque(maxlen=verlauf_laenge)
        self._zaehler: Dict[str, int] = {}
        # Wohin Ausnahmen von Zuhörern gehen. Wird vom Kern auf das
        # Betriebsprotokoll gesetzt; ohne ihn gehen sie nirgendwohin, und
        # genau das wäre der stille Fehlschlag, den dieses Projekt nicht will.
        self.fehlermelder: Optional[Callable[[str], None]] = None

    # ---------------------------------------------------------- Abonnieren
    def abonnieren(self, muster: str, empfaenger: Callable[[Ereignis], None],
                   beschreibung: str = "") -> Callable[[], None]:
        """Hört auf Ereignisse. Gibt eine Funktion zum Abmelden zurück.

        Das Muster ist entweder ein voller Name ("auftrag.fertig"), ein
        Präfix mit Stern ("auftrag.*") oder "*" für alles.
        """
        eintrag = _Zuhörer(muster=str(muster or "*"), empfaenger=empfaenger,
                           beschreibung=beschreibung)
        with self._sperre:
            self._zuhoerer.append(eintrag)

        def abmelden() -> None:
            with self._sperre:
                if eintrag in self._zuhoerer:
                    self._zuhoerer.remove(eintrag)

        return abmelden

    # ------------------------------------------------------------- Melden
    def melden(self, name: str, quelle: str = "", daten: Optional[dict] = None,
               **weitere) -> Ereignis:
        """Ein Ereignis melden.

        Die Nutzlast laesst sich als Schluesselwoerter uebergeben - bequem,
        aber mit einer Falle: `name` und `quelle` sind hier schon vergeben.
        Wer ein Feld dieses Namens mitgeben will (und das kommt vor: eine
        Datei hat einen Namen, ein Auftrag eine Quelle), nimmt `daten={...}`.
        Ohne diesen Weg endete der Versuch in einem TypeError an einer
        Stelle, die mit dem Ereignis nichts zu tun hat.
        """
        nutzlast = dict(daten or {})
        nutzlast.update(weitere)
        ereignis = Ereignis(name=str(name), quelle=str(quelle or ""),
                            daten=nutzlast)
        with self._sperre:
            self._verlauf.append(ereignis)
            self._zaehler[ereignis.name] = self._zaehler.get(ereignis.name, 0) + 1
            # Kopie ziehen: Ein Zuhörer darf sich während der Zustellung
            # abmelden, ohne die Schleife unter sich zu verändern.
            empfaenger = [z for z in self._zuhoerer
                          if not z.abgehaengt and _passt(z.muster, ereignis.name)]
        for zuhoerer in empfaenger:
            try:
                zuhoerer.empfaenger(ereignis)
            except Exception as exc:      # bewusst breit - siehe Modulkopf
                zuhoerer.fehler += 1
                text = (f"Zuhörer '{zuhoerer.beschreibung or zuhoerer.muster}' hat bei "
                        f"'{ereignis.name}' geworfen ({type(exc).__name__}: {exc})")
                if zuhoerer.fehler >= FEHLERGRENZE:
                    zuhoerer.abgehaengt = True
                    text += f" – nach {zuhoerer.fehler} Fehlern abgehängt."
                if self.fehlermelder:
                    try:
                        self.fehlermelder(text)
                    except Exception:
                        pass
        return ereignis

    # ------------------------------------------------------------ Auskunft
    def verlauf(self, anzahl: int = 40, muster: str = "*") -> List[Ereignis]:
        with self._sperre:
            passend = [e for e in self._verlauf if _passt(muster, e.name)]
        return passend[-max(1, anzahl):]

    def zaehlerstand(self) -> Dict[str, int]:
        with self._sperre:
            return dict(self._zaehler)

    def zuhoerer(self) -> List[Tuple[str, str, int, bool]]:
        with self._sperre:
            return [(z.muster, z.beschreibung, z.fehler, z.abgehaengt)
                    for z in self._zuhoerer]

    def leeren(self) -> None:
        with self._sperre:
            self._verlauf.clear()
            self._zaehler.clear()


def _passt(muster: str, name: str) -> bool:
    muster = str(muster or "*")
    if muster in ("*", ""):
        return True
    if muster.endswith("*"):
        return name.startswith(muster[:-1])
    return name == muster
