"""Das Modul, das den Aufgabenstapel hochfaehrt und an den Kern haengt.

Warum ein eigenes Modul und nicht ein paar Zeilen in `aufbau.py`: Der Stapel
ist das Teil, das am ehesten NICHT hochkommt - eine gesperrte Datenbank, ein
schreibgeschuetzter Ordner, eine Vault auf einem getrennten Netzlaufwerk.
Als Modul hat er dieselben Eigenschaften wie alles andere am Kern:

* Er wird benannt gestoert statt still kaputt (`/kern` zeigt es).
* Er reisst nichts mit, weil nichts von ihm abhaengt, was ohne ihn nicht
  laufen koennte - das Gespraech laeuft weiter, nur ohne persistente Ziele.
* Er faehrt geordnet herunter: Arbeiter erst zu Ende arbeiten lassen, dann
  die Datenbank schliessen. Andersherum schreibt ein Arbeiter in eine
  geschlossene Datei, und das ist der Fehler, den man erst beim naechsten
  Start sieht.

Die Reihenfolge beim Hochfahren hat einen Grund:

    1. Speicher       ohne ihn geht nichts
    2. Wiederaufnahme WAS lief beim letzten Mal? Vor dem ersten Arbeiter,
                      sonst greift sich einer eine Aufgabe, deren Stand
                      noch nicht beurteilt ist.
    3. Tor, Laeufer   die Fachteile
    4. Regelwerk      erst jetzt am Bus anmelden - vorher koennte ein
                      Ereignis ein Ziel erzeugen, das niemand bearbeitet
    5. Arbeiter       ab Rechtestufe 2, sonst ruht der Stapel sichtbar
    6. Zeitgeber      zuletzt, aus demselben Grund wie 4
"""
from __future__ import annotations

import time
from typing import Optional

from .ablauf import Ablaufwerk
from .ausfuehrung import Aufgabenlaeufer
from .bewertung import Aufgabenbewerter
from .modelle import Modellregister
from .router import Strategieweiche
from .aufgabenspeicher import Aufgabenspeicher
from .modul import Befund, Modul
from .planung import Zerleger
from .regelwerk import Regelwerk
from .warteschlange import Arbeiterstube
from .werkzeugtor import Werkzeugtor
from .zeitplan import Zeitgeber


class AufgabenModul(Modul):
    """Ziele, Aufgaben, Arbeiter, Termine, Regeln und Ablaeufe."""

    name = "aufgaben"
    beschreibung = "Persistente Ziele und Aufgaben, Arbeiter, Termine, Ablaeufe"
    wesentlich = False          # siehe Modulkopf: das Gespraech laeuft ohne
    abhaengig_von = ("gedaechtnis", "chat")

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.speicher: Optional[Aufgabenspeicher] = None
        self.stube: Optional[Arbeiterstube] = None
        self.zeitgeber: Optional[Zeitgeber] = None
        self.regelwerk: Optional[Regelwerk] = None
        self.ablaufwerk: Optional[Ablaufwerk] = None
        self.ruht = ""
        self.wiederaufgenommen = 0
        self.register: Optional[Modellregister] = None
        self.bewerter: Optional[Aufgabenbewerter] = None
        self.weiche: Optional[Strategieweiche] = None

    # ------------------------------------------------------------ Hochfahren
    def _starten(self, kern) -> None:
        cfg = self.agent.cfg
        if not bool(getattr(cfg, "aufgaben_aktiv", True)):
            self.ruht = "abgeschaltet (AUFGABEN_AKTIV=false)"
            return

        # 1. Speicher
        self.speicher = Aufgabenspeicher(cfg.work_dir / "aufgaben.sqlite3")
        kern.speicher = self.speicher

        # 2. Wiederaufnahme - VOR dem ersten Arbeiter.
        wieder = self.speicher.wiederaufnehmen()
        self.wiederaufgenommen = len(wieder)
        if wieder:
            kern.bus.melden("aufgaben.wiederaufnahme", quelle=self.name,
                            anzahl=len(wieder),
                            titel=[a.titel[:60] for a in wieder[:5]])

        # 3. Tor und Laeufer
        melder = self._melder()
        kern.tor = Werkzeugtor(
            self.agent.tools, cfg, rechte=kern.rechte, speicher=self.speicher,
            fertigkeiten=getattr(self.agent, "fertigkeiten", None),
            verhalten=getattr(self.agent, "verhalten", None),
            bus=kern.bus, melden=melder)
        # Einordnen und Routen - VOR dem Zerleger, denn er soll wissen,
        # womit er es zu tun hat.
        self.register = Modellregister(cfg=cfg, melden=melder).erfassen()
        for befund in self.register.pruefen():
            # Nicht nur ins Protokoll: Ein FAST_MODEL auf dem falschen Server
            # laesst jeden Routineaufruf ins 404 laufen, und das sieht man
            # sonst erst an der Langsamkeit.
            melder(f"Modellkonfiguration: {befund}")
            kern.bus.melden("modelle.passt_nicht", quelle=self.name,
                            befund=befund[:200])
        kern.modellregister = self.register

        self.bewerter = Aufgabenbewerter(
            llm=getattr(self.agent, "llm", None), cfg=cfg, melden=melder)
        kern.bewerter = self.bewerter
        self.weiche = Strategieweiche(register=self.register, cfg=cfg,
                                      melden=melder)
        kern.weiche = self.weiche

        zerleger = Zerleger(llm=getattr(self.agent, "llm", None), melden=melder)
        kern.laeufer = Aufgabenlaeufer(
            self.speicher, kern.tor, zerleger=zerleger,
            llm=getattr(self.agent, "llm", None), cfg=cfg, bus=kern.bus,
            # Wissen und Gedaechtnis des Agenten - dieselben Quellen, aus
            # denen auch das Gespraech schoepft. Zwei getrennte Gedaechtnisse
            # waeren zwei Wahrheiten.
            index=getattr(self.agent, "index", None),
            sitzung=getattr(self.agent, "sitzung", None),
            lernwerk=getattr(self.agent, "lernwerk", None),
            bewerter=self.bewerter, weiche=self.weiche,
            melden=melder, unbeaufsichtigt=True)
        self.ablaufwerk = Ablaufwerk(self.speicher, kern.tor, kern.laeufer,
                                     bus=kern.bus, melden=melder)
        kern.ablaufwerk = self.ablaufwerk

        # Das Gespraech bekommt dasselbe Tor. Ab hier gibt es genau EINEN
        # Weg zu einem Werkzeug - mit Recht, Budget, Freigabe, Idempotenz
        # und Verifikation. Vorher hatte die Gespraechsschleife ihren
        # eigenen, und auf dem wurde die Rechtestufe nie geprueft.
        try:
            self.agent.werkzeugtor_setzen(kern.tor)
        except AttributeError:
            pass

        self.stube = Arbeiterstube(
            self.speicher, kern.laeufer, bus=kern.bus, melden=melder,
            arbeiter=int(getattr(cfg, "aufgaben_arbeiter", 1) or 1))
        kern.stube = self.stube

        # 4. Regelwerk
        self.regelwerk = Regelwerk(kern.bus, kern.laeufer, melden=melder,
                                   rechte=kern.rechte)
        kern.regelwerk = self.regelwerk
        self.regelwerk.horchen()

        # 5./6. Arbeiter und Termine - nur unbeaufsichtigt, also ab Stufe 2.
        if not kern.rechte.verlangen("unbeaufsichtigt_arbeiten",
                                     "Aufgaben im Hintergrund abarbeiten"):
            # Kein Startfehler: Der Stapel LAEUFT, er arbeitet nur nichts von
            # selbst ab. Ziele lassen sich anlegen und im Vordergrund
            # abarbeiten (`/arbeiten`). Der Unterschied zwischen "kaputt" und
            # "darf nicht" gehoert sichtbar gemacht, nicht verwischt.
            self.ruht = kern.rechte.grund("unbeaufsichtigt_arbeiten")
            return

        self.stube.starten()
        self.zeitgeber = Zeitgeber(self.speicher, kern.laeufer, bus=kern.bus,
                                   rechte=kern.rechte, melden=melder)
        kern.zeitgeber = self.zeitgeber
        self.zeitgeber.starten()

    # ---------------------------------------------------------- Herunterfahren
    def _anhalten(self) -> None:
        # Reihenfolge umgekehrt zum Start: erst die, die schreiben, dann die
        # Datenbank. Ein Arbeiter, der in eine geschlossene Datei schreibt,
        # hinterlaesst einen Fehler, den man erst beim naechsten Start sieht.
        for teil in (self.zeitgeber, self.stube):
            if teil is not None:
                try:
                    teil.anhalten()
                except Exception:
                    pass
        if self.regelwerk is not None:
            try:
                self.regelwerk.schweigen()
            except Exception:
                pass
        if self.speicher is not None:
            try:
                self.speicher.schliessen()
            except Exception:
                pass

    # -------------------------------------------------------------- Auskunft
    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        if self.speicher is None:
            return Befund(True, f"ruht: {self.ruht or 'nicht aufgebaut'}",
                          {"ziele": 0, "offen": 0})
        try:
            zahlen = self.speicher.kennzahlen()
            ziele = len(self.speicher.ziele(nur_offene=True))
            briefe = len(self.speicher.tote_briefe(nur_ungesichtet=True))
        except Exception as exc:
            return Befund(False, f"Aufgabendatenbank antwortet nicht: {exc}", {})
        kennzahlen = {
            "ziele_offen": ziele,
            "aufgaben_offen": zahlen.get("offen", 0),
            "wartet_auf_dich": zahlen.get("wartet_auf_mensch", 0),
            "arbeiter": self.stube.arbeiterzahl if self.stube else 0,
            "briefe": briefe,
        }
        if self.ruht:
            return Befund(True, f"ruht: {self.ruht}", kennzahlen)
        if zahlen.get("wartet_auf_mensch"):
            # Eine Aufgabe, die auf einen Menschen wartet, ist kein Fehler -
            # aber sie ist auch nicht "alles in Ordnung". Sie gehoert an die
            # Stelle, die man ansieht.
            return Befund(False,
                          f"{zahlen['wartet_auf_mensch']} Aufgabe(n) warten auf "
                          f"deine Entscheidung – '/aufgaben' zeigt sie",
                          kennzahlen)
        if briefe:
            return Befund(False,
                          f"{briefe} endgueltig gescheiterte Aufgabe(n) liegen "
                          f"ungesichtet – '/aufgaben briefe'", kennzahlen)
        laeuft = "arbeitet" if (self.stube and self.stube.laeuft) else "bereit"
        return Befund(True, f"{laeuft}: {ziele} offene Ziele, "
                            f"{kennzahlen['aufgaben_offen']} offene Aufgaben",
                      kennzahlen)

    # ---------------------------------------------------------------- Helfer
    def _melder(self):
        """Wohin Meldungen des Stapels gehen: ins Betriebsprotokoll.

        Nicht auf die Konsole: Der Stapel laeuft im Hintergrund, und eine
        Zeile, die mitten in eine Antwort platzt, ist schlimmer als keine.
        """
        def melden(text: str) -> None:
            try:
                self.agent.tools.log.info(str(text)[:500], "aufgaben")
            except Exception:
                pass
        return melden
