"""Leerlauf: was Zeus tut, wenn niemand etwas eingibt.

Die Aufgabe war, eine Schleife zu ergänzen, die bei fehlender Nutzereingabe die
Aufgabenliste liest, nach Priorität sortiert, den ersten offenen Punkt nimmt und
seinen Kontext holt. Genau das steht hier - mit drei Festlegungen, die nicht in
der Aufgabe standen und trotzdem dazugehören:

1. ES WIRD VORGESCHLAGEN, NICHT AUSGEFÜHRT. Der Leerlauf holt die Aufgabe,
   lässt einen Plan erstellen und ZEIGT ihn. Geschrieben wird erst nach einem
   Enter. Ein Agent, der unbeaufsichtigt in die Vault schreibt, während niemand
   hinsieht, ist kein Komfortgewinn - der erste falsche Lauf kostet mehr, als
   alle richtigen einbringen.

2. EINE AUFGABE JE LEERLAUF. Danach wird wieder auf Eingabe gewartet. Ohne das
   arbeitet die Schleife eine halbe Liste ab, während Josh Kaffee holt.

3. UNSCHARFE AUFGABEN WERDEN NICHT GERATEN. Fehlt bei einer komplexen Aufgabe
   die Zieldatei oder das Fertigstellungskriterium, sagt der Leerlauf das und
   nimmt sie NICHT in Angriff. Raten kostet mehr Zeit, als das Aufschreiben
   gespart hätte - und das Ergebnis landet an der falschen Stelle.

Warum nicht in planner.py? Der Planer weiß nichts von Eingabe und Leerlauf; er
bekommt einen Auftrag und macht daraus Schritte. Die Frage "kommt gerade nichts"
kann nur die Oberfläche beantworten. Hier steht deshalb das Stück dazwischen:
Aufgabe holen, prüfen, Auftrag formen, Plan anfordern - und der Planer bleibt,
was er ist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .config import settings
from .vorhaben import Aufgabenbuch, Vorhaben


@dataclass
class Vorschlag:
    """Was der Leerlauf gefunden hat - und ob daraus etwas werden kann."""

    vorhaben: Optional[Vorhaben] = None
    plan: object = None
    grund: str = ""                       # warum nichts vorgeschlagen wird
    kontext: str = ""
    diagnose: List[str] = field(default_factory=list)

    @property
    def brauchbar(self) -> bool:
        return self.vorhaben is not None and self.plan is not None

    def bericht(self) -> str:
        if self.vorhaben is None:
            return self.grund or "Nichts zu tun."
        zeilen = [f"**{self.vorhaben.titel}**",
                  f"*Priorität {self.vorhaben.prioritaetsname} · "
                  f"{self.vorhaben.komplexitaet}*", ""]
        for schluessel, beschriftung in (("datei", "Zieldatei"),
                                         ("fertig", "Fertig, wenn")):
            wert = self.vorhaben.felder.get(schluessel)
            if wert:
                zeilen.append(f"- {beschriftung}: {wert}")
        if self.vorhaben.beschreibung:
            zeilen += ["", self.vorhaben.beschreibung.strip()]
        if self.grund:
            zeilen += ["", self.grund]
        if self.plan is not None:
            zeilen += ["", "**Geplante Schritte**", "",
                       "```", self.plan.uebersicht(), "```", "",
                       "Ausführen mit `/weiter ja` – oder Enter drücken und "
                       "etwas anderes tun."]
        return "\n".join(zeilen)


class Leerlauf:
    """Holt bei fehlender Eingabe die nächste Aufgabe und bereitet sie vor."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg
        self.buch = Aufgabenbuch(cfg)
        # Was zuletzt vorgeschlagen wurde - damit `/weiter ja` weiß, worum es
        # geht, und damit nicht zweimal hintereinander dasselbe kommt.
        self.letzter: Optional[Vorschlag] = None
        self._gezeigt: set = set()

    # ------------------------------------------------------------ Auswahl
    def naechste_aufgabe(self) -> Vorschlag:
        """Aufgabenliste lesen, sortieren, den ersten brauchbaren Punkt nehmen.

        Die Sortierung ist die aus der Aufgabenstellung: erst `priorität:`,
        bei Gleichstand die Reihenfolge im Dokument. Übersprungen wird nur,
        was zu ungenau ist - und das wird dann auch gesagt.
        """
        self.buch.laden()
        if not self.buch.existiert():
            return Vorschlag(grund=(
                f"Es gibt noch keine Aufgabenliste. `/aufgaben neu <Titel>` "
                f"legt eine an ({self.buch.pfad})."))

        offen = self.buch.sortiert()
        if not offen:
            return Vorschlag(grund="Nichts offen in der Aufgabenliste.")

        uebersprungen: List[str] = []
        for vorhaben in offen:
            if not vorhaben.scharf:
                uebersprungen.append(
                    f"„{vorhaben.titel}“ – {'; '.join(vorhaben.maengel)}")
                continue
            return Vorschlag(vorhaben=vorhaben)

        # Alles offene ist zu ungenau. Das ist ein Befund, kein Leerlauf.
        zeilen = ["Es ist etwas offen, aber nichts davon ist bearbeitbar:", ""]
        zeilen += [f"- {eintrag}" for eintrag in uebersprungen[:5]]
        zeilen += ["", "Ergänze `datei:` und `fertig-wenn:` – bei einer "
                       "komplexen Aufgabe entscheidet das darüber, ob das "
                       "Ergebnis brauchbar wird oder geraten ist."]
        return Vorschlag(grund="\n".join(zeilen))

    # ------------------------------------------------------------ Vorbereiten
    def vorbereiten(self, melden: Optional[Callable[[str], None]] = None) -> Vorschlag:
        """Aufgabe holen, Kontext dazu sammeln, Plan erstellen lassen."""
        sagen = melden or (lambda _t: None)
        vorschlag = self.naechste_aufgabe()
        if vorschlag.vorhaben is None:
            self.letzter = vorschlag
            return vorschlag

        vorhaben = vorschlag.vorhaben
        sagen(f"Leerlauf: nehme „{vorhaben.titel}“ "
              f"(Priorität {vorhaben.prioritaetsname})")

        auftrag = vorhaben.als_auftrag()
        # Der Kontext aus der Vault gehört zum Auftrag - ohne ihn plant der
        # Planer gegen einen leeren Bestand und legt Notizen doppelt an.
        try:
            treffer = self.agent.index.search(
                f"{vorhaben.titel} {vorhaben.felder.get('datei', '')}",
                k=max(3, self.cfg.top_k // 2))
            vorschlag.kontext = self.agent._build_context(
                treffer, structural=False, frage=vorhaben.titel)
        except Exception as exc:
            vorschlag.diagnose.append(f"Kontext nicht abrufbar: {exc}")

        try:
            plan = self.agent.planner.plan_erstellen(auftrag, vorschlag.kontext)
        except Exception as exc:
            vorschlag.grund = f"Plan nicht erstellbar: {exc}"
            self.letzter = vorschlag
            return vorschlag

        vorschlag.diagnose += list(getattr(self.agent.planner, "diagnose", []))
        if plan is None:
            vorschlag.grund = (
                "Aus dieser Aufgabe ließ sich kein Plan ableiten. Das heißt "
                "meist: Sie beschreibt ein Ziel, aber keine Handlung an der "
                "Vault. Formuliere sie als Handlung („lege an“, „ergänze“, "
                "„verschiebe“) – oder arbeite sie von Hand ab.")
        vorschlag.plan = plan
        self.letzter = vorschlag
        self._gezeigt.add(vorhaben.zeile)
        return vorschlag

    # ------------------------------------------------------------ Ausführen
    def ausfuehren(self, on_event=None):
        """Den zuletzt vorgeschlagenen Plan wirklich ausführen.

        Getrennt von `vorbereiten`, und das ist der Kern der Absicherung: Zwei
        Aufrufe, dazwischen eine Entscheidung eines Menschen.
        """
        if self.letzter is None or not self.letzter.brauchbar:
            return None, "Kein vorbereiteter Vorschlag. Erst `/weiter`."
        vorschlag = self.letzter
        ergebnis = self.agent.planner.plan_ausfuehren(vorschlag.plan, on_event)

        vorhaben = vorschlag.vorhaben
        if ergebnis.erstellt and not ergebnis.fehlgeschlagen:
            beleg = "; ".join(e.get("meldung", "") for e in ergebnis.erstellt[:2])
            if self.buch.abhaken(vorhaben, beleg):
                meldung = f"„{vorhaben.titel}“ erledigt und abgehakt."
            else:
                meldung = (f"„{vorhaben.titel}“ ausgeführt, aber die "
                           f"Aufgabenliste war nicht schreibbar – "
                           f"bitte von Hand abhaken.")
        elif ergebnis.erstellt:
            meldung = (f"Teilweise erledigt: {len(ergebnis.erstellt)} Schritte "
                       f"geglückt, {len(ergebnis.fehlgeschlagen)} nicht. "
                       f"Die Aufgabe bleibt offen.")
        else:
            meldung = ("Kein Schritt ist durchgegangen. Die Aufgabe bleibt "
                       "offen und unverändert.")
        self.letzter = None
        return ergebnis, meldung
