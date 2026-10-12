"""Autolauf: die Aufgabenliste unbeaufsichtigt abarbeiten - mit Bericht.

Abgrenzung zu dem, was schon da ist
-----------------------------------
* `vorhaben.py` führt die Aufgabenliste, die Josh von Hand schreibt.
* `leerlauf.py` nimmt daraus EINEN Punkt und trennt bewusst in zwei Aufrufe:
  `vorbereiten()` zeigt den Plan, `ausfuehren()` führt ihn aus - "dazwischen
  eine Entscheidung eines Menschen".
* Dieses Modul ist der Betrieb OHNE diese Zwischenentscheidung, für den
  Zeitplaner. Es arbeitet mehrere Punkte hintereinander ab und schreibt am
  Ende einen Bericht.

Warum die menschliche Entscheidung trotzdem nicht entfällt
----------------------------------------------------------
Sie wird nur VORVERLEGT. Unbeaufsichtigt laufen ausschließlich Aufgaben, die
`Vorhaben.scharf` sind - also solche, die eine Zieldatei und ein prüfbares
`fertig-wenn:` nennen. Diese Angaben macht Josh beim Aufschreiben. Eine vage
Aufgabe wird nicht geraten, sondern übersprungen und im Bericht benannt.

Damit gilt weiter, was das Projekt seit v3 durchhält: Der Agent handelt nur
dort selbständig, wo vorher festgelegt wurde, woran Erfolg zu erkennen ist.

Was dieses Modul NICHT tut
--------------------------
Es umgeht keine Sicherung. Jede Änderung läuft durch denselben Planer und
dieselben Werkzeuge wie im Gespräch - also mit Backup, Journal, Papierkorb,
`DRY_RUN`, `PROTECTED_FOLDERS` und `/rueckgaengig`. Der Autolauf fügt keine
neuen Rechte hinzu, er nimmt nur die Rückfrage weg.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .config import settings
from .vorhaben import Aufgabenbuch, Vorhaben

# Ein Autolauf, der sich durch eine kaputte Liste mahlt, richtet mehr Schaden
# an als er Nutzen bringt. Nach so vielen Fehlschlägen HINTEREINANDER bricht er
# ab - nicht nach so vielen insgesamt: Ein Fehlschlag zwischen Erfolgen ist
# normal, drei in Folge heißen, dass etwas Grundsätzliches nicht stimmt.
ABBRUCH_NACH_FEHLSCHLAEGEN = 3
STANDARD_GRENZE = 5


@dataclass
class Schritt:
    """Was mit EINER Aufgabe passiert ist - vollständig, auch im Fehlerfall."""
    titel: str
    prioritaet: str = ""
    status: str = "uebersprungen"      # erledigt | teilweise | gescheitert | uebersprungen
    grund: str = ""
    plan_schritte: List[str] = field(default_factory=list)
    erstellt: List[str] = field(default_factory=list)
    fehlgeschlagen: List[str] = field(default_factory=list)
    geaendert: List[str] = field(default_factory=list)
    abgehakt: bool = False
    sekunden: float = 0.0

    @property
    def marke(self) -> str:
        return {"erledigt": "✔", "teilweise": "~", "gescheitert": "✖",
                "uebersprungen": "○"}.get(self.status, "?")


@dataclass
class Laufbericht:
    begonnen: str = ""
    beendet: str = ""
    schritte: List[Schritt] = field(default_factory=list)
    abbruchgrund: str = ""
    trockenlauf: bool = False

    def zaehlung(self, status: str) -> int:
        return sum(1 for s in self.schritte if s.status == status)

    def kurzfassung(self) -> str:
        if not self.schritte:
            return "Nichts zu tun - keine bearbeitbare Aufgabe offen."
        teile = [f"{self.zaehlung('erledigt')} erledigt",
                 f"{self.zaehlung('teilweise')} teilweise",
                 f"{self.zaehlung('gescheitert')} gescheitert",
                 f"{self.zaehlung('uebersprungen')} übersprungen"]
        kopf = ", ".join(teile)
        if self.trockenlauf:
            kopf += "  (DRY_RUN - nichts wurde geschrieben)"
        if self.abbruchgrund:
            kopf += f"  ·  abgebrochen: {self.abbruchgrund}"
        return kopf

    def als_markdown(self) -> str:
        zeilen = [
            "---",
            f'titel: "Autolauf {self.begonnen[:10]}"',
            "tags:",
            "  - system",
            "  - autolauf",
            "  - bericht",
            f"aktualisiert: {self.beendet[:10] or self.begonnen[:10]}",
            "---",
            "",
            f"# Autolauf {self.begonnen}",
            "",
            self.kurzfassung(),
            "",
            f"Beginn: {self.begonnen}  ·  Ende: {self.beendet}",
            "",
        ]
        if self.trockenlauf:
            zeilen += ["> **DRY_RUN war an.** Es wurde geplant und berichtet, "
                       "aber nichts geschrieben.", ""]
        if not self.schritte:
            zeilen += ["Es war nichts zu tun."]
            return "\n".join(zeilen) + "\n"

        zeilen += ["## Was bearbeitet wurde", ""]
        for i, s in enumerate(self.schritte, start=1):
            zeilen.append(f"### {i}. {s.marke} {s.titel}")
            zeilen.append("")
            zeilen.append(f"- Status: **{s.status}**"
                          + (f" · Priorität {s.prioritaet}" if s.prioritaet else "")
                          + (f" · {s.sekunden:.0f}s" if s.sekunden else ""))
            if s.grund:
                zeilen.append(f"- Grund: {s.grund}")
            if s.plan_schritte:
                zeilen.append(f"- Geplante Schritte: {', '.join(s.plan_schritte)}")
            for eintrag in s.erstellt:
                zeilen.append(f"  - ✔ {eintrag}")
            for eintrag in s.fehlgeschlagen:
                zeilen.append(f"  - ✖ {eintrag}")
            if s.geaendert:
                zeilen.append("- Geänderte Dateien: "
                              + ", ".join(f"`{p}`" for p in s.geaendert))
            zeilen.append(f"- In der Aufgabenliste abgehakt: "
                          f"{'ja' if s.abgehakt else 'nein'}")
            zeilen.append("")

        offen = [s for s in self.schritte if s.status != "erledigt"]
        if offen:
            zeilen += ["## Was offen blieb", ""]
            for s in offen:
                zeilen.append(f"- **{s.titel}** – {s.grund or s.status}")
            zeilen += ["", "Diese Punkte stehen weiterhin in der Aufgabenliste. "
                           "Nichts davon wurde abgehakt."]
        return "\n".join(zeilen) + "\n"


class Autolauf:
    """Arbeitet die Aufgabenliste ab, solange es etwas Scharfes gibt."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg
        self.buch = Aufgabenbuch(cfg)

    # ------------------------------------------------------------ Durchlauf
    def durchlauf(self, grenze: int = STANDARD_GRENZE,
                  nur_scharfe: bool = True,
                  on_event: Optional[Callable[[str, str], None]] = None) -> Laufbericht:
        melden = on_event or (lambda art, text: None)
        bericht = Laufbericht(
            begonnen=dt.datetime.now().isoformat(timespec="seconds"),
            trockenlauf=bool(self.cfg.dry_run),
        )

        self.buch.laden()
        if not self.buch.existiert():
            bericht.abbruchgrund = f"Keine Aufgabenliste unter {self.buch.pfad}."
            bericht.beendet = dt.datetime.now().isoformat(timespec="seconds")
            melden("fehler", bericht.abbruchgrund)
            return bericht

        erledigt_in_folge_gescheitert = 0
        bearbeitet = 0

        for vorhaben in self.buch.sortiert():
            if bearbeitet >= max(1, grenze):
                bericht.abbruchgrund = f"Grenze von {grenze} Aufgaben erreicht."
                break
            if erledigt_in_folge_gescheitert >= ABBRUCH_NACH_FEHLSCHLAEGEN:
                bericht.abbruchgrund = (
                    f"{ABBRUCH_NACH_FEHLSCHLAEGEN} Fehlschläge hintereinander - "
                    f"abgebrochen, statt sich durch die Liste zu mahlen.")
                break

            schritt = self._eine_aufgabe(vorhaben, nur_scharfe, melden)
            bericht.schritte.append(schritt)
            if schritt.status == "uebersprungen":
                continue                      # zählt weder als Arbeit noch als Fehler
            bearbeitet += 1
            if schritt.status == "gescheitert":
                erledigt_in_folge_gescheitert += 1
            else:
                erledigt_in_folge_gescheitert = 0

        bericht.beendet = dt.datetime.now().isoformat(timespec="seconds")
        melden("arbeit", bericht.kurzfassung())
        return bericht

    # ----------------------------------------------------------- Eine Aufgabe
    def _eine_aufgabe(self, vorhaben: Vorhaben, nur_scharfe: bool,
                      melden: Callable[[str, str], None]) -> Schritt:
        schritt = Schritt(titel=vorhaben.titel,
                          prioritaet=vorhaben.prioritaetsname)

        # 1. Ist die Aufgabe überhaupt scharf? Unbeaufsichtigt wird NICHT geraten.
        if nur_scharfe and not vorhaben.scharf:
            schritt.grund = "; ".join(vorhaben.maengel)
            melden("fehler", f"übersprungen: „{vorhaben.titel}“ – {schritt.grund}")
            return schritt

        begonnen = time.time()
        melden("arbeit", f"Autolauf: „{vorhaben.titel}“ "
                         f"(Priorität {vorhaben.prioritaetsname})")

        # 2. Kontext holen - ohne ihn plant der Planer gegen einen leeren
        #    Bestand und legt Vorhandenes ein zweites Mal an.
        kontext = ""
        try:
            treffer = self.agent.index.search(
                f"{vorhaben.titel} {vorhaben.felder.get('datei', '')}",
                k=max(3, self.cfg.top_k // 2))
            kontext = self.agent._build_context(treffer, structural=False,
                                                frage=vorhaben.titel)
        except Exception as exc:
            schritt.grund = f"Kontext nicht abrufbar: {exc}"

        # 3. Plan erstellen
        try:
            plan = self.agent.planner.plan_erstellen(vorhaben.als_auftrag(), kontext)
        except Exception as exc:
            schritt.status = "gescheitert"
            schritt.grund = f"Plan nicht erstellbar: {exc}"
            schritt.sekunden = time.time() - begonnen
            melden("fehler", schritt.grund)
            return schritt

        if plan is None or not getattr(plan, "operationen", None):
            schritt.status = "uebersprungen"
            diagnose = list(getattr(self.agent.planner, "diagnose", []))[-2:]
            schritt.grund = ("Kein ausführbarer Plan ableitbar"
                             + (f" ({'; '.join(diagnose)})" if diagnose else "")
                             + ". Aufgabe als Handlung formulieren "
                               "(„lege an“, „ergänze“, „verschiebe“).")
            schritt.sekunden = time.time() - begonnen
            melden("fehler", schritt.grund)
            return schritt

        schritt.plan_schritte = sorted({op.aktion for op in plan.operationen})

        # 4. Ausführen - über denselben Weg wie im Gespräch, also mit allen
        #    Sicherungen (Backup, Journal, Papierkorb, Rücklesekontrolle).
        try:
            ergebnis = self.agent.planner.plan_ausfuehren(plan, lambda art, t: melden(art, t))
        except Exception as exc:
            schritt.status = "gescheitert"
            schritt.grund = f"Ausführung abgebrochen: {exc}"
            schritt.sekunden = time.time() - begonnen
            melden("fehler", schritt.grund)
            return schritt

        schritt.erstellt = [e.get("meldung") or e.get("titel", "")
                            for e in ergebnis.erstellt]
        schritt.fehlgeschlagen = [f"{e.get('titel', '?')}: {e.get('fehler', '')}"
                                  for e in ergebnis.fehlgeschlagen]
        schritt.geaendert = [e.get("pfad", "") for e in ergebnis.erstellt
                             if e.get("pfad")]

        # 5. Status aus TATSACHEN ableiten, nicht aus einer Modellaussage.
        if ergebnis.erstellt and not ergebnis.fehlgeschlagen:
            schritt.status = "erledigt"
            beleg = "; ".join(str(e.get("meldung", ""))[:80]
                              for e in ergebnis.erstellt[:2])
            # Im Trockenlauf wird nichts abgehakt - es ist ja nichts passiert.
            if not self.cfg.dry_run:
                schritt.abgehakt = bool(self.buch.abhaken(vorhaben, beleg))
                if not schritt.abgehakt:
                    schritt.grund = ("Ausgeführt, aber die Aufgabenliste war nicht "
                                     "schreibbar - bitte von Hand abhaken.")
        elif ergebnis.erstellt:
            schritt.status = "teilweise"
            schritt.grund = (f"{len(ergebnis.erstellt)} Schritte geglückt, "
                             f"{len(ergebnis.fehlgeschlagen)} nicht. "
                             f"Aufgabe bleibt offen.")
        else:
            schritt.status = "gescheitert"
            schritt.grund = "Kein Schritt ist durchgegangen. Aufgabe bleibt offen."

        schritt.sekunden = time.time() - begonnen
        return schritt

    # -------------------------------------------------------------- Bericht
    def bericht_ablegen(self, bericht: Laufbericht) -> str:
        """Bericht als Notiz in die Vault - lesbar wie jede andere.

        Ein zweiter Lauf am selben Tag ERSETZT den Bericht, statt `…_2.md`
        danebenzulegen - dieselbe Regel wie bei der Code-Prüfung.
        """
        titel = f"Autolauf {bericht.begonnen[:10]}"
        ordner = f"{(self.cfg.code_folder or '_System').split('/')[0]}/Autolauf"
        tools = self.agent.tools
        vorhanden = tools.notiz_exakt(titel)
        if vorhanden is not None:
            ergebnis = tools.notiz_ueberschreiben(vorhanden.stem, bericht.als_markdown())
            return vorhanden.rel_path if ergebnis.ok else f"(nicht schreibbar: {ergebnis.meldung})"
        ergebnis = tools.notiz_erstellen(titel=titel, inhalt=bericht.als_markdown(),
                                         ordner=ordner, tags=["system", "autolauf"])
        return ergebnis.daten.get("pfad", "") if ergebnis.ok \
            else f"(nicht schreibbar: {ergebnis.meldung})"
