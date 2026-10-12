"""Selbstentwicklung: Zeus darf seinen Code ändern - aber nicht heimlich.

Bis hierher galt in diesem Projekt eine harte Grenze (ENTWICKLUNG.md):

    "Und die bewusste Grenze beim Selbstzugriff: nur lesend. Ein Agent, der
     sich selbst umschreibt, während er läuft, ist keine Funktion, sondern
     eine Fehlerquelle ohne Rückweg."

Josh will jetzt einen Zeus, der sich weiterentwickelt. Der Satz oben bleibt
trotzdem richtig - jedes Wort davon. Was falsch war, ist der Schluss daraus:
Aus "nicht während er läuft, nicht ohne Rückweg" folgt nicht "gar nicht",
sondern **nicht direkt**.

Deshalb dieser Weg, und zwar genau so:

    1. Zeus schreibt seinen Vorschlag als vollständige Datei in einen
       QUARANTÄNE-Ordner. Der Quellbaum wird nicht angefasst.
    2. Der Vorschlag wird in einer KOPIE des ganzen Projekts eingespielt, und
       dort läuft die komplette Testsuite.
    3. Nur ein Vorschlag, dessen Tests grün sind, wird Josh überhaupt
       vorgelegt - mit Diff, Testausgabe und Begründung.
    4. Übernommen wird auf Joshs ausdrückliches Wort. Vorher wird der aktuelle
       Stand gesichert, sodass ein Rückweg bleibt.

Damit gilt weiter, was die Grenze eigentlich schützen sollte: Es ändert sich
nichts, während er läuft; es ändert sich nichts unbemerkt; und es gibt immer
einen Rückweg.

Der Testlauf ist dabei kein Beiwerk, sondern der ganze Punkt. Ohne ihn wäre
das hier nur eine umständliche Art, dieselbe Datei zu überschreiben. Und er
läuft auf der KOPIE, nicht im Original - ein Vorschlag, der beim Testen etwas
kaputt macht, macht die Kopie kaputt.
"""
from __future__ import annotations

import difflib
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .auftrag import HINTERGRUND
from .modul import Befund, Modul

# Wie lange die Testsuite auf der Kopie höchstens laufen darf. Danach gilt der
# Vorschlag als durchgefallen: Ein Patch, der die Tests zum Hängen bringt, ist
# schlimmer als einer, der sie rot macht - er fällt später auf, im Betrieb.
TEST_FRIST = 900
# Dateien, die ein Vorschlag NIE anfassen darf. Nicht weil sie heilig wären,
# sondern weil ein Fehler darin den Rückweg selbst zerstört.
UNANTASTBAR = {
    "jarvis/kern/selbst.py",     # das hier - sonst schafft es seine Prüfung ab
    "jarvis/tools.py",           # Sicherung, Journal, Papierkorb hängen daran
    ".env",
}
# Grössenschranke. Ein Vorschlag, der eine ganze Datei umschreibt, ist kein
# Vorschlag mehr, sondern ein Neubau - den soll ein Mensch schreiben.
MAX_GEAENDERTE_ZEILEN = 120

# Fortlaufende Nummer fuer die Kennung - siehe einreichen().
_LAUFNUMMER = itertools.count(1)


@dataclass
class Vorschlag:
    """Ein Änderungsvorschlag am eigenen Code - mit allem, was zur Prüfung gehört."""
    kennung: str
    datei: str                       # relativ zur Projektwurzel
    begruendung: str
    neuer_inhalt: str
    erstellt: float = field(default_factory=time.time)
    zustand: str = "neu"             # neu | geprueft | durchgefallen | uebernommen | verworfen
    diff: str = ""
    testausgabe: str = ""
    geaenderte_zeilen: int = 0
    abgelehnt_weil: str = ""

    def als_dict(self) -> dict:
        return {"kennung": self.kennung, "datei": self.datei, "zustand": self.zustand,
                "begruendung": self.begruendung, "zeilen": self.geaenderte_zeilen,
                "erstellt": self.erstellt, "abgelehnt_weil": self.abgelehnt_weil}


class Quarantaene:
    """Der Ort, an dem Vorschläge liegen, bis jemand sie ansieht."""

    def __init__(self, wurzel: Path, ablage: Path):
        self.wurzel = Path(wurzel)          # Projektwurzel (enthält jarvis/ und tests/)
        self.ablage = Path(ablage)          # z. B. .jarvis/quarantaene
        self.ablage.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ Einreichen
    def einreichen(self, datei: str, neuer_inhalt: str, begruendung: str) -> Vorschlag:
        """Nimmt einen Vorschlag an - nach den Prüfungen, die ohne Testlauf gehen."""
        rel = str(datei).replace("\\", "/").lstrip("./")
        # Die Kennung muss eindeutig sein, auch wenn zwei Vorschlaege in
        # derselben Sekunde entstehen - sonst ueberschreibt der zweite die
        # Ablage des ersten, und ein geprueftes Ergebnis verschwindet
        # zugunsten eines durchgefallenen. Genau das ist beim ersten Testlauf
        # dieser Datei passiert.
        kennung = f"{int(time.time())}-{next(_LAUFNUMMER):03d}-{Path(rel).stem}"
        vorschlag = Vorschlag(kennung=kennung, datei=rel, begruendung=str(begruendung)[:500],
                              neuer_inhalt=neuer_inhalt)

        ziel = (self.wurzel / rel).resolve()
        try:
            gepruefter_pfad = ziel.relative_to(self.wurzel.resolve()).as_posix()
        except ValueError:
            return self._ablehnen(vorschlag, "zeigt aus dem Projektbaum heraus")

        # Verglichen wird der AUFGELOESTE Pfad, nicht die rohe Zeichenkette.
        # Vorher stand hier `if rel in UNANTASTBAR`, also ein Vergleich gegen
        # das, was das Modell geschrieben hatte - geschrieben wurde aber gegen
        # den aufgeloesten Pfad. Vier Schreibweisen kamen dadurch durch:
        #
        #     jarvis/kern/../kern/selbst.py
        #     jarvis//kern/selbst.py
        #     jarvis/./kern/selbst.py
        #     jarvis/kern/Selbst.py        (NTFS ist Grossschreibung egal)
        #
        # Damit konnte ein Vorschlag genau die Datei aendern, die diese
        # Pruefung durchfuehrt - das, was der Eintrag verhindern soll.
        # casefold(), weil Windows-Dateisysteme nicht zwischen Selbst.py und
        # selbst.py unterscheiden, der Mengenvergleich in Python aber schon.
        gesperrt = {u.casefold() for u in UNANTASTBAR}
        if gepruefter_pfad.casefold() in gesperrt or rel.casefold() in gesperrt:
            return self._ablehnen(
                vorschlag,
                f"'{gepruefter_pfad}' ist von Selbstaenderungen ausgenommen - ein "
                f"Fehler darin zerstoert den Rueckweg selbst")
        if not ziel.exists():
            return self._ablehnen(vorschlag, "die Datei gibt es nicht (neue Dateien "
                                             "legt ein Mensch an)")
        if ziel.suffix != ".py":
            return self._ablehnen(vorschlag, "nur Python-Dateien")

        alt = ziel.read_text(encoding="utf-8")
        if alt == neuer_inhalt:
            return self._ablehnen(vorschlag, "aendert nichts")

        vorschlag.diff = "".join(difflib.unified_diff(
            alt.splitlines(keepends=True), neuer_inhalt.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        vorschlag.geaenderte_zeilen = sum(
            1 for z in vorschlag.diff.splitlines()
            if (z.startswith("+") or z.startswith("-")) and not z.startswith(("+++", "---")))
        if vorschlag.geaenderte_zeilen > MAX_GEAENDERTE_ZEILEN:
            return self._ablehnen(
                vorschlag,
                f"{vorschlag.geaenderte_zeilen} geaenderte Zeilen - ueber "
                f"{MAX_GEAENDERTE_ZEILEN} ist das kein Vorschlag mehr, sondern ein Neubau")

        # Syntax zuerst. Ein Vorschlag, der nicht einmal uebersetzt, muss nicht
        # erst eine Projektkopie und einen Testlauf kosten.
        try:
            compile(neuer_inhalt, rel, "exec")
        except SyntaxError as exc:
            return self._ablehnen(vorschlag, f"Syntaxfehler in Zeile {exc.lineno}: {exc.msg}")

        self._sichern(vorschlag)
        return vorschlag

    def _ablehnen(self, vorschlag: Vorschlag, grund: str) -> Vorschlag:
        vorschlag.zustand = "durchgefallen"
        vorschlag.abgelehnt_weil = grund
        self._sichern(vorschlag)
        return vorschlag

    # -------------------------------------------------------------- Prüfen
    def pruefen(self, vorschlag: Vorschlag, haltepunkt=None) -> Vorschlag:
        """Spielt den Vorschlag in eine KOPIE ein und laesst dort die Tests laufen.

        Auf der Kopie, nicht im Original - das ist der ganze Unterschied
        zwischen "Vorschlag" und "Aenderung". Ein Patch, der beim Testen etwas
        zerlegt, zerlegt die Kopie.
        """
        if vorschlag.zustand == "durchgefallen":
            return vorschlag

        # Der Sandkasten statt eines selbstgebauten Temp-Ordners: Er bringt
        # die Schranken mit, die hier bisher fehlten - eine AUFGERAEUMTE
        # Umgebung (kein OBSIDIAN_VAULT, keine Schluessel, HOME und TMPDIR
        # umgelenkt), eine Ausgabegrenze und ein sauberes Beenden von
        # Enkelprozessen. Bisher lief die Testreihe der Kopie mit der vollen
        # Umgebung des Nutzers und konnte damit in die ECHTE Vault schreiben,
        # obwohl "auf der Kopie" getestet wurde. Was der Sandkasten NICHT
        # leistet, steht in seinem Modulkopf - nachlesbar statt geraten.
        from .sandbox import Sandkasten

        with Sandkasten("patch", frist=300, netz=False) as sand:
            kopie = sand.hineinkopieren(self.wurzel)
            (kopie / vorschlag.datei).write_text(vorschlag.neuer_inhalt,
                                                 encoding="utf-8")
            if haltepunkt:
                haltepunkt()

            testordner = kopie / "tests"
            if not (testordner.is_dir() and any(testordner.glob("test_*.py"))):
                vorschlag.zustand = "durchgefallen"
                vorschlag.abgelehnt_weil = ("keine Testreihen gefunden - ohne Tests "
                                            "wird kein Vorschlag uebernommen")
                self._sichern(vorschlag)
                return vorschlag

            laeufe = sand.testreihen(testordner, gesamtfrist=TEST_FRIST,
                                     haltepunkt=haltepunkt)
            ausgabe: List[str] = []
            rot: List[str] = []
            for lauf in laeufe:
                name = Path(lauf.befehl[-1]).name if lauf.befehl else "?"
                ausgabe.append(f"{name}: {lauf.bericht()}")
                if not lauf.ok:
                    rot.append(f"{name}: {lauf.bericht()}")

            vorschlag.testausgabe = chr(10).join(ausgabe[-40:])
            if rot:
                vorschlag.zustand = "durchgefallen"
                vorschlag.abgelehnt_weil = ("Testreihen rot: "
                                            + "; ".join(rot[:4]))
            else:
                vorschlag.zustand = "geprueft"
            self._sichern(vorschlag)
            return vorschlag

    # ----------------------------------------------------------- Übernehmen
    def uebernehmen(self, kennung: str, sicherung: Optional[Path] = None) -> tuple:
        """Spielt einen GEPRUEFTEN Vorschlag ein. Nur auf ausdrueckliche Ansage.

        Rueckgabe: (geglueckt, Meldung). Vorher wird der bisherige Stand
        gesichert - ohne Sicherung waere das genau der Rueckweg, den es laut
        ENTWICKLUNG.md nicht geben darf.
        """
        vorschlag = self.holen(kennung)
        if not vorschlag:
            return False, f"Vorschlag '{kennung}' ist nicht bekannt."
        if vorschlag.zustand != "geprueft":
            return False, (f"Vorschlag '{kennung}' steht auf '{vorschlag.zustand}' und "
                           f"wird nicht uebernommen"
                           + (f": {vorschlag.abgelehnt_weil}" if vorschlag.abgelehnt_weil
                              else ". Nur geprüfte Vorschläge sind übernehmbar."))
        ziel = self.wurzel / vorschlag.datei
        if not ziel.exists():
            return False, f"'{vorschlag.datei}' gibt es nicht mehr."

        sicherung = sicherung or (self.ablage / "vorher")
        sicherung.mkdir(parents=True, exist_ok=True)
        gesichert = sicherung / f"{vorschlag.kennung}-{Path(vorschlag.datei).name}"
        try:
            gesichert.write_text(ziel.read_text(encoding="utf-8"), encoding="utf-8")
            ziel.write_text(vorschlag.neuer_inhalt, encoding="utf-8")
        except OSError as exc:
            return False, f"Nicht einspielbar: {exc}"

        vorschlag.zustand = "uebernommen"
        self._sichern(vorschlag)
        return True, (f"'{vorschlag.datei}' ersetzt. Der vorherige Stand liegt unter "
                      f"{gesichert}. Die Aenderung wirkt erst nach einem Neustart - "
                      f"laufender Code wird nicht ausgetauscht.")

    def verwerfen(self, kennung: str) -> bool:
        vorschlag = self.holen(kennung)
        if not vorschlag:
            return False
        vorschlag.zustand = "verworfen"
        self._sichern(vorschlag)
        return True

    # -------------------------------------------------------------- Ablage
    def _sichern(self, vorschlag: Vorschlag) -> None:
        try:
            (self.ablage / f"{vorschlag.kennung}.json").write_text(
                json.dumps({
                    "kennung": vorschlag.kennung, "datei": vorschlag.datei,
                    "begruendung": vorschlag.begruendung, "zustand": vorschlag.zustand,
                    "erstellt": vorschlag.erstellt, "diff": vorschlag.diff,
                    "testausgabe": vorschlag.testausgabe,
                    "geaenderte_zeilen": vorschlag.geaenderte_zeilen,
                    "abgelehnt_weil": vorschlag.abgelehnt_weil,
                    "neuer_inhalt": vorschlag.neuer_inhalt,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def holen(self, kennung: str) -> Optional[Vorschlag]:
        pfad = self.ablage / f"{kennung}.json"
        if not pfad.exists():
            return None
        try:
            daten = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Vorschlag(
            kennung=daten.get("kennung", kennung), datei=daten.get("datei", ""),
            begruendung=daten.get("begruendung", ""),
            neuer_inhalt=daten.get("neuer_inhalt", ""),
            erstellt=float(daten.get("erstellt", 0)), zustand=daten.get("zustand", "neu"),
            diff=daten.get("diff", ""), testausgabe=daten.get("testausgabe", ""),
            geaenderte_zeilen=int(daten.get("geaenderte_zeilen", 0)),
            abgelehnt_weil=daten.get("abgelehnt_weil", ""))

    def alle(self) -> List[Vorschlag]:
        heraus = []
        for pfad in sorted(self.ablage.glob("*.json")):
            eintrag = self.holen(pfad.stem)
            if eintrag:
                heraus.append(eintrag)
        return heraus

    def offen(self) -> List[Vorschlag]:
        return [v for v in self.alle() if v.zustand == "geprueft"]

    def bericht(self) -> str:
        eintraege = self.alle()
        if not eintraege:
            return ("Keine Aenderungsvorschlaege am eigenen Code. "
                    "Zeus schlaegt nur etwas vor, wenn er beim Arbeiten auf ein "
                    "konkretes Problem stoesst - nicht auf Vorrat.")
        zeichen = {"geprueft": "✔", "durchgefallen": "✖", "uebernommen": "▲",
                   "verworfen": "·", "neu": "…"}
        zeilen = []
        for eintrag in eintraege[-15:]:
            zeilen.append(f"{zeichen.get(eintrag.zustand, '?')} `{eintrag.kennung}`  "
                          f"{eintrag.datei} ({eintrag.geaenderte_zeilen} Zeilen)")
            zeilen.append(f"    {eintrag.begruendung[:110]}")
            if eintrag.abgelehnt_weil:
                zeilen.append(f"    → {eintrag.abgelehnt_weil[:110]}")
        offen = [e for e in eintraege if e.zustand == "geprueft"]
        if offen:
            zeilen.append("")
            zeilen.append(f"{len(offen)} geprueft und zur Freigabe: "
                          f"`/selbst uebernehmen {offen[0].kennung}`")
        return "\n".join(zeilen)


class SelbstentwicklungsModul(Modul):
    """Haengt die Quarantaene an den Kern und fuehrt ueber sie Buch."""

    name = "selbstentwicklung"
    beschreibung = "Aenderungsvorschlaege am eigenen Code, in Quarantaene geprueft"
    abhaengig_von = ("gedaechtnis",)

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.quarantaene: Optional[Quarantaene] = None
        self.abgelehnt = ""

    def _starten(self, kern):
        wurzeln = list(getattr(self.agent.cfg, "code_root_list", []) or [])
        if not wurzeln:
            self.abgelehnt = "CODE_ROOTS ist nicht gesetzt - kein eigener Code zu sehen"
            return
        wurzel = Path(wurzeln[0])
        if not (wurzel / "tests").is_dir():
            # Ohne Testsuite kein Vorschlagswesen. Das ist keine Formalie: Der
            # Testlauf IST die Pruefung; ohne ihn waere das hier nur eine
            # umstaendliche Art, eine Datei zu ueberschreiben.
            self.abgelehnt = (f"unter {wurzel} liegt kein tests/-Ordner - ohne "
                              f"Testsuite wird kein Vorschlag geprueft")
            return
        self.quarantaene = Quarantaene(wurzel, self.agent.cfg.work_dir / "quarantaene")

    def vorschlagen(self, datei: str, neuer_inhalt: str, begruendung: str,
                    sofort_pruefen: bool = True) -> Vorschlag:
        """Nimmt einen Vorschlag an und stellt den Testlauf als Auftrag ein."""
        if not self.quarantaene:
            raise RuntimeError(self.abgelehnt or "Selbstentwicklung ist nicht bereit.")
        if self.kern and not self.kern.rechte.verlangen("code_patch_bauen", begruendung):
            raise PermissionError(self.kern.rechte.grund("code_patch_bauen"))

        vorschlag = self.quarantaene.einreichen(datei, neuer_inhalt, begruendung)
        if self.kern:
            self.kern.bus.melden("selbst.vorschlag", quelle=self.name,
                                 **vorschlag.als_dict())
        if sofort_pruefen and vorschlag.zustand != "durchgefallen" and self.kern:
            # Der Testlauf dauert Minuten - er gehoert in den Hintergrund und
            # nicht in die Antwortzeit einer Frage.
            self.kern.planer.auftrag(
                "selbstpruefung", f"Vorschlag {vorschlag.kennung} testen",
                lambda auftrag, v=vorschlag: self._pruefen(auftrag, v),
                prioritaet=HINTERGRUND, quelle=self.name)
        return vorschlag

    def _pruefen(self, auftrag, vorschlag: Vorschlag):
        geprueft = self.quarantaene.pruefen(vorschlag, haltepunkt=auftrag.haltepunkt)
        if self.kern:
            self.kern.bus.melden("selbst.geprueft", quelle=self.name,
                                 **geprueft.als_dict())
        return geprueft.als_dict()

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        if not self.quarantaene:
            return Befund(True, f"ruht: {self.abgelehnt}", {})
        alle = self.quarantaene.alle()
        zahlen = {
            "vorschlaege": len(alle),
            "zur_freigabe": len([v for v in alle if v.zustand == "geprueft"]),
            "durchgefallen": len([v for v in alle if v.zustand == "durchgefallen"]),
            "uebernommen": len([v for v in alle if v.zustand == "uebernommen"]),
        }
        if zahlen["zur_freigabe"]:
            return Befund(True, f"{zahlen['zur_freigabe']} Vorschlag/Vorschlaege warten "
                                f"auf Freigabe (/selbst)", zahlen)
        return Befund(True, "keine offenen Vorschlaege", zahlen)
