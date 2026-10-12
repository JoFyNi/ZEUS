"""Vom Ereignis zur Arbeit - mit einer Regel dazwischen, nicht mit Code.

Der Ereignisbus (`bus.py`) war bisher eine Meldestelle: Module riefen
`melden(...)`, die Oberflaeche zeigte es an, das Protokoll schrieb es auf.
Nachgemessen am 21.09.: Kein einziger Zuhoerer im ganzen Paket ERZEUGTE
Arbeit. Ein Ereignisbus, der nur protokolliert, ist ein Logger mit
Abonnement.

Damit fehlte die Proaktivitaet (§35) vollstaendig: Zeus konnte nur
antworten. Ein System, das erst handelt, wenn jemand tippt, ist ein
Programm - kein Agent.

Der Weg, den diese Datei einzieht:

    EREIGNIS  ->  REGEL  ->  ZIEL  ->  AUFGABE  ->  AGENTENSCHLEIFE

Die REGEL in der Mitte ist der Punkt. Man koennte den Zuhoerer auch direkt
Arbeit anlegen lassen - dann stuende die Entscheidung "worauf reagiert Zeus"
in Python verteilt ueber zwanzig Dateien, und niemand koennte sie
beantworten, ohne alle zwanzig zu lesen. Eine Regel ist Daten: sichtbar,
aufzaehlbar, abschaltbar, vom Nutzer zu aendern.

Drei Schranken, die eine solche Bruecke zwingend braucht:

1. **Keine Rueckkopplung.** Eine Regel, die auf `ziel.aufgenommen` reagiert
   und ein Ziel anlegt, erzeugt eine Lawine. Deshalb sind die Ereignisse des
   Laeufers selbst gesperrt, und eine Regel, die sich selbst ausloest, wird
   erkannt und stillgelegt.

2. **Ein Takt, eine Zuendung.** Dieselbe Regel feuert nicht oefter als ihre
   Sperrzeit erlaubt. Ohne das legt ein Ordner mit 200 geaenderten Dateien
   200 Ziele an.

3. **Nie werfen.** Eine kaputte Regel darf das Ereignis nicht verschlucken
   und schon gar nicht den Melder mitnehmen. Sie meldet sich und wird nach
   zu vielen Fehlern abgehaengt - dieselbe Regel wie beim Bus.
"""
from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Ereignisse, auf die NIE eine Regel reagieren darf. Sie entstehen durch die
# Arbeit selbst; wer darauf Arbeit anlegt, baut eine Rueckkopplung.
GESPERRT = (
    "ziel.", "aufgabe.", "stube.", "arbeiter.", "werkzeug.", "freigabe.",
    "regel.",
)

# Wie oft dieselbe Regel hoechstens zuendet, wenn nichts anderes gesagt ist.
SPERRZEIT = 30.0

# Nach so vielen Fehlern wird eine Regel stillgelegt.
FEHLERGRENZE = 5


@dataclass
class Regel:
    """Wenn DAS passiert, entsteht DIESES Ziel.

    `bedingung` ist bewusst ein Woerterbuch und keine Funktion: Eine Regel
    soll sich speichern, anzeigen und vom Nutzer aendern lassen. Eine
    Funktion kann das nicht.
    """
    name: str
    ereignis: str                       # "datei.geaendert" oder "datei.*"
    ziel: str                           # Vorlage, {feld} wird ersetzt
    beschreibung: str = ""
    bedingung: Dict[str, Any] = field(default_factory=dict)
    ergebnis: str = ""
    prioritaet: int = 5
    sperrzeit: float = SPERRZEIT
    schritte: Optional[List[dict]] = None   # feste Schritte statt Zerlegung
    aktiv: bool = True

    # Betriebsdaten
    zuendungen: int = 0
    zuletzt: float = 0.0
    fehler: int = 0
    stillgelegt: str = ""

    def passt(self, ereignis) -> bool:
        """Name und Bedingungen pruefen - ohne die Sperrzeit."""
        if not self.aktiv or self.stillgelegt:
            return False
        if not fnmatch.fnmatch(str(ereignis.name), self.ereignis):
            return False
        for schluessel, erwartet in (self.bedingung or {}).items():
            wert = (ereignis.daten or {}).get(schluessel)
            if not _vergleichen(wert, erwartet):
                return False
        return True

    def bereit(self, jetzt: Optional[float] = None) -> bool:
        jetzt = time.time() if jetzt is None else jetzt
        return (jetzt - self.zuletzt) >= max(0.0, float(self.sperrzeit))

    def zieltext(self, ereignis) -> str:
        """Die Vorlage mit den Ereignisdaten fuellen.

        Fehlende Felder bleiben als `{feld}` stehen, statt zu werfen: Ein
        Ziel mit einer offenen Klammer im Text ist haesslich, aber es ist
        DA - und die Luecke sagt einem Menschen sofort, was fehlte.
        """
        return _fuellen(self.ziel, ereignis)

    def stichpunkt(self) -> str:
        marke = "·" if self.stillgelegt else ("▶" if self.aktiv else "○")
        text = f"{marke} {self.name}: {self.ereignis} → {self.ziel[:60]}"
        if self.zuendungen:
            text += f"  ({self.zuendungen}×)"
        if self.stillgelegt:
            text += f"  – stillgelegt: {self.stillgelegt}"
        return text

    def als_dict(self) -> dict:
        return {"name": self.name, "ereignis": self.ereignis, "ziel": self.ziel,
                "beschreibung": self.beschreibung, "bedingung": dict(self.bedingung),
                "ergebnis": self.ergebnis, "prioritaet": self.prioritaet,
                "sperrzeit": self.sperrzeit, "schritte": self.schritte,
                "aktiv": self.aktiv, "zuendungen": self.zuendungen,
                "stillgelegt": self.stillgelegt}


class Regelwerk:
    """Haengt am Bus, prueft Regeln, legt Ziele an."""

    def __init__(self, bus, laeufer, *, melden: Optional[Callable[[str], None]] = None,
                 rechte=None) -> None:
        self.bus = bus
        self.laeufer = laeufer
        self.melden = melden
        self.rechte = rechte
        self.regeln: Dict[str, Regel] = {}
        self._abmelden: Optional[Callable[[], None]] = None
        self.verworfen: List[dict] = []

    # ------------------------------------------------------------- Verwalten
    def aufnehmen(self, regel: Regel) -> Regel:
        if any(regel.ereignis.startswith(p) for p in GESPERRT):
            # Nicht stillschweigend ignorieren: Wer eine solche Regel
            # anlegt, hat sich etwas dabei gedacht und soll erfahren, warum
            # sie nicht kommt.
            raise ValueError(
                f"'{regel.ereignis}' sind Ereignisse der Arbeit selbst. Eine "
                f"Regel darauf erzeugt eine Rueckkopplung: Arbeit meldet, die "
                f"Meldung erzeugt Arbeit. Gesperrt sind: "
                f"{', '.join(GESPERRT)}")
        self.regeln[regel.name] = regel
        return regel

    def entfernen(self, name: str) -> bool:
        return self.regeln.pop(str(name), None) is not None

    def anschalten(self, name: str, an: bool = True) -> bool:
        regel = self.regeln.get(str(name))
        if regel is None:
            return False
        regel.aktiv = bool(an)
        if an:
            regel.stillgelegt = ""
            regel.fehler = 0
        return True

    # --------------------------------------------------------------- Betrieb
    def horchen(self) -> None:
        """Meldet sich am Bus an - fuer ALLE Ereignisse, gefiltert wird hier."""
        if self._abmelden is not None:
            return
        self._abmelden = self.bus.abonnieren("*", self._eingang,
                                             beschreibung="regelwerk")

    def schweigen(self) -> None:
        if self._abmelden is not None:
            try:
                self._abmelden()
            finally:
                self._abmelden = None

    def _eingang(self, ereignis) -> None:
        """Der Zuhoerer. Wirft nie - siehe Modulkopf, Schranke 3."""
        if any(str(ereignis.name).startswith(p) for p in GESPERRT):
            return
        for regel in list(self.regeln.values()):
            try:
                self.pruefen(regel, ereignis)
            except Exception as exc:
                regel.fehler += 1
                text = (f"Regel '{regel.name}' hat geworfen: "
                        f"{type(exc).__name__}: {exc}")
                if regel.fehler >= FEHLERGRENZE:
                    regel.stillgelegt = f"nach {regel.fehler} Fehlern"
                    text += " – stillgelegt."
                self._sagen(text)

    def pruefen(self, regel: Regel, ereignis) -> Optional[str]:
        """Eine Regel gegen ein Ereignis. Gibt die Zielkennung zurueck."""
        if not regel.passt(ereignis):
            return None
        if not regel.bereit():
            # Nicht verschweigen: Eine Regel, die staendig gedrosselt wird,
            # ist falsch eingestellt, und das sieht man nur, wenn es
            # irgendwo steht.
            self.verworfen.append({"regel": regel.name, "grund": "Sperrzeit",
                                   "ereignis": ereignis.name, "zeit": time.time()})
            del self.verworfen[:-50]
            return None
        if self.rechte is not None and not self.rechte.verlangen(
                "unbeaufsichtigt_arbeiten", f"Regel '{regel.name}'"):
            self.verworfen.append({"regel": regel.name, "grund": "Rechtestufe",
                                   "ereignis": ereignis.name, "zeit": time.time()})
            return None

        beschreibung = regel.zieltext(ereignis)
        daten = {"regel": regel.name, "ereignis": ereignis.name,
                 "ereignisdaten": dict(ereignis.daten or {})}
        if regel.schritte:
            daten["schritte"] = [dict(s) for s in regel.schritte]
            daten["schritte"] = [_schritt_fuellen(s, ereignis)
                                 for s in daten["schritte"]]
        ziel, _ = self.laeufer.ziel_aufnehmen(
            beschreibung, ergebnis=_fuellen(regel.ergebnis, ereignis),
            prioritaet=regel.prioritaet, quelle=f"ereignis:{ereignis.name}",
            auftrag=beschreibung, daten=daten)
        regel.zuendungen += 1
        regel.zuletzt = time.time()
        self.bus.melden("regel.gezuendet", quelle="regelwerk", regel=regel.name,
                        ereignis=ereignis.name, ziel=ziel.id)
        self._sagen(f"Regel '{regel.name}' hat ausgeloest: {beschreibung[:80]}")
        return ziel.id

    # -------------------------------------------------------------- Auskunft
    def uebersicht(self) -> str:
        if not self.regeln:
            return ("Keine Regeln hinterlegt. Ohne sie reagiert Zeus nur auf "
                    "Eingaben, nicht auf Ereignisse.")
        zeilen = [f"{len(self.regeln)} Regel(n):"]
        zeilen += [f"  {r.stichpunkt()}" for r in self.regeln.values()]
        if self.verworfen:
            zeilen.append("")
            zeilen.append("Zuletzt nicht gezuendet:")
            for eintrag in self.verworfen[-5:]:
                zeilen.append(f"  {eintrag['regel']} bei {eintrag['ereignis']}"
                              f" – {eintrag['grund']}")
        return "\n".join(zeilen)

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass


# ------------------------------------------------------------------- Helfer
_FELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _fuellen(vorlage: str, ereignis) -> str:
    daten = dict(getattr(ereignis, "daten", None) or {})
    daten.setdefault("ereignis", getattr(ereignis, "name", ""))
    daten.setdefault("quelle", getattr(ereignis, "quelle", ""))

    def ersetzen(treffer):
        wert = daten.get(treffer.group(1))
        return str(wert) if wert is not None else treffer.group(0)

    return _FELD.sub(ersetzen, str(vorlage or ""))


def _schritt_fuellen(schritt: dict, ereignis) -> dict:
    raus = dict(schritt)
    raus["beschreibung"] = _fuellen(raus.get("beschreibung", ""), ereignis)
    parameter = raus.get("parameter")
    if isinstance(parameter, dict):
        raus["parameter"] = {k: (_fuellen(v, ereignis) if isinstance(v, str) else v)
                             for k, v in parameter.items()}
    return raus


def _vergleichen(wert, erwartet) -> bool:
    """Bedingungen: Gleichheit, Muster, Liste, Zahlenvergleich.

    Bewusst klein gehalten. Eine Bedingungssprache mit eigenem Parser waere
    die naechste Stelle, an der etwas falsch gelesen wird - und eine Regel,
    die anders greift als gedacht, ist schlimmer als eine, die gar nicht
    greift.
    """
    if isinstance(erwartet, dict):
        for name, vergleich in erwartet.items():
            if name == "enthaelt":
                if str(vergleich).lower() not in str(wert or "").lower():
                    return False
            elif name == "muster":
                if not fnmatch.fnmatch(str(wert or ""), str(vergleich)):
                    return False
            elif name in ("groesser", "kleiner"):
                try:
                    zahl = float(wert)
                except (TypeError, ValueError):
                    return False
                if name == "groesser" and not zahl > float(vergleich):
                    return False
                if name == "kleiner" and not zahl < float(vergleich):
                    return False
            elif name == "eines_von":
                if wert not in list(vergleich):
                    return False
            else:
                return False
        return True
    if isinstance(erwartet, (list, tuple, set)):
        return wert in erwartet
    return wert == erwartet
