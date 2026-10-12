"""Daten für die Oberfläche: der Vault-Graph und der Fertigkeitsbaum.

Bewusst getrennt vom Server. Der Server soll HTTP sprechen, sonst nichts; was
ein Knoten ist und warum ein Stern hell leuchtet, gehört hierher - und lässt
sich so ohne laufenden Server testen.

Zwei Ansichten:

**Der Graph** ist das, was Obsidian zeigt: jede Notiz ein Punkt, jeder
`[[Wikilink]]` eine Linie. Die Punktgröße folgt dem Grad (wie viele Linien
hängen dran), die Farbe dem obersten Ordner. Verwaiste Notizen bleiben drin -
gerade sie sind interessant, denn sie sind das, was das zweite Gehirn noch
nicht verknüpft hat.

**Der Fertigkeitsbaum** ist die Skyrim-Ansicht. Vier Größen je Stern, und
jede beantwortet eine andere Frage:

| Merkmal | Woraus | Was es beantwortet |
|---|---|---|
| Helligkeit/Größe | Aufrufe (`nutzung.py`) | Benutze ich das überhaupt? |
| Farbton | Fehlerquote | Ist es zuverlässig? |
| Ring | Kommt in einer Testdatei vor | Kann ich mich darauf verlassen? |
| Sternbild | Kategorie | Wo ist die Lücke? |

Ein nie aufgerufenes Werkzeug ist damit ein nicht gelernter Perk: sichtbar,
aber matt. Das ist genau die Auskunft, die man haben will - ein Werkzeug, das
das Modell nie anfasst, ist entweder überflüssig oder schlecht beschrieben.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import settings
from . import nutzung
from .schritte import schritte

# ---------------------------------------------------------------- Sternbilder
# Reihenfolge = Anordnung im Bild, von links nach rechts. Die Zuordnung ist
# absichtlich von Hand gemacht und nicht aus dem Werkzeugnamen geraten: Ein
# Sternbild soll ein Aufgabenfeld zeigen, keine Namensähnlichkeit.
STERNBILDER: List[Tuple[str, str, List[str]]] = [
    ("lesen", "Erkenntnis", [
        "notizen_suchen", "notiz_lesen", "struktur_anzeigen", "bloecke_anzeigen",
        "verwandte_finden", "links_pruefen", "code_auslesen",
    ]),
    ("schreiben", "Schöpfung", [
        "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten",
        "notiz_ueberschreiben", "block_ersetzen", "abschnitt_einfuegen",
        "abschnitt_ersetzen", "abschnitt_loeschen", "codeblock_hinzufuegen",
    ]),
    ("struktur", "Ordnung", [
        "ordner_anlegen", "ordner_umbenennen", "notiz_verschieben",
        "notizen_verknuepfen", "auto_verlinken", "links_bereinigen",
        "uebersichtsnotiz", "notizen_zusammenfuehren", "notiz_aufteilen",
        "frontmatter_setzen", "tags_setzen", "vault_wartung",
    ]),
    ("bewahren", "Rückweg", [
        "notiz_loeschen", "notiz_wiederherstellen", "rueckgaengig",
    ]),
    ("code", "Werkstatt", [
        "code_dateien", "quellcode_lesen", "code_suchen",
        "codebasis_importieren", "datei_importieren",
    ]),
    ("fertigkeit", "Beschwörung", [
        "fertigkeit_definieren", "fertigkeit_ausfuehren", "fertigkeiten_lesen",
    ]),
]

# Ab wie vielen Aufrufen ein Stern seine nächste Stufe erreicht. Fünf Stufen
# wie die fünf Ränge eines Perks. Bewusst logarithmisch: Der Sprung von 0 auf 1
# ist die eigentliche Nachricht ("wird überhaupt benutzt"), der von 50 auf 100
# sagt fast nichts mehr.
STUFEN = (1, 3, 10, 30, 100)

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def stufe(aufrufe: int) -> int:
    """0 = nie benutzt, 5 = ständig im Einsatz."""
    hoehe = 0
    for grenze in STUFEN:
        if aufrufe >= grenze:
            hoehe += 1
    return hoehe


# ================================================================== Graph
def _kurzname(roh: str) -> str:
    """Aus '[[Ordner/Notiz|Anzeige]]' wird 'Notiz'."""
    text = str(roh or "").strip().strip("[]").split("|")[0].split("#")[0].strip()
    text = re.sub(r"\.md$", "", text, flags=re.IGNORECASE)
    return text.rsplit("/", 1)[-1].strip()


def graph_daten(notizen: Sequence, grenze: int = 4000) -> dict:
    """Knoten und Kanten für die Graph-Ansicht.

    `notizen` ist die Liste aus VaultTools._notes(). Übergeben statt selbst
    geladen, damit dieselbe Sicht wie im Agenten gilt und der Test ohne Vault
    auskommt.
    """
    knoten: List[dict] = []
    nach_name: Dict[str, int] = {}
    for i, note in enumerate(notizen[:grenze]):
        oberordner = (note.folder or "").split("/")[0] or "(Wurzel)"
        knoten.append({
            "id": i,
            "name": note.stem,
            "titel": note.title or note.stem,
            "pfad": note.rel_path,
            "ordner": note.folder or "",
            "gruppe": oberordner,
            "tags": list(note.tags or [])[:8],
            "zeichen": len(note.body or ""),
            "grad": 0,
        })
        for schluessel in {note.stem.lower(), (note.title or "").lower(),
                           *(a.lower() for a in (note.aliases or []))}:
            if schluessel:
                nach_name.setdefault(schluessel, i)

    kanten: List[dict] = []
    gesehen = set()
    for i, note in enumerate(notizen[:grenze]):
        for roh in (note.links or []):
            ziel = nach_name.get(_kurzname(roh).lower())
            # Ein Link auf eine Notiz, die es nicht gibt, wird nicht zur Kante -
            # er ist ein toter Link und gehört in den Bericht, nicht ins Bild.
            if ziel is None or ziel == i:
                continue
            schluessel = (min(i, ziel), max(i, ziel))
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            kanten.append({"von": i, "nach": ziel})
            knoten[i]["grad"] += 1
            knoten[ziel]["grad"] += 1

    gruppen = sorted({k["gruppe"] for k in knoten})
    return {
        "knoten": knoten,
        "kanten": kanten,
        "gruppen": gruppen,
        "verwaist": sum(1 for k in knoten if k["grad"] == 0),
        "gekappt": max(0, len(notizen) - grenze),
    }


# ========================================================== Fertigkeitsbaum
# Ein Werkzeugname gilt als in Tests benutzt, wenn er AUFGERUFEN wird
# (`tools.notiz_lesen(`) oder als Zeichenkette an ausfuehren() geht
# ("notiz_lesen"). Bloße Erwähnung in einem Kommentar zählt nicht - sonst
# bekäme jedes Werkzeug seinen Ring geschenkt, und das Merkmal wäre wertlos.
def _benutzt_muster(name: str) -> re.Pattern:
    return re.compile(rf"(?:\.{name}\s*\(|\b{name}\s*\(|[\"']{name}[\"'])")


def _testbenutzung(testordner: Path, namen: Sequence[str]) -> set:
    """Welche Werkzeuge werden in den Testdateien tatsächlich aufgerufen?

    Ehrlich benannt: Es beweist nicht, dass das Werkzeug GRÜNDLICH geprüft
    wird - nur, dass es in einer Testreihe vorkommt. Deshalb heißt das Merkmal
    in der Oberfläche "kommt in Tests vor" und nicht "getestet".
    """
    text = ""
    try:
        for datei in sorted(testordner.glob("test_*.py")):
            try:
                text += datei.read_text(encoding="utf-8", errors="replace") + "\n"
            except OSError:
                continue
    except OSError:
        return set()
    return {name for name in namen if _benutzt_muster(name).search(text)}


def _stern(name: str, beschreibung: str, strich: dict, in_tests: bool,
           eigen: bool = False, schreibend: bool = False) -> dict:
    aufrufe = int(strich.get("aufrufe") or 0)
    fehler = int(strich.get("fehler") or 0)
    quote = (fehler / aufrufe) if aufrufe else 0.0
    return {
        "name": name,
        "beschreibung": beschreibung,
        "aufrufe": aufrufe,
        "fehler": fehler,
        "fehlerquote": round(quote, 3),
        "stufe": stufe(aufrufe),
        "zuletzt": strich.get("zuletzt", ""),
        "getestet": bool(in_tests),
        "eigen": bool(eigen),
        "freigeschaltet": aufrufe > 0,
        # Was das Werkzeug INNEN tut. Die Oberfläche zeigt daraus die kleinen
        # Trabanten um den Stern - und lässt sie beim Arbeiten der Reihe nach
        # aufleuchten.
        "schritte": schritte(name, schreibend),
    }


# Werkzeuge, die nicht in TOOL_SCHEMAS stehen, weil sie erst zur Laufzeit
# entstehen. Sie bekommen trotzdem ihren Stern - sonst fehlte ausgerechnet die
# Selbsterweiterung im Bild der Fähigkeiten.
_LAUFZEITWERKZEUGE = ("fertigkeit_definieren", "fertigkeit_ausfuehren",
                      "fertigkeiten_lesen")


def _eigene_sterne(fertigkeiten: Sequence, strichliste: Dict[str, dict],
                   in_tests: set) -> List[dict]:
    """Die selbstgebauten Fertigkeiten als eigenes Sternbild."""
    sterne: List[dict] = []
    for fertigkeit in fertigkeiten:
        name = getattr(fertigkeit, "name", str(fertigkeit))
        stern = _stern(name, getattr(fertigkeit, "beschreibung", ""),
                       strichliste.get(name, {}), name in in_tests, eigen=True)
        stern["parameter"] = list(getattr(fertigkeit, "parameter", []) or [])
        # Eine Fertigkeit hat ECHTE Schritte - ihre Werkzeugaufrufe. Die sind
        # die ehrlichste Schrittliste, die es hier gibt.
        stern["schritte"] = [
            {"schluessel": f"s{i}", "text": str(schritt)}
            for i, schritt in enumerate(getattr(fertigkeit, "schritte", []) or [], 1)]
        sterne.append(stern)
    return sterne


def baum_daten(schemas: Sequence[dict], fertigkeiten: Sequence = (),
               testordner: Optional[Path] = None, cfg=settings) -> dict:
    """Sternbilder mit allen Werkzeugen und den selbstgebauten Fertigkeiten."""
    from .tools import SCHREIBENDE_WERKZEUGE as schreibende

    strichliste = nutzung.lesen(cfg)
    beschreibung = {s["name"]: s.get("description", "") for s in schemas}
    parameter = {s["name"]: list((s.get("parameters") or {}).keys()) for s in schemas}
    alle_namen = ([s["name"] for s in schemas]
                  + [n for _, _, ns in STERNBILDER for n in ns]
                  + [getattr(f, "name", str(f)) for f in fertigkeiten])
    in_tests = _testbenutzung(testordner, sorted(set(alle_namen))) if testordner else set()

    def bauen(name: str, eigen: bool = False) -> dict:
        stern = _stern(name, beschreibung.get(name, ""),
                       strichliste.get(name, {}), name in in_tests, eigen=eigen,
                       schreibend=name in schreibende)
        stern["parameter"] = parameter.get(name, [])
        return stern

    zugeordnet: set = set()
    bilder: List[dict] = []
    for schluessel, titel, namen in STERNBILDER:
        sterne = [bauen(n) for n in namen
                  if n in beschreibung or n in _LAUFZEITWERKZEUGE]
        zugeordnet.update(s["name"] for s in sterne)
        if sterne:
            bilder.append({"schluessel": schluessel, "titel": titel, "sterne": sterne})

    # Was keiner Kategorie zugeordnet ist, verschwindet nicht - es bekommt ein
    # eigenes Sternbild. Sonst wäre ein neu hinzugefügtes Werkzeug unsichtbar,
    # und genau das soll die Ansicht ja aufdecken.
    uebrig = [s["name"] for s in schemas if s["name"] not in zugeordnet]
    if uebrig:
        bilder.append({"schluessel": "sonstige", "titel": "Noch ohne Sternbild",
                       "sterne": [bauen(n) for n in uebrig]})

    eigene = _eigene_sterne(fertigkeiten, strichliste, in_tests)
    if eigene:
        bilder.append({"schluessel": "eigene", "titel": "Selbst gelernt",
                       "sterne": eigene})

    alle = [s for b in bilder for s in b["sterne"]]
    return {"sternbilder": bilder, **_kennzahlen(alle)}


def _kennzahlen(sterne: List[dict]) -> dict:
    """Die Zahlen über dem Bild - und die zwei Listen, auf die es ankommt.

    'unzuverlaessig' und 'nie_benutzt' sind der eigentliche Ertrag der ganzen
    Ansicht: Das eine sagt, welches Werkzeug repariert gehört, das andere,
    welche Beschreibung das Modell nicht versteht.
    """
    return {
        "gesamt": len(sterne),
        "freigeschaltet": sum(1 for s in sterne if s["freigeschaltet"]),
        "getestet": sum(1 for s in sterne if s["getestet"]),
        "aufrufe_gesamt": sum(s["aufrufe"] for s in sterne),
        "unzuverlaessig": sorted(
            s["name"] for s in sterne if s["aufrufe"] >= 3 and s["fehlerquote"] > 0.3),
        "nie_benutzt": sorted(s["name"] for s in sterne if not s["freigeschaltet"]),
    }
