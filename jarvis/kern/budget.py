"""Was eine Aufgabe kosten darf - und was passiert, wenn sie es ueberzieht.

Der Anlass steht im Protokoll vom 18.09.: Eine Zusammenfuehrung las dieselbe
Notiz sieben Mal, bis `MAX_TOOL_STEPS` griff. Das war Glueck, kein Entwurf -
die Schranke galt der Gespraechsschleife und zaehlte nur Runden. Alles
andere war unbegrenzt: Modellaufrufe, Laufzeit, Neuplanungen, Zeichenmenge.

Fuer eine Aufgabe, die STUNDEN laufen darf, ohne dass jemand zusieht, ist das
nicht tragbar. Ein Agent ohne Kostenschranke hat genau zwei Endzustaende: Er
wird fertig, oder er laeuft, bis ihn jemand bemerkt. Der zweite ist der
haeufigere.

Deshalb hier ein Budget mit fuenf Groessen (Master-Prompt §30) und einer
klaren Reaktion auf die Ueberschreitung:

    NICHT abbrechen. NICHT stillschweigend weiterlaufen.
    Sondern: die Aufgabe kontrolliert ANHALTEN und sagen, warum.

Der Unterschied ist wichtig. Ein Abbruch verliert die Zwischenergebnisse. Ein
Anhalten laesst sie stehen: Der Mensch sieht, was bis dahin geschah, und
entscheidet, ob er das Budget erhoeht oder die Aufgabe abschreibt. Genau
diese Entscheidung will man haben, und man hat sie nur, wenn der Zustand noch
da ist.

Was hier NICHT steht: Geld. Zeus laeuft gegen einen lokalen Ollama; ein
Modellaufruf kostet Strom und Zeit, nicht Cent. `modellaufrufe` und `zeichen`
sind die lokalen Entsprechungen. Wer spaeter ein bezahltes Modell anhaengt,
rechnet sie an dieser einen Stelle um statt an zwanzig.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


class BudgetErschoepft(RuntimeError):
    """Eine Grenze ist erreicht. Traegt mit, WELCHE - fuer die Meldung."""

    def __init__(self, groesse: str, verbraucht: float, grenze: float) -> None:
        self.groesse = groesse
        self.verbraucht = verbraucht
        self.grenze = grenze
        super().__init__(
            f"Budget erschoepft: {groesse} bei {_zahl(verbraucht)} von "
            f"{_zahl(grenze)}.")


def _zahl(wert: float) -> str:
    return str(int(wert)) if float(wert).is_integer() else f"{wert:.1f}"


@dataclass
class Budget:
    """Die Grenzen EINER Aufgabe. Jede Aufgabe bekommt ihr eigenes.

    Bewusst ein Wertobjekt und kein Dienst: Ein globaler Zaehler waere genau
    der versteckte Zustand, den die Architekturregel (§53) ausschliesst -
    zwei nebenlaeufige Aufgaben wuerden sich gegenseitig das Budget
    wegnehmen, und keine von beiden wuesste warum.
    """

    # Konvention fuer alle Grenzen: 0 heisst "nichts erlaubt", eine NEGATIVE
    # Zahl heisst "unbegrenzt". Das ist der Unterschied zwischen einer
    # abgeschalteten und einer auf null gesetzten Schranke - und wer beides
    # gleichsetzt, baut eine Grenze, die bei der schaerfsten Einstellung
    # nichts tut.
    modellaufrufe: int = 40
    werkzeugaufrufe: int = 60
    laufzeit: float = 900.0          # Sekunden
    versuche: int = 3
    neuplanungen: int = 2
    zeichen: int = 400_000           # grobe Entsprechung zu Token

    # Getrennte Modellbudgets (§25). Ein einzelner Zaehler fuer "Modell"
    # kann die Entscheidung nicht stuetzen, um die es geht: Zwanzig Aufrufe
    # an ein 3b-Modell kosten weniger als zwei an ein 27b. Wer beides gleich
    # zaehlt, kann nicht zwischen ihnen waehlen - und genau das soll die
    # Strategieweiche tun.
    #
    # Gemessen am 22.09. auf diesem Rechner: qwen3.8:27b 10,0 s je Aufruf,
    # qwen2.5:3b 5,5 s kalt. Der Unterschied ist kein Rundungsfehler.
    kleine_modellaufrufe: int = 30
    grosse_modellaufrufe: int = 12

    # Verbrauch
    verbraucht: Dict[str, float] = field(default_factory=dict)
    begonnen: float = field(default_factory=time.time)

    # ------------------------------------------------------------- Zaehlen
    def stand(self, groesse: str) -> float:
        return float(self.verbraucht.get(groesse, 0.0))

    def grenze(self, groesse: str) -> float:
        return float(getattr(self, groesse, 0) or 0)

    # Ein Aufruf an ein kleines oder grosses Modell ist AUCH ein
    # Modellaufruf. Ohne diese Zuordnung fuehren zwei Zaehler nebeneinander
    # Buch und laufen auseinander - dann stimmt weder die Summe noch die
    # Aufteilung, und keine von beiden faellt auf.
    _ZAEHLT_AUCH = {"kleine_modellaufrufe": "modellaufrufe",
                    "grosse_modellaufrufe": "modellaufrufe"}

    def buchen(self, groesse: str, menge: float = 1.0) -> None:
        """Verbrauch eintragen. Wirft NICHT - das tut `pruefen`.

        Die Trennung ist Absicht: Ein Schritt, der gerade zu Ende gelaufen
        ist, soll gezaehlt werden, auch wenn er das Budget damit sprengt.
        Sonst fehlt genau der Posten, der die Ueberschreitung erklaert.
        """
        self.verbraucht[groesse] = self.stand(groesse) + float(menge)
        summe = self._ZAEHLT_AUCH.get(groesse)
        if summe:
            self.verbraucht[summe] = self.stand(summe) + float(menge)

    def rest(self, groesse: str) -> float:
        grenze = self.grenze(groesse)
        return grenze - self.stand(groesse) if grenze >= 0 else float("inf")

    @property
    def verstrichen(self) -> float:
        return time.time() - self.begonnen

    # -------------------------------------------------------------- Pruefen
    def ueberzogen(self) -> Optional[str]:
        """Die erste ueberschrittene Groesse - oder None."""
        if self.laufzeit >= 0 and self.verstrichen > self.laufzeit:
            return "laufzeit"
        for groesse in ("modellaufrufe", "kleine_modellaufrufe",
                        "grosse_modellaufrufe", "werkzeugaufrufe", "versuche",
                        "neuplanungen", "zeichen"):
            grenze = self.grenze(groesse)
            if grenze >= 0 and self.stand(groesse) >= grenze:
                return groesse
        return None

    def pruefen(self, groesse: str = "", menge: float = 1.0) -> None:
        """Wirft, wenn der naechste Schritt die Grenze reissen wuerde.

        `groesse` ist das, was der Schritt verbrauchen WIRD. Ohne Angabe
        wird nur nachgesehen, ob schon etwas ueberzogen ist - so laesst sich
        zwischen zwei Schritten pruefen, ohne etwas zu unterstellen.
        """
        if self.laufzeit >= 0 and self.verstrichen > self.laufzeit:
            raise BudgetErschoepft("laufzeit", round(self.verstrichen, 1),
                                   self.laufzeit)
        if groesse:
            # Auch die Summe pruefen: Zwanzig kleine Aufrufe duerfen das
            # Gesamtbudget nicht umgehen, nur weil ihr eigener Topf noch
            # Luft hat.
            for name in (groesse, self._ZAEHLT_AUCH.get(groesse)):
                if not name:
                    continue
                grenze = self.grenze(name)
                if grenze >= 0 and self.stand(name) + float(menge) > grenze:
                    raise BudgetErschoepft(name, self.stand(name), grenze)
            return
        offen = self.ueberzogen()
        if offen:
            raise BudgetErschoepft(offen, self.stand(offen), self.grenze(offen))

    # -------------------------------------------------------------- Ausgabe
    def bericht(self) -> str:
        teile = [f"Laufzeit {int(self.verstrichen)}/{int(self.laufzeit)}s"]
        for groesse in ("modellaufrufe", "kleine_modellaufrufe",
                        "grosse_modellaufrufe", "werkzeugaufrufe",
                        "neuplanungen", "zeichen"):
            grenze = self.grenze(groesse)
            teile.append(f"{groesse} {_zahl(self.stand(groesse))}/"
                         + (_zahl(grenze) if grenze >= 0 else "unbegrenzt"))
        return ", ".join(teile)

    def als_dict(self) -> dict:
        return {
            "grenzen": {g: self.grenze(g) for g in
                        ("modellaufrufe", "kleine_modellaufrufe",
                         "grosse_modellaufrufe", "werkzeugaufrufe",
                         "laufzeit", "versuche", "neuplanungen", "zeichen")},
            "verbraucht": dict(self.verbraucht),
            "verstrichen": round(self.verstrichen, 1),
        }

    @classmethod
    def aus_config(cls, cfg, **ueberschreiben) -> "Budget":
        """Baut ein Budget aus den Einstellungen - mit den Voreinstellungen
        dieser Klasse, wo nichts gesetzt ist."""
        werte = {
            "modellaufrufe": _i(cfg, "budget_modellaufrufe", cls.modellaufrufe),
            "werkzeugaufrufe": _i(cfg, "budget_werkzeugaufrufe",
                                  cls.werkzeugaufrufe),
            "laufzeit": _f(cfg, "budget_laufzeit", cls.laufzeit),
            "versuche": _i(cfg, "budget_versuche", cls.versuche),
            "neuplanungen": _i(cfg, "budget_neuplanungen", cls.neuplanungen),
            "zeichen": _i(cfg, "budget_zeichen", cls.zeichen),
            "kleine_modellaufrufe": _i(cfg, "budget_kleine_modellaufrufe",
                                       cls.kleine_modellaufrufe),
            "grosse_modellaufrufe": _i(cfg, "budget_grosse_modellaufrufe",
                                       cls.grosse_modellaufrufe),
        }
        werte.update({k: v for k, v in ueberschreiben.items() if v is not None})
        return cls(**werte)


def _i(cfg, name: str, ersatz: int) -> int:
    """Ein Wert aus den Einstellungen - inklusive der Null.

    Bewusst NICHT `or ersatz`: Damit waere eine eingestellte Null stillschweigend
    zur Voreinstellung geworden, und "null Werkzeugaufrufe erlaubt" haette in
    Wahrheit sechzig bedeutet. Genau diese Sorte Umdeutung findet niemand
    wieder. Unbegrenzt wird durch eine NEGATIVE Zahl ausgedrueckt.
    """
    wert = getattr(cfg, name, None)
    if wert is None:
        return int(ersatz)
    try:
        return int(wert)
    except (TypeError, ValueError):
        return int(ersatz)


def _f(cfg, name: str, ersatz: float) -> float:
    wert = getattr(cfg, name, None)
    if wert is None:
        return float(ersatz)
    try:
        return float(wert)
    except (TypeError, ValueError):
        return float(ersatz)
