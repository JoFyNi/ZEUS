"""Was ein Modul ist - und was mit einem kaputten Modul geschieht.

Der wichtigste Satz steht in `starten()`: **Ein Modul, das beim Start
scheitert, hält den Kern nicht auf.** Es wird als gestört vermerkt, der Grund
wird festgehalten, und alles andere fährt hoch.

Das ist keine Bequemlichkeit, sondern die Übertragung einer Regel, die dieses
Projekt schon kennt ("Kein Fehler darf die Sitzung mitreißen", ENTWICKLUNG.md)
auf eine Ebene tiefer. Ein Betriebssystem, das nicht startet, weil die
Sprachausgabe keine Stimme findet, ist schlechter als eines, das ohne Stimme
startet und das sagt.

Die Gegenprobe gehört dazu: Ein gestörtes Modul darf NICHT so tun, als liefe
es. `gesundheit()` gibt einen Befund zurück, und wer das Modul benutzen will,
bekommt eine Ausnahme statt eines halb funktionierenden Objekts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:                      # nur fürs Lesen, kein Importzyklus
    from .kernel import Zeus


class Modulzustand(str, Enum):
    NEU = "neu"                  # angemeldet, noch nicht gestartet
    STARTET = "startet"
    LAEUFT = "laeuft"
    ANGEHALTEN = "angehalten"
    GESTOERT = "gestoert"        # Start oder Betrieb gescheitert, Grund bekannt

    def __str__(self) -> str:
        return self.value


@dataclass
class Befund:
    """Was ein Modul über sich sagen kann - Zahlen, keine Selbsteinschätzung."""
    ok: bool = True
    meldung: str = ""
    kennzahlen: Dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        zeichen = "✔" if self.ok else "✖"
        zahlen = ", ".join(f"{k}={v}" for k, v in list(self.kennzahlen.items())[:5])
        return f"{zeichen} {self.meldung}" + (f"  ({zahlen})" if zahlen else "")


class ModulGestoert(RuntimeError):
    """Wird geworfen, wenn ein gestörtes Modul benutzt werden soll."""


class Modul:
    """Basisklasse. Ableitungen überschreiben `_starten` und `_anhalten`.

    Bewusst NICHT abstrakt: Ein Modul, das nur Ereignisse abonniert und sonst
    nichts hochfährt, soll ohne leere Methodenrümpfe auskommen.
    """

    #: Eindeutiger Name. Über ihn wird das Modul angesprochen.
    name: str = ""
    #: Ein Satz für die Statusanzeige.
    beschreibung: str = ""
    #: Namen der Module, die vorher laufen müssen.
    abhaengig_von: Sequence[str] = ()
    #: Ein Modul ohne dieses Kennzeichen darf ausfallen, ohne dass der Kern
    #: als gestört gilt. Chat und Gedächtnis sind wesentlich, die Stimme nicht.
    wesentlich: bool = False

    def __init__(self, name: str = "", beschreibung: str = "") -> None:
        self.name = name or self.name or type(self).__name__.lower()
        self.beschreibung = beschreibung or self.beschreibung
        self.zustand = Modulzustand.NEU
        self.stoerung: str = ""
        self.gestartet_um: float = 0.0
        self.kern: Optional["Zeus"] = None
        self._abmelder = []

    # ------------------------------------------------------------ Lebenslauf
    def starten(self, kern: "Zeus") -> bool:
        """Fährt das Modul hoch. Gibt zurück, ob es läuft.

        Wirft NICHT. Der Kern soll weiterfahren können; was schiefging, steht
        danach in `self.stoerung` und im Ereignis 'modul.gestoert'.
        """
        self.kern = kern
        self.zustand = Modulzustand.STARTET
        try:
            self._starten(kern)
        except Exception as exc:
            self.zustand = Modulzustand.GESTOERT
            self.stoerung = f"{type(exc).__name__}: {exc}"
            kern.bus.melden("modul.gestoert", quelle=self.name,
                            grund=self.stoerung, wesentlich=self.wesentlich)
            return False
        self.zustand = Modulzustand.LAEUFT
        self.stoerung = ""
        self.gestartet_um = time.time()
        kern.bus.melden("modul.gestartet", quelle=self.name,
                        beschreibung=self.beschreibung)
        return True

    def anhalten(self) -> None:
        """Fährt das Modul herunter. Wirft ebenfalls nicht.

        Beim Herunterfahren zählt nur eins: dass die übrigen Module danach auch
        noch drankommen. Ein Modul, dessen Stopp wirft, würde sonst alle
        folgenden offen lassen - Dateien, Threads, Verbindungen.
        """
        for abmelden in self._abmelder:
            try:
                abmelden()
            except Exception:
                pass
        self._abmelder = []
        try:
            self._anhalten()
        except Exception as exc:
            self.stoerung = f"beim Anhalten: {type(exc).__name__}: {exc}"
            if self.kern:
                self.kern.bus.melden("modul.stopp_fehler", quelle=self.name,
                                     grund=self.stoerung)
        self.zustand = Modulzustand.ANGEHALTEN
        if self.kern:
            self.kern.bus.melden("modul.angehalten", quelle=self.name)

    # ----------------------------------------------------- Für Ableitungen
    def _starten(self, kern: "Zeus") -> None:
        """Hier steht, was beim Hochfahren zu tun ist. Darf werfen."""

    def _anhalten(self) -> None:
        """Hier steht, was beim Herunterfahren aufzuräumen ist. Darf werfen."""

    def gesundheit(self) -> Befund:
        """Auskunft über den eigenen Zustand - mit Zahlen, wo es welche gibt."""
        if self.zustand is Modulzustand.GESTOERT:
            return Befund(False, self.stoerung or "gestört")
        if self.zustand is not Modulzustand.LAEUFT:
            return Befund(False, f"läuft nicht ({self.zustand})")
        return Befund(True, "läuft")

    # ------------------------------------------------------------- Hilfen
    def horchen(self, muster: str, empfaenger) -> None:
        """Ereignis abonnieren - wird beim Anhalten automatisch abgemeldet.

        Ohne diese Klammer bleibt nach einem Neustart des Moduls der alte
        Zuhörer hängen, und jedes Ereignis wird zweimal verarbeitet. Das ist
        beim Zählen sofort sichtbar und beim Schreiben schmerzhaft.
        """
        if not self.kern:
            raise ModulGestoert(f"'{self.name}' ist nicht am Kern angemeldet.")
        self._abmelder.append(
            self.kern.bus.abonnieren(muster, empfaenger, beschreibung=self.name))

    def pruefen(self) -> None:
        """Wirft, wenn das Modul nicht läuft. Für Aufrufer, die es benutzen."""
        if self.zustand is not Modulzustand.LAEUFT:
            raise ModulGestoert(
                f"Modul '{self.name}' läuft nicht ({self.zustand})"
                + (f": {self.stoerung}" if self.stoerung else ""))

    def __repr__(self) -> str:
        return f"<Modul {self.name} {self.zustand}>"
