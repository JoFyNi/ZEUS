"""Der Auftragsplaner: wer wann arbeitet - und wer dabei schreiben darf.

Die eine Entscheidung, an der alles hängt:

    Es dürfen mehrere Aufträge gleichzeitig laufen,
    aber immer nur EINER in die Vault schreiben.

Warum das nicht verhandelbar ist: Das Werkzeugsystem sichert jede einzelne
Änderung ab - Backup, atomares Schreiben, Rücklesen, Journal, Rollback. Alle
diese Sicherungen setzen voraus, dass zwischen "Datei lesen" und "Datei
zurückschreiben" niemand anders dieselbe Datei anfasst. Zwei Aufträge, die
gleichzeitig `notiz_ergaenzen` auf dieselbe Notiz rufen, lesen beide denselben
Stand, und der zweite schreibt die Ergänzung des ersten weg. Beide melden
Erfolg. Das ist exakt der stille Datenverlust, gegen den das Projekt seit v3
kämpft - nur eine Ebene darüber, wo die Sicherung nicht hinreicht.

Lesende Aufträge laufen dagegen parallel, und das ist der eigentliche Gewinn:
Während der Autolauf eine Notiz schreibt, kann eine Suche antworten.

Weitere Festlegungen:

* WIEDERHOLUNG NUR BEI AUSNAHMEN, NICHT BEI FEHLSCHLÄGEN. Ein Auftrag, der
  sauber "hat nicht geklappt" zurückgibt, wird nicht wiederholt - das Ergebnis
  wäre dasselbe. Wiederholt wird, was geworfen hat: Netzfehler, Zeitüberlauf.
* ABBRUCH BITTET, ERZWINGT NICHT. Siehe auftrag.py.
* HERUNTERFAHREN WARTET. Ein Arbeiter mitten im Schreiben wird zu Ende
  gelassen. Die Vault ist wichtiger als ein schneller Programmschluss.
* DIE UHR IST EIN EIGENER THREAD. Aufträge mit Zeitpunkt liegen nicht in der
  Warteschlange herum und blockieren dort die Reihenfolge, sondern warten
  daneben, bis sie fällig sind.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Dict, List, Optional

from .auftrag import Abgebrochen, Auftrag, Auftragszustand

# Wie oft die Uhr nachsieht, ob ein geplanter Auftrag fällig geworden ist.
# Eine Sekunde ist fein genug für alles, was hier vorkommt (Wartung, Autolauf,
# Nachdenken im Leerlauf) und kostet praktisch nichts.
UHR_TAKT = 1.0
# Pause zwischen zwei Versuchen, wächst mit dem Versuch. Ein sofortiger zweiter
# Versuch trifft denselben überlasteten Ollama-Server wie der erste.
RUECKZUG = 2.0


class Auftragsplaner:
    """Warteschlange mit Priorität, mehreren Arbeitern und einer Vault-Sperre."""

    def __init__(self, bus=None, arbeiter: int = 2, name: str = "planer") -> None:
        self.bus = bus
        self.name = name
        self.arbeiterzahl = max(1, int(arbeiter))

        self._warteschlange: "queue.PriorityQueue[Auftrag]" = queue.PriorityQueue()
        self._geplant: List[Auftrag] = []          # warten auf ihren Zeitpunkt
        self._laufend: Dict[int, Auftrag] = {}
        self._erledigt: List[Auftrag] = []         # Ringpuffer der letzten
        self._alle: Dict[int, Auftrag] = {}

        self._sperre = threading.RLock()
        self._vault_sperre = threading.Lock()      # nur EIN Schreiber
        self._halt = threading.Event()
        # Weckt die Uhr, wenn ein Auftrag NEU eingeplant wird. Ohne das
        # schlief sie ihren angefangenen Takt zu Ende, und ein Auftrag,
        # der sich alle 200 ms wiederholen sollte, lief einmal pro
        # Sekunde - ohne dass irgendwo etwas davon stand.
        self._weckruf = threading.Event()
        self._threads: List[threading.Thread] = []
        self._uhr: Optional[threading.Thread] = None
        self.laeuft = False

        # Zahlen fürs Protokoll - keine Selbsteinschätzung, nur Striche.
        self.gezaehlt = {"eingereiht": 0, "fertig": 0, "gescheitert": 0,
                         "abgebrochen": 0, "wiederholt": 0}

    # ----------------------------------------------------------- Betrieb
    def starten(self) -> None:
        if self.laeuft:
            return
        self._halt.clear()
        self.laeuft = True
        for nummer in range(self.arbeiterzahl):
            faden = threading.Thread(target=self._arbeiten, name=f"{self.name}-{nummer + 1}",
                                     daemon=True)
            faden.start()
            self._threads.append(faden)
        self._uhr = threading.Thread(target=self._uhr_laufen, name=f"{self.name}-uhr",
                                     daemon=True)
        self._uhr.start()
        self._melden("planer.gestartet", arbeiter=self.arbeiterzahl)

    def anhalten(self, warten: bool = True, frist: float = 30.0) -> None:
        """Fährt herunter. Laufende Aufträge dürfen zu Ende gehen."""
        if not self.laeuft:
            return
        self.laeuft = False
        self._halt.set()
        self._weckruf.set()          # holt die Uhr aus ihrem Schlaf
        # Ein Stupser je Arbeiter, damit keiner in queue.get() hängen bleibt.
        for _ in self._threads:
            try:
                self._warteschlange.put_nowait(_WECKER)
            except Exception:
                pass
        if warten:
            ende = time.time() + max(0.0, frist)
            for faden in self._threads + ([self._uhr] if self._uhr else []):
                rest = max(0.0, ende - time.time())
                faden.join(timeout=rest)
        self._threads = []
        self._uhr = None
        self._melden("planer.angehalten", **self.gezaehlt)

    # --------------------------------------------------------- Einreichen
    def einreihen(self, auftrag: Auftrag) -> Auftrag:
        with self._sperre:
            self._alle[auftrag.id] = auftrag
            self.gezaehlt["eingereiht"] += 1
            if auftrag.faellig_ab and auftrag.faellig_ab > time.time():
                auftrag.zustand = Auftragszustand.GEPLANT
                self._geplant.append(auftrag)
                self._melden("auftrag.geplant", auftrag=auftrag.id, art=auftrag.art,
                             in_sekunden=round(auftrag.faellig_ab - time.time(), 1))
                self._weckruf.set()
                return auftrag
        auftrag.zustand = Auftragszustand.WARTET
        self._warteschlange.put(auftrag)
        self._melden("auftrag.eingereiht", auftrag=auftrag.id, art=auftrag.art,
                     beschreibung=auftrag.beschreibung, prioritaet=auftrag.prioritaet)
        return auftrag

    def auftrag(self, art: str, beschreibung: str, aufgabe: Callable, **kw) -> Auftrag:
        """Bequemer Weg: Auftrag bauen und gleich einreihen."""
        return self.einreihen(Auftrag(art=art, beschreibung=beschreibung,
                                      aufgabe=aufgabe, **kw))

    def abbrechen(self, auftrag_id: int) -> bool:
        with self._sperre:
            eintrag = self._alle.get(int(auftrag_id))
        if not eintrag or eintrag.zustand.endgueltig:
            return False
        eintrag.abbrechen()
        if eintrag.zustand in (Auftragszustand.WARTET, Auftragszustand.GEPLANT):
            # Noch nicht gestartet: sofort und sauber abschließen. Aus der
            # Warteschlange nehmen lässt er sich nicht - der Arbeiter erkennt
            # ihn am Zustand und überspringt ihn.
            eintrag._abschliessen(Auftragszustand.ABGEBROCHEN)
            with self._sperre:
                self.gezaehlt["abgebrochen"] += 1
                if eintrag in self._geplant:
                    self._geplant.remove(eintrag)
                self._ablegen(eintrag)
        self._melden("auftrag.abgebrochen", auftrag=eintrag.id, art=eintrag.art)
        return True

    def warten_auf(self, auftrag_id: int, frist: Optional[float] = None) -> Optional[Auftrag]:
        with self._sperre:
            eintrag = self._alle.get(int(auftrag_id))
        if not eintrag:
            return None
        eintrag.warten(frist)
        return eintrag

    def ruhig(self, frist: float = 30.0) -> bool:
        """Wartet, bis nichts mehr wartet und nichts mehr läuft. Für Tests."""
        ende = time.time() + frist
        while time.time() < ende:
            with self._sperre:
                offen = bool(self._laufend) or bool(self._geplant)
            if not offen and self._warteschlange.empty():
                # Kurz nachfassen: Ein Arbeiter kann den Auftrag gerade aus der
                # Warteschlange geholt, aber noch nicht in _laufend eingetragen
                # haben. Ohne diese zweite Prüfung meldet 'ruhig' zu früh Ruhe.
                time.sleep(0.05)
                with self._sperre:
                    if not self._laufend and self._warteschlange.empty():
                        return True
                continue
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------ Auskunft
    def zustand(self) -> dict:
        with self._sperre:
            return {
                "laeuft": self.laeuft,
                "arbeiter": self.arbeiterzahl,
                "wartend": self._warteschlange.qsize(),
                "geplant": len(self._geplant),
                "laufend": [a.als_dict() for a in self._laufend.values()],
                "zuletzt": [a.als_dict() for a in self._erledigt[-8:]],
                "vault_belegt": self._vault_sperre.locked(),
                "zaehler": dict(self.gezaehlt),
            }

    def liste(self, anzahl: int = 20) -> List[Auftrag]:
        with self._sperre:
            return list(self._laufend.values()) + self._erledigt[-anzahl:]

    # ------------------------------------------------------------- Innerei
    def _uhr_laufen(self) -> None:
        while not self._halt.is_set():
            jetzt = time.time()
            faellig: List[Auftrag] = []
            naechster = jetzt + UHR_TAKT
            with self._sperre:
                for eintrag in list(self._geplant):
                    if eintrag.zustand.endgueltig:
                        self._geplant.remove(eintrag)
                    elif eintrag.faellig_ab <= jetzt:
                        self._geplant.remove(eintrag)
                        faellig.append(eintrag)
                    else:
                        naechster = min(naechster, eintrag.faellig_ab)
            for eintrag in faellig:
                eintrag.zustand = Auftragszustand.WARTET
                self._warteschlange.put(eintrag)
                self._melden("auftrag.faellig", auftrag=eintrag.id, art=eintrag.art)
            # Bis zum NÄCHSTEN fälligen Zeitpunkt schlafen, höchstens einen
            # Takt. Ein fester Takt von einer Sekunde verschluckt jeden
            # kürzeren Abstand: Ein Auftrag alle 200 ms lief in Wahrheit
            # einmal pro Sekunde, ohne dass irgendwo etwas davon stand.
            self._weckruf.clear()
            self._weckruf.wait(max(0.01, min(UHR_TAKT, naechster - time.time())))

    def _arbeiten(self) -> None:
        while not self._halt.is_set():
            try:
                eintrag = self._warteschlange.get(timeout=0.5)
            except queue.Empty:
                continue
            if eintrag is _WECKER:
                self._warteschlange.task_done()
                continue
            try:
                if eintrag.abgebrochen or eintrag.zustand.endgueltig:
                    continue
                self._ausfuehren(eintrag)
            finally:
                self._warteschlange.task_done()

    def _ausfuehren(self, eintrag: Auftrag) -> None:
        # Die Vault-Sperre wird VOR dem Zustandswechsel genommen: Ein Auftrag,
        # der als "läuft" gilt, während er noch auf die Sperre wartet, macht
        # jede Zeitmessung falsch und sieht in der Anzeige wie ein Hänger aus.
        sperre_genommen = False
        if eintrag.braucht_vault:
            while not self._vault_sperre.acquire(timeout=0.5):
                if self._halt.is_set() or eintrag.abgebrochen:
                    eintrag._abschliessen(Auftragszustand.ABGEBROCHEN)
                    with self._sperre:
                        self.gezaehlt["abgebrochen"] += 1
                        self._ablegen(eintrag)
                    return
            sperre_genommen = True

        try:
            eintrag.zustand = Auftragszustand.LAEUFT
            eintrag.gestartet = time.time()
            eintrag.versuche += 1
            with self._sperre:
                self._laufend[eintrag.id] = eintrag
            self._melden("auftrag.gestartet", auftrag=eintrag.id, art=eintrag.art,
                         versuch=eintrag.versuche, beschreibung=eintrag.beschreibung)

            try:
                eintrag.ergebnis = eintrag.aufgabe(eintrag)
                eintrag.fehler = ""
                eintrag._abschliessen(Auftragszustand.FERTIG)
                with self._sperre:
                    self.gezaehlt["fertig"] += 1
                self._melden("auftrag.fertig", auftrag=eintrag.id, art=eintrag.art,
                             dauer=round(eintrag.dauer, 2))
            except Abgebrochen as exc:
                eintrag.fehler = str(exc)
                eintrag._abschliessen(Auftragszustand.ABGEBROCHEN)
                with self._sperre:
                    self.gezaehlt["abgebrochen"] += 1
                self._melden("auftrag.abgebrochen", auftrag=eintrag.id, art=eintrag.art)
            except Exception as exc:
                # Nur GEWORFENE Fehler werden wiederholt. Ein Auftrag, der
                # ordentlich "hat nicht geklappt" zurückgibt, hat sein Urteil
                # gefällt - den zu wiederholen heißt, dasselbe Ergebnis noch
                # einmal abzuwarten.
                eintrag.fehler = f"{type(exc).__name__}: {exc}"
                if eintrag.versuche < max(1, eintrag.max_versuche) and not eintrag.abgebrochen:
                    with self._sperre:
                        self.gezaehlt["wiederholt"] += 1
                    eintrag.faellig_ab = time.time() + RUECKZUG * eintrag.versuche
                    eintrag.zustand = Auftragszustand.GEPLANT
                    with self._sperre:
                        self._geplant.append(eintrag)
                    self._weckruf.set()
                    self._melden("auftrag.wiederholung", auftrag=eintrag.id,
                                 art=eintrag.art, versuch=eintrag.versuche,
                                 grund=eintrag.fehler[:120])
                    return
                eintrag._abschliessen(Auftragszustand.GESCHEITERT)
                with self._sperre:
                    self.gezaehlt["gescheitert"] += 1
                self._melden("auftrag.gescheitert", auftrag=eintrag.id, art=eintrag.art,
                             grund=eintrag.fehler[:200], versuche=eintrag.versuche)
        finally:
            with self._sperre:
                self._laufend.pop(eintrag.id, None)
                if eintrag.zustand.endgueltig:
                    self._ablegen(eintrag)
            if sperre_genommen:
                self._vault_sperre.release()

        # Wiederkehrende Aufträge: als NEUER Auftrag einplanen, nicht denselben
        # zurücklegen. Sonst wachsen Versuchszähler und Laufzeit endlos, und
        # ein Fehlschlag von gestern verhindert den Lauf von morgen.
        if (eintrag.wiederholen_alle > 0 and self.laeuft
                and eintrag.zustand is not Auftragszustand.ABGEBROCHEN):
            naechster = Auftrag(
                art=eintrag.art, beschreibung=eintrag.beschreibung,
                aufgabe=eintrag.aufgabe, prioritaet=eintrag.prioritaet,
                braucht_vault=eintrag.braucht_vault, max_versuche=eintrag.max_versuche,
                faellig_ab=time.time() + eintrag.wiederholen_alle,
                wiederholen_alle=eintrag.wiederholen_alle, quelle=eintrag.quelle)
            self.einreihen(naechster)

    def _ablegen(self, eintrag: Auftrag) -> None:
        self._erledigt.append(eintrag)
        if len(self._erledigt) > 200:
            del self._erledigt[:-200]
            # Auch die Nachschlagetabelle beschneiden, sonst wächst sie
            # unbegrenzt weiter - bei einem System, das wochenlang läuft, ist
            # das kein theoretisches Problem.
            behalten = {a.id for a in self._erledigt} | set(self._laufend)
            for schluessel in [k for k in self._alle if k not in behalten]:
                del self._alle[schluessel]

    def _melden(self, name: str, **daten) -> None:
        if self.bus:
            self.bus.melden(name, quelle=self.name, **daten)


# Ein Aufwecker, damit ein wartender Arbeiter beim Herunterfahren aus
# queue.get() herauskommt. Er trägt Priorität -1, geht also vor allem anderen.
_WECKER = Auftrag(art="_wecker", beschreibung="Aufwecker beim Herunterfahren",
                  aufgabe=lambda a: None, prioritaet=-1)
