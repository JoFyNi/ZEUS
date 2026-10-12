"""Termine, die einen Neustart ueberleben - und die ZIELE erzeugen, keine Aufrufe.

Es gibt bereits eine Uhr: `scheduler.Auftragsplaner` kann `faellig_ab` und
`wiederholen_alle`. Sie ist richtig fuer das, wofuer sie gebaut wurde - die
Wartung alle sechs Stunden. Fuer einen Zeitplan taugt sie aus zwei Gruenden
nicht:

1. **Sie vergisst.** Die Termine liegen in einer Python-Liste. Ein Neustart
   um 07:59 loescht den 08:00-Termin. Bei einem System, das wochenlang
   laufen soll, ist "meistens" keine Eigenschaft.

2. **Sie ruft eine FUNKTION.** Das ist genau der Fall, den §20 des Auftrags
   ausschliesst: "Ein Scheduler, der lediglich einen Funktionsaufruf
   ausfuehrt, erfuellt diese Anforderung nicht." Eine Funktion kann nicht
   auf eine Freigabe warten, nicht neu planen, nicht nach einem Absturz
   fortsetzen - sie laeuft oder sie laeuft nicht.

Hier steht deshalb der andere Weg:

    08:00 Montag
      -> Zeitplan (Tabelle `zeitplaene`)
      -> ZIEL
      -> Aufgabengraph
      -> Warteschlange
      -> Arbeiter
      -> Verifikation
      -> Abschluss

Zwei Festlegungen aus dem Betrieb:

* EIN VERPASSTER TERMIN WIRD EINMAL NACHGEHOLT, nicht zwanzigmal. Wer einen
  Rechner eine Woche ausschaltet, will beim Einschalten nicht sieben
  Wochenberichte. `faellig_ab` wird beim Quittieren auf JETZT + Takt
  gesetzt, nicht auf den alten Termin + Takt.

* DIE UHR PRUEFT, SIE RECHNET NICHT. `faellige()` fragt die Datenbank, was
  ueberfaellig ist. Wer stattdessen Termine im Kopf mitrechnet, hat nach
  einer Zeitumstellung zwei Wahrheiten.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

# Wie oft nachgesehen wird, ob etwas faellig ist. Eine Sekunde ist fuer
# Termine im Minuten- und Stundenbereich reichlich fein und kostet nichts.
TAKT = 1.0

# Gaengige Abstaende als Wort, damit niemand Sekunden ausrechnen muss.
ABSTAENDE = {
    "minuetlich": 60.0,
    "viertelstuendlich": 900.0,
    "stuendlich": 3600.0,
    "zweistuendlich": 7200.0,
    "taeglich": 86400.0,
    "woechentlich": 604800.0,
}


class Zeitgeber:
    """Sieht nach, was faellig ist, und macht Ziele daraus."""

    def __init__(self, speicher, laeufer, *, bus=None, rechte=None,
                 takt: float = TAKT,
                 melden: Optional[Callable[[str], None]] = None) -> None:
        self.speicher = speicher
        self.laeufer = laeufer
        self.bus = bus
        self.rechte = rechte
        self.takt = max(0.05, float(takt))
        self.melden = melden
        self._halt = threading.Event()
        self._faden: Optional[threading.Thread] = None
        self.laeuft = False
        self.gezaehlt = {"ausgeloest": 0, "verweigert": 0, "fehler": 0}

    # ------------------------------------------------------------ Eintragen
    def eintragen(self, name: str, auftrag: str, *,
                  abstand: Optional[str] = None,
                  alle_sekunden: float = 0.0,
                  in_sekunden: Optional[float] = None,
                  ergebnis: str = "", schritte: Optional[List[dict]] = None,
                  prioritaet: int = 5) -> dict:
        """Legt einen Termin an. `abstand` ist ein Wort aus ABSTAENDE.

        Ohne Wiederholung ist es ein einmaliger Termin; er wird nach dem
        Ausloesen stillgelegt, nicht geloescht - sonst laesst sich hinterher
        nicht nachsehen, ob er gelaufen ist.
        """
        takt = float(alle_sekunden or 0.0)
        if abstand:
            wort = str(abstand).strip().lower()
            if wort not in ABSTAENDE:
                raise ValueError(
                    f"'{abstand}' ist kein bekannter Abstand. Moeglich: "
                    f"{', '.join(sorted(ABSTAENDE))} - oder alle_sekunden=…")
            takt = ABSTAENDE[wort]
        faellig = time.time() + (in_sekunden if in_sekunden is not None else takt)
        eintrag = self.speicher.zeitplan_anlegen(
            name, auftrag, alle_sekunden=takt, faellig_ab=faellig,
            daten={"ergebnis": ergebnis, "schritte": schritte or None,
                   "prioritaet": int(prioritaet)})
        self._ereignis("zeitplan.eingetragen", name=name,
                       in_sekunden=round(faellig - time.time(), 1),
                       wiederholt=bool(takt))
        return eintrag

    def abschalten(self, kennung: str) -> bool:
        if not self.speicher.zeitplan(kennung):
            return False
        self.speicher.zeitplan_abschalten(kennung)
        self._ereignis("zeitplan.abgeschaltet", zeitplan=kennung)
        return True

    # -------------------------------------------------------------- Pruefen
    def runde(self, jetzt: Optional[float] = None) -> List[str]:
        """Ein Durchgang: was faellig ist, wird zu einem Ziel.

        Synchron und ohne Thread - genau wie `Arbeiterstube.runde`. Ein Test,
        der auf einen Hintergrundfaden warten muss, ist ein Test, der
        manchmal gruen ist.
        """
        jetzt = time.time() if jetzt is None else float(jetzt)
        entstanden: List[str] = []
        for eintrag in self.speicher.faellige_zeitplaene(jetzt):
            if self.rechte is not None and not self.rechte.verlangen(
                    "zeitplan", f"Zeitplan '{eintrag['name']}'"):
                self.gezaehlt["verweigert"] += 1
                # Den Termin trotzdem quittieren: Sonst steht er beim
                # naechsten Durchgang wieder da und die Ablehnung wird im
                # Sekundentakt wiederholt.
                self.speicher.zeitplan_quittieren(eintrag["id"], jetzt)
                continue
            try:
                kennung = self._ausloesen(eintrag, jetzt)
            except Exception as exc:
                self.gezaehlt["fehler"] += 1
                self._sagen(f"Zeitplan '{eintrag['name']}' nicht ausgeloest: "
                            f"{type(exc).__name__}: {exc}")
                self.speicher.zeitplan_quittieren(eintrag["id"], jetzt)
                continue
            if kennung:
                entstanden.append(kennung)
        return entstanden

    def _ausloesen(self, eintrag: dict, jetzt: float) -> Optional[str]:
        daten = dict(eintrag.get("daten") or {})
        zieldaten = {"zeitplan": eintrag["id"], "zeitplan_name": eintrag["name"]}
        if daten.get("schritte"):
            zieldaten["schritte"] = daten["schritte"]
        ziel, _ = self.laeufer.ziel_aufnehmen(
            eintrag["auftrag"], ergebnis=str(daten.get("ergebnis") or ""),
            prioritaet=int(daten.get("prioritaet") or 5),
            quelle=f"zeitplan:{eintrag['name']}",
            auftrag=eintrag["auftrag"], daten=zieldaten)
        # Erst quittieren, NACHDEM das Ziel steht. Andersherum waere ein
        # Absturz dazwischen ein verlorener Termin - und niemand wuesste es.
        self.speicher.zeitplan_quittieren(eintrag["id"], jetzt)
        self.gezaehlt["ausgeloest"] += 1
        self._ereignis("zeitplan.ausgeloest", zeitplan=eintrag["id"],
                       name=eintrag["name"], ziel=ziel.id)
        self._sagen(f"Termin '{eintrag['name']}' faellig - Ziel angelegt.")
        return ziel.id

    # -------------------------------------------------------------- Betrieb
    def starten(self) -> None:
        if self.laeuft:
            return
        self._halt.clear()
        self.laeuft = True
        self._faden = threading.Thread(target=self._schleife, name="zeitgeber",
                                       daemon=True)
        self._faden.start()
        self._ereignis("zeitgeber.gestartet", takt=self.takt)

    def anhalten(self, frist: float = 10.0) -> None:
        if not self.laeuft:
            return
        self.laeuft = False
        self._halt.set()
        if self._faden is not None:
            self._faden.join(timeout=max(0.0, float(frist)))
        self._faden = None
        self._ereignis("zeitgeber.angehalten", **self.gezaehlt)

    def _schleife(self) -> None:
        while not self._halt.is_set():
            try:
                self.runde()
            except Exception as exc:
                self.gezaehlt["fehler"] += 1
                self._sagen(f"Zeitgeber hat geworfen: {type(exc).__name__}: {exc}")
            self._halt.wait(self.takt)

    # -------------------------------------------------------------- Auskunft
    def uebersicht(self) -> str:
        eintraege = self.speicher.zeitplaene(nur_aktive=False)
        if not eintraege:
            return "Keine Termine hinterlegt."
        jetzt = time.time()
        zeilen = [f"{len(eintraege)} Termin(e):"]
        for eintrag in eintraege:
            marke = "▶" if eintrag["aktiv"] else "·"
            rest = eintrag["faellig_ab"] - jetzt
            wann = (f"in {_dauer(rest)}" if rest > 0 else "ueberfaellig") \
                if eintrag["aktiv"] else "abgeschaltet"
            takt = (f", alle {_dauer(eintrag['alle_sekunden'])}"
                    if eintrag["alle_sekunden"] else ", einmalig")
            zeilen.append(f"  {marke} {eintrag['name']:<20} {wann}{takt}"
                          f"  ({eintrag['laeufe']}×)")
            zeilen.append(f"      {eintrag['auftrag'][:90]}")
        return "\n".join(zeilen)

    def _ereignis(self, name: str, /, **daten) -> None:
        # Der Schraegstrich ist kein Schmuck: Ohne ihn kollidiert ein
        # Nutzlastfeld namens 'name' mit dem Parameter, und der TypeError
        # faellt an einer Stelle an, die mit dem Ereignis nichts zu tun hat.
        # Genau das ist beim Zeitplan passiert.
        if self.bus is not None:
            try:
                self.bus.melden(name, quelle="zeitgeber", **daten)
            except Exception:
                pass

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass


def _dauer(sekunden: float) -> str:
    sekunden = abs(float(sekunden or 0))
    if sekunden < 90:
        return f"{int(sekunden)}s"
    if sekunden < 5400:
        return f"{int(sekunden / 60)}min"
    if sekunden < 172800:
        return f"{sekunden / 3600:.1f}h"
    return f"{sekunden / 86400:.1f}d"
