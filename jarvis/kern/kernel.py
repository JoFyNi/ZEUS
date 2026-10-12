"""Der Kern selbst: anmelden, hochfahren, herunterfahren, Auskunft geben.

Was er tut, steht in vier Methoden. Was er NICHT tut, ist wichtiger:

* Er kennt keine Notiz, kein Modell, keine Fertigkeit. Fachwissen steht in den
  Modulen. Wer hier eine Vault-Operation einbaut, hat in einem halben Jahr
  wieder den Klumpen, den dieser Kern auflösen soll.
* Er hält keine Arbeit selbst. Arbeit läuft über den Planer.
* Er verspricht nichts. `status()` gibt zurück, was gemessen ist - welche
  Module laufen, welche gestört sind, was in der Warteschlange steht.

Die Startreihenfolge ergibt sich aus `Modul.abhaengig_von` und wird
topologisch sortiert. Ein Zyklus wird gemeldet und die betroffenen Module
werden hinten angehängt, statt in einer Endlosschleife zu enden - hochfahren
ist kein Ort für Rätsel.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from .auftrag import Auftrag, HINTERGRUND, NORMAL, SOFORT
from .bus import Bus
from .modul import Befund, Modul, Modulzustand
from .rechte import Rechteverwaltung
from .scheduler import Auftragsplaner


class Zeus:
    """Das Betriebssystem: Module, Bus, Planer, Rechte."""

    def __init__(self, cfg=None, arbeiter: int = 2, rechte_ablage=None,
                 rechte_stufe: Optional[int] = None) -> None:
        self.cfg = cfg
        self.bus = Bus()
        self.planer = Auftragsplaner(bus=self.bus, arbeiter=arbeiter)
        stufe = (rechte_stufe if rechte_stufe is not None
                 else getattr(cfg, "rechte_stufe", 1))
        self.rechte = Rechteverwaltung(stufe=stufe, ablage=rechte_ablage, bus=self.bus)

        self._module: Dict[str, Modul] = {}
        self._reihenfolge: List[str] = []
        self._sperre = threading.RLock()
        self.laeuft = False
        self.gestartet_um: float = 0.0
        self.startprobleme: List[str] = []

        # Der Aufgabenstapel. Er wird vom AufgabenModul eingehaengt, nicht
        # hier gebaut: Der Kern kennt keine Fachlogik, und Werkzeuge,
        # Modell und Fertigkeiten gehoeren dem Agenten. Bleibt alles None,
        # verhaelt sich Zeus wie vor dem Umbau - das ist die Rueckfallebene,
        # wenn der Stapel nicht hochkommt.
        self.speicher = None        # Aufgabenspeicher
        self.tor = None             # Werkzeugtor
        self.laeufer = None         # Aufgabenlaeufer
        self.stube = None           # Arbeiterstube
        self.zeitgeber = None       # Zeitgeber
        self.regelwerk = None       # Regelwerk
        self.ablaufwerk = None      # Ablaufwerk
        self.modellregister = None  # Modellregister
        self.bewerter = None        # Aufgabenbewerter
        self.weiche = None          # Strategieweiche

    # ------------------------------------------------------------- Anmelden
    def anmelden(self, modul: Modul) -> Modul:
        with self._sperre:
            if modul.name in self._module:
                raise ValueError(f"Es gibt bereits ein Modul namens '{modul.name}'.")
            self._module[modul.name] = modul
        self.bus.melden("modul.angemeldet", quelle=modul.name,
                        beschreibung=modul.beschreibung,
                        abhaengig_von=list(modul.abhaengig_von))
        return modul

    def modul(self, name: str) -> Optional[Modul]:
        return self._module.get(str(name))

    @property
    def module(self) -> Dict[str, Modul]:
        return dict(self._module)

    # ------------------------------------------------------------ Hochfahren
    def starten(self) -> bool:
        """Fährt alle Module hoch. Gibt zurück, ob alle WESENTLICHEN laufen.

        Ein gestörtes Nebenmodul hält nichts auf - siehe modul.py. Ein
        gestörtes wesentliches Modul auch nicht; der Kern läuft, meldet aber
        `False`, damit der Aufrufer entscheiden kann, ob er das hinnimmt.
        """
        if self.laeuft:
            return not self.startprobleme
        self.startprobleme = []
        self._reihenfolge = self._sortieren()
        self.planer.starten()

        for name in self._reihenfolge:
            modul = self._module[name]
            fehlend = [a for a in modul.abhaengig_von
                       if self._module.get(a) is None
                       or self._module[a].zustand is not Modulzustand.LAEUFT]
            if fehlend:
                # Ein Modul ohne seine Grundlage zu starten ist schlimmer als
                # es nicht zu starten: Es läuft scheinbar und fällt beim ersten
                # Zugriff um, an einer Stelle, die mit der Ursache nichts zu
                # tun hat.
                modul.zustand = Modulzustand.GESTOERT
                modul.stoerung = ("braucht " + ", ".join(fehlend)
                                  + " – das/die läuft nicht")
                self.bus.melden("modul.gestoert", quelle=name, grund=modul.stoerung,
                                wesentlich=modul.wesentlich)
                self.startprobleme.append(f"{name}: {modul.stoerung}")
                continue
            if not modul.starten(self):
                self.startprobleme.append(f"{name}: {modul.stoerung}")

        self.laeuft = True
        self.gestartet_um = time.time()
        wesentlich_gestoert = [m.name for m in self._module.values()
                               if m.wesentlich and m.zustand is not Modulzustand.LAEUFT]
        self.bus.melden("kern.gestartet", quelle="kern",
                        module=len(self._module),
                        gestoert=[m.name for m in self._module.values()
                                  if m.zustand is Modulzustand.GESTOERT])
        return not wesentlich_gestoert

    def anhalten(self, frist: float = 30.0) -> None:
        """Fährt herunter - Module in umgekehrter Startreihenfolge, dann der Planer."""
        if not self.laeuft:
            return
        self.laeuft = False
        for name in reversed(self._reihenfolge):
            modul = self._module.get(name)
            if modul and modul.zustand in (Modulzustand.LAEUFT, Modulzustand.STARTET):
                modul.anhalten()
        # Der Planer zuletzt: Ein Modul könnte beim Herunterfahren noch einen
        # letzten Auftrag einreihen (Sitzung verdichten, Protokoll schreiben).
        self.planer.anhalten(warten=True, frist=frist)
        self.bus.melden("kern.angehalten", quelle="kern",
                        laufzeit=round(time.time() - self.gestartet_um, 1))

    # -------------------------------------------------------------- Arbeit
    def beauftragen(self, art: str, beschreibung: str, aufgabe: Callable,
                    **kw) -> Auftrag:
        """Arbeit einreichen. Prüft vorher, ob die Rechtestufe reicht.

        Unbeaufsichtigte Arbeit (mit Zeitpunkt oder Wiederholung) braucht
        Stufe 2. Sonst könnte man die Rechteschranke umgehen, indem man
        dieselbe Arbeit statt direkt eben über den Planer schickt.
        """
        unbeaufsichtigt = bool(kw.get("faellig_ab") or kw.get("wiederholen_alle"))
        if unbeaufsichtigt and not self.rechte.verlangen("unbeaufsichtigt_arbeiten",
                                                         beschreibung):
            raise PermissionError(self.rechte.grund("unbeaufsichtigt_arbeiten"))
        if kw.get("braucht_vault") and not self.rechte.verlangen("vault_schreiben",
                                                                 beschreibung):
            raise PermissionError(self.rechte.grund("vault_schreiben"))
        return self.planer.auftrag(art, beschreibung, aufgabe, **kw)

    # ----------------------------------------------------------- Auftraege
    def auftrag_annehmen(self, text: str, *, quelle: str = "nutzer",
                         ergebnis: str = "", prioritaet: int = 5,
                         bedingungen: Optional[List[str]] = None,
                         daten: Optional[dict] = None):
        """DIE Tuer. Terminal, Oberflaeche, Sprache, Zeitplan, Ereignis, Ablauf.

        Das ist der Architekturgrundsatz aus §8 in einer Methode:
        Eingabekanaele fuehren keine Geschaeftslogik aus, sie reichen ein
        Ziel herein. Was danach passiert - zerlegen, ausfuehren, pruefen,
        neu planen, fragen - ist fuer alle Kanaele dasselbe, weil es nur
        einmal existiert.

        Gibt (Ziel, Wurzelaufgabe) zurueck. Ohne Aufgabenstapel wirft sie -
        stillschweigend nichts zu tun waere die schlechteste Antwort auf
        "kuemmere dich darum".
        """
        if self.laeufer is None:
            raise RuntimeError(
                "Der Aufgabenstapel laeuft nicht - es kann kein Ziel "
                "aufgenommen werden. Der Grund steht in '/kern' beim Modul "
                "'aufgaben'. Haeufigste Ursache: AUFGABEN_AKTIV=false, oder "
                "die Aufgabendatenbank ist nicht beschreibbar.")
        return self.laeufer.ziel_aufnehmen(
            text, ergebnis=ergebnis, prioritaet=prioritaet,
            bedingungen=bedingungen, quelle=quelle, auftrag=text, daten=daten)

    def arbeiten(self, max_runden: int = 50, frist: float = 120.0) -> dict:
        """Arbeitet die Warteschlange im VORDERGRUND ab.

        Fuer das Terminal und fuer Tests: Wer zusehen will, wie eine Aufgabe
        laeuft, will nicht auf einen Hintergrundfaden warten.
        """
        if self.stube is None:
            return {"runden": 0, "grund": "kein Aufgabenstapel"}
        return self.stube.abarbeiten(max_runden=max_runden, frist=frist)

    # ------------------------------------------------------------ Auskunft
    def status(self) -> dict:
        module = []
        for name in (self._reihenfolge or list(self._module)):
            modul = self._module.get(name)
            if not modul:
                continue
            befund = modul.gesundheit()
            module.append({
                "name": modul.name,
                "beschreibung": modul.beschreibung,
                "zustand": str(modul.zustand),
                "wesentlich": modul.wesentlich,
                "ok": befund.ok,
                "meldung": befund.meldung,
                "kennzahlen": befund.kennzahlen,
                "abhaengig_von": list(modul.abhaengig_von),
            })
        return {
            "laeuft": self.laeuft,
            "laufzeit": round(time.time() - self.gestartet_um, 1) if self.gestartet_um else 0,
            "rechtestufe": self.rechte.stufe,
            "module": module,
            "startprobleme": list(self.startprobleme),
            "planer": self.planer.zustand(),
            "ereignisse": self.bus.zaehlerstand(),
            "aufgaben": self.stube.zustand() if self.stube else None,
        }

    def bericht(self) -> str:
        """Der Status als Text - für /kern im Terminal."""
        stand = self.status()
        zeilen = [
            f"Zeus {'läuft' if stand['laeuft'] else 'steht'} "
            f"({stand['laufzeit']:.0f}s, Rechtestufe {stand['rechtestufe']})",
            "",
            "**Module**",
        ]
        for modul in stand["module"]:
            zeichen = {"laeuft": "✔", "gestoert": "✖", "angehalten": "·",
                       "neu": "·", "startet": "…"}.get(modul["zustand"], "?")
            # Ein Modul kann LAUFEN und trotzdem nichts leisten - Ollama nicht
            # erreichbar, Index leer, Fertigkeiten gesperrt. Ein Haken davor
            # wäre die Sorte Anzeige, die man nach dem zweiten Mal nicht mehr
            # liest, weil sie immer grün ist.
            if modul["zustand"] == "laeuft" and not modul["ok"]:
                zeichen = "!"
            zeile = f"{zeichen} {modul['name']:<16} {modul['meldung']}"
            if modul["kennzahlen"]:
                zeile += "  (" + ", ".join(f"{k}={v}" for k, v in
                                           list(modul["kennzahlen"].items())[:4]) + ")"
            zeilen.append(zeile)
        planer = stand["planer"]
        zeilen += [
            "",
            f"**Planer** {planer['arbeiter']} Arbeiter, {planer['wartend']} wartend, "
            f"{planer['geplant']} geplant, {len(planer['laufend'])} laufend"
            + (" – Vault belegt" if planer["vault_belegt"] else ""),
        ]
        for auftrag in planer["laufend"]:
            zeilen.append(f"  ▶ {auftrag['art']}: {auftrag['beschreibung'][:50]} "
                          f"({auftrag['dauer']}s)")
        zaehler = planer["zaehler"]
        zeilen.append(f"  Bilanz: {zaehler['fertig']} fertig, "
                      f"{zaehler['gescheitert']} gescheitert, "
                      f"{zaehler['wiederholt']} wiederholt, "
                      f"{zaehler['abgebrochen']} abgebrochen")
        if stand.get("aufgaben"):
            zeilen += ["", self.stube.bericht()]
        else:
            zeilen += ["", "**Aufgaben** kein persistenter Stapel aktiv - "
                           "Ziele, Zeitplaene und Ablaeufe stehen nicht zur "
                           "Verfuegung."]
        if stand["startprobleme"]:
            zeilen += ["", "**Beim Start nicht hochgekommen**"]
            zeilen += [f"  ✖ {p}" for p in stand["startprobleme"]]
        return "\n".join(zeilen)

    # ------------------------------------------------------------- Innerei
    def _sortieren(self) -> List[str]:
        """Startreihenfolge aus den Abhängigkeiten. Zyklen werden gemeldet."""
        offen = dict(self._module)
        fertig: List[str] = []
        while offen:
            bereit = [name for name, modul in offen.items()
                      if all(a in fertig or a not in self._module
                             for a in modul.abhaengig_von)]
            if not bereit:
                # Zyklus oder gegenseitige Abhängigkeit. Nicht raten, nicht
                # hängen: melden und den Rest in Anmeldereihenfolge anhängen.
                rest = sorted(offen)
                self.bus.melden("kern.abhaengigkeitszyklus", quelle="kern",
                                betroffen=rest)
                self.startprobleme.append(
                    "Ringabhängigkeit zwischen: " + ", ".join(rest)
                    + " – sie starten in Anmeldereihenfolge, was schiefgehen kann.")
                fertig.extend(rest)
                break
            for name in sorted(bereit):
                fertig.append(name)
                del offen[name]
        return fertig

    def __repr__(self) -> str:
        return f"<Zeus {len(self._module)} Module, {'läuft' if self.laeuft else 'steht'}>"
