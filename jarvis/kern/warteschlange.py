"""Warteschlange und Arbeiter - aber die Schlange liegt in der Datenbank.

Es gibt bereits einen Auftragsplaner (`scheduler.py`), und er bleibt: Er
fuehrt FUNKTIONEN aus - Wartung, Verdichtung, Abgleich -, haelt die
Vault-Sperre und kann Termine. Was er nicht kann, und auch nicht koennen
soll: einen Neustart ueberleben. Seine `queue.PriorityQueue` liegt im
Arbeitsspeicher; wenn der Prozess stirbt, ist sie weg.

Fuer Arbeit, die Stunden dauert und auf Menschen wartet, ist das der
Unterschied zwischen einem System und einem Programm. Deshalb hier eine
zweite Schlange mit einer anderen Eigenschaft:

    scheduler.Auftragsplaner   Funktionen, schnell, fluechtig
    warteschlange.Arbeiterstube  Aufgaben, langlebig, ueberlebt den Neustart

Die Schlange SELBST ist die Tabelle `aufgaben`: Was auf BEREIT steht, ist
eingereiht. Das ist kein Kunstgriff, sondern spart den Fehler, den zwei
getrennte Listen zwangslaeufig machen - sie laufen auseinander, und dann
gibt es Aufgaben, die in der Schlange stehen und nicht in der Datenbank
oder umgekehrt.

Vier Dinge, die eine Arbeiterstube koennen muss und die hier drinstehen:

    BEANSPRUCHEN   Zwei Arbeiter duerfen nicht dieselbe Aufgabe ziehen. Das
                   loest `speicher.beanspruchen` mit einem bedingten UPDATE.
    BELEGUNG       Ein Arbeiter, der abstuerzt, darf die Aufgabe nicht fuer
                   immer halten. Sie laeuft nach der Frist ab und wird
                   eingesammelt.
    AUFRAEUMEN     Abhaengigkeiten loesen, verwaiste Aufgaben einsammeln -
                   einmal je Runde, an EINER Stelle.
    ANHALTEN       Ein laufender Durchgang wird zu Ende gelassen. Die Vault
                   ist wichtiger als ein schneller Programmschluss.

Eine Festlegung, die Ueberraschungen spart: `runde()` ist synchron und tut
genau einen Durchgang. Die Threads rufen sie in einer Schleife. Alles, was
ein Test wissen will, laesst sich damit ohne einen einzigen Thread pruefen -
und ein nebenlaeufiger Test, der nur manchmal gruen ist, ist kein Test.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from .aufgabenspeicher import (BEREIT, ENDZUSTAENDE, LAEUFT, NEUPLANUNG,
                               PAUSIERT, WARTET_AUF_MENSCH)

# Wie lange eine Belegung gilt. Laenger als der laengste erwartete Durchgang,
# kuerzer als die Geduld eines Menschen. Ein Durchgang, der laenger braucht,
# verlaengert selbst (`belegung_verlaengern`).
BELEGUNGSFRIST = 300.0

# Pause, wenn nichts zu tun ist. Kurz genug, dass eine neu eingereihte
# Aufgabe nicht spuerbar liegen bleibt; lang genug, dass ein Leerlauf keine
# Rechenzeit kostet.
LEERLAUFTAKT = 0.4


class Arbeiterstube:
    """Mehrere Arbeiter, die persistente Aufgaben abarbeiten."""

    def __init__(self, speicher, laeufer, *, arbeiter: int = 2, bus=None,
                 takt: float = LEERLAUFTAKT,
                 belegungsfrist: float = BELEGUNGSFRIST,
                 melden: Optional[Callable[[str], None]] = None) -> None:
        self.speicher = speicher
        self.laeufer = laeufer
        self.bus = bus
        self.arbeiterzahl = max(1, int(arbeiter))
        self.takt = max(0.01, float(takt))
        self.belegungsfrist = max(5.0, float(belegungsfrist))
        self.melden = melden

        self._halt = threading.Event()
        self._faeden: List[threading.Thread] = []
        self._sperre = threading.RLock()
        self._aufraeumen_zuletzt = 0.0
        self.laeuft = False
        self.gezaehlt = {"bearbeitet": 0, "fertig": 0, "gescheitert": 0,
                         "neugeplant": 0, "pausiert": 0, "wartet": 0,
                         "leerlauf": 0}

    # ------------------------------------------------------------- Betrieb
    def starten(self) -> None:
        if self.laeuft:
            return
        self._halt.clear()
        self.laeuft = True
        for nummer in range(self.arbeiterzahl):
            faden = threading.Thread(target=self._schleife,
                                     name=f"aufgabe-{nummer + 1}", daemon=True)
            faden.start()
            self._faeden.append(faden)
        self._ereignis("stube.gestartet", arbeiter=self.arbeiterzahl)

    def anhalten(self, warten: bool = True, frist: float = 30.0) -> None:
        if not self.laeuft:
            return
        self.laeuft = False
        self._halt.set()
        if warten:
            ende = time.time() + max(0.0, float(frist))
            for faden in self._faeden:
                faden.join(timeout=max(0.0, ende - time.time()))
        self._faeden = []
        self._ereignis("stube.angehalten", **self.gezaehlt)

    # ---------------------------------------------------------- Ein Durchgang
    def runde(self, arbeiter: str = "runde") -> Optional[str]:
        """Genau EIN Durchgang. Gibt den neuen Zustand zurueck - oder None.

        None heisst: Es gab nichts zu tun. Das ist die Antwort, auf die
        `abarbeiten` und die Tests warten; eine Schleife, die nicht erkennt,
        wann sie fertig ist, laeuft entweder ewig oder zu kurz.
        """
        self.aufraeumen()
        aufgabe = self.speicher.beanspruchen(arbeiter, self.belegungsfrist)
        if aufgabe is None:
            # Nichts gefunden - aber vielleicht nur, weil das Aufraeumen
            # gerade uebersprungen wurde (es laeuft hoechstens einmal je
            # Sekunde). Ein Schritt, der eben fertig wurde, gibt den
            # naechsten frei; ohne dieses erzwungene Nachfassen bricht ein
            # schneller Stapellauf mitten im Aufgabengraphen ab, und das sah
            # aus wie "es gibt nichts mehr zu tun".
            bericht = self.aufraeumen(erzwingen=True)
            if (bericht.get("freigegeben") or bericht.get("umgeleitet")
                    or bericht.get("verwaist") or bericht.get("gelockert")):
                aufgabe = self.speicher.beanspruchen(arbeiter, self.belegungsfrist)
        if aufgabe is None:
            self.gezaehlt["leerlauf"] += 1
            return None
        self._ereignis("aufgabe.uebernommen", aufgabe=aufgabe.id,
                       titel=aufgabe.titel[:80], arbeiter=arbeiter)
        neu = self.laeufer.bearbeiten(aufgabe)
        with self._sperre:
            self.gezaehlt["bearbeitet"] += 1
            if neu == "fertig":
                self.gezaehlt["fertig"] += 1
            elif neu == "gescheitert":
                self.gezaehlt["gescheitert"] += 1
            elif neu == NEUPLANUNG:
                self.gezaehlt["neugeplant"] += 1
            elif neu == PAUSIERT:
                self.gezaehlt["pausiert"] += 1
            elif neu in WARTET_AUF_MENSCH:
                self.gezaehlt["wartet"] += 1
        return neu

    def abarbeiten(self, max_runden: int = 200, frist: float = 120.0) -> dict:
        """Arbeitet, bis nichts mehr geht - fuer CLI, Tests und Ablaeufe.

        Zwei Schranken, und beide werden gebraucht: `max_runden` gegen eine
        Aufgabe, die sich selbst immer wieder einreiht, `frist` gegen eine,
        die einzeln langsam ist. Eine unbegrenzte Schleife im Kern eines
        Agenten ist genau das, was §53 ausschliesst.
        """
        ende = time.time() + max(0.0, float(frist))
        runden = 0
        while runden < max(1, int(max_runden)) and time.time() < ende:
            if self.runde("stapel") is not None:
                runden += 1
                continue
            # Leerer Zug - das heisst NICHT zwangslaeufig "fertig". Laufen
            # Hintergrundarbeiter mit (ab Rechtestufe 2 tun sie das), kann
            # einer von ihnen die Aufgabe gerade halten. Ein Abbruch an
            # dieser Stelle meldete "nichts zu tun", waehrend die Arbeit
            # noch lief - genau so gemessen: Der Abnahmetest sah null
            # Durchgaenge und ein unerledigtes Ziel, obwohl der Arbeiter
            # eine Sekunde spaeter fertig war.
            if not self._noch_in_arbeit():
                break
            self._halt.wait(min(self.takt, max(0.01, ende - time.time())))
        return {"runden": runden, **dict(self.gezaehlt),
                "offen": len(self.speicher.alle(nur_offene=True, grenze=200))}

    def _noch_in_arbeit(self) -> bool:
        """Haelt gerade jemand eine Aufgabe? Dann ist die Schlange nicht leer.

        Gefragt wird die Datenbank, nicht der eigene Zaehler: Der Halter
        kann ein Arbeiter in einem anderen Faden sein - oder, bei geteilter
        Vault, sogar in einem anderen Prozess.
        """
        try:
            jetzt = time.time()
            # Ueber ALLE offenen Zustaende, nicht nur ueber LAEUFT: Eine
            # Aufgabe in Neuplanung wird belegt, ohne ihren Zustand zu
            # wechseln (sie plant ja gerade um). Wer nur auf LAEUFT sieht,
            # haelt genau diese fuer unbearbeitet und bricht ab.
            return any(a.belegt_bis > jetzt
                       for a in self.speicher.alle(nur_offene=True, grenze=200))
        except Exception:
            return False

    # -------------------------------------------------------------- Aufraeumen
    def aufraeumen(self, erzwingen: bool = False) -> dict:
        """Blockaden loesen und verwaiste Aufgaben einsammeln.

        Hoechstens einmal je Sekunde: Bei mehreren Arbeitern liefe das sonst
        dutzendfach parallel und nur der erste faende etwas.
        """
        jetzt = time.time()
        with self._sperre:
            if not erzwingen and jetzt - self._aufraeumen_zuletzt < 1.0:
                return {}
            self._aufraeumen_zuletzt = jetzt
        bericht = {}
        try:
            bericht.update(self.speicher.blockaden_loesen())
        except Exception as exc:
            self._sagen(f"Blockaden nicht geloest: {exc}")
        try:
            verwaist = self.speicher.verwaiste_einsammeln()
            gelockert = 0
            try:
                gelockert = self.speicher.gelockert()
            except Exception:
                gelockert = 0
            if gelockert:
                bericht["gelockert"] = gelockert
            if verwaist:
                bericht["verwaist"] = [a.id for a in verwaist]
                self._ereignis("aufgabe.verwaist", anzahl=len(verwaist))
                self._sagen(f"{len(verwaist)} Aufgabe(n) ohne Arbeiter "
                            f"vorgefunden - sie werden neu geplant.")
        except Exception as exc:
            self._sagen(f"Verwaiste nicht eingesammelt: {exc}")
        return bericht

    def wiederaufnehmen(self) -> List:
        """Nach einem Neustart: was lief, ist von unbekanntem Stand.

        Wird beim Hochfahren aufgerufen. Was auf LAEUFT stand, wird NICHT
        blind fortgesetzt - niemand weiss, wie weit es kam, und ein blind
        wiederholter Schritt kann eine Wirkung doppelt ausloesen. Die
        Idempotenzsperre im Werkzeugtor faengt das zwar ab, aber sich auf
        genau eine Sicherung zu verlassen ist eine Sicherung zu wenig.
        """
        wieder = self.speicher.wiederaufnehmen()
        if wieder:
            self._ereignis("stube.wiederaufnahme", anzahl=len(wieder))
            self._sagen(f"{len(wieder)} unterbrochene Aufgabe(n) gefunden - "
                        f"sie werden neu geplant statt blind fortgesetzt.")
        return wieder

    # --------------------------------------------------------------- Abbruch
    def abbrechen(self, aufgabe_id: str, grund: str = "vom Nutzer abgebrochen") -> bool:
        """Bricht eine Aufgabe ab - samt ihrer offenen Kinder."""
        aufgabe = self.speicher.holen(aufgabe_id)
        if aufgabe is None or not aufgabe.offen:
            return False
        for kind in self.speicher.kinder(aufgabe_id):
            if kind.offen:
                try:
                    self.speicher.wechseln(kind.id, "abgebrochen", grund, "nutzer")
                except Exception:
                    pass
        try:
            self.speicher.wechseln(aufgabe_id, "abgebrochen", grund, "nutzer")
        except Exception:
            return False
        self._ereignis("aufgabe.abgebrochen", aufgabe=aufgabe_id, grund=grund)
        return True

    def fortsetzen(self, aufgabe_id: str, grund: str = "vom Nutzer fortgesetzt") -> bool:
        """Weckt eine pausierte Aufgabe - mit frischem Budget.

        Ohne das Zuruecksetzen waere die Aufgabe beim ersten Durchgang sofort
        wieder ueber ihrem Budget und ginge zurueck in die Pause. Genau so
        entstehen die Schalter, die nichts tun.
        """
        aufgabe = self.speicher.holen(aufgabe_id)
        if aufgabe is None or aufgabe.zustand != PAUSIERT:
            return False
        daten = dict(aufgabe.daten or {})
        daten.pop("budget", None)
        self.speicher.aktualisieren(aufgabe_id, daten=daten, fehler="")
        self.speicher.wechseln(aufgabe_id, BEREIT, grund, "nutzer")
        self._ereignis("aufgabe.fortgesetzt", aufgabe=aufgabe_id)
        return True

    # -------------------------------------------------------------- Auskunft
    def zustand(self) -> dict:
        zahlen = self.speicher.kennzahlen()
        return {
            "laeuft": self.laeuft,
            "arbeiter": self.arbeiterzahl,
            "bereit": len(self.speicher.alle(zustand=BEREIT, grenze=200)),
            "laufend": len(self.speicher.alle(zustand=LAEUFT, grenze=200)),
            "offen": zahlen.get("offen", 0),
            "wartet_auf_mensch": zahlen.get("wartet_auf_mensch", 0),
            "tote_briefe": len(self.speicher.tote_briefe(nur_ungesichtet=True)),
            "zaehler": dict(self.gezaehlt),
        }

    def bericht(self) -> str:
        stand = self.zustand()
        zeilen = [
            f"Arbeiterstube {'laeuft' if stand['laeuft'] else 'steht'} "
            f"({stand['arbeiter']} Arbeiter)",
            f"  bereit {stand['bereit']}, laufend {stand['laufend']}, "
            f"offen {stand['offen']}, auf dich wartend {stand['wartet_auf_mensch']}",
        ]
        zahl = stand["zaehler"]
        zeilen.append(f"  Bilanz: {zahl['fertig']} fertig, "
                      f"{zahl['gescheitert']} gescheitert, "
                      f"{zahl['neugeplant']} neu geplant, "
                      f"{zahl['pausiert']} angehalten")
        if stand["tote_briefe"]:
            zeilen.append(f"  {stand['tote_briefe']} unge­sichtete(r) tote(r) Brief(e) "
                          f"- '/aufgaben briefe' zeigt sie")
        return "\n".join(zeilen)

    # ---------------------------------------------------------------- Innerei
    def _schleife(self) -> None:
        name = threading.current_thread().name
        while not self._halt.is_set():
            try:
                neu = self.runde(name)
            except Exception as exc:
                # Ein Arbeiter, der stirbt, nimmt die Stube mit. Deshalb faengt
                # er hier ALLES - was durchkommt, ist ein Programmfehler im
                # Laeufer, und der gehoert gemeldet, nicht verschwiegen.
                self._sagen(f"Arbeiter {name} hat geworfen: "
                            f"{type(exc).__name__}: {exc}")
                self._ereignis("arbeiter.fehler", arbeiter=name,
                               grund=f"{type(exc).__name__}: {exc}")
                neu = None
            if neu is None:
                self._halt.wait(self.takt)

    def _ereignis(self, name: str, /, **daten) -> None:
        # Der Schraegstrich ist kein Schmuck: Ohne ihn kollidiert ein
        # Nutzlastfeld namens 'name' mit dem Parameter, und der TypeError
        # faellt an einer Stelle an, die mit dem Ereignis nichts zu tun hat.
        # Genau das ist beim Zeitplan passiert.
        if self.bus is not None:
            try:
                self.bus.melden(name, quelle="stube", **daten)
            except Exception:
                pass

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (f"<Arbeiterstube {self.arbeiterzahl} Arbeiter, "
                f"{'laeuft' if self.laeuft else 'steht'}>")
