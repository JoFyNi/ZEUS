"""Die Strategieweiche: Was ist der billigste Weg, der diese Aufgabe traegt?

Das ist die Frage aus §37, und sie wird hier gestellt - EINMAL, bevor
gearbeitet wird, und erneut, wenn ein Ergebnis das Bild aendert.

Vorher gab es diese Frage nicht. Der Weg war:

    Auftrag -> Fachagent oder Modell -> Schritte -> grosses Modell

Das grosse Modell war die Vorgabe, nicht die Wahl. Gemessen am 22.09. kostet
ein Aufruf an qwen3.8:27b zehn Sekunden - auch fuer "Wie viele Dateien liegen
in der Inbox?", was ein Werkzeug in Millisekunden beantwortet.

Die Rangfolge
-------------
    WERKZEUG      Deterministisch. Kein Modell, kein Warten, kein Raten.
    REGEL         Eine feste Antwort aus dem eigenen Zustand.
    KLEIN         Klassifikation, Extraktion, einfache Zusammenfassung.
    GROSS         Planung, Synthese, Widersprueche, schwierige Analyse.

Und die Kombinationen, die in der Praxis haeufiger sind als die reinen
Formen: erst das Werkzeug holen, dann das Modell darauf ansetzen.

Was die Weiche NICHT darf
-------------------------
Sie entscheidet ueber den WEG, nie ueber die Erlaubnis. Rechte, Budgets,
Freigaben und Verifikation sitzen im `Werkzeugtor` und bleiben dort. Eine
Weiche, die aus Geschwindigkeitsgruenden eine Pruefung ueberspringen darf,
ist keine Weiche mehr, sondern ein Loch.

Sie darf ausserdem nicht behaupten, ein Modell sei verfuegbar, das es nicht
gibt. Deshalb fragt sie das `Modellregister` - und wenn dort auf dem aktiven
Server kein kleines Modell liegt, sagt sie das, statt still auf das grosse
auszuweichen und die Ersparnis nur zu behaupten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .bewertung import (ABRUF, ANALYSE, Befund, FRAGE, GESPRAECH, LESEN, META,
                        SCHREIBEN, UMBAUEN, WARTUNG, ZAEHLEN)
from .modelle import GROSS as R_GROSS, KLEIN as R_KLEIN, WINZIG as R_WINZIG

# ------------------------------------------------------------- Strategien
WERKZEUG = "werkzeug"
REGEL = "regel"
KLEIN = "klein"
GROSS = "gross"
WERKZEUG_DANN_KLEIN = "werkzeug->klein"
WERKZEUG_DANN_GROSS = "werkzeug->gross"
KLEIN_DANN_GROSS = "klein->gross"

STRATEGIEN = (WERKZEUG, REGEL, KLEIN, GROSS, WERKZEUG_DANN_KLEIN,
              WERKZEUG_DANN_GROSS, KLEIN_DANN_GROSS)

# Welche Strategie wieviel Modellkraft braucht - fuer Vergleiche und fuer die
# Frage, ob eine Eskalation ueberhaupt noch nach oben fuehrt.
_HOEHE = {WERKZEUG: 0, REGEL: 0, WERKZEUG_DANN_KLEIN: 1, KLEIN: 1,
          KLEIN_DANN_GROSS: 2, WERKZEUG_DANN_GROSS: 2, GROSS: 2}


@dataclass
class Entscheidung:
    """Der gewaehlte Weg - mit Begruendung und einem Plan B."""
    strategie: str
    rolle: str = ""                       # winzig | klein | gross | ""
    modell: str = ""                      # konkreter Name, wenn bekannt
    host: str = ""
    begruendung: List[str] = field(default_factory=list)
    eskalation: Optional[str] = None      # Strategie fuer den Fehlschlag
    erwartete_dauer: float = 0.0          # Sekunden, gemessen wenn moeglich
    braucht_modell: bool = False
    fremder_host: bool = False            # das Modell liegt woanders

    @property
    def hoehe(self) -> int:
        return _HOEHE.get(self.strategie, 1)

    def als_dict(self) -> dict:
        return {"strategie": self.strategie, "rolle": self.rolle,
                "modell": self.modell, "host": self.host,
                "eskalation": self.eskalation,
                "erwartete_dauer": round(self.erwartete_dauer, 2),
                "fremder_host": self.fremder_host,
                "begruendung": list(self.begruendung)}

    def zeile(self) -> str:
        teile = [self.strategie]
        if self.modell:
            teile.append(self.modell)
        if self.erwartete_dauer:
            teile.append(f"~{self.erwartete_dauer:.1f}s")
        if self.eskalation:
            teile.append(f"sonst {self.eskalation}")
        return "  ·  ".join(teile)

    def bericht(self) -> str:
        zeilen = [self.zeile()]
        zeilen += [f"    - {g}" for g in self.begruendung]
        return "\n".join(zeilen)


class Strategieweiche:
    """Waehlt den Weg zu einer Aufgabe - und den naechsten, wenn er nicht traegt.

    `register` ist das `Modellregister`. Ohne es arbeitet die Weiche weiter,
    kann dann aber nicht wissen, welches Modell wirklich erreichbar ist; sie
    nennt dann nur die ROLLE und ueberlaesst die Auswahl dem Aufrufer.

    `erfahrung` ist der Haken fuer das Lernen aus Routing-Ergebnissen (§23).
    Er wird hier bereits gefragt, damit die spaetere Stufe nichts umbauen
    muss - ohne ihn entscheidet die Weiche rein nach Regeln.
    """

    # Ab hier gilt eine Aufgabe als zu schwer fuer das kleine Modell.
    SCHWELLE_GROSS = 0.68
    # Darunter reicht das kleinste, was da ist.
    SCHWELLE_WINZIG = 0.2

    def __init__(self, register=None, cfg=None, erfahrung=None,
                 melden=None) -> None:
        self.register = register
        self.cfg = cfg
        self.erfahrung = erfahrung
        self.melden = melden
        # Ein Modell auf einem ANDEREN Server zu benutzen, ist eine
        # Betriebsentscheidung (Netzweg, zweiter Rechner an). Vorgabe: nein.
        # Die Weiche sagt dann, dass sie gern klein geworden waere, und
        # warum sie es nicht wurde - statt still Verkehr umzuleiten.
        self.fremder_host_erlaubt = bool(
            getattr(cfg, "router_fremder_host", False))
        self.gezaehlt = {name: 0 for name in STRATEGIEN}
        self.gezaehlt["eskalationen"] = 0
        self.gezaehlt["deeskalationen"] = 0

    # ------------------------------------------------------------- Waehlen
    def waehlen(self, befund: Befund, *, budget=None) -> Entscheidung:
        """Der erste Weg. Deterministisch und mit Begruendung."""
        gruende: List[str] = []

        # 1. Reicht ein Werkzeug? Die erste Frage aus §37 - und die
        #    billigste Antwort. Kein Modell, keine zehn Sekunden.
        if befund.deterministisch_loesbar:
            gruende.append(f"'{befund.absicht}' ist deterministisch loesbar, "
                           f"Mehrdeutigkeit nur {befund.mehrdeutigkeit:.2f}")
            return self._fertig(Entscheidung(
                strategie=WERKZEUG, begruendung=gruende,
                # Wenn das Werkzeug nichts findet, liegt es oft am Namen -
                # dann kann ein kleines Modell den Parameter klaeren.
                eskalation=WERKZEUG_DANN_KLEIN, erwartete_dauer=0.05))

        # 2. Dinge, die gar kein Modell brauchen.
        if befund.absicht == GESPRAECH:
            gruende.append("Gruss oder Dank - dafuer gibt es eine feste Antwort")
            return self._fertig(Entscheidung(strategie=REGEL,
                                             begruendung=gruende,
                                             erwartete_dauer=0.01))
        if befund.absicht == META:
            gruende.append("Frage ueber Zeus selbst - die Antwort steht im "
                           "eigenen Zustand, nicht im Modell")
            return self._fertig(Entscheidung(strategie=REGEL,
                                             begruendung=gruende,
                                             erwartete_dauer=0.01))

        # 3. Erfahrung aus frueheren Laeufen. Sie darf die Regel verschieben,
        #    nicht ersetzen - und nur, wenn sie auf genug Laeufen beruht.
        vorschlag = self._erfahrung_fragen(befund)

        # 4. Welche Modellgroesse traegt das?
        rolle, warum = self._rolle_waehlen(befund)
        gruende.append(warum)

        if vorschlag and vorschlag != rolle:
            gruende.append(f"Erfahrung riet zu '{vorschlag}' statt '{rolle}'")
            rolle = vorschlag

        # 5. Muss vorher etwas geholt werden?
        vorher_werkzeug = befund.abruf_noetig or befund.werkzeug_noetig
        if vorher_werkzeug:
            gruende.append("braucht erst Material aus der Vault")
            strategie = (WERKZEUG_DANN_GROSS if rolle == R_GROSS
                         else WERKZEUG_DANN_KLEIN)
        else:
            strategie = GROSS if rolle == R_GROSS else KLEIN

        # 6. Eskalationsweg festlegen, BEVOR etwas laeuft. Wer erst im
        #    Fehlerfall ueberlegt, wohin, entscheidet unter Druck.
        eskalation = None
        if rolle != R_GROSS:
            eskalation = WERKZEUG_DANN_GROSS if vorher_werkzeug else GROSS
            if strategie == KLEIN:
                strategie = KLEIN_DANN_GROSS if befund.unsicherheit >= 0.5 else KLEIN
                if strategie == KLEIN_DANN_GROSS:
                    gruende.append(f"Unsicherheit {befund.unsicherheit:.2f} - "
                                   f"Eskalation von vornherein eingeplant")

        entscheidung = Entscheidung(strategie=strategie, rolle=rolle,
                                    begruendung=gruende, eskalation=eskalation,
                                    braucht_modell=True)
        return self._fertig(self._modell_zuordnen(entscheidung, befund))

    def _rolle_waehlen(self, befund: Befund):
        """Welche Modellgroesse - und warum genau die."""
        if befund.absicht == ANALYSE:
            return R_GROSS, "Analyse und Synthese - das traegt das kleine nicht"
        if befund.komplexitaet >= self.SCHWELLE_GROSS:
            return R_GROSS, (f"Komplexitaet {befund.komplexitaet:.2f} ueber "
                             f"{self.SCHWELLE_GROSS}")
        if befund.unsicherheit >= 0.8:
            return R_GROSS, f"Unsicherheit {befund.unsicherheit:.2f} sehr hoch"
        if befund.mehrdeutigkeit >= 0.6:
            return R_GROSS, (f"Mehrdeutigkeit {befund.mehrdeutigkeit:.2f} - "
                             f"der Auftrag muss erst verstanden werden")
        if (befund.komplexitaet <= self.SCHWELLE_WINZIG
                and not befund.denken_noetig):
            return R_WINZIG, (f"Komplexitaet nur {befund.komplexitaet:.2f} und "
                              f"nichts zu denken")
        return R_KLEIN, f"Komplexitaet {befund.komplexitaet:.2f} im Mittelfeld"

    def _modell_zuordnen(self, e: Entscheidung, befund: Befund) -> Entscheidung:
        """Aus der Rolle ein konkretes Modell machen - oder ehrlich scheitern."""
        if not e.rolle or self.register is None:
            return e
        aktiv = getattr(self.cfg, "host", "") or ""

        modell = self.register.waehlen(e.rolle, host=aktiv or None)
        if modell is None and e.rolle == R_WINZIG:
            # Kein winziges da? Dann das kleine - das ist keine Niederlage.
            modell = self.register.waehlen(R_KLEIN, host=aktiv or None)
            if modell is not None:
                e.begruendung.append("kein winziges Modell da, nehme das kleine")
                e.rolle = R_KLEIN

        if modell is None:
            woanders = self.register.waehlen(e.rolle)
            if woanders is not None and self.fremder_host_erlaubt:
                e.begruendung.append(
                    f"'{e.rolle}' liegt nur auf {woanders.host} - dorthin "
                    f"geleitet (ROUTER_FREMDER_HOST=true)")
                e.modell, e.host, e.fremder_host = woanders.name, woanders.host, True
                e.erwartete_dauer = woanders.latenz or 3.0
                return e
            # Der ehrliche Fall: Wir HAETTEN gern klein gerechnet, koennen es
            # aber nicht. Das gehoert gesagt - sonst sieht es aus, als waere
            # das grosse Modell die Wahl gewesen.
            hinweis = (f"'{e.rolle}' liegt nicht auf {aktiv}"
                       + (f" (nur auf {woanders.host})" if woanders else ""))
            e.begruendung.append(hinweis + " - es laeuft auf dem grossen Modell")
            e.rolle = R_GROSS
            e.strategie = (WERKZEUG_DANN_GROSS
                           if e.strategie in (WERKZEUG_DANN_KLEIN,)
                           else GROSS)
            e.eskalation = None            # hoeher geht es nicht mehr
            modell = self.register.waehlen(R_GROSS, host=aktiv or None)

        if modell is not None:
            e.modell, e.host = modell.name, modell.host
            e.erwartete_dauer = modell.latenz or (10.0 if modell.rolle == R_GROSS
                                                  else 3.0)
            if modell.denkt and befund.komplexitaet < 0.3:
                e.begruendung.append(
                    f"{modell.name} denkt vor dem Antworten - fuer diese "
                    f"einfache Aufgabe ist das Aufwand ohne Gegenwert")
        return e

    # ---------------------------------------------------------- Eskalation
    def eskalieren(self, e: Entscheidung, grund: str,
                   befund: Optional[Befund] = None) -> Optional[Entscheidung]:
        """Eine Stufe hoeher - §8. Gibt None, wenn es keine hoehere gibt.

        Wichtig ist das None: Ein System, das immer noch eine Eskalation
        anbietet, versucht ewig weiter. Wenn das grosse Modell nicht reicht,
        ist die richtige naechste Handlung, den Menschen zu fragen - nicht,
        dasselbe noch einmal zu rufen.
        """
        naechste = e.eskalation
        if not naechste or _HOEHE.get(naechste, 0) <= e.hoehe:
            self._sagen(f"Keine Eskalation mehr moeglich: {grund}")
            return None
        gruende = [f"eskaliert, weil: {grund}",
                   f"vorher: {e.strategie}"
                   + (f" ({e.modell})" if e.modell else "")]
        neu = Entscheidung(strategie=naechste, rolle=R_GROSS,
                           begruendung=gruende, braucht_modell=True)
        if befund is not None:
            neu = self._modell_zuordnen(neu, befund)
        elif self.register is not None:
            modell = self.register.waehlen(
                R_GROSS, host=getattr(self.cfg, "host", "") or None)
            if modell is not None:
                neu.modell, neu.host = modell.name, modell.host
                neu.erwartete_dauer = modell.latenz or 10.0
        self.gezaehlt["eskalationen"] += 1
        return self._fertig(neu)

    def deeskalieren(self, e: Entscheidung, grund: str) -> Optional[Entscheidung]:
        """Eine Stufe TIEFER - §9. Der Fall, den man gern vergisst.

        Beispiel: Das grosse Modell hat den Plan gemacht und festgestellt,
        dass der Rest reine Dateiarbeit ist. Dann waere es Verschwendung,
        jeden weiteren Schritt wieder dorthin zu geben.
        """
        if e.hoehe <= 0:
            return None
        self.gezaehlt["deeskalationen"] += 1
        return self._fertig(Entscheidung(
            strategie=WERKZEUG,
            begruendung=[f"herabgestuft, weil: {grund}",
                         f"vorher: {e.strategie}"],
            erwartete_dauer=0.05))

    # --------------------------------------------------------------- Innen
    def _erfahrung_fragen(self, befund: Befund) -> Optional[str]:
        if self.erfahrung is None:
            return None
        try:
            return self.erfahrung.empfehlung(befund.signatur)
        except Exception as exc:
            self._sagen(f"Erfahrung nicht lesbar: {exc}")
            return None

    def _fertig(self, e: Entscheidung) -> Entscheidung:
        self.gezaehlt[e.strategie] = self.gezaehlt.get(e.strategie, 0) + 1
        return e

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass

    # ------------------------------------------------------------ Auskunft
    def uebersicht(self) -> str:
        gesamt = sum(self.gezaehlt.get(s, 0) for s in STRATEGIEN)
        if not gesamt:
            return "Noch nichts geroutet."
        zeilen = [f"{gesamt} Entscheidung(en):"]
        for strategie in STRATEGIEN:
            zahl = self.gezaehlt.get(strategie, 0)
            if zahl:
                zeilen.append(f"  {strategie:<18} {zahl:>4}  "
                              f"({zahl / gesamt:.0%})")
        ohne_modell = sum(self.gezaehlt.get(s, 0) for s in (WERKZEUG, REGEL))
        zeilen.append(f"  ohne Modell:       {ohne_modell:>4}  "
                      f"({ohne_modell / gesamt:.0%})")
        zeilen.append(f"  eskaliert {self.gezaehlt['eskalationen']}, "
                      f"herabgestuft {self.gezaehlt['deeskalationen']}")
        return "\n".join(zeilen)
