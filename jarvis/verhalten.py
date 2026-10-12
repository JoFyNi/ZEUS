"""Verhaltensfertigkeiten - die zweite Art, sich etwas zu merken.

Warum es das geben muss, steht in Joshs Protokoll vom 04.09.:

    Josh:  "Ich will einen neuen Skill erstellen. Ziel ist es, einen Ablauf
            oder Prozess visuell darzustellen. [...] Nicht mal eine Notiz,
            sondern einfach als Chatwiedergabe."
    Zeus:  legt eine NOTIZ an, erklärt dann, warum es als Fertigkeit nicht
           geht, und schlägt vor, es einfach immer so zu machen.

Zeus hatte recht - und konnte es trotzdem nicht. Denn `skills.py` kennt genau
eine Art von Fertigkeit: einen Ablaufplan aus vorhandenen Werkzeugen. Was Josh
wollte, ist kein Ablauf an der Vault, sondern eine HALTUNG: "wenn eine Anfrage
so aussieht, dann antworte so". Dafür gab es keine Form, also wurde daraus das
Nächstbeste - eine Notiz, die niemand je wieder liest.

Damit sind es jetzt zwei Arten, und die Trennung ist scharf:

    Ablauffertigkeit  (skills.py)   TUT etwas an der Vault.
                                    Geprüft, probegelaufen, ausführbar.
    Verhaltensfertigkeit (hier)     ÄNDERT, WIE geantwortet wird.
                                    Ausgelöst über Stichwörter, wirkt im Prompt.

Eine Verhaltensfertigkeit ist damit das Gegenstück zu `_System/Grundregeln`:
Die Grundregeln gelten immer und schreibt Josh von Hand. Eine
Verhaltensfertigkeit gilt bei passendem Anlass und darf Zeus sich selbst
geben.

Aufbau einer solchen Notiz in `_Fertigkeiten/Verhalten/`:

    ---
    fertigkeit: prozess_visuell
    art: verhalten
    beschreibung: "Stellt Abläufe als Zeichnung dar statt in Prosa."
    ausloeser: [visualisier, darstell, ablauf, prozess, struktur, schaubild]
    ---

    ## Anleitung
    <Genau dieser Text geht bei einem Treffer in den Systemprompt.>

Die Auslösung entscheidet Python über Stichwörter, nicht das Modell über
Selbsteinschätzung. Das ist dieselbe Entscheidung wie überall sonst in diesem
Projekt (ENTWICKLUNG.md, Muster 2.3): Ein Modell, das entscheiden soll, ob es
eine Regel anwendet, wendet sie mal an und mal nicht.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import settings
from .tools import TOOL_NAMES, ToolResult, VaultTools
from .vault import safe_filename, strip_frontmatter

# Wie viel Platz alle ausgelösten Verhaltensfertigkeiten zusammen im Prompt
# bekommen dürfen. Ohne Deckel frisst eine einzige geschwätzige Anleitung den
# Kontext, den der Abruf braucht - der bekannte Fall aus ENTWICKLUNG.md,
# "die Trefferquote ist mit Gedächtnis oft gesunken".
PROMPT_BUDGET = 2200
MAX_GLEICHZEITIG = 3

MIN_ANLEITUNG = 40
MIN_AUSLOESER = 3     # kürzere Stichwörter treffen alles und damit nichts


@dataclass
class Verhaltensfertigkeit:
    name: str
    beschreibung: str
    ausloeser: List[str] = field(default_factory=list)
    anleitung: str = ""
    beispiel: str = ""
    pfad: Optional[Path] = None
    probleme: List[str] = field(default_factory=list)
    # Wie oft hat sie gegriffen. Reine Buchhaltung, damit man später sieht,
    # welche Fertigkeit nie anspringt - eine, die nie greift, hat die falschen
    # Auslöser und nicht etwa keinen Anlass.
    treffer: int = 0

    @property
    def intakt(self) -> bool:
        return not self.probleme

    def signatur(self) -> str:
        return f"{self.name}: {self.beschreibung} (bei: {', '.join(self.ausloeser[:6])})"

    def passt(self, text: str) -> bool:
        gefaltet = _falten(text)
        return any(_falten(a) in gefaltet for a in self.ausloeser if a)


class Verhaltensfertigkeiten:
    """Lädt, prüft, speichert Verhaltensfertigkeiten und wählt die passenden aus."""

    def __init__(self, tools: VaultTools, cfg=settings):
        self.tools = tools
        self.cfg = cfg
        self._geladen: Dict[str, Verhaltensfertigkeit] = {}
        self._defekt: Dict[str, Verhaltensfertigkeit] = {}
        self._gelesen = False

    @property
    def ordner(self) -> Path:
        return self.tools.root / self.cfg.skills_folder / "Verhalten"

    # ------------------------------------------------------------------ Laden
    def laden(self) -> Dict[str, Verhaltensfertigkeit]:
        self._geladen, self._defekt = {}, {}
        self._gelesen = True
        self._mitgeliefertes_sichern()
        if not self.ordner.exists():
            return self._geladen
        for pfad in sorted(self.ordner.glob("*.md")):
            eintrag = self._aus_datei(pfad)
            if not eintrag:
                continue
            (self._geladen if eintrag.intakt else self._defekt)[eintrag.name] = eintrag
        return self._geladen

    @property
    def alle(self) -> Dict[str, Verhaltensfertigkeit]:
        if not self._gelesen:
            self.laden()
        return self._geladen

    @property
    def defekt(self) -> Dict[str, Verhaltensfertigkeit]:
        if not self._gelesen:
            self.laden()
        return self._defekt

    def _aus_datei(self, pfad: Path) -> Optional[Verhaltensfertigkeit]:
        try:
            roh = pfad.read_text(encoding="utf-8")
        except OSError:
            return None
        kopf, koerper = strip_frontmatter(roh)
        if str(kopf.get("art") or "").strip().lower() not in ("verhalten", ""):
            return None
        name = str(kopf.get("fertigkeit") or kopf.get("name") or pfad.stem).strip().lower()
        if not name:
            return None
        ausloeser = kopf.get("ausloeser") or kopf.get("ausloser") or []
        if isinstance(ausloeser, str):
            ausloeser = [a.strip() for a in ausloeser.strip("[]").split(",")]
        ausloeser = [str(a).strip().strip("\"'") for a in ausloeser if str(a).strip()]

        anleitung = _abschnitt(koerper, "Anleitung")
        beispiel = _abschnitt(koerper, "Beispiel")
        probleme = _pruefen(name, ausloeser, anleitung)
        return Verhaltensfertigkeit(
            name=name,
            beschreibung=str(kopf.get("beschreibung") or kopf.get("description") or name),
            ausloeser=ausloeser,
            anleitung=anleitung,
            beispiel=beispiel,
            pfad=pfad,
            probleme=probleme,
        )

    # ------------------------------------------------------------- Definieren
    def definieren(self, name: str, beschreibung: str, ausloeser: Sequence[str],
                   anleitung: str, beispiel: str = "",
                   ersetzen: bool = False) -> ToolResult:
        """Legt eine Verhaltensfertigkeit an - nach Prüfung, ohne Probelauf.

        Einen Probelauf gibt es hier bewusst nicht: Es wird nichts ausgeführt,
        also lässt sich nichts probeweise ausführen. Der Nachweis, dass sie
        wirkt, ist ein anderer und steht in `probe()`: Löst der genannte
        Beispielsatz sie wirklich aus? Eine Fertigkeit mit Auslösern, die
        nicht einmal auf ihren eigenen Anlass passen, ist wertlos - und genau
        das ist der Fehler, den ein Modell hier macht.
        """
        if not self.cfg.self_extension:
            return ToolResult(False, "Selbsterweiterung ist deaktiviert (SELF_EXTENSION=false).")

        name = safe_filename(str(name or "").strip().lower().replace(" ", "_"))
        if not name:
            return ToolResult(False, "Ungültiger Name für die Verhaltensfertigkeit.")
        if name in TOOL_NAMES:
            return ToolResult(False, f"'{name}' ist bereits ein eingebautes Werkzeug.")

        ausloeser = [str(a).strip().strip("\"'").lower()
                     for a in (ausloeser or []) if str(a).strip()]
        anleitung = str(anleitung or "").strip()
        probleme = _pruefen(name, ausloeser, anleitung)
        if probleme:
            return ToolResult(
                False,
                "Verhaltensfertigkeit abgelehnt: " + "; ".join(probleme),
                {"probleme": probleme, "hilfe": HILFE})

        pfad = self.ordner / f"{name}.md"
        if pfad.exists() and not ersetzen:
            bisher = self.alle.get(name) or self.defekt.get(name)
            return ToolResult(
                False,
                f"Verhaltensfertigkeit '{name}' gibt es bereits. Zum Ersetzen denselben "
                f"Aufruf mit ersetzen=true wiederholen, oder einen anderen Namen wählen.",
                {"bisherige_ausloeser": bisher.ausloeser if bisher else []})

        inhalt = (
            "---\n"
            f"fertigkeit: {name}\n"
            "art: verhalten\n"
            f'beschreibung: "{str(beschreibung)[:200]}"\n'
            f"ausloeser: [{', '.join(ausloeser)}]\n"
            "tags:\n  - fertigkeit\n  - verhalten\n  - selbstgebaut\n"
            "---\n\n"
            f"# Verhaltensfertigkeit: {name}\n\n{beschreibung}\n\n"
            f"## Anleitung\n{anleitung}\n"
        )
        if str(beispiel or "").strip():
            inhalt += f"\n## Beispiel\n{str(beispiel).strip()}\n"
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            pfad.write_text(inhalt, encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"Nicht speicherbar: {exc}")

        self.laden()
        return ToolResult(
            True,
            f"Verhaltensfertigkeit '{name}' ANGELEGT. Sie greift ab sofort, sobald "
            f"eine Anfrage eines dieser Stichwörter enthält: {', '.join(ausloeser)}. "
            f"Sie führt nichts aus - sie ändert, WIE geantwortet wird.",
            {"pfad": pfad.name, "ausloeser": ausloeser, "art": "verhalten"})

    def probe(self, name: str, satz: str) -> ToolResult:
        """Löst dieser Satz die Fertigkeit aus? Der einzige belastbare Nachweis."""
        eintrag = self.alle.get(name) or self.defekt.get(name)
        if not eintrag:
            return ToolResult(False, f"Verhaltensfertigkeit '{name}' ist nicht bekannt.",
                              {"vorhanden": sorted(self.alle)})
        getroffen = [a for a in eintrag.ausloeser if _falten(a) in _falten(satz)]
        if getroffen:
            return ToolResult(True, f"'{name}' greift bei diesem Satz (Auslöser: "
                                    f"{', '.join(getroffen)}).", {"ausloeser": getroffen})
        return ToolResult(
            False,
            f"'{name}' greift bei diesem Satz NICHT. Keines der Stichwörter "
            f"({', '.join(eintrag.ausloeser)}) kommt darin vor. Ergänze ein Stichwort, "
            f"das wirklich in solchen Anfragen steht.",
            {"ausloeser": eintrag.ausloeser})

    def loeschen(self, name: str, grund: str = "") -> ToolResult:
        if not self.cfg.self_extension:
            return ToolResult(False, "Selbsterweiterung ist deaktiviert (SELF_EXTENSION=false).")
        name = str(name or "").strip().lower().replace(" ", "_")
        pfad = self.ordner / f"{safe_filename(name)}.md"
        if not pfad.exists():
            return ToolResult(False, f"Verhaltensfertigkeit '{name}' ist nicht bekannt.",
                              {"vorhanden": sorted(set(self.alle) | set(self.defekt))})
        rel = pfad.relative_to(self.tools.root).as_posix()
        ergebnis = self.tools.ausfuehren("notiz_loeschen", {"name": rel})
        if not ergebnis.ok:
            return ToolResult(False, f"Nicht löschbar: {ergebnis.meldung}")
        self.laden()
        return ToolResult(True, f"Verhaltensfertigkeit '{name}' in den Papierkorb verschoben."
                                + (f" Grund: {grund}" if grund else ""))

    # ------------------------------------------------------------------ Wirken
    def passend(self, frage: str) -> List[Verhaltensfertigkeit]:
        """Welche Fertigkeiten löst diese Anfrage aus - in Python entschieden."""
        if not str(frage or "").strip():
            return []
        treffer = [f for f in self.alle.values() if f.passt(frage)]
        # Die spezifischere zuerst: wer mehr Stichwörter trifft, passt besser.
        gefaltet = _falten(frage)
        treffer.sort(key=lambda f: -sum(1 for a in f.ausloeser if _falten(a) in gefaltet))
        return treffer[:MAX_GLEICHZEITIG]

    def promptblock(self, frage: str) -> str:
        """Der Textblock, der bei einem Treffer in den Systemprompt kommt."""
        treffer = self.passend(frage)
        if not treffer:
            return ""
        zeilen = ["\nAUSGELÖSTE VERHALTENSFERTIGKEITEN – so wird diese Antwort gebaut:"]
        rest = PROMPT_BUDGET
        for eintrag in treffer:
            block = f"\n[{eintrag.name}] {eintrag.beschreibung}\n{eintrag.anleitung.strip()}"
            if eintrag.beispiel.strip():
                block += f"\nBeispiel:\n{eintrag.beispiel.strip()}"
            if len(block) > rest:
                block = block[:rest].rstrip() + " …"
            zeilen.append(block)
            eintrag.treffer += 1
            rest -= len(block)
            if rest <= 200:
                break
        zeilen.append("\nDiese Anweisungen gelten für DIESE Antwort und gehen einer "
                      "allgemeinen Gewohnheit vor.\n")
        return "\n".join(zeilen)

    def uebersicht(self) -> str:
        if not self.alle:
            return "(noch keine Verhaltensfertigkeiten)"
        return "\n".join(f"- {f.signatur()}" for f in self.alle.values())

    def pruefbericht(self) -> str:
        zeilen: List[str] = []
        if self.alle:
            zeilen.append("**Verhalten – einsatzbereit**")
            for f in self.alle.values():
                zeilen.append(f"- ✔ {f.signatur()}")
        else:
            zeilen.append("(noch keine Verhaltensfertigkeiten)")
        if self.defekt:
            zeilen.append("")
            zeilen.append("**Verhalten – gesperrt**")
            for f in self.defekt.values():
                zeilen.append(f"- ✖ {f.name}: {'; '.join(f.probleme[:2])}")
        return "\n".join(zeilen)

    # ------------------------------------------------------------ Mitgeliefert
    def _mitgeliefertes_sichern(self) -> None:
        """Legt die mitgelieferten Verhaltensfertigkeiten an, wenn sie fehlen.

        Sie werden NICHT überschrieben, wenn sie schon da sind - Josh soll
        seine Änderungen daran behalten. Und sie werden nicht wieder angelegt,
        wenn er sie gelöscht hat: dafür merkt sich der Ordner eine Marke.
        """
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            marke = self.ordner / ".mitgeliefert"
            schon = set(marke.read_text(encoding="utf-8").split()) if marke.exists() else set()
            neu = []
            for name, text in MITGELIEFERT.items():
                if name in schon:
                    continue
                pfad = self.ordner / f"{name}.md"
                if not pfad.exists():
                    pfad.write_text(text, encoding="utf-8")
                neu.append(name)
            if neu:
                marke.write_text("\n".join(sorted(schon | set(neu))), encoding="utf-8")
        except OSError:
            pass   # Ohne die Mitgelieferten läuft alles weiter - nur ärmer.


# ------------------------------------------------------------------- Prüfung
HILFE = (
    "Eine Verhaltensfertigkeit braucht: einen Namen, mindestens zwei Auslöser "
    "(Stichwörter, die wirklich in solchen Anfragen vorkommen) und eine Anleitung, "
    "die in der BEFEHLSFORM sagt, wie geantwortet werden soll. Sie führt nichts aus. "
    "Wer etwas an der Vault tun will, nimmt fertigkeit_definieren."
)

# Formulierungen, die aus einer Anleitung eine Beschreibung machen. Eine
# Anleitung im Konjunktiv ("man könnte", "es wäre gut") wird vom Modell als
# Hintergrundwissen gelesen und nicht befolgt.
_UNVERBINDLICH = re.compile(
    r"\b(k[öo]nnte|w[äa]re\s+(?:gut|sinnvoll|denkbar)|vielleicht|eventuell|"
    r"m[öo]glicherweise|sollte\s+man\s+vielleicht)\b", re.IGNORECASE)


def _pruefen(name: str, ausloeser: Sequence[str], anleitung: str) -> List[str]:
    probleme: List[str] = []
    if len(ausloeser) < 2:
        probleme.append(
            "mindestens zwei Auslöser nötig - mit einem einzigen Stichwort greift sie "
            "fast nie oder fast immer")
    zu_kurz = [a for a in ausloeser if len(a.strip()) < MIN_AUSLOESER]
    if zu_kurz:
        probleme.append(
            f"zu kurze Auslöser ({', '.join(zu_kurz)}) - sie stecken in beliebigen "
            f"Wörtern und lösen dauernd falsch aus")
    if len(anleitung.strip()) < MIN_ANLEITUNG:
        probleme.append(
            f"die Anleitung ist mit {len(anleitung.strip())} Zeichen zu dünn. Sie ist "
            f"das, was gewirkt wird - schreib hin, was konkret zu tun ist")
    if _UNVERBINDLICH.search(anleitung):
        probleme.append(
            "die Anleitung ist im Konjunktiv formuliert ('könnte', 'wäre gut'). So "
            "wird sie als Hintergrundwissen gelesen, nicht befolgt. Befehlsform nehmen")
    return probleme


def _abschnitt(koerper: str, ueberschrift: str) -> str:
    """Den Text unter '## Ueberschrift' bis zur nächsten Überschrift."""
    muster = re.compile(rf"^#{{1,4}}\s*{re.escape(ueberschrift)}\s*$", re.IGNORECASE)
    zeilen = koerper.splitlines()
    sammeln = False
    heraus: List[str] = []
    for zeile in zeilen:
        if muster.match(zeile.strip()):
            sammeln = True
            continue
        if sammeln and re.match(r"^#{1,4}\s+\S", zeile.strip()):
            break
        if sammeln:
            heraus.append(zeile)
    return "\n".join(heraus).strip()


def _falten(text: str) -> str:
    """Kleinschreibung ohne Umlaut-Unterschied - 'Ablaeufe' trifft 'Abläufe'."""
    text = str(text or "").lower()
    for von, nach in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(von, nach)
    return text


# ============================================================= Mitgeliefert
# Genau die Fertigkeit, die Josh am 04.09. haben wollte und nicht bekam.
_PROZESS_VISUELL = """---
fertigkeit: prozess_visuell
art: verhalten
beschreibung: "Zeigt Abläufe, Strukturen und Zusammenhänge als Zeichnung statt in Prosa."
ausloeser: [visualisier, darstell, veranschaulich, zeichne, schaubild, diagramm,
  ablauf, prozess, struktur, aufbau, zusammenhang, wie haengt, wie funktioniert,
  zeig mir, uebersicht ueber, schema, architektur, kette, pipeline]
tags:
  - fertigkeit
  - verhalten
---

# Verhaltensfertigkeit: prozess_visuell

Josh ist visueller Lerner. Er will Strukturen sehen, nicht lesen.

## Anleitung
Beantworte das ZEICHNEND, nicht beschreibend.

Rufe `struktur_zeichnen` auf. Übergib in `beschreibung` eine Verbindung je
Zeile, in dieser Schreibweise:

    (Anfang) -> [Schritt]
    [Schritt] -> {Entscheidung}
    {Entscheidung} -> [Weg A]: ja
    {Entscheidung} -> [Weg B]: nein
    <Eingabe> -> [Schritt]

Die Klammern geben die Form: (rund) Anfang oder Ende, [eckig] ein Schritt,
{geschweift} eine Entscheidung, <spitz> eine Ein- oder Ausgabe. Nach dem
Doppelpunkt steht die Beschriftung des Pfeils.

Regeln:
1. Zeichne zuerst, erkläre danach. Höchstens drei Sätze Text unter dem Bild,
   und nur zu dem, was das Bild nicht zeigen kann.
2. Nimm die Namen, die der Nutzer benutzt hat. Erfinde keine Zwischenschritte,
   die er nicht genannt hat.
3. Kennst du den Ablauf nicht vollständig, zeichne den bekannten Teil und
   frage nach genau der Stelle, die fehlt. Rate nicht.
4. Lege KEINE Notiz an. Das Bild gehört in die Antwort. Erst wenn der Nutzer
   ausdrücklich "speichern", "merken" oder "in die Vault" sagt, legst du eine
   Notiz an - dann mit dem Mermaid-Quelltext aus dem Werkzeugergebnis, denn
   Obsidian zeichnet den selbst.
5. Passt der Sachverhalt nicht in einen gerichteten Ablauf (eine reine
   Aufzählung, ein Vergleich, eine Zeitreihe), sag das in einem Satz und nimm
   die Form, die passt - eine Tabelle. Erzwinge kein Diagramm.

## Beispiel
Anfrage: "wie läuft eine Messung bei mir durch?"
Aufruf: struktur_zeichnen(titel="Messkette", beschreibung=
  "<Maschine1> -> [Connector]
   <Maschine2> -> [Connector]
   [Connector] -> [Verarbeitung]: Rohwerte
   [Verarbeitung] -> {Grenzwert?}
   {Grenzwert?} -> [Datenbank]: ok
   {Grenzwert?} -> [Alarm]: überschritten")
"""

_ANTWORT_KNAPP = """---
fertigkeit: antwort_knapp
art: verhalten
beschreibung: "Kürzt die Antwort auf das, was gefragt wurde."
ausloeser: [kurz, knapp, in einem satz, auf den punkt, ohne umschweife,
  zusammengefasst, tldr, schnell]
tags:
  - fertigkeit
  - verhalten
---

# Verhaltensfertigkeit: antwort_knapp

## Anleitung
Antworte in höchstens drei Sätzen. Kein Vorspann, keine Wiederholung der
Frage, keine Zusammenfassung am Ende. Nenne das Ergebnis zuerst und die
Begründung nur, wenn sie das Ergebnis ändert. Lass jede Einschränkung weg,
die nicht praktisch zu einer anderen Entscheidung führt.
"""

MITGELIEFERT = {
    "prozess_visuell": _PROZESS_VISUELL,
    "antwort_knapp": _ANTWORT_KNAPP,
}
