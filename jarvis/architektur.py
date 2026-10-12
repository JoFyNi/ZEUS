"""Architekturprüfung: taugt der Aufbau, und was fehlt an Fähigkeiten?

`codereview.py` sucht Defektmuster in einzelnen Zeilen - verschluckte Fehler,
fehlendes encoding, ungesicherte Programmaufrufe. Das ist nützlich und findet
echte Fehler, beantwortet aber drei Fragen nicht:

  1. Ist der AUFBAU tragfähig? Eine Datei mit 2000 Zeilen und einer Funktion
     mit 15 Verzweigungen hat kein einziges Defektmuster und ist trotzdem der
     Ort, an dem der nächste Fehler entsteht.
  2. Ist das, was da ist, überhaupt GEPRÜFT? Ein Werkzeug ohne Test ist ein
     Versprechen ohne Deckung.
  3. Was FEHLT? Das ist die Frage, die am schwersten zu stellen ist, weil
     Fehlendes keine Zeile hat, auf die man zeigen kann.

Frage 3 ist der eigentliche Grund für dieses Modul. Der Agent hat mehrfach
improvisiert, weil ihm ein Werkzeug fehlte - tote Links entfernen gab es nicht,
also wurde notizen_verknuepfen zweckentfremdet. Solche Lücken lassen sich
finden, wenn man den Werkzeugkatalog gegen die tatsächlichen Fehlschläge im
Protokoll hält.

Alles hier ist deterministisch: Python-Parser plus die eigenen Protokolldaten.
Kein Modellurteil, keine Vermutung.
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .config import settings

# --------------------------------------------------------------- Schwellen
# Bewusst großzügig: Eine Warnung, die bei jeder zweiten Funktion anschlägt,
# wird nach dem zweiten Bericht ignoriert.
MAX_FUNKTIONSZEILEN = 80
MAX_KOMPLEXITAET = 12
MAX_TIEFE = 5
MAX_PARAMETER = 7
MAX_MODULZEILEN = 1200


@dataclass
class Funktionsmass:
    modul: str
    name: str
    zeile: int
    zeilen: int
    komplexitaet: int
    tiefe: int
    parameter: int
    hat_docstring: bool
    ist_oeffentlich: bool

    @property
    def auffaellig(self) -> List[str]:
        gruende = []
        if self.zeilen > MAX_FUNKTIONSZEILEN:
            gruende.append(f"{self.zeilen} Zeilen")
        if self.komplexitaet > MAX_KOMPLEXITAET:
            gruende.append(f"Komplexität {self.komplexitaet}")
        if self.tiefe > MAX_TIEFE:
            gruende.append(f"Verschachtelung {self.tiefe}")
        if self.parameter > MAX_PARAMETER:
            gruende.append(f"{self.parameter} Parameter")
        return gruende


@dataclass
class Modulmass:
    name: str
    zeilen: int
    funktionen: int
    klassen: int
    importiert: Set[str] = field(default_factory=set)
    importiert_von: Set[str] = field(default_factory=set)

    @property
    def kopplung(self) -> int:
        return len(self.importiert) + len(self.importiert_von)


@dataclass
class Architekturbericht:
    module: Dict[str, Modulmass] = field(default_factory=dict)
    funktionen: List[Funktionsmass] = field(default_factory=list)
    zyklen: List[List[str]] = field(default_factory=list)
    ungenutzt: List[Tuple[str, str, int]] = field(default_factory=list)
    dubletten: List[Tuple[str, str]] = field(default_factory=list)
    ohne_docstring: List[Tuple[str, str, int]] = field(default_factory=list)
    nicht_lesbar: List[str] = field(default_factory=list)
    # Wie viele Testdateien überhaupt eingelesen wurden. Bei 0 ist jede
    # Aussage über Testabdeckung eine Aussage über den Suchpfad.
    testdateien: int = 0

    # Fähigkeiten
    werkzeuge_gesamt: int = 0
    werkzeuge_ohne_test: List[str] = field(default_factory=list)
    werkzeuge_nie_benutzt: List[str] = field(default_factory=list)
    werkzeuge_oft_gescheitert: List[Tuple[str, int]] = field(default_factory=list)
    werkzeuge_unzuverlaessig: List[Tuple[str, int, int]] = field(default_factory=list)
    luecken: List[dict] = field(default_factory=list)
    # Wie viele Aufrufe die Strichliste überhaupt kennt. Bei 0 darf über
    # "nie benutzt" nichts behauptet werden - es wurde nur noch nichts gezählt.
    nutzung_aufrufe: int = 0

    def kurzfassung(self) -> str:
        auffaellig = [f for f in self.funktionen if f.auffaellig]
        kern = (f"{len(self.module)} Module, {len(self.funktionen)} Funktionen · "
                f"{len(auffaellig)} auffällig · {len(self.zyklen)} Importzyklen · "
                f"{len(self.ungenutzt)} vermutlich ungenutzt")
        if not self.testdateien:
            return kern + " · Testabdeckung nicht prüfbar (keine Testdateien gefunden)"
        return (kern + f" · {len(self.werkzeuge_ohne_test)}/{self.werkzeuge_gesamt} "
                       f"Werkzeuge ohne Test")


def _ist_typpruefung(test: ast.expr) -> bool:
    """Ist das ein `if TYPE_CHECKING:` (auch als `typing.TYPE_CHECKING`)?"""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


# ====================================================================== AST
class _Vermessung(ast.NodeVisitor):
    """Misst Funktionen: Länge, Verzweigungen, Verschachtelung, Parameter."""

    # Knoten, die eine Entscheidung darstellen - die Grundlage der
    # zyklomatischen Komplexität. Sie zählt, wie viele Pfade durch eine
    # Funktion führen, und ist damit ein direktes Maß dafür, wie viele Fälle
    # ein Test abdecken müsste.
    VERZWEIGUNGEN = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                     ast.With, ast.AsyncWith, ast.Assert, ast.IfExp,
                     ast.comprehension)

    def __init__(self, modul: str, zeilen: List[str]):
        self.modul = modul
        self.zeilen = zeilen
        self.masse: List[Funktionsmass] = []
        self.definiert: Dict[str, int] = {}
        self.benutzt: Set[str] = set()
        self.importe: Set[str] = set()
        self.klassen = 0
        self._rumpf_hashes: Dict[str, str] = {}
        self._klassenstapel: List[str] = []
        # Tiefe innerhalb eines 'if TYPE_CHECKING:'-Blocks. Importe darin
        # laufen ZUR LAUFZEIT NIE - sie stehen nur da, damit ein Prüfwerkzeug
        # die Typnamen auflösen kann. Sie als Importzyklus zu melden ist ein
        # Fehlalarm, und der ist hier teurer als eine Lücke: Ein Bericht, der
        # beim ersten Blick etwas Falsches behauptet, wird beim zweiten Mal
        # nicht mehr gelesen (ENTWICKLUNG.md, Abschnitt 3).
        self._nur_typen = 0

    # ------------------------------------------------------------ Importe
    def visit_If(self, node: ast.If) -> None:
        if _ist_typpruefung(node.test):
            self._nur_typen += 1
            for kind in node.body:
                self.visit(kind)
            self._nur_typen -= 1
            # Der else-Zweig läuft dagegen wirklich.
            for kind in node.orelse:
                self.visit(kind)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if not self._nur_typen:
            for alias in node.names:
                self.importe.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Bei `from .tools import x` steht in node.module nur "tools" - der
        # Punkt steckt in node.level. Beide Fälle liefern denselben Kurznamen.
        if self._nur_typen:
            self.generic_visit(node)
            return
        if node.module:
            self.importe.add(node.module.split(".")[0])
        elif node.level:
            # `from . import nutzung` - das Ziel steht in den Namen.
            for alias in node.names:
                self.importe.add(alias.name.split(".")[0])
        self.generic_visit(node)

    # ----------------------------------------------------------- Benutzung
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.benutzt.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.benutzt.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Werkzeugnamen tauchen als Zeichenketten auf (getattr, Schemata).
        # Ohne das gälte jedes Werkzeug als ungenutzt.
        if isinstance(node.value, str) and re.fullmatch(r"[a-z_][a-z0-9_]{2,}", node.value):
            self.benutzt.add(node.value)
        self.generic_visit(node)

    # ---------------------------------------------------------- Klassen
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.klassen += 1
        self.definiert.setdefault(node.name, node.lineno)
        self._klassenstapel.append(node.name)
        self.generic_visit(node)
        self._klassenstapel.pop()

    # --------------------------------------------------------- Funktionen
    def visit_FunctionDef(self, node) -> None:
        ende = getattr(node, "end_lineno", node.lineno) or node.lineno
        laenge = max(1, ende - node.lineno)
        args = node.args
        anzahl = (len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
                  + (1 if args.vararg else 0) + (1 if args.kwarg else 0))
        if node.args.args and node.args.args[0].arg in ("self", "cls"):
            anzahl -= 1

        self.masse.append(Funktionsmass(
            modul=self.modul, name=node.name, zeile=node.lineno, zeilen=laenge,
            komplexitaet=self._komplexitaet(node), tiefe=self._tiefe(node),
            parameter=anzahl,
            hat_docstring=ast.get_docstring(node) is not None,
            ist_oeffentlich=not node.name.startswith("_"),
        ))
        # Nur Namen auf Modulebene taugen für die Frage "wird das noch
        # gebraucht?". Methoden werden über die Instanz aufgerufen, über
        # getattr verteilt oder von einer Basisklasse abgerufen (ast.NodeVisitor
        # ruft `visit_Try` auf, ohne dass der Name je im Quelltext steht).
        # Sie hier mitzuzählen hieß beim ersten Lauf: zehn von vierzehn
        # Meldungen falsch - und damit die ganze Rubrik wertlos.
        if not self._klassenstapel:
            self.definiert.setdefault(node.name, node.lineno)
        voll = ".".join(self._klassenstapel + [node.name])
        self._rumpf_hashes[f"{self.modul}:{voll}"] = _rumpf_signatur(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    @classmethod
    def _komplexitaet(cls, node) -> int:
        wert = 1
        for kind in ast.walk(node):
            if isinstance(kind, cls.VERZWEIGUNGEN):
                wert += 1
            elif isinstance(kind, ast.BoolOp):
                # 'a and b and c' sind zwei zusätzliche Pfade, nicht einer
                wert += len(kind.values) - 1
        return wert

    BLOCK = (ast.If, ast.For, ast.While, ast.With, ast.Try,
             ast.AsyncFor, ast.AsyncWith)

    @classmethod
    def _tiefe(cls, node) -> int:
        """Größte Verschachtelungstiefe von Blöcken im Rumpf.

        Wichtig ist, was `elif` im Syntaxbaum wirklich ist: ein `If` im
        `orelse` des vorherigen. Naiv gezählt bekommt eine flache Kette aus
        vierzig `elif` die Tiefe 40 - und der Wert misst dann die Länge einer
        Fallunterscheidung statt der Verschachtelung, die er messen soll.
        Genau das ist beim ersten Lauf über `_command` passiert. Ein `orelse`,
        das aus genau einem `If` besteht, zählt deshalb auf derselben Ebene.
        """
        def messen(knoten, ebene: int) -> int:
            tiefste = ebene
            for feld, wert in ast.iter_fields(knoten):
                kinder = wert if isinstance(wert, list) else [wert]
                elif_kette = (feld == "orelse" and isinstance(knoten, ast.If)
                              and len(kinder) == 1 and isinstance(kinder[0], ast.If))
                for kind in kinder:
                    if not isinstance(kind, ast.AST):
                        continue
                    tiefer = (isinstance(kind, cls.BLOCK) and not elif_kette)
                    tiefste = max(tiefste, messen(kind, ebene + 1 if tiefer else ebene))
            return tiefste

        return messen(node, 0)


def _rumpf_signatur(node) -> str:
    """Struktur-Fingerabdruck einer Funktion - für die Dublettensuche.

    Verglichen wird die FORM, nicht der Text: Knotenarten in Reihenfolge,
    ohne Namen und Werte. Zwei Funktionen, die dasselbe anders benannt tun,
    bekommen damit denselben Abdruck.
    """
    arten = [type(k).__name__ for k in ast.walk(node)
             if not isinstance(k, (ast.Load, ast.Store, ast.Del))]
    return "|".join(arten)


# ================================================================ Sammeln
def modul_vermessen(pfad: Path, anzeige: str) -> Tuple[Optional[_Vermessung], Optional[str]]:
    try:
        quelltext = pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"{anzeige}: nicht lesbar ({exc})"
    try:
        baum = ast.parse(quelltext, filename=str(pfad))
    except SyntaxError as exc:
        return None, f"{anzeige}: Syntaxfehler in Zeile {exc.lineno}"
    vermessung = _Vermessung(anzeige, quelltext.splitlines())
    vermessung.visit(baum)
    return vermessung, None


def architektur_pruefen(dateien: Sequence[Tuple[Path, str]],
                        testdateien: Sequence[Tuple[Path, str]] = (),
                        cfg=settings) -> Architekturbericht:
    """Alle Module vermessen und zueinander in Beziehung setzen."""
    bericht = Architekturbericht()
    vermessungen: Dict[str, _Vermessung] = {}
    alle_benutzt: Set[str] = set()
    signaturen: Dict[str, str] = {}

    for pfad, anzeige in dateien:
        vermessung, grund = modul_vermessen(pfad, anzeige)
        if grund:
            bericht.nicht_lesbar.append(grund)
            continue
        vermessungen[anzeige] = vermessung
        bericht.funktionen.extend(vermessung.masse)
        alle_benutzt |= vermessung.benutzt
        signaturen.update(vermessung._rumpf_hashes)
        bericht.module[anzeige] = Modulmass(
            name=anzeige, zeilen=len(vermessung.zeilen),
            funktionen=len(vermessung.masse), klassen=vermessung.klassen,
            importiert={i for i in vermessung.importe},
        )

    # Tests zählen für "benutzt" mit - ein Werkzeug, das nur der Test aufruft,
    # ist nicht tot, sondern ungenutzt in der Anwendung. Das ist ein
    # Unterschied, und beides wird getrennt ausgewiesen.
    test_benutzt: Set[str] = set()
    for pfad, anzeige in testdateien:
        vermessung, grund = modul_vermessen(pfad, anzeige)
        if vermessung:
            bericht.testdateien += 1
            test_benutzt |= vermessung.benutzt

    # ------------------------------------------------------- Importgraph
    modulnamen = {a.rsplit("/", 1)[-1].removesuffix(".py") for a in bericht.module}
    for anzeige, mass in bericht.module.items():
        mass.importiert = {i for i in mass.importiert if i in modulnamen}
    for anzeige, mass in bericht.module.items():
        for ziel in mass.importiert:
            for kandidat, anderes in bericht.module.items():
                if kandidat.rsplit("/", 1)[-1].removesuffix(".py") == ziel:
                    anderes.importiert_von.add(anzeige)
    bericht.zyklen = _zyklen_finden(bericht.module)

    # -------------------------------------------------- Vermutlich ungenutzt
    for anzeige, vermessung in vermessungen.items():
        for name, zeile in vermessung.definiert.items():
            if name.startswith("_") or name in ("main", "__init__"):
                continue
            if name in alle_benutzt or name in test_benutzt:
                continue
            bericht.ungenutzt.append((anzeige, name, zeile))

    # --------------------------------------------------------- Dubletten
    nach_signatur: Dict[str, List[str]] = defaultdict(list)
    for schluessel, signatur in signaturen.items():
        if len(signatur) > 400:          # triviale Funktionen nicht vergleichen
            nach_signatur[signatur].append(schluessel)
    for gruppe in nach_signatur.values():
        if len(gruppe) > 1:
            for zweiter in gruppe[1:]:
                bericht.dubletten.append((gruppe[0], zweiter))

    # ------------------------------------------------------- Ohne Docstring
    for mass in bericht.funktionen:
        if mass.ist_oeffentlich and not mass.hat_docstring and mass.zeilen > 12:
            bericht.ohne_docstring.append((mass.modul, mass.name, mass.zeile))

    _faehigkeiten_pruefen(bericht, test_benutzt, cfg)
    return bericht


def _zyklen_finden(module: Dict[str, Modulmass]) -> List[List[str]]:
    """Importzyklen aufspüren - Tiefensuche mit Farbmarkierung."""
    kurz = {a: a.rsplit("/", 1)[-1].removesuffix(".py") for a in module}
    nach_kurz = {v: k for k, v in kurz.items()}
    zyklen: List[List[str]] = []
    weiss, grau, schwarz = 0, 1, 2
    farbe = {a: weiss for a in module}
    pfad: List[str] = []

    def besuchen(knoten: str) -> None:
        farbe[knoten] = grau
        pfad.append(knoten)
        for ziel_kurz in module[knoten].importiert:
            ziel = nach_kurz.get(ziel_kurz)
            if ziel is None or ziel not in farbe:
                continue
            if farbe[ziel] == grau:
                start = pfad.index(ziel)
                zyklus = pfad[start:] + [ziel]
                if zyklus not in zyklen:
                    zyklen.append(zyklus)
            elif farbe[ziel] == weiss:
                besuchen(ziel)
        pfad.pop()
        farbe[knoten] = schwarz

    for knoten in module:
        if farbe[knoten] == weiss:
            besuchen(knoten)
    return zyklen


# ============================================================ Fähigkeiten
# Aufgabenarten, die ein Wissensagent abdecken sollte. Die Liste ist der
# Maßstab, an dem "was fehlt mir?" überhaupt beantwortbar wird - ohne sie
# lässt sich über Fehlendes nur spekulieren.
FAEHIGKEITSFELDER = [
    ("Finden", "Etwas in der Vault suchen",
     ["notizen_suchen", "struktur_anzeigen", "verwandte_finden", "code_suchen"]),
    ("Lesen", "Inhalt einer Notiz holen", ["notiz_lesen", "quellcode_lesen"]),
    ("Anlegen", "Neue Notiz oder Ordner", ["notiz_erstellen", "ordner_anlegen"]),
    ("Ergänzen", "Bestehendes erweitern",
     ["notiz_ergaenzen", "abschnitt_einfuegen", "codeblock_hinzufuegen"]),
    ("Ändern", "Bestehendes umschreiben",
     ["notiz_bearbeiten", "notiz_ueberschreiben", "abschnitt_ersetzen"]),
    ("Entfernen", "Loswerden",
     ["notiz_loeschen", "abschnitt_loeschen", "links_bereinigen"]),
    ("Umordnen", "Struktur verändern",
     ["notiz_verschieben", "ordner_umbenennen", "notiz_aufteilen",
      "notizen_zusammenfuehren"]),
    ("Verknüpfen", "Beziehungen herstellen",
     ["notizen_verknuepfen", "auto_verlinken", "links_pruefen"]),
    ("Beschreiben", "Metadaten pflegen", ["tags_setzen", "frontmatter_setzen"]),
    ("Übersicht", "Verdichten", ["uebersichtsnotiz", "vault_wartung"]),
    ("Zurücknehmen", "Fehler rückgängig machen",
     ["notiz_wiederherstellen", "rueckgaengig"]),
    ("Einlesen", "Fremde Daten aufnehmen",
     ["datei_importieren", "codebasis_importieren"]),
    ("Selbstprüfung", "Eigenen Code ansehen", ["code_dateien", "code_auslesen"]),
]


def _faehigkeiten_pruefen(bericht: Architekturbericht, test_benutzt: Set[str],
                          cfg=settings) -> None:
    """Werkzeugkatalog gegen Tests und gegen die eigene Betriebserfahrung."""
    from .tools import TOOL_NAMES

    bericht.werkzeuge_gesamt = len(TOOL_NAMES)
    # Ohne eingelesene Testdateien darf NICHT behauptet werden, es gebe keine
    # Tests. Bei Josh lagen die Tests schlicht nicht im gescannten Ordner, und
    # der Bericht meldete "34/34 Werkzeuge ohne Test" - eine Aussage über den
    # Suchpfad, die wie eine Aussage über den Code aussah.
    if bericht.testdateien:
        bericht.werkzeuge_ohne_test = sorted(n for n in TOOL_NAMES if n not in test_benutzt)

    strichliste, gescheitert = _protokoll_auswerten(cfg)
    bericht.nutzung_aufrufe = sum(int(e.get("aufrufe") or 0)
                                  for e in strichliste.values())
    # Nur wenn überhaupt gezählt wurde, darf "nie benutzt" behauptet werden.
    # Sonst stünde beim ersten Lauf die gesamte Werkzeugliste als tot da.
    if bericht.nutzung_aufrufe:
        bericht.werkzeuge_nie_benutzt = sorted(
            n for n in TOOL_NAMES if n not in strichliste)
    bericht.werkzeuge_oft_gescheitert = sorted(
        gescheitert.items(), key=lambda p: -p[1])[:8]

    # Unzuverlässig heißt: oft genug aufgerufen, um etwas sagen zu können, und
    # trotzdem scheitert mindestens jeder dritte Aufruf. Das ist der Ort, an
    # dem entweder die Fehlermeldung oder das Werkzeug selbst nicht taugt.
    from . import nutzung

    unzuverlaessig = []
    for name, eintrag in strichliste.items():
        aufrufe = int(eintrag.get("aufrufe") or 0)
        fehler = int(eintrag.get("fehler") or 0)
        if aufrufe >= 3 and nutzung.fehlerquote(eintrag) >= 0.34:
            unzuverlaessig.append((name, fehler, aufrufe))
    bericht.werkzeuge_unzuverlaessig = sorted(
        unzuverlaessig, key=lambda t: -(t[1] / t[2]))[:8]

    for feld, zweck, werkzeuge in FAEHIGKEITSFELDER:
        vorhanden = [w for w in werkzeuge if w in TOOL_NAMES]
        fehlend = [w for w in werkzeuge if w not in TOOL_NAMES]
        getestet = [w for w in vorhanden if w in test_benutzt]
        if fehlend or not vorhanden:
            bericht.luecken.append({
                "feld": feld, "zweck": zweck, "art": "Werkzeug fehlt",
                "fehlend": fehlend, "vorhanden": vorhanden,
            })
        elif not getestet and bericht.testdateien:
            # Nur melden, wenn überhaupt Tests eingelesen wurden. Sonst stünde
            # bei JEDEM Feld "ungetestet" - dreizehn Meldungen, die alle
            # dasselbe sagen: der Suchpfad enthielt keine Tests.
            bericht.luecken.append({
                "feld": feld, "zweck": zweck, "art": "kein Test",
                "fehlend": [], "vorhanden": vorhanden,
            })


def _protokoll_auswerten(cfg=settings) -> Tuple[Dict[str, dict], Counter]:
    """Was wurde im Betrieb wirklich benutzt, was ist wiederholt gescheitert?

    Drei Quellen, weil keine für sich reicht:

    * `werkzeugnutzung.json` - die Strichliste jedes Aufrufs. Die einzige
      Quelle, die auch lesende Werkzeuge kennt.
    * `journal.jsonl` - Änderungen. Reicht bis vor die Einführung der
      Strichliste zurück und wird deshalb ergänzend gelesen.
    * `aufgaben.jsonl` - offene Fehlschläge mit Versuchszähler.
    """
    from . import nutzung

    strichliste = nutzung.lesen(cfg)
    gescheitert: Counter = Counter()

    # Altbestand: was das Änderungsjournal kennt, gilt als benutzt - aber
    # ohne Zählwerte, damit es die echte Strichliste nicht verfälscht.
    journal = cfg.work_dir / "journal.jsonl"
    try:
        for zeile in journal.read_text(encoding="utf-8").splitlines():
            try:
                eintrag = json.loads(zeile)
            except ValueError:
                continue
            aktion = str(eintrag.get("aktion") or "")
            if aktion and aktion not in strichliste:
                strichliste[aktion] = {"aufrufe": 1, "fehler": 0, "zuletzt": ""}
    except OSError:
        pass

    aufgaben = cfg.work_dir / "aufgaben.jsonl"
    try:
        for zeile in aufgaben.read_text(encoding="utf-8").splitlines():
            try:
                eintrag = json.loads(zeile)
            except ValueError:
                continue
            werkzeug = eintrag.get("werkzeug")
            try:
                versuche = int(eintrag.get("versuche") or 0)
            except (TypeError, ValueError):
                versuche = 0
            if werkzeug and eintrag.get("status") != "erledigt" and versuche:
                gescheitert[str(werkzeug)] += versuche
    except OSError:
        pass
    return strichliste, gescheitert


# ========================================================== Was jetzt zu tun ist
def naechste_schritte(bericht: Architekturbericht) -> List[Tuple[str, str, str]]:
    """Aus den Messwerten eine Reihenfolge machen: (Dringlichkeit, Schritt, Grund).

    Eine Liste von 60 Befunden ist keine Handlungsanweisung, sie ist eine
    Ausrede, nichts zu tun. Diese Funktion beantwortet die einzige Frage, die
    nach einer Prüfung zählt: *Womit fange ich an?* Die Reihenfolge ist
    begründet und nicht verhandelbar:

    1. Was den Agenten IM BETRIEB nachweislich blockiert (belegt durch
       Fehlversuche) - dort geht heute Arbeit verloren.
    2. Was ihm an Fähigkeit fehlt - dort kann er eine Aufgabenart gar nicht.
    3. Was unzuverlässig ist - dort verliert er auf halbem Weg.
    4. Was den Aufbau brüchig macht - dort entsteht der nächste Fehler.
    5. Was ungeprüft ist - dort weiß niemand, ob es überhaupt tut.
    """
    schritte: List[Tuple[str, str, str]] = []
    blockierend = {w for w, _ in bericht.werkzeuge_oft_gescheitert}

    # 0. Wenn die Tests gar nicht mitgelesen wurden, ist das der erste Schritt -
    #    ohne sie fehlt der halben Prüfung die Grundlage.
    if not bericht.testdateien:
        schritte.append((
            "jetzt", "Den Ordner `tests/` unter CODE_ROOTS bringen",
            "Es wurde keine Testdatei eingelesen. Damit ist die Testabdeckung "
            "nicht prüfbar – und zwar unabhängig davon, ob es Tests gibt. "
            "Beim Auspacken eines Updates wird `tests/` leicht vergessen, weil "
            "für den Betrieb nur `jarvis/` und `jarvis_run.py` nötig sind."))

    # 1./2. Fehlende Fähigkeiten - mit Vorrang für die, an denen er scheitert
    fehlende = [l for l in bericht.luecken if l["fehlend"]]
    fehlende.sort(key=lambda l: (not any(w in blockierend for w in l["vorhanden"]),
                                 l["feld"]))
    for luecke in fehlende[:6]:
        belegt = [w for w in luecke["vorhanden"] if w in blockierend]
        dringend = "jetzt" if belegt else "bald"
        grund = (f"Feld „{luecke['feld']}\" ({luecke['zweck']}) ist unvollständig. "
                 + (f"Der Agent scheitert bereits an {', '.join(belegt)} – "
                    "das ist der Ausweichversuch für die fehlende Fähigkeit."
                    if belegt else
                    "Fehlt die Fähigkeit, improvisiert das Modell mit einem "
                    "Werkzeug, das dafür nicht gedacht ist."))
        schritte.append((dringend,
                         f"Werkzeug ergänzen: {', '.join(luecke['fehlend'])}", grund))

    # 3. Unzuverlässige Werkzeuge
    for werkzeug, fehler, aufrufe in bericht.werkzeuge_unzuverlaessig[:3]:
        schritte.append((
            "jetzt",
            f"Fehlermeldung von `{werkzeug}` brauchbar machen",
            f"{fehler} von {aufrufe} Aufrufen scheitern ({100 * fehler // aufrufe} %). "
            "Die Meldung muss sagen, was zu ÄNDERN ist, nicht nur, dass es schiefging."))

    # 4. Aufbau
    zu_gross = sorted((m for m in bericht.module.values() if m.zeilen > MAX_MODULZEILEN),
                      key=lambda m: -m.zeilen)
    for mass in zu_gross[:2]:
        schritte.append((
            "bald", f"`{mass.name}` aufteilen ({mass.zeilen} Zeilen, "
                    f"{mass.funktionen} Funktionen)",
            "Ein Modul dieser Größe wird zum Sammelbecken: Jede Änderung berührt "
            "Unbeteiligtes, und beim Lesen findet man die zuständige Stelle nicht mehr."))

    schlimmste = sorted((f for f in bericht.funktionen if f.auffaellig),
                        key=lambda f: -f.komplexitaet)[:3]
    for mass in schlimmste:
        schritte.append((
            "bald", f"`{mass.name}` entzerren ({mass.modul}:{mass.zeile})",
            f"{', '.join(mass.auffaellig)}. Bei {mass.komplexitaet} Entscheidungspfaden "
            "wäre das die Mindestzahl an Testfällen – die schreibt niemand."))

    for zyklus in bericht.zyklen[:2]:
        schritte.append((
            "bald", "Importzyklus auflösen: " + " → ".join(zyklus),
            "Zyklisch verbundene Module sind nicht getrennt testbar, und die "
            "Importreihenfolge wird zur Fehlerquelle."))

    # 5. Ungeprüftes
    ohne_test = [l for l in bericht.luecken if not l["fehlend"]]
    if ohne_test:
        felder = ", ".join(l["feld"] for l in ohne_test[:5])
        schritte.append((
            "danach", f"Tests für die Felder {felder}",
            "Diese Aufgabenfelder haben ein Werkzeug, aber keinen einzigen Test. "
            "Ob sie tun, was draufsteht, hat noch nie jemand nachgesehen."))
    elif bericht.werkzeuge_ohne_test:
        schritte.append((
            "danach",
            f"Tests für {len(bericht.werkzeuge_ohne_test)} ungeprüfte Werkzeuge",
            "Beginnend mit " + ", ".join(bericht.werkzeuge_ohne_test[:5]) + "."))

    if bericht.dubletten:
        schritte.append((
            "danach", f"{len(bericht.dubletten)} strukturgleiche Funktionen zusammenlegen",
            "Was doppelt existiert, wird einmal korrigiert und einmal vergessen."))
    return schritte


# ================================================================= Bericht
def _abschnitt_anfangen(bericht: Architekturbericht) -> List[str]:
    """Womit anfangen - ganz nach vorn, vor alle Messwerte.

    Ein Bericht, der mit Tabellen beginnt, wird gelesen und nicht befolgt.
    """
    schritte = naechste_schritte(bericht)
    if not schritte:
        return []
    zeilen = ["### Womit anfangen", "", "| Wann | Schritt | Warum |", "|---|---|---|"]
    for wann, schritt, grund in schritte:
        zeilen.append(f"| **{wann}** | {schritt} | {grund} |")
    return zeilen + [""]


def _abschnitt_module(bericht: Architekturbericht) -> List[str]:
    groesste = sorted(bericht.module.values(), key=lambda m: -m.zeilen)[:8]
    if not groesste:
        return []
    zeilen = ["### Module nach Größe und Kopplung", "",
              "| Modul | Zeilen | Funktionen | importiert | wird importiert von |",
              "|---|---|---|---|---|"]
    for mass in groesste:
        marke = " ⚠️" if mass.zeilen > MAX_MODULZEILEN else ""
        zeilen.append(f"| `{mass.name}`{marke} | {mass.zeilen} | {mass.funktionen} "
                      f"| {len(mass.importiert)} | {len(mass.importiert_von)} |")
    zeilen.append("")
    zu_gross = [m for m in groesste if m.zeilen > MAX_MODULZEILEN]
    if zu_gross:
        zeilen += [f"> Über {MAX_MODULZEILEN} Zeilen wird ein Modul zum Sammelbecken: "
                   f"Jede Änderung berührt Unbeteiligtes, und der Überblick, welche "
                   f"Funktion wovon abhängt, geht verloren. Betroffen: "
                   + ", ".join(f"`{m.name}`" for m in zu_gross) + ".", ""]
    return zeilen


def _abschnitt_funktionen(bericht: Architekturbericht) -> List[str]:
    auffaellig = sorted((f for f in bericht.funktionen if f.auffaellig),
                        key=lambda f: -f.komplexitaet)[:12]
    if not auffaellig:
        return []
    zeilen = ["### Funktionen, die zu viel auf einmal tun", "",
              "| Funktion | Stelle | Befund |", "|---|---|---|"]
    for mass in auffaellig:
        zeilen.append(f"| `{mass.name}` | {mass.modul}:{mass.zeile} "
                      f"| {', '.join(mass.auffaellig)} |")
    return zeilen + [
        "",
        "> Die Komplexität zählt die Entscheidungspfade. Sie ist zugleich die "
        "Mindestzahl an Testfällen, die nötig wäre, um die Funktion wirklich "
        "abzudecken – bei 15 Pfaden testet man in der Praxis nie alle.", ""]


def _abschnitt_aufbau(bericht: Architekturbericht) -> List[str]:
    """Zyklen, vermutlich Totes, Dubletten - die drei Aufbau-Warnungen."""
    zeilen: List[str] = []
    if bericht.zyklen:
        zeilen += ["### Importzyklen", ""]
        zeilen += ["- " + " → ".join(f"`{z}`" for z in zyklus)
                   for zyklus in bericht.zyklen[:5]]
        zeilen += ["",
                   "> Ein Zyklus heißt: Die beiden Module sind nicht wirklich getrennt. "
                   "Einzeln testen geht nicht, und die Importreihenfolge wird zur "
                   "Fehlerquelle.", ""]

    if bericht.ungenutzt:
        zeilen += [f"### Vermutlich ungenutzt ({len(bericht.ungenutzt)})", ""]
        zeilen += [f"- `{name}` in {modul}:{zeile}"
                   for modul, name, zeile in bericht.ungenutzt[:12]]
        zeilen += ["",
                   "> **Vorsicht, Heuristik.** Gezählt werden nur Namen auf Modulebene; "
                   "Methoden bleiben außen vor, weil sie über die Instanz oder von einer "
                   "Basisklasse aufgerufen werden. Namen, die nur als Zeichenkette "
                   "vorkommen (Werkzeugaufruf über `getattr`), gelten als benutzt. "
                   "Trotzdem: vor dem Löschen selbst nachsehen.", ""]

    if bericht.dubletten:
        zeilen += ["### Strukturgleiche Funktionen", ""]
        zeilen += [f"- `{erste}` ≙ `{zweite}`" for erste, zweite in bericht.dubletten[:8]]
        zeilen += ["", "> Gleiche Form, vielleicht gleicher Zweck. Lohnt einen Blick: "
                       "Was doppelt existiert, wird einmal korrigiert und einmal vergessen.",
                   ""]
    return zeilen


def _abschnitt_faehigkeiten(bericht: Architekturbericht) -> List[str]:
    zeilen = ["### Fähigkeiten", "",
              f"Werkzeuge insgesamt: **{bericht.werkzeuge_gesamt}**", ""]

    if not bericht.testdateien:
        zeilen += ["**Testabdeckung: nicht prüfbar.** Es wurde keine einzige "
                   "Testdatei eingelesen. Das ist eine Aussage über den Suchpfad, "
                   "nicht über den Code – gäbe es wirklich keine Tests, stünde hier "
                   "dasselbe. Prüfen: Liegt der Ordner `tests/` unter `CODE_ROOTS`? "
                   "Beim Auspacken eines Updates wird er leicht vergessen, weil nur "
                   "`jarvis/` und `jarvis_run.py` gebraucht werden.", ""]
    elif bericht.werkzeuge_ohne_test:
        zeilen += [f"**Ohne Test ({len(bericht.werkzeuge_ohne_test)} von "
                   f"{bericht.werkzeuge_gesamt}, aus {bericht.testdateien} "
                   f"Testdateien):** "
                   + ", ".join(f"`{w}`" for w in bericht.werkzeuge_ohne_test[:15])
                   + (" …" if len(bericht.werkzeuge_ohne_test) > 15 else ""),
                   "",
                   "> Ein Werkzeug ohne Test ist ein Versprechen ohne Deckung. "
                   "Es hat noch nie jemand nachgesehen, ob es tut, was draufsteht.", ""]

    zeilen += _abschnitt_betrieb(bericht)

    if bericht.luecken:
        zeilen += ["**Abdeckung der Aufgabenfelder**", "",
                   "| Feld | Zweck | Befund |", "|---|---|---|"]
        for luecke in bericht.luecken:
            was = (", ".join(f"`{w}`" for w in luecke["fehlend"]) + " fehlt"
                   if luecke["fehlend"] else "vorhanden, aber ungetestet")
            zeilen.append(f"| {luecke['feld']} | {luecke['zweck']} | {was} |")
        zeilen.append("")
    else:
        zeilen += ["Alle Aufgabenfelder sind mit mindestens einem getesteten Werkzeug "
                   "abgedeckt.", ""]
    return zeilen


def _abschnitt_betrieb(bericht: Architekturbericht) -> List[str]:
    """Was die Protokolle über den tatsächlichen Gebrauch hergeben."""
    zeilen: List[str] = []
    if not bericht.nutzung_aufrufe:
        return ["**Betriebserfahrung:** noch keine Aufrufe gezählt. Die Strichliste "
                "(`.jarvis/werkzeugnutzung.json`) füllt sich ab dem nächsten "
                "Agentenlauf; erst dann lässt sich sagen, welches Werkzeug das "
                "Modell nie anfasst.", ""]

    if bericht.werkzeuge_nie_benutzt:
        zeilen += [f"**Im Betrieb nie benutzt ({len(bericht.werkzeuge_nie_benutzt)} von "
                   f"{bericht.werkzeuge_gesamt}, bei {bericht.nutzung_aufrufe} "
                   f"gezählten Aufrufen):** "
                   + ", ".join(f"`{w}`" for w in bericht.werkzeuge_nie_benutzt[:15])
                   + (" …" if len(bericht.werkzeuge_nie_benutzt) > 15 else ""),
                   "",
                   "> Entweder kennt das Modell sie nicht (Beschreibung zu unklar), oder "
                   "es braucht sie nicht. Beides ist eine Erkenntnis: unklare Beschreibung "
                   "verbessern – oder das Werkzeug aus dem Prompt nehmen und den Platz "
                   "im Kontextfenster zurückgewinnen. Gelöscht wird deshalb nichts: "
                   "aus dem Prompt heißt nicht aus dem Programm.", ""]

    if bericht.werkzeuge_unzuverlaessig:
        zeilen += ["**Scheitert im Betrieb überdurchschnittlich oft:**", "",
                   "| Werkzeug | Fehler | Aufrufe | Quote |", "|---|---|---|---|"]
        for werkzeug, fehler, aufrufe in bericht.werkzeuge_unzuverlaessig:
            zeilen.append(f"| `{werkzeug}` | {fehler} | {aufrufe} "
                          f"| {100 * fehler // aufrufe} % |")
        zeilen += ["",
                   "> Eine hohe Quote ist selten die Schuld des Modells. Meist sagt die "
                   "Fehlermeldung nicht, was zu ändern wäre, oder die Parameter des "
                   "Werkzeugs verlangen etwas, das das Modell an dieser Stelle nicht "
                   "wissen kann.", ""]

    if bericht.werkzeuge_oft_gescheitert:
        zeilen += ["**Woran der Agent scheitert (aus den offenen Punkten):**", "",
                   "| Werkzeug | Fehlversuche |", "|---|---|"]
        for werkzeug, anzahl in bericht.werkzeuge_oft_gescheitert:
            zeilen.append(f"| `{werkzeug}` | {anzahl} |")
        zeilen += ["",
                   "> Wiederholtes Scheitern an einem Werkzeug heißt meist eines von zwei "
                   "Dingen: Die Fehlermeldung sagt nicht, was zu ändern wäre – oder es "
                   "fehlt das Werkzeug, das eigentlich gemeint war.", ""]
    return zeilen


def als_markdown(bericht: Architekturbericht) -> str:
    """Der vollständige Bericht. Reihenfolge ist Absicht: erst was zu tun ist,
    dann die Messwerte, die es begründen."""
    zeilen: List[str] = ["## Aufbau und Fähigkeiten", "", bericht.kurzfassung(), ""]
    zeilen += _abschnitt_anfangen(bericht)
    zeilen += _abschnitt_module(bericht)
    zeilen += _abschnitt_funktionen(bericht)
    zeilen += _abschnitt_aufbau(bericht)
    zeilen += _abschnitt_faehigkeiten(bericht)
    if bericht.nicht_lesbar:
        zeilen += ["### Nicht vermessen", ""]
        zeilen += [f"- {e}" for e in bericht.nicht_lesbar]
        zeilen.append("")
    return "\n".join(zeilen)


def terminalfassung(bericht: Architekturbericht, breite: int = 6) -> List[str]:
    zeilen = [bericht.kurzfassung(), ""]

    schritte = naechste_schritte(bericht)
    if schritte:
        zeilen.append("Womit anfangen:")
        for wann, schritt, _grund in schritte[:6]:
            zeilen.append(f"  [{wann}] {schritt}")
        zeilen.append("")

    zu_gross = [m for m in bericht.module.values() if m.zeilen > MAX_MODULZEILEN]
    if zu_gross:
        zeilen.append("Sammelbecken (über %d Zeilen):" % MAX_MODULZEILEN)
        for mass in sorted(zu_gross, key=lambda m: -m.zeilen):
            zeilen.append(f"  {mass.zeilen:5} Zeilen  {mass.name}")

    auffaellig = sorted((f for f in bericht.funktionen if f.auffaellig),
                        key=lambda f: -f.komplexitaet)[:breite]
    if auffaellig:
        zeilen.append("")
        zeilen.append("Funktionen mit den meisten Entscheidungspfaden:")
        for mass in auffaellig:
            zeilen.append(f"  {mass.komplexitaet:3}  {mass.modul}:{mass.zeile} "
                          f"{mass.name}  ({', '.join(mass.auffaellig)})")

    if bericht.zyklen:
        zeilen.append("")
        zeilen.append("Importzyklen:")
        for zyklus in bericht.zyklen[:3]:
            zeilen.append("  " + " → ".join(zyklus))

    if not bericht.testdateien:
        zeilen.append("")
        zeilen.append("Testabdeckung nicht prüfbar: keine Testdatei gefunden.")
        zeilen.append("  Liegt 'tests/' unter CODE_ROOTS? Ohne die Tests sagt diese")
        zeilen.append("  Rubrik nichts über den Code, nur über den Suchpfad.")
    elif bericht.werkzeuge_ohne_test:
        zeilen.append("")
        zeilen.append(f"Werkzeuge ohne Test: {len(bericht.werkzeuge_ohne_test)} von "
                      f"{bericht.werkzeuge_gesamt}  ({bericht.testdateien} Testdateien)")
        zeilen.append("  " + ", ".join(bericht.werkzeuge_ohne_test[:8])
                      + (" …" if len(bericht.werkzeuge_ohne_test) > 8 else ""))

    if bericht.werkzeuge_unzuverlaessig:
        zeilen.append("")
        zeilen.append("Hohe Fehlerquote im Betrieb:")
        for werkzeug, fehler, aufrufe in bericht.werkzeuge_unzuverlaessig[:5]:
            zeilen.append(f"  {100 * fehler // aufrufe:3} %  {werkzeug} "
                          f"({fehler} von {aufrufe})")

    if bericht.werkzeuge_oft_gescheitert:
        zeilen.append("")
        zeilen.append("Woran der Agent scheitert (offene Punkte):")
        for werkzeug, anzahl in bericht.werkzeuge_oft_gescheitert[:5]:
            zeilen.append(f"  {anzahl:3}× {werkzeug}")

    fehlend = [l for l in bericht.luecken if l["fehlend"]]
    if fehlend:
        zeilen.append("")
        zeilen.append("Fehlende Werkzeuge:")
        for luecke in fehlend:
            zeilen.append(f"  {luecke['feld']}: {', '.join(luecke['fehlend'])}")
    return zeilen
