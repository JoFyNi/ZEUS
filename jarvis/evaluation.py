"""Der Messstand: wird der Agent wirklich besser?

Aus deinen eigenen Notizen (Teil 4, §4.3):

    "Ohne Messung weißt du nicht, ob dein Agent besser wird. Und ohne das zu
    wissen, ist die gesamte Selbstverbesserung Theater."

Genau diese Stufe fehlte. Gebaut sind Schleife (1), Gedächtnis (3), Reflexion
(4) und Fertigkeiten (5) - die Messung dazwischen nicht. Damit war bisher
unbekannt, ob Lektionen und Fertigkeiten die Trefferquote heben oder senken.
Deine Notiz sagt auch, warum das kein Randproblem ist: Oft SINKT sie, weil sich
der Kontext mit mittelmäßigen Lektionen füllt.

Drei Entwurfsentscheidungen:

1. ERFOLGSBEDINGUNGEN SIND DATEN, KEIN MODELLURTEIL. Jede Aufgabe trägt
   prüfbare Bedingungen ("Notiz existiert", "liegt in Ordner X", "Antwort
   nennt Y"). Ein Modell, das seine eigene Leistung bewertet, misst nichts.

2. GEMESSEN WIRD AUF EINER KOPIE. Ein Durchlauf legt Notizen an, verschiebt
   und löscht. Auf der echten Vault wäre die Messung ein Eingriff - und nach
   dem zweiten Lauf wären die Ausgangsbedingungen andere.

3. A/B GEGEN DAS GEDÄCHTNIS. Derselbe Aufgabensatz einmal mit und einmal ohne
   Lektionen und Fertigkeiten. Die Differenz ist die Antwort auf die einzige
   Frage, die zählt: Bringt das Systemlernen etwas?
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import settings


# ===================================================================== Aufgaben
@dataclass
class Pruefung:
    art: str
    ziel: str = ""
    wert: str = ""

    def beschreibung(self) -> str:
        if self.wert:
            return f"{self.art}: {self.ziel} → {self.wert}"
        return f"{self.art}: {self.ziel}" if self.ziel else self.art


@dataclass
class Aufgabe:
    kennung: str
    frage: str
    pruefungen: List[Pruefung] = field(default_factory=list)
    notiz: str = ""


@dataclass
class Ergebnis:
    kennung: str
    geglueckt: bool
    erfuellt: List[str] = field(default_factory=list)
    verfehlt: List[str] = field(default_factory=list)
    dauer: float = 0.0
    werkzeuge: int = 0
    fehlschlaege: int = 0
    fehler: str = ""
    # Was im Prompt stand: Quellen, Kontextlänge, Lektionen, Fertigkeiten.
    kontext: dict = field(default_factory=dict)


# Die drei Varianten und ihre Klarnamen. "Gedächtnis" war als Wort zu weit -
# es wurde für das Vault-Wissen UND für die gelernten Regeln benutzt, obwohl
# nur letztere überhaupt umgeschaltet wurden.
VARIANTEN = {
    "mit": "voll (Wissen + gelernte Regeln + Sitzungen)",
    "ohne": "ohne gelernte Regeln (Wissen bleibt)",
    "blind": "blind (kein Abruf – Kontrollgruppe)",
    # Trennt das Sitzungsgedächtnis von den Lektionen: Beide landen im selben
    # Promptblock, also lässt sich ihr Beitrag sonst nicht auseinanderhalten.
    "ohne_sitzung": "ohne Sitzungsgedächtnis (Regeln bleiben)",
}


@dataclass
class Durchlauf:
    variante: str
    ergebnisse: List[Ergebnis] = field(default_factory=list)
    schluessel: str = ""
    # Alle Runden dieser Variante (die erste ist dieser Durchlauf selbst).
    runden: List["Durchlauf"] = field(default_factory=list)

    @property
    def quoten_aller_runden(self) -> List[float]:
        return [r.quote for r in self.runden] or [self.quote]

    @property
    def streuung(self) -> float:
        """Spannweite der Trefferquote über die Runden, in Prozentpunkten.

        Die ehrlichste Einzelzahl über die Verlässlichkeit einer Messung. Ist
        sie größer als der gemessene Effekt, ist der Effekt nicht belegt.
        """
        quoten = self.quoten_aller_runden
        return (max(quoten) - min(quoten)) * 100

    @property
    def mittlere_quote(self) -> float:
        quoten = self.quoten_aller_runden
        return sum(quoten) / len(quoten)

    @property
    def quote(self) -> float:
        if not self.ergebnisse:
            return 0.0
        return sum(1 for e in self.ergebnisse if e.geglueckt) / len(self.ergebnisse)

    @property
    def dauer(self) -> float:
        return sum(e.dauer for e in self.ergebnisse)

    @property
    def lektionen_gesamt(self) -> int:
        """Wie viele gelernte Regeln über alle Aufgaben in den Prompt kamen."""
        return sum(int((e.kontext or {}).get("lektionen") or 0) for e in self.ergebnisse)

    @property
    def sitzungsfakten_gesamt(self) -> int:
        """Wie viele Sitzungsfakten über alle Aufgaben in den Prompt kamen.

        Dieselbe Vorfrage wie bei den Lektionen: Kamen in beiden Läufen null
        Fakten an, wurde NICHTS verglichen - und das muss im Urteil so
        dastehen, nicht als "kein Unterschied".
        """
        return sum(int((e.kontext or {}).get("sitzungsfakten") or 0)
                   for e in self.ergebnisse)

    @property
    def kontext_zeichen(self) -> int:
        return sum(int((e.kontext or {}).get("kontext_zeichen") or 0)
                   for e in self.ergebnisse)

    def nach_gruppe(self) -> Dict[str, Tuple[int, int]]:
        """Trefferquote je Aufgabengruppe (A, B, C …).

        Eine Gesamtquote sagt, DASS es hakt. Erst die Aufteilung sagt, WO -
        und das ist der Unterschied zwischen einer Zahl und einem nächsten
        Schritt. Fällt Gruppe D durch, während A trägt, liegt es an der
        Kettenlänge und nicht am Abruf; dann hilft ein besserer Index nicht.
        """
        gruppen: Dict[str, List[bool]] = {}
        for e in self.ergebnisse:
            marke = (e.kennung or "?")[0].upper()
            gruppen.setdefault(marke, []).append(e.geglueckt)
        return {g: (sum(1 for x in w if x), len(w)) for g, w in sorted(gruppen.items())}

    def zusammenfassung(self) -> str:
        geschafft = sum(1 for e in self.ergebnisse if e.geglueckt)
        return (f"{self.variante}: {geschafft}/{len(self.ergebnisse)} "
                f"({self.quote * 100:.0f} %), {self.dauer / 60:.1f} min")


# =============================================================== Prüfbedingungen
# Jede Bedingung bekommt (tools, antwort, aufgabe, pruefung) und gibt
# (erfuellt, Begruendung) zurueck. Kein Modell im Spiel - das ist der Punkt.
def _p_notiz_existiert(tools, antwort, aufgabe, pruefung):
    note = tools.resolve_note(pruefung.ziel)
    return (bool(note),
            f"'{note.rel_path}'" if note else f"'{pruefung.ziel}' nicht gefunden")


def _p_notiz_fehlt(tools, antwort, aufgabe, pruefung):
    note = tools.resolve_note(pruefung.ziel)
    return (note is None,
            f"'{pruefung.ziel}' liegt noch da" if note else "nicht vorhanden ✓")


def _p_ordner_ist(tools, antwort, aufgabe, pruefung):
    note = tools.resolve_note(pruefung.ziel)
    if not note:
        return False, f"'{pruefung.ziel}' nicht gefunden"
    ist = (note.folder or "").strip("/").lower()
    soll = pruefung.wert.strip("/").lower()
    return ist == soll, f"liegt in '{note.folder or 'Wurzel'}'"


def _p_enthaelt(tools, antwort, aufgabe, pruefung):
    note = tools.resolve_note(pruefung.ziel)
    if not note:
        return False, f"'{pruefung.ziel}' nicht gefunden"
    try:
        text = note.path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"nicht lesbar ({exc})"
    treffer = pruefung.wert.lower() in text.lower()
    return treffer, ("gefunden" if treffer else f"'{pruefung.wert}' fehlt im Text")


def _p_link_vorhanden(tools, antwort, aufgabe, pruefung):
    note = tools.resolve_note(pruefung.ziel)
    if not note:
        return False, f"'{pruefung.ziel}' nicht gefunden"
    try:
        text = note.path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"nicht lesbar ({exc})"
    muster = r"\[\[\s*" + re.escape(pruefung.wert) + r"\s*[\]|#]"
    treffer = bool(re.search(muster, text, re.IGNORECASE))
    return treffer, ("Link steht drin" if treffer else f"kein [[{pruefung.wert}]]")


def _p_antwort_enthaelt(tools, antwort, aufgabe, pruefung):
    treffer = pruefung.ziel.lower() in (antwort.text or "").lower()
    return treffer, ("genannt" if treffer else f"'{pruefung.ziel}' nicht genannt")


def _p_antwort_enthaelt_nicht(tools, antwort, aufgabe, pruefung):
    treffer = pruefung.ziel.lower() in (antwort.text or "").lower()
    return not treffer, (f"'{pruefung.ziel}' kommt vor" if treffer else "kommt nicht vor")


def _p_werkzeug_benutzt(tools, antwort, aufgabe, pruefung):
    benutzt = [c.werkzeug for c in antwort.tool_calls]
    return (pruefung.ziel in benutzt,
            ", ".join(benutzt[:5]) or "kein Werkzeug benutzt")


def _p_kein_fehlschlag(tools, antwort, aufgabe, pruefung):
    fehl = [c for c in antwort.tool_calls if not c.ergebnis.ok]
    return (not fehl,
            "alle Aufrufe geglückt" if not fehl
            else f"{len(fehl)}× fehlgeschlagen: {fehl[0].ergebnis.meldung[:60]}")


def _p_keine_toten_links(tools, antwort, aufgabe, pruefung):
    ergebnis = tools.links_pruefen(reparieren=False)
    offen = len(ergebnis.daten.get("kaputt") or [])
    return offen == 0, f"{offen} tote Links"


def _p_quelle_genannt(tools, antwort, aufgabe, pruefung):
    quellen = " ".join(antwort.sources).lower()
    treffer = pruefung.ziel.lower() in quellen
    return treffer, (quellen[:80] or "keine Quellen")


PRUEFUNGEN: Dict[str, Callable] = {
    "notiz_existiert": _p_notiz_existiert,
    "notiz_fehlt": _p_notiz_fehlt,
    "ordner_ist": _p_ordner_ist,
    "enthaelt": _p_enthaelt,
    "link_vorhanden": _p_link_vorhanden,
    "antwort_enthaelt": _p_antwort_enthaelt,
    "antwort_enthaelt_nicht": _p_antwort_enthaelt_nicht,
    "werkzeug_benutzt": _p_werkzeug_benutzt,
    "kein_fehlschlag": _p_kein_fehlschlag,
    "keine_toten_links": _p_keine_toten_links,
    "quelle_genannt": _p_quelle_genannt,
}


# ================================================================ Aufgabendatei
VORLAGE = """---
titel: "Prüfaufgaben"
tags:
  - system
  - evaluation
---

# Prüfaufgaben

Der Maßstab, an dem gemessen wird, ob der Agent besser wird. Trage hier echte
Aufgaben aus deinem Alltag ein – 20 bis 30 Stück. Jede braucht mindestens eine
prüfbare Bedingung; geprüft wird im Code, nicht vom Modell.

Verfügbare Bedingungen:

| Bedingung | Bedeutung |
|---|---|
| `notiz_existiert: Name` | Die Notiz gibt es danach |
| `notiz_fehlt: Name` | Die Notiz ist weg |
| `ordner_ist: Name -> Ordner` | Die Notiz liegt in diesem Ordner |
| `enthaelt: Name -> Text` | Der Text steht in der Notiz |
| `link_vorhanden: Name -> Ziel` | `[[Ziel]]` steht in der Notiz |
| `antwort_enthaelt: Text` | Die Antwort nennt das |
| `antwort_enthaelt_nicht: Text` | Die Antwort nennt das NICHT |
| `werkzeug_benutzt: name` | Dieses Werkzeug wurde aufgerufen |
| `kein_fehlschlag:` | Kein Werkzeugaufruf ist gescheitert |
| `keine_toten_links:` | Danach gibt es keine toten `[[Links]]` |
| `quelle_genannt: Name` | Diese Notiz war unter den Quellen |

## Aufbau des Satzes

Die Aufgaben sind in fünf Gruppen gestaffelt. Das ist Absicht: Eine Messung, in
der alle Aufgaben gleich schwer sind, zeigt nur eine Zahl. Gestaffelt zeigt sie,
WO die Grenze liegt - und die verschiebt sich, wenn der Agent besser wird.

| Gruppe | Was sie prüft | Aufgaben |
|---|---|---|
| A | Abruf: findet er, was da ist? | A1–A5 |
| B | Anlegen und Ergänzen | B1–B5 |
| C | Umbauen: verschieben, verknüpfen, aufräumen | C1–C5 |
| D | Mehrschrittig: erst suchen, dann schreiben | D1–D5 |
| E | Ehrlichkeit: nicht erfinden, Grenzen zugeben | E1–E4 |

**Wichtig:** Die Gruppen A und E messen anderes als B–D. Schneidet der Agent bei
A und E gut ab und bei D schlecht, liegt es nicht am Wissen, sondern an der
Kettenlänge - und dann hilft ein besserer Abruf gar nichts.

**Anpassen!** Die Notiznamen unten (`01_Skript_Deep_Learning`, `Wissen`) sind
Beispiele aus einer fremden Vault. Ersetze sie durch DEINE - eine Aufgabe, die
auf eine nicht vorhandene Notiz zeigt, schlägt immer fehl und misst nichts.

---

# Gruppe A – Abruf

## A1 Struktur benennen
frage: Welche Ordner gibt es in meiner Vault?
pruefung: kein_fehlschlag:
pruefung: werkzeug_benutzt: struktur_anzeigen

## A2 Wissensfrage mit Beleg
frage: Was steht in meinen Notizen zu Backpropagation?
pruefung: kein_fehlschlag:
pruefung: quelle_genannt: Deep Learning

## A3 Gezielte Notiz finden
frage: Fasse zusammen, was in der Notiz 01_Skript_Deep_Learning über Aktivierungsfunktionen steht.
pruefung: kein_fehlschlag:
pruefung: quelle_genannt: 01_Skript_Deep_Learning

## A4 Fachbegriff wörtlich
frage: Welche Lernrate wird in meinen Notizen empfohlen?
pruefung: kein_fehlschlag:

## A5 Verwandtes finden
frage: Welche meiner Notizen hängen thematisch mit Optimierern zusammen?
pruefung: kein_fehlschlag:

---

# Gruppe B – Anlegen und Ergänzen

## B1 Notiz anlegen
frage: Lege eine Notiz "Prüfnotiz Messstand" mit einer kurzen Zusammenfassung an.
pruefung: notiz_existiert: Prüfnotiz Messstand
pruefung: kein_fehlschlag:

## B2 Notiz ergänzen
frage: Ergänze die Notiz "Prüfnotiz Messstand" um einen Abschnitt "Offene Fragen".
pruefung: enthaelt: Prüfnotiz Messstand -> Offene Fragen
pruefung: kein_fehlschlag:

## B3 In einen bestimmten Ordner
frage: Lege im Ordner Wissen eine Notiz "Prüfnotiz Ordnerwahl" über Regelkreise an.
pruefung: notiz_existiert: Prüfnotiz Ordnerwahl
pruefung: ordner_ist: Prüfnotiz Ordnerwahl -> Wissen

## B4 Mit Schlagworten
frage: Lege eine Notiz "Prüfnotiz Tags" über Gradientenabstieg an und versieh sie mit den Tags mathematik und optimierung.
pruefung: notiz_existiert: Prüfnotiz Tags
pruefung: enthaelt: Prüfnotiz Tags -> optimierung

## B5 Code als Codeblock
frage: Füge der Notiz "Prüfnotiz Messstand" ein kurzes Python-Beispiel für eine Sigmoid-Funktion hinzu.
pruefung: enthaelt: Prüfnotiz Messstand -> ```
pruefung: kein_fehlschlag:

---

# Gruppe C – Umbauen

## C1 Verknüpfen
frage: Verknüpfe die Notizen "Prüfnotiz Messstand" und "Prüfnotiz Ordnerwahl" miteinander.
pruefung: link_vorhanden: Prüfnotiz Messstand -> Prüfnotiz Ordnerwahl
pruefung: kein_fehlschlag:

## C2 Verschieben
frage: Verschiebe die Notiz "Prüfnotiz Tags" in den Ordner Wissen.
pruefung: ordner_ist: Prüfnotiz Tags -> Wissen
pruefung: kein_fehlschlag:

## C3 Keine toten Links hinterlassen
frage: Prüfe die Vault auf kaputte Links und repariere, was sich reparieren lässt.
pruefung: keine_toten_links:
pruefung: kein_fehlschlag:

## C4 Löschen
frage: Lösche die Notiz "Prüfnotiz Tags".
pruefung: notiz_fehlt: Prüfnotiz Tags
pruefung: kein_fehlschlag:

## C5 Zurücknehmen
frage: Mach die letzte Änderung rückgängig.
pruefung: kein_fehlschlag:
pruefung: werkzeug_benutzt: rueckgaengig

---

# Gruppe D – Mehrschrittig

Hier bricht die Kette am ehesten. Bei einer Erfolgsquote p je Schritt liegt die
Quote nach n Schritten bei p hoch n: Bei 95 % je Schritt sind vier Schritte nur
noch 81 %. Genau das soll sichtbar werden.

## D1 Suchen, dann schreiben
frage: Suche in meinen Notizen alles zum Thema Optimierer und lege daraus eine Übersichtsnotiz "Prüfnotiz Optimierer" an.
pruefung: notiz_existiert: Prüfnotiz Optimierer
pruefung: kein_fehlschlag:

## D2 Lesen, verdichten, ablegen
frage: Lies die Notiz 01_Skript_Deep_Learning und lege eine Kurzfassung als "Prüfnotiz Kurzfassung" im Ordner Wissen ab.
pruefung: notiz_existiert: Prüfnotiz Kurzfassung
pruefung: ordner_ist: Prüfnotiz Kurzfassung -> Wissen

## D3 Anlegen und sofort verknüpfen
frage: Lege eine Notiz "Prüfnotiz Kette" über Fehlerfortpflanzung an und verknüpfe sie mit "Prüfnotiz Optimierer".
pruefung: notiz_existiert: Prüfnotiz Kette
pruefung: link_vorhanden: Prüfnotiz Kette -> Prüfnotiz Optimierer

## D4 Bedingt handeln
frage: Falls es eine Notiz zu Adam gibt, ergänze sie um einen Hinweis auf Momentum. Falls nicht, sage mir das.
pruefung: kein_fehlschlag:

## D5 Eigenen Code ansehen
frage: In welcher Datei meines Quellcodes wird der Index aufgebaut?
pruefung: kein_fehlschlag:
pruefung: antwort_enthaelt: index

---

# Gruppe E – Ehrlichkeit

Die wichtigste Gruppe und die am leichtesten zu übersehende. Ein Agent, der auf
alles eine Antwort hat, ist nicht besser, sondern gefährlicher.

## E1 Keine Erfindung
frage: Was steht in meinen Notizen über den Zusammenhang von Quantenfeldtheorie und Bauleitplanung?
pruefung: kein_fehlschlag:
pruefung: antwort_enthaelt_nicht: Der Zusammenhang besteht darin

## E2 Nicht vorhandene Notiz
frage: Fasse die Notiz "Zwölfdimensionale Kaffeeanalyse" zusammen.
pruefung: antwort_enthaelt: nicht

## E3 Grenzen der eigenen Werkzeuge
frage: Schicke eine E-Mail an meinen Kollegen mit der Zusammenfassung von gestern.
pruefung: antwort_enthaelt: nicht

## E4 Quelle statt Vermutung
frage: Welche Zahl steht in meinen Notizen für die empfohlene Batchgröße? Nenne die Notiz, aus der du sie hast.
pruefung: kein_fehlschlag:
"""

BLOCK_RE = re.compile(r"^##\s+(\S+)\s*(.*)$")


def aufgaben_lesen(pfad: Path) -> List[Aufgabe]:
    """Aufgaben aus der Markdown-Datei lesen - tolerant gegen Handbearbeitung."""
    try:
        roh = pfad.read_text(encoding="utf-8")
    except OSError:
        return []
    aufgaben: List[Aufgabe] = []
    aktuell: Optional[Aufgabe] = None
    for zeile in roh.splitlines():
        kopf = BLOCK_RE.match(zeile)
        if kopf:
            if aktuell and aktuell.frage:
                aufgaben.append(aktuell)
            aktuell = Aufgabe(kennung=kopf.group(1), frage="", notiz=kopf.group(2).strip())
            continue
        if aktuell is None:
            continue
        schlank = zeile.strip()
        if schlank.lower().startswith("frage:"):
            aktuell.frage = schlank.split(":", 1)[1].strip()
        elif schlank.lower().startswith("pruefung:") or schlank.lower().startswith("prüfung:"):
            rest = schlank.split(":", 1)[1].strip()
            aktuell.pruefungen.append(_pruefung_lesen(rest))
    if aktuell and aktuell.frage:
        aufgaben.append(aktuell)
    return [a for a in aufgaben if a.pruefungen]


def _pruefung_lesen(rest: str) -> Pruefung:
    """'ordner_ist: Notiz -> Ordner' zerlegen."""
    art, _, arg = rest.partition(":")
    art = art.strip().lower()
    arg = arg.strip()
    if "->" in arg:
        ziel, _, wert = arg.partition("->")
        return Pruefung(art, ziel.strip(), wert.strip())
    return Pruefung(art, arg)


def vorlage_schreiben(pfad: Path) -> bool:
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(VORLAGE, encoding="utf-8")
        return True
    except OSError:
        return False


# ==================================================================== Messstand
class Messstand:
    """Führt Prüfaufgaben aus und vergleicht mit/ohne Gedächtnis."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg

    @property
    def aufgabendatei(self) -> Path:
        return (self.agent.tools.root / (self.cfg.learning_folder or "_Lernen")
                / "Pruefaufgaben.md")

    # ------------------------------------------------------------- Kopie
    def _kopie_anlegen(self, melden) -> Tuple[Path, Path]:
        """Vault und Index in einen Wegwerf-Ordner kopieren.

        Ohne das wäre die Messung ein Eingriff: Der erste Durchlauf legt
        Notizen an, der zweite fände andere Ausgangsbedingungen vor - und
        A/B wäre wertlos, weil A die Welt für B verändert hat.
        """
        basis = Path(tempfile.mkdtemp(prefix="jarvis_messung_"))
        ziel_vault = basis / "vault"
        melden("kopiere Vault …")
        shutil.copytree(self.agent.tools.root, ziel_vault,
                        ignore=shutil.ignore_patterns(".jarvis", ".git", ".obsidian"))
        ziel_store = basis / "vector_store"
        if self.cfg.store_path.is_dir():
            melden("kopiere Index …")
            shutil.copytree(self.cfg.store_path, ziel_store)
        return ziel_vault, ziel_store

    def _agent_fuer(self, vault: Path, store: Path, variante: str):
        """Ein frischer Agent auf der Kopie, eingestellt nach Variante.

        Die drei Varianten trennen drei verschiedene Dinge, die vorher alle
        "Gedächtnis" hießen:

        * ``voll``  – alles an: Vault-Wissen, Lektionen, Fertigkeiten.
        * ``ohne``  – Vault-Wissen ja, Lektionen und Fertigkeiten nein.
          Das ist die Frage "bringt das Systemlernen etwas?".
        * ``blind`` – gar kein Abruf. Das Modell antwortet aus seinen
          Gewichten. Das ist die KONTROLLGRUPPE: Schneidet sie genauso gut ab
          wie ``voll``, dann prüfen die Aufgaben nicht das Wissen der Vault,
          und jeder Vergleich zwischen den ersten beiden ist wertlos.
        """
        from copy import copy

        from .agent import JarvisAgent
        from .index import KnowledgeIndex

        mit_gedaechtnis = variante in ("mit", "ohne_sitzung")
        cfg = copy(self.cfg)
        cfg.vault_path = vault
        cfg.store_path = store
        cfg.tts_enabled = False
        cfg.stt_enabled = False
        cfg.learning_enabled = mit_gedaechtnis
        cfg.self_extension = mit_gedaechtnis
        # SESSION_MEMORY legt Lesen UND Schreiben still - genau dafür ist der
        # Schalter da. Ohne ihn ließe sich der Beitrag der Sitzungsfakten nicht
        # von dem der Lektionen trennen, weil beide im selben Block stehen.
        cfg.session_memory = bool(getattr(self.cfg, "session_memory", False)) and \
            variante == "mit"
        # Temperatur auf null. Bei 0,3 schwankt dieselbe Aufgabe zwischen zwei
        # Läufen von allein - und bei fünf Aufgaben verschiebt EIN Umschlag die
        # Quote um 20 Punkte. Dann misst man das Würfeln, nicht die Wirkung des
        # Gedächtnisses. Das ist der Grund, warum "mit" und "ohne" bisher
        # zufällig mal gleich, mal verschieden aussahen.
        cfg.temperature = 0.0
        cfg.content_temperature = 0.0
        index = KnowledgeIndex(cfg, embeddings=getattr(self.agent.index, "embeddings", None))
        agent = JarvisAgent(cfg, index=index)
        agent.sync()
        # Auch die Lektionen im Prompt abschalten, sonst misst "ohne" nichts.
        agent.gedaechtnis_aus = not mit_gedaechtnis
        agent.wissen_aus = variante == "blind"
        return agent

    # ---------------------------------------------------------- Durchlauf
    def durchlauf(self, aufgaben: List[Aufgabe], mit_gedaechtnis: bool = True,
                  fortschritt=None, variante: str = "") -> Durchlauf:
        import time as _time

        melden = fortschritt or (lambda _t: None)
        schluessel = variante or ("mit" if mit_gedaechtnis else "ohne")
        ergebnis = Durchlauf(variante=VARIANTEN.get(schluessel, schluessel),
                             schluessel=schluessel)

        vault, store = self._kopie_anlegen(melden)
        try:
            agent = self._agent_fuer(vault, store, schluessel)
            for nummer, aufgabe in enumerate(aufgaben, 1):
                melden(f"[{ergebnis.variante}] {nummer}/{len(aufgaben)} {aufgabe.kennung}")
                begonnen = _time.time()
                try:
                    antwort = agent.ask(aufgabe.frage)
                except Exception as exc:
                    ergebnis.ergebnisse.append(Ergebnis(
                        kennung=aufgabe.kennung, geglueckt=False,
                        verfehlt=[f"Absturz: {type(exc).__name__}: {exc}"],
                        dauer=_time.time() - begonnen, fehler=str(exc)[:200]))
                    continue
                einzeln = self._bewerten(agent, aufgabe, antwort, _time.time() - begonnen)
                # Festhalten, WAS im Prompt stand. Nur so lässt sich hinterher
                # sagen, ob sich zwei Varianten überhaupt unterschieden haben.
                gemerkt = dict(getattr(agent, "letzter_kontext", {}) or {})
                einzeln.kontext = gemerkt
                ergebnis.ergebnisse.append(einzeln)
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)
        return ergebnis

    def _bewerten(self, agent, aufgabe: Aufgabe, antwort, dauer: float) -> Ergebnis:
        erfuellt: List[str] = []
        verfehlt: List[str] = []
        for pruefung in aufgabe.pruefungen:
            funktion = PRUEFUNGEN.get(pruefung.art)
            if funktion is None:
                verfehlt.append(f"{pruefung.art}: unbekannte Bedingung")
                continue
            try:
                ok, grund = funktion(agent.tools, antwort, aufgabe, pruefung)
            except Exception as exc:
                verfehlt.append(f"{pruefung.beschreibung()} – Prüfung scheiterte ({exc})")
                continue
            (erfuellt if ok else verfehlt).append(
                f"{pruefung.beschreibung()} – {grund}")
        return Ergebnis(
            kennung=aufgabe.kennung,
            geglueckt=not verfehlt,
            erfuellt=erfuellt, verfehlt=verfehlt, dauer=dauer,
            werkzeuge=len(antwort.tool_calls),
            fehlschlaege=sum(1 for c in antwort.tool_calls if not c.ergebnis.ok),
        )

    # ------------------------------------------------------------- Messung
    def messen(self, varianten: List[str] = None, grenze: int = 0,
               fortschritt=None, runden: int = 1) -> Dict[str, Durchlauf]:
        """Alle Varianten messen, optional mehrfach.

        `runden > 1` ist der Weg gegen die Streuung. Zwei Läufe desselben
        Aufgabensatzes lieferten bei Josh gegenteilige Urteile - einmal
        "Regeln schaden -20", einmal "Regeln helfen +40". Temperatur 0 hilft
        gegen das Würfeln im Modell, aber nicht gegen alles: Ein Modell mit
        Denkschritten (qwen3) ist auch bei 0 nicht reproduzierbar, und
        Werkzeugaufrufe treffen auf eine Vault, die sich unterwegs ändert.
        Mehrere Runden machen diese Streuung sichtbar, statt sie in eine
        einzige Zahl zu verstecken.
        """
        melden = fortschritt or (lambda _t: None)
        aufgaben = aufgaben_lesen(self.aufgabendatei)
        if not aufgaben:
            return {}
        if grenze > 0:
            aufgaben = aufgaben[:grenze]
        varianten = varianten or ["mit", "ohne"]
        runden = max(1, min(10, runden))
        laeufe: Dict[str, Durchlauf] = {}
        for variante in varianten:
            if variante not in VARIANTEN:
                melden(f"Unbekannte Variante '{variante}' – übersprungen "
                       f"(möglich: {', '.join(VARIANTEN)})")
                continue
            for runde in range(1, runden + 1):
                if runden > 1:
                    melden(f"— {VARIANTEN[variante]}, Runde {runde}/{runden} —")
                lauf = self.durchlauf(aufgaben, fortschritt=melden, variante=variante)
                if variante in laeufe:
                    laeufe[variante].runden.append(lauf)
                else:
                    lauf.runden = [lauf]
                    laeufe[variante] = lauf
        return laeufe

    # -------------------------------------------------------------- Bericht
    def bericht(self, laeufe: Dict[str, Durchlauf]) -> str:
        heute = dt.date.today().isoformat()
        zeilen = [
            "---", f'titel: "Messung {heute}"',
            "tags:\n  - system\n  - evaluation", f"datum: {heute}", "---", "",
            f"# Messung {heute}", "",
        ]
        if not laeufe:
            zeilen.append("Keine Prüfaufgaben gefunden.")
            return "\n".join(zeilen)

        # Die Spalten "Regeln" und "Kontext" sind der Kern der Verbesserung:
        # Sie zeigen, ob sich die Varianten im PROMPT unterschieden haben. Ohne
        # sie liest man zwei gleiche Quoten und hält das für ein Ergebnis.
        mehrfach = any(len(d.quoten_aller_runden) > 1 for d in laeufe.values())
        kopf = "| Variante | Erfolg | Quote | Regeln im Prompt | Kontext | Dauer |"
        trenn = "|---|---|---|---|---|---|"
        if mehrfach:
            kopf = ("| Variante | Quoten der Runden | Mittel | Streuung "
                    "| Regeln im Prompt | Dauer |")
            trenn = "|---|---|---|---|---|---|"
        zeilen += [kopf, trenn]
        for durchlauf in laeufe.values():
            geschafft = sum(1 for e in durchlauf.ergebnisse if e.geglueckt)
            if mehrfach:
                quoten = ", ".join(f"{q * 100:.0f} %"
                                   for q in durchlauf.quoten_aller_runden)
                zeilen.append(
                    f"| {durchlauf.variante} | {quoten} "
                    f"| {durchlauf.mittlere_quote * 100:.0f} % "
                    f"| ±{durchlauf.streuung:.0f} Punkte "
                    f"| {durchlauf.lektionen_gesamt} "
                    f"| {sum(r.dauer for r in durchlauf.runden) / 60:.1f} min |")
            else:
                zeilen.append(
                    f"| {durchlauf.variante} | {geschafft}/{len(durchlauf.ergebnisse)} "
                    f"| {durchlauf.quote * 100:.0f} % | {durchlauf.lektionen_gesamt} "
                    f"| {durchlauf.kontext_zeichen} Zeichen "
                    f"| {durchlauf.dauer / 60:.1f} min |")
        zeilen += ["",
                   "*Gemessen bei Temperatur 0 – sonst schwankt dieselbe Aufgabe "
                   "zwischen zwei Läufen von allein, und bei wenigen Aufgaben misst "
                   "man das Würfeln statt der Wirkung.*", ""]

        # Aufteilung nach Gruppen - nur sinnvoll, wenn es mehr als eine gibt.
        gruppen = {g for d in laeufe.values() for g in d.nach_gruppe()}
        if len(gruppen) > 1:
            zeilen += ["### Wo es hakt", "",
                       "| Gruppe | " + " | ".join(d.variante for d in laeufe.values())
                       + " |",
                       "|---" * (len(laeufe) + 1) + "|"]
            for gruppe in sorted(gruppen):
                spalten = []
                for durchlauf in laeufe.values():
                    geschafft, gesamt = durchlauf.nach_gruppe().get(gruppe, (0, 0))
                    spalten.append(f"{geschafft}/{gesamt}" if gesamt else "–")
                zeilen.append(f"| {gruppe} | " + " | ".join(spalten) + " |")
            zeilen += ["",
                       "> Eine Gesamtquote sagt, DASS es hakt. Erst diese Aufteilung "
                       "sagt, WO. Trägt A (Abruf) und fällt D (mehrschrittig) durch, "
                       "liegt es an der Kettenlänge, nicht am Wissen – dann hilft ein "
                       "besserer Index nichts. Fällt E (Ehrlichkeit) durch, erfindet "
                       "der Agent, und jede andere Quote ist wertlos.", ""]

        if "mit" in laeufe and "ohne_sitzung" in laeufe:
            zeilen.append(self._urteil_sitzung(laeufe["mit"],
                                               laeufe["ohne_sitzung"]))
            zeilen.append("")

        if "mit" in laeufe and "ohne" in laeufe:
            zeilen.append(self._urteil(laeufe["mit"], laeufe["ohne"],
                                       laeufe.get("blind")))
            zeilen.append("")
            zeilen.append("## Wo sich die Varianten unterscheiden")
            zeilen.append("")
            unterschiede = self._unterschiede(laeufe["mit"], laeufe["ohne"])
            zeilen.extend(unterschiede or ["- (keine – beide Varianten verhalten sich gleich)"])
            zeilen.append("")

        for schluessel, durchlauf in laeufe.items():
            zeilen.append(f"## Einzelergebnisse – {durchlauf.variante}")
            zeilen.append("")
            for e in durchlauf.ergebnisse:
                marke = "✔" if e.geglueckt else "✖"
                zeilen.append(f"- {marke} **{e.kennung}** "
                              f"({e.dauer:.0f} s, {e.werkzeuge} Werkzeuge"
                              + (f", {e.fehlschlaege} fehlgeschlagen" if e.fehlschlaege else "")
                              + ")")
                for grund in e.verfehlt:
                    zeilen.append(f"    - ✖ {grund}")
            zeilen.append("")
        return "\n".join(zeilen)

    @staticmethod
    def _urteil_sitzung(mit: Durchlauf, ohne_sitzung: Durchlauf) -> str:
        """Hat das SITZUNGSGEDÄCHTNIS etwas gebracht?

        Dieselbe Vorfrage wie bei den Lektionen, und sie ist hier noch
        wichtiger: Sitzungsfakten entstehen erst im Betrieb. Ein frisch
        aufgesetzter Messstand hat gar keine - dann sind beide Läufe
        wortgleich und jede Differenz ist Rauschen, kein Ergebnis.
        """
        anzahl = len(mit.ergebnisse)
        if mit.sitzungsfakten_gesamt == 0:
            return (
                "## Urteil zum Sitzungsgedächtnis\n\n"
                "**Es wurde nichts verglichen.** In der Variante „voll\" kam über "
                f"alle {anzahl} Aufgaben hinweg KEIN einziger Sitzungsfakt in den "
                "Prompt. Beide Läufe waren damit wortgleich; eine Differenz in den "
                "Quoten ist Streuung, kein Beitrag des Sitzungsgedächtnisses.\n\n"
                "Zwei prüfbare Ursachen:\n\n"
                "1. Es gibt noch keine Sitzungsnotizen – `/sitzungen` zeigt den "
                "Bestand, `/sitzung merken` legt eine an.\n"
                "2. Der Abruf findet sie nicht: Er sucht über den Index im Ordner "
                "`SESSION_FOLDER`. Passen die Prüfaufgaben thematisch nicht zu dem, "
                "worüber du bisher gesprochen hast, greift kein Fakt.\n"
            )
        differenz = (mit.quote - ohne_sitzung.quote) * 100
        richtung = ("hilft" if differenz > 0 else
                    "schadet" if differenz < 0 else "ändert nichts")
        return (
            "## Urteil zum Sitzungsgedächtnis\n\n"
            f"Mit Sitzungsgedächtnis: **{mit.quote * 100:.0f} %**, ohne: "
            f"**{ohne_sitzung.quote * 100:.0f} %** – das Sitzungsgedächtnis "
            f"{richtung} ({differenz:+.0f} Punkte) bei "
            f"{mit.sitzungsfakten_gesamt} eingesetzten Fakten über {anzahl} "
            "Aufgaben.\n\n"
            "> Bei wenigen Aufgaben verschiebt ein einziger Umschlag die Quote "
            "erheblich. Erst mehrere Runden (`--messung --runden 3`) trennen "
            "Wirkung von Streuung.\n"
        )

    @staticmethod
    def _urteil(mit: Durchlauf, ohne: Durchlauf,
                blind: Optional[Durchlauf] = None) -> str:
        """Die einzige Frage, die zählt - beantwortet ohne Beschönigung.

        Wichtiger als die Zahl ist die Vorfrage: HAT sich überhaupt etwas
        unterschieden? Kamen in beiden Läufen null Lektionen in den Prompt,
        dann waren die Prompts identisch - und dann ist "kein Unterschied"
        keine Erkenntnis über das Gedächtnis, sondern die Feststellung, dass
        nichts verglichen wurde. Genau diese Verwechslung ließ die Messung
        aussagekräftig aussehen, obwohl sie leerlief.
        """
        anzahl = len(mit.ergebnisse)
        differenz = (mit.quote - ohne.quote) * 100
        teile: List[str] = []

        # --- Vorfrage 1: Gab es einen Unterschied im Prompt? ---
        if mit.lektionen_gesamt == 0:
            return (
                "## Urteil\n\n"
                "**Es wurde nichts verglichen.** In der Variante „voll\" kam über alle "
                f"{anzahl} Aufgaben hinweg KEINE einzige gelernte Regel in den Prompt. "
                "Damit waren beide Läufe wortgleich, und die Quoten müssen "
                "übereinstimmen – das ist kein Ergebnis über das Gedächtnis, sondern "
                "der Beweis, dass es an diesen Aufgaben nicht beteiligt war.\n\n"
                "Zwei mögliche Ursachen, beide prüfbar:\n\n"
                "1. Es gibt noch zu wenige Lektionen. `/lernen` zeigt den Bestand.\n"
                "2. Der Abruf findet sie nicht: `relevante_lektionen()` wählt nach "
                "Stichwörtern aus der Frage. Passen die Prüfaufgaben thematisch nicht "
                "zu dem, wobei du bisher korrigiert hast, greift keine Regel.\n\n"
                "Bis dahin ist die Variante `blind` der aussagekräftige Vergleich – "
                "sie zeigt wenigstens, ob die Aufgaben das Vault-Wissen überhaupt "
                "verlangen."
            )

        # --- Vorfrage 2: Prüfen die Aufgaben überhaupt das Wissen? ---
        if blind is not None and blind.ergebnisse:
            abstand = (mit.quote - blind.quote) * 100
            if abstand < 10:
                teile.append(
                    f"**Warnung zur Aussagekraft:** Ohne jeden Abruf (blind) werden "
                    f"{blind.quote * 100:.0f} % erreicht, mit vollem Zugriff "
                    f"{mit.quote * 100:.0f} % – ein Abstand von nur {abstand:+.0f} Punkten. "
                    f"Diese Aufgaben prüfen also kaum das Wissen der Vault, sondern "
                    f"etwas, das das Modell ohnehin kann. Solange das so ist, misst "
                    f"jeder Vergleich zwischen „voll\" und „ohne Regeln\" Rauschen. "
                    f"Aufgaben ergänzen, deren Antwort NUR in deinen Notizen steht.")
            else:
                teile.append(
                    f"Die Aufgaben prüfen tatsächlich Wissen: blind {blind.quote * 100:.0f} %, "
                    f"mit Zugriff {mit.quote * 100:.0f} % – {abstand:+.0f} Punkte. "
                    f"Der Abruf trägt also.")

        # --- Vorfrage 3: Ist der Effekt größer als die eigene Streuung? ---
        # Der wichtigste Zusatz nach zwei Läufen mit gegenteiligem Urteil:
        # -20 Punkte im ersten, +40 im zweiten, gleicher Aufgabensatz. Wenn
        # dieselbe Variante zwischen ihren Runden stärker schwankt als der
        # Unterschied zwischen den Varianten, misst man die Schwankung.
        streuung = max(mit.streuung, ohne.streuung)
        runden = len(mit.quoten_aller_runden)
        if runden > 1 and streuung >= abs(differenz):
            teile.append(
                f"**Der Effekt verschwindet in der Streuung.** Über {runden} Runden "
                f"schwankt dieselbe Variante um bis zu {streuung:.0f} Punkte, der "
                f"Unterschied zwischen den Varianten beträgt {differenz:+.0f}. "
                f"Damit ist nichts belegt – weder Nutzen noch Schaden. "
                f"Mehr Aufgaben helfen hier mehr als mehr Runden: Bei "
                f"{anzahl} Aufgaben ist die kleinste messbare Stufe "
                f"{100 / max(1, anzahl):.0f} Punkte.")
            if anzahl < 10:
                teile.append(
                    f"> **Vorbehalt:** {anzahl} Aufgaben sind zu wenig. Ziel sind 20–30.")
            return "## Urteil\n\n" + "\n\n".join(teile)
        if runden == 1 and abs(differenz) <= 100 / max(1, anzahl) * 1.5:
            teile.append(
                f"**Vorsicht:** Der Unterschied von {differenz:+.0f} Punkten entspricht "
                f"etwa {abs(differenz) * anzahl / 100:.0f} von {anzahl} Aufgaben. "
                f"Das kann ein einzelner Umschlag sein. Mit `/messen alle 3x` läuft "
                f"jede Variante dreimal – dann steht die Streuung daneben und man "
                f"sieht, ob der Effekt trägt.")

        # --- Die eigentliche Frage ---
        if differenz > 5:
            teile.append(f"**Die gelernten Regeln helfen: +{differenz:.0f} Prozentpunkte.** "
                         f"Sie verdienen ihren Platz im Kontext.")
        elif differenz < -5:
            teile.append(
                f"**Die gelernten Regeln schaden: {differenz:.0f} Prozentpunkte.** "
                f"Der Kontext füllt sich mit Regeln, die mehr ablenken als helfen. "
                f"Erst die Abrufqualität verbessern – nicht mehr Lektionen hinzufügen. "
                f"`/lektionen konsolidieren` und schwache verwerfen.")
        else:
            teile.append(
                f"**Kein messbarer Unterschied ({differenz:+.0f} Punkte)** – bei "
                f"{mit.lektionen_gesamt} eingespielten Regeln über {anzahl} Aufgaben. "
                f"Die Regeln erreichen den Prompt, ändern aber nichts am Ergebnis.")

        if anzahl < 10:
            teile.append(
                f"> **Vorbehalt:** {anzahl} Aufgaben sind zu wenig für eine belastbare "
                f"Aussage. Ein einzelner Umschlag verschiebt die Quote um "
                f"{100 / max(1, anzahl):.0f} Punkte – mehr als jeder hier gemessene "
                f"Effekt. Ziel sind 20–30.")
        return "## Urteil\n\n" + "\n\n".join(teile)

    @staticmethod
    def _unterschiede(mit: Durchlauf, ohne: Durchlauf) -> List[str]:
        nach_kennung = {e.kennung: e for e in ohne.ergebnisse}
        zeilen: List[str] = []
        for e in mit.ergebnisse:
            gegen = nach_kennung.get(e.kennung)
            if gegen is None or e.geglueckt == gegen.geglueckt:
                continue
            if e.geglueckt:
                zeilen.append(f"- **{e.kennung}**: nur MIT Gedächtnis geglückt "
                              f"– dafür ist es da")
            else:
                zeilen.append(f"- **{e.kennung}**: nur OHNE Gedächtnis geglückt "
                              f"– hier stört der Kontext ({'; '.join(e.verfehlt[:1])})")
        return zeilen

    def journal_schreiben(self, laeufe: Dict[str, Durchlauf]) -> bool:
        """Quoten fortschreiben, damit der Verlauf über Wochen sichtbar wird."""
        eintrag = {
            "datum": dt.datetime.now().isoformat(timespec="seconds"),
            "modell": self.cfg.llm_model,
            "aufgaben": len(next(iter(laeufe.values())).ergebnisse) if laeufe else 0,
            "quoten": {k: round(d.quote, 3) for k, d in laeufe.items()},
        }
        try:
            ziel = self.cfg.work_dir / "messungen.jsonl"
            ziel.parent.mkdir(parents=True, exist_ok=True)
            with open(ziel, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
            return True
        except OSError:
            # Der Bericht steht auch ohne Journal; nur der Verlauf fehlt dann.
            return False

    def verlauf(self, anzahl: int = 10) -> List[dict]:
        try:
            roh = (self.cfg.work_dir / "messungen.jsonl").read_text(encoding="utf-8")
        except OSError:
            return []
        eintraege = []
        for zeile in roh.splitlines():
            try:
                eintraege.append(json.loads(zeile))
            except ValueError:
                continue
        return eintraege[-anzahl:]
