"""Die Aufgabenliste: was Josh von Hand aufschreibt und abgearbeitet haben will.

Nicht zu verwechseln mit `aufgaben.py`. Der Unterschied ist der Urheber:

* `aufgaben.py` führt OFFENE PUNKTE - fehlgeschlagene Werkzeugaufrufe, die der
  Agent selbst einträgt, damit sie nicht im Gesprächsverlauf verschwinden.
* Dieses Modul liest die Aufgabenliste, die ein MENSCH schreibt: Vorhaben mit
  Priorität, Zieldatei und Fertigstellungskriterium.

Beide sind Markdown-Notizen in der Vault, beide sind in Obsidian les- und
schreibbar. Das ist Absicht: Die Liste soll auch ohne laufenden Agenten
bearbeitbar sein.

DAS FORMAT
----------
Eine Aufgabe ist eine Zeile mit `- [ ]`. Alles, was eingerückt darunter steht,
gehört dazu - entweder als `feld: wert` oder als Beschreibungstext::

    - [ ] Idle-Schleife in planner.py ergänzen
        priorität: hoch
        datei: jarvis/planner.py
        fertig-wenn: Bei leerer Eingabe wird die oberste offene Aufgabe geladen
        Ergänze eine while-Loop, die bei fehlender Nutzer-Eingabe die Datei
        Aufgabenliste.md parst. Lade alle Zeilen mit Präfix "- [ ]", sortiere
        nach dem Attribut priorität, wähle den ersten Treffer.

`[x]` ist erledigt, `[!]` zurückgestellt. Beides bleibt stehen - eine Liste,
aus der Erledigtes verschwindet, verliert ihren Wert als Verlauf.

WIE GENAU MUSS EINE AUFGABE SEIN?
---------------------------------
Das hängt von der Aufgabe ab, und genau so wird hier auch geprüft. "Lege eine
Notiz zu Backpropagation an" ist vollständig - es gibt nichts zu präzisieren.
"Idle-Schleife ergänzen" ist es nicht: Ohne Zieldatei und ohne die Frage, woran
man die Fertigstellung erkennt, rät der Agent, und Raten kostet mehr Zeit als
das Aufschreiben gespart hat.

Deshalb wird zuerst die KOMPLEXITÄT geschätzt und erst danach entschieden, was
fehlt. Eine einfache Aufgabe bekommt keine Auflagen.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings

# ------------------------------------------------------------------ Format
AUFGABE_RE = re.compile(r"^(\s*)[-*]\s*\[([ xX!~])\]\s*(.+?)\s*$")
FELD_RE = re.compile(r"^([A-Za-zÄÖÜäöüß][\wÄÖÜäöüß -]{1,24})\s*:\s*(.+?)\s*$")
UEBERSCHRIFT_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

OFFEN, ERLEDIGT, ZURUECK = "offen", "erledigt", "zurueckgestellt"
_MARKEN = {" ": OFFEN, "x": ERLEDIGT, "X": ERLEDIGT, "!": ZURUECK, "~": ZURUECK}
_ZEICHEN = {OFFEN: " ", ERLEDIGT: "x", ZURUECK: "!"}

# Priorität in Sortierreihenfolge. Zahlen gehen auch: "priorität: 1".
PRIORITAETEN = {
    "hoch": 0, "high": 0, "dringend": 0, "kritisch": 0, "1": 0, "a": 0,
    "mittel": 1, "medium": 1, "normal": 1, "2": 1, "b": 1,
    "niedrig": 2, "low": 2, "gering": 2, "irgendwann": 2, "3": 2, "c": 2,
}
STANDARD_PRIORITAET = 1

FELD_ALIASE = {
    "priorität": "prioritaet", "prioritaet": "prioritaet", "prio": "prioritaet",
    "priority": "prioritaet",
    "datei": "datei", "dateien": "datei", "file": "datei", "modul": "datei",
    "notiz": "datei", "ziel": "datei",
    "fertig-wenn": "fertig", "fertig wenn": "fertig", "fertig": "fertig",
    "abnahme": "fertig", "prüfung": "fertig", "pruefung": "fertig",
    "erledigt-wenn": "fertig", "definition of done": "fertig",
    "warum": "warum", "grund": "warum", "begründung": "warum",
    "abhängig": "abhaengig", "abhaengig": "abhaengig", "abhängigkeit": "abhaengig",
}


# ------------------------------------------------------- Komplexitätsschätzung
# Signale dafür, dass eine Aufgabe mehr als einen Handgriff bedeutet. Bewusst
# aus dem Wortlaut abgeleitet und ohne Modell - eine Einschätzung, die selbst
# einen Modellaufruf kostet, ist an dieser Stelle nichts wert.
#
# Zwei Eigenheiten des Deutschen mussten hier ausdrücklich berücksichtigt
# werden, beide durch Fehlurteile aufgefallen:
#
# 1. KOMPOSITA. 'Werkzeugschleife' enthält 'Schleife', aber \bschleife\b
#    trifft es nicht - vor dem Wort steht ein 'g' und keine Wortgrenze. Ohne
#    diese Zulassung galt "Refactor der Werkzeugschleife" als mittelschwer.
# 2. HARMLOSE HOMONYME. 'prüf' als Codewort zu führen, war ein Fehlgriff:
#    'Lösche die Notiz Prüfnotiz Messstand' wurde dadurch zur Codeaufgabe.
#    Ein Wort, das genauso gut in einem Notiznamen steht, taugt nicht als
#    Signal.
_CODE_WOERTER = re.compile(
    r"(?:\w*(?:schleife|funktion|klasse|modul|methode|parameter|schnittstelle)"
    r"|\b(?:loop|rückgabe|rueckgabe|api|attribut|präfix|praefix|suffix|"
    r"regex|json|parser?|datenstruktur|algorithm\w*|signatur|import)\b"
    r"|\b(?:parse|parst|parsen|sortier|filter|test|iterier|instanziier)\w*\b"
    r"|\.py\b|\(\))",
    re.IGNORECASE)
# Umbau am Bestehenden. Das ist NIE einfach, egal wie kurz es dasteht -
# genau deshalb steht "Refactor der Werkzeugschleife" hier allein schon für
# eine komplexe Aufgabe.
_UMBAU = re.compile(
    r"\b(?:refactor\w*|umbau\w*|umbauen|zerlege\w*|zerteil\w*|aufteil\w*|"
    r"aufspalt\w*|umstrukturier\w*|entkoppel\w*|migrier\w*|portier\w*|"
    r"vereinheitlich\w*|zusammenleg\w*)\b",
    re.IGNORECASE)
_MEHRSCHRITT = re.compile(
    r"\b(?:dann|danach|anschließend|anschliessend|zuerst|zusätzlich|"
    r"zusaetzlich|außerdem|ausserdem|sowie|konkret|jeweils)\b|[;]|"
    r"\b\d\.\s",
    re.IGNORECASE)
_BEDINGUNG = re.compile(
    r"\b(?:falls|sofern|wenn|bzw\.|beziehungsweise|andernfalls|"
    r"optional|je\s+nach)\b", re.IGNORECASE)
# Aufgaben, die vollständig sind, sobald das Verb dasteht.
_EINFACHE_VERBEN = re.compile(
    r"^\s*(?:lege\s+an|lege|erstelle|schreibe|ergänze|ergaenze|notiere|sammle|"
    r"lies|lese|zeige|liste|suche|finde|verknüpfe|verknuepfe|verschiebe|"
    r"lösche|loesche|benenne|tagge|aktualisiere|sichere|kopiere|exportiere)\b",
    re.IGNORECASE)

EINFACH, MITTEL, KOMPLEX = "einfach", "mittel", "komplex"


@dataclass
class Vorhaben:
    """Eine Aufgabe aus der Liste."""

    titel: str
    status: str = OFFEN
    beschreibung: str = ""
    felder: Dict[str, str] = field(default_factory=dict)
    abschnitt: str = ""
    zeile: int = 0
    einrueckung: str = ""

    # ------------------------------------------------------------ Sortierung
    @property
    def prioritaet(self) -> int:
        roh = str(self.felder.get("prioritaet", "")).strip().lower()
        return PRIORITAETEN.get(roh, STANDARD_PRIORITAET)

    @property
    def prioritaetsname(self) -> str:
        return {0: "hoch", 1: "mittel", 2: "niedrig"}[self.prioritaet]

    @property
    def offen(self) -> bool:
        return self.status == OFFEN

    # ------------------------------------------------------- Komplexität
    @property
    def komplexitaet(self) -> str:
        """Wie viel Arbeit steckt darin - einfach, mittel oder komplex?

        Die Frage entscheidet, wie streng die Schärfeprüfung ausfällt. "Lege
        eine Notiz zu X an" braucht keine Spezifikation; "Idle-Schleife in
        planner.py ergänzen" schon. Beides gleich zu behandeln, hieße entweder
        zu gängeln oder zu wenig zu verlangen - und eine Prüfung, die gängelt,
        wird nach dem zweiten Mal ignoriert.
        """
        text = f"{self.titel} {self.beschreibung}"
        punkte = 0

        # Wie viele VERSCHIEDENE Codebegriffe vorkommen. Einer kann Zufall
        # sein, zwei sind ein Muster.
        begriffe = {t.lower() for t in _CODE_WOERTER.findall(text)}
        if len(begriffe) >= 2:
            punkte += 3
        elif begriffe:
            punkte += 2

        if _UMBAU.search(text):
            punkte += 3
        if _MEHRSCHRITT.search(text):
            punkte += 1
        if _BEDINGUNG.search(text):
            punkte += 1
        if len(text.split()) > 25:
            punkte += 1
        if self.felder.get("abhaengig"):
            punkte += 1

        # Ein klares Handlungsverb am Anfang, ohne Code und ohne Umbau: das
        # sind die Aufgaben, die einsichtig sind.
        if (_EINFACHE_VERBEN.match(self.titel) and not begriffe
                and not _UMBAU.search(text)):
            punkte -= 2

        if punkte >= 3:
            return KOMPLEX
        return MITTEL if punkte >= 1 else EINFACH

    @property
    def maengel(self) -> List[str]:
        """Was zum Arbeiten fehlt - gemessen an der Komplexität.

        Gibt eine leere Liste zurück, wenn die Aufgabe so, wie sie dasteht,
        bearbeitbar ist. Das ist bei einfachen Aufgaben der Normalfall.
        """
        stufe = self.komplexitaet
        fehlt: List[str] = []
        if len(self.titel.split()) < 3:
            fehlt.append("Der Titel ist zu knapp – was genau soll getan werden?")
        if stufe == EINFACH:
            return fehlt

        beschreibung = self.beschreibung.strip()
        if stufe == KOMPLEX:
            if not self.felder.get("datei"):
                fehlt.append("Keine Zieldatei/Notiz genannt (`datei: …`) – "
                             "sonst wird geraten, wo etwas hingehört")
            if not self.felder.get("fertig"):
                fehlt.append("Kein prüfbares Ergebnis (`fertig-wenn: …`) – "
                             "sonst ist nicht entscheidbar, ob es erledigt ist")
            if len(beschreibung.split()) < 15:
                fehlt.append("Die Beschreibung nennt das Vorgehen nicht "
                             "(unter 15 Wörtern)")
        else:                                     # MITTEL
            if not self.felder.get("datei") and not beschreibung:
                fehlt.append("Weder Zieldatei noch Beschreibung – "
                             "eines von beidem braucht es")
        return fehlt

    @property
    def scharf(self) -> bool:
        return not self.maengel

    # ---------------------------------------------------------- Darstellung
    def stichpunkt(self, mit_maengeln: bool = True) -> str:
        marke = {OFFEN: "○", ERLEDIGT: "✔", ZURUECK: "⏸"}[self.status]
        zusatz = []
        if self.prioritaet != STANDARD_PRIORITAET or self.felder.get("prioritaet"):
            zusatz.append(self.prioritaetsname)
        if self.felder.get("datei"):
            zusatz.append(self.felder["datei"])
        kopf = f"{marke} **{self.titel}**"
        if zusatz:
            kopf += f"  ·  {' · '.join(zusatz)}"
        zeilen = [f"- {kopf}"]
        if mit_maengeln and self.offen and self.maengel:
            zeilen.append(f"    - ⚠ zu ungenau ({self.komplexitaet}): "
                          + "; ".join(self.maengel))
        return "\n".join(zeilen)

    def als_auftrag(self) -> str:
        """Der Text, der als Auftrag an den Planer geht.

        Alles, was dasteht, kommt mit - Felder zuerst, damit Zieldatei und
        Fertigstellungskriterium nicht am Ende eines langen Absatzes stehen.
        """
        teile = [self.titel]
        for schluessel, beschriftung in (("datei", "Zieldatei/Notiz"),
                                         ("fertig", "Fertig, wenn"),
                                         ("warum", "Zweck"),
                                         ("abhaengig", "Hängt ab von")):
            wert = self.felder.get(schluessel)
            if wert:
                teile.append(f"{beschriftung}: {wert}")
        if self.beschreibung.strip():
            teile.append(self.beschreibung.strip())
        return "\n".join(teile)

    def als_zeilen(self) -> List[str]:
        """Zurück ins Markdown-Format - für das Speichern."""
        ein = self.einrueckung
        zeilen = [f"{ein}- [{_ZEICHEN[self.status]}] {self.titel}"]
        unter = ein + "    "
        for schluessel, wert in self.felder.items():
            beschriftung = {"prioritaet": "priorität", "fertig": "fertig-wenn",
                            "abhaengig": "abhängig"}.get(schluessel, schluessel)
            zeilen.append(f"{unter}{beschriftung}: {wert}")
        for zeile in self.beschreibung.splitlines():
            zeilen.append(f"{unter}{zeile}" if zeile.strip() else "")
        return zeilen


# ==================================================================== Datei
VORLAGE = """---
titel: "Aufgabenliste"
tags:
  - system
  - aufgaben
---

# Aufgabenliste

Was hier steht, arbeitet Zeus ab - von oben nach unten, `priorität: hoch`
zuerst. Bearbeitbar in Obsidian wie jede andere Notiz.

## Format

    - [ ] Kurzer Titel, was getan werden soll
        priorität: hoch | mittel | niedrig
        datei: wo es hingehört (Datei oder Notizname)
        fertig-wenn: woran man erkennt, dass es erledigt ist
        Dann der Absatz, der das Vorgehen beschreibt. Alles, was eingerückt
        unter der Aufgabe steht, gehört zu ihr.

`[x]` erledigt, `[!]` zurückgestellt. Beides bleibt stehen.

## Wie genau muss das sein?

So genau, wie die Aufgabe komplex ist - mehr nicht.

**Einfach** (eine Handlung, klares Ziel): Der Titel reicht.

    - [ ] Lege eine Notiz zu Backpropagation an

**Komplex** (Code, mehrere Schritte, Bedingungen): Zieldatei und
Fertigstellungskriterium sind Pflicht, sonst wird geraten.

    - [ ] Idle-Schleife in planner.py ergänzen
        priorität: hoch
        datei: jarvis/planner.py
        fertig-wenn: Bei leerer Eingabe wird die oberste offene Aufgabe geladen
        Ergänze eine while-Loop, die bei fehlender Nutzer-Eingabe die Datei
        Aufgabenliste.md parst. Konkret: Lade alle Zeilen mit Präfix "- [ ]",
        sortiere nach dem Attribut priorität (falls vorhanden), sonst nach
        Dokumentenreihenfolge. Wähle den ersten Treffer und extrahiere den
        dazugehörigen Kontext.

`/aufgaben` zeigt die Liste und markiert, wo etwas fehlt.

## Offen

- [ ] Ersetze diese Zeile durch dein erstes Vorhaben
    priorität: mittel
"""


class Aufgabenbuch:
    """Liest und schreibt die Aufgabenliste."""

    def __init__(self, cfg=settings):
        self.cfg = cfg
        self.vorhaben: List[Vorhaben] = []
        self.kopf: List[str] = []          # alles vor der ersten Aufgabe
        self._roh: List[str] = []

    # ------------------------------------------------------------- Fundort
    @property
    def pfad(self) -> Path:
        """Wo die Liste liegt. AUFGABEN_DATEI in der .env sticht alles."""
        eigene = str(getattr(self.cfg, "aufgaben_datei", "") or "").strip()
        if eigene:
            return self.cfg.vault_path / eigene
        # Sonst dort suchen, wo sie üblicherweise landet - und den ersten
        # Treffer nehmen. Ohne diese Suche legt Zeus eine zweite Liste an,
        # während die erste daneben liegt und ignoriert wird.
        for kandidat in ("_System/00_JARVIS/Aufgabenliste.md",
                         "_System/Aufgabenliste.md",
                         "Aufgabenliste.md"):
            pfad = self.cfg.vault_path / kandidat
            if pfad.is_file():
                return pfad
        return self.cfg.vault_path / "_System/Aufgabenliste.md"

    def existiert(self) -> bool:
        return self.pfad.is_file()

    def vorlage_schreiben(self) -> bool:
        try:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            self.pfad.write_text(VORLAGE, encoding="utf-8")
            return True
        except OSError:
            return False

    # -------------------------------------------------------------- Lesen
    def laden(self) -> List[Vorhaben]:
        try:
            roh = self.pfad.read_text(encoding="utf-8")
        except OSError:
            self.vorhaben, self._roh = [], []
            return []
        self._roh = roh.splitlines()
        self.vorhaben = list(_lesen(self._roh))
        return self.vorhaben

    def offene(self) -> List[Vorhaben]:
        return [v for v in self.vorhaben if v.offen]

    def sortiert(self) -> List[Vorhaben]:
        """Offene Aufgaben in Arbeitsreihenfolge.

        Priorität zuerst, danach die Reihenfolge im Dokument. Genau diese
        zwei Kriterien, in dieser Reihenfolge - eine Liste, deren Reihenfolge
        man nicht vorhersagen kann, wird nicht gepflegt.
        """
        return sorted(self.offene(), key=lambda v: (v.prioritaet, v.zeile))

    def naechste(self, nur_scharfe: bool = False) -> Optional[Vorhaben]:
        for vorhaben in self.sortiert():
            if nur_scharfe and not vorhaben.scharf:
                continue
            return vorhaben
        return None

    # ------------------------------------------------------------ Schreiben
    def abhaken(self, vorhaben: Vorhaben, beleg: str = "") -> bool:
        """Eine Aufgabe als erledigt markieren - in der Datei selbst."""
        return self._status_setzen(vorhaben, ERLEDIGT, beleg)

    def zuruecklegen(self, vorhaben: Vorhaben, grund: str = "") -> bool:
        return self._status_setzen(vorhaben, ZURUECK, grund)

    def _status_setzen(self, vorhaben: Vorhaben, status: str, notiz: str) -> bool:
        if not self._roh or not (0 < vorhaben.zeile <= len(self._roh)):
            return False
        alt = self._roh[vorhaben.zeile - 1]
        treffer = AUFGABE_RE.match(alt)
        if not treffer:
            return False
        self._roh[vorhaben.zeile - 1] = (
            f"{treffer.group(1)}- [{_ZEICHEN[status]}] {treffer.group(3)}")
        if notiz:
            marke = "erledigt" if status == ERLEDIGT else "zurückgestellt"
            self._roh.insert(
                vorhaben.zeile,
                f"{treffer.group(1)}    {marke}: {dt.date.today().isoformat()} – "
                f"{notiz.strip()[:200]}")
        try:
            self.pfad.write_text("\n".join(self._roh) + "\n", encoding="utf-8")
        except OSError:
            return False
        vorhaben.status = status
        return True

    def anhaengen(self, titel: str, prioritaet: str = "mittel",
                  datei: str = "", fertig: str = "",
                  beschreibung: str = "") -> Tuple[bool, str]:
        """Eine neue Aufgabe unten anfügen - mit Gerüst für die Pflichtfelder.

        Leere Felder werden als Platzhalter geschrieben statt weggelassen.
        Ein sichtbares `fertig-wenn: ???` erinnert daran; ein fehlendes Feld
        fällt niemandem auf.
        """
        titel = str(titel or "").strip()
        if not titel:
            return False, "Kein Titel angegeben."
        if not self.existiert() and not self.vorlage_schreiben():
            return False, f"Aufgabenliste nicht schreibbar: {self.pfad}"
        self.laden()

        neu = Vorhaben(titel=titel, felder={"prioritaet": prioritaet or "mittel"},
                       beschreibung=beschreibung)
        if datei:
            neu.felder["datei"] = datei
        if fertig:
            neu.felder["fertig"] = fertig
        # Was die Komplexität verlangt, aber fehlt, kommt als Platzhalter mit.
        for schluessel in ("datei", "fertig"):
            if schluessel not in neu.felder and neu.komplexitaet == KOMPLEX:
                neu.felder[schluessel] = "???"

        zeilen = list(self._roh)
        if zeilen and zeilen[-1].strip():
            zeilen.append("")
        zeilen.extend(neu.als_zeilen())
        try:
            self.pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        except OSError as exc:
            return False, f"Nicht schreibbar: {exc}"
        self.laden()
        offen = [m for m in neu.maengel]
        hinweis = (" Noch auszufüllen: " + "; ".join(offen)) if offen else ""
        return True, f"Aufgabe aufgenommen ({neu.komplexitaet})." + hinweis

    # ------------------------------------------------------------- Bericht
    def stichpunkte(self, alle: bool = False) -> str:
        """Die Liste als Stichpunkte - so, wie sie im Chat erscheinen soll."""
        if not self.vorhaben:
            return ("Die Aufgabenliste ist leer.\n\n"
                    f"Datei: `{self.pfad}`\n"
                    "Anlegen mit `/aufgaben neu <Titel>` oder direkt in Obsidian.")
        offen = self.sortiert()
        zeilen: List[str] = []
        if offen:
            nach_stufe: Dict[int, List[Vorhaben]] = {}
            for vorhaben in offen:
                nach_stufe.setdefault(vorhaben.prioritaet, []).append(vorhaben)
            for stufe in sorted(nach_stufe):
                zeilen.append(f"**{['Hoch', 'Mittel', 'Niedrig'][stufe]}**")
                zeilen.append("")
                zeilen += [v.stichpunkt() for v in nach_stufe[stufe]]
                zeilen.append("")
        else:
            zeilen += ["Nichts offen.", ""]

        if alle:
            for status, beschriftung in ((ZURUECK, "Zurückgestellt"),
                                         (ERLEDIGT, "Erledigt")):
                gruppe = [v for v in self.vorhaben if v.status == status]
                if gruppe:
                    zeilen += [f"**{beschriftung}**", ""]
                    zeilen += [v.stichpunkt(mit_maengeln=False) for v in gruppe]
                    zeilen.append("")
        else:
            erledigt = sum(1 for v in self.vorhaben if v.status == ERLEDIGT)
            zurueck = sum(1 for v in self.vorhaben if v.status == ZURUECK)
            if erledigt or zurueck:
                zeilen.append(f"*{erledigt} erledigt, {zurueck} zurückgestellt – "
                              f"`/aufgaben alle` zeigt sie.*")
        return "\n".join(zeilen).strip()

    def zusammenfassung(self) -> str:
        offen = len(self.offene())
        unscharf = sum(1 for v in self.offene() if not v.scharf)
        text = f"{offen} offen"
        if unscharf:
            text += f", davon {unscharf} zu ungenau"
        return text


# ==================================================================== Parser
def _lesen(zeilen: List[str]):
    """Aufgaben aus den Zeilen einer Markdown-Datei holen.

    Bewusst nachsichtig: Die Datei wird von Hand gepflegt, und ein Parser, der
    an einer verrutschten Einrückung aussteigt, macht die Liste unbrauchbar.
    Was nicht erkannt wird, wird übergangen - nie ein Fehler geworfen.
    """
    aktuell: Optional[Vorhaben] = None
    abschnitt = ""
    beschreibung: List[str] = []
    in_codeblock = False

    def abschliessen():
        if aktuell is not None:
            aktuell.beschreibung = "\n".join(beschreibung).strip()
        return aktuell

    for nummer, zeile in enumerate(zeilen, 1):
        # Codeblöcke enthalten Beispiele mit '- [ ]' (siehe die Vorlage) -
        # die dürfen nicht als echte Aufgaben gelesen werden.
        if zeile.strip().startswith("```"):
            in_codeblock = not in_codeblock
            continue
        if in_codeblock:
            continue

        kopf = UEBERSCHRIFT_RE.match(zeile)
        if kopf:
            fertig = abschliessen()
            if fertig is not None:
                yield fertig
            aktuell, beschreibung = None, []
            abschnitt = kopf.group(2).strip()
            continue

        treffer = AUFGABE_RE.match(zeile)
        # Tiefer eingerückt als drei Leerzeichen ist keine Aufgabe, sondern
        # ein Beispiel in einem eingerückten Codeblock - so steht es in der
        # Vorlage, und dort wären es sonst Aufgaben, die niemand gestellt hat.
        if treffer and len(treffer.group(1).replace("\t", "    ")) > 3:
            treffer = None
        if treffer:
            fertig = abschliessen()
            if fertig is not None:
                yield fertig
            beschreibung = []
            aktuell = Vorhaben(
                titel=treffer.group(3).strip(),
                status=_MARKEN.get(treffer.group(2), OFFEN),
                abschnitt=abschnitt, zeile=nummer,
                einrueckung=treffer.group(1),
            )
            continue

        if aktuell is None:
            continue
        # Eingerückte Zeilen gehören zur laufenden Aufgabe. Eine nicht
        # eingerückte, nicht leere Zeile beendet sie.
        if zeile.strip() and not zeile.startswith((" ", "\t")):
            fertig = abschliessen()
            if fertig is not None:
                yield fertig
            aktuell, beschreibung = None, []
            continue
        if not zeile.strip():
            continue

        rest = zeile.strip()
        feld = FELD_RE.match(rest)
        if feld:
            name = FELD_ALIASE.get(feld.group(1).strip().lower())
            if name:
                aktuell.felder[name] = feld.group(2).strip()
                continue
            # Unbekanntes Feld: als Text behalten statt verwerfen. Der Mensch
            # hat es hingeschrieben, also bedeutet es etwas.
        beschreibung.append(rest)

    fertig = abschliessen()
    if fertig is not None:
        yield fertig


def aufgaben_lesen(pfad: Path) -> List[Vorhaben]:
    """Freistehender Zugang für Tests und Skripte."""
    try:
        return list(_lesen(pfad.read_text(encoding="utf-8").splitlines()))
    except OSError:
        return []
