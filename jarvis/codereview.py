"""Selbstprüfung des eigenen Quellcodes - vom Code gesteuert, nicht vom Modell.

Warum nicht per Prompt? Der Versuch, den Agenten mit einer Anweisung durch eine
mehrstufige Prüfung zu führen ("erst code_dateien, dann quellcode_lesen, dann
code_suchen"), endet bei einem 14B-Modell zuverlässig nach dem ERSTEN Werkzeug:
Es bekommt die Dateiliste, formuliert sie in Prosa aus und hält das für die
Antwort. Dieselbe Erfahrung wie bei Schreibaufträgen - und dieselbe Lösung:
Die Schrittfolge gehört in Python, das Modell bekommt pro Schritt genau eine
kleine, klar begrenzte Frage.

Zwei Ebenen:

1. STATISCH (kein Modell, sofort, immer korrekt)
   Der Python-Parser findet Muster, die im Betrieb nachweislich wehtun:
   verschluckte Fehler, fehlendes encoding, subprocess ohne Timeout,
   veränderliche Vorgabewerte. Diese Befunde sind keine Vermutung.

2. MIT MODELL (optional, langsam, wird überprüft)
   Ausgewählte Abschnitte gehen einzeln ans Modell. Jeder Befund muss ein
   wörtliches Zitat der beanstandeten Zeile enthalten; stimmt es nicht mit dem
   echten Quelltext überein, wird der Befund VERWORFEN. Damit kann das Modell
   nichts erfinden, was hinterher als Tatsache im Bericht steht.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import architektur as _arch
from .architektur import Architekturbericht, architektur_pruefen
from .config import settings

SCHWERE_REIHENFOLGE = {"hoch": 0, "mittel": 1, "gering": 2}

# Dateien, die den Aufbau prüfen statt ihn zu bilden. Sie zählen bei der
# Architekturmessung nicht als Modul, aber sehr wohl als Nachweis, dass ein
# Werkzeug überhaupt einmal ausprobiert wurde.
def ist_testdatei(anzeige: str) -> bool:
    name = anzeige.rsplit("/", 1)[-1]
    return (anzeige.startswith("tests/") or "/tests/" in anzeige
            or name.startswith("test_") or name == "conftest.py"
            or name == "stubs.py")


@dataclass
class Befund:
    datei: str
    zeile: int
    zitat: str
    problem: str
    auswirkung: str
    vorschlag: str
    schwere: str = "mittel"
    quelle: str = "statisch"     # statisch | modell
    belegt: bool = True          # Zitat stimmt mit dem echten Quelltext überein

    def schluessel(self) -> Tuple[str, int, str]:
        return (self.datei, self.zeile, self.problem[:40])

    def als_markdown(self, nummer: int) -> str:
        marke = {"hoch": "🔴", "mittel": "🟡", "gering": "⚪"}.get(self.schwere, "🟡")
        herkunft = "statisch geprüft" if self.quelle == "statisch" else "vom Modell, Zitat belegt"
        return (
            f"### {nummer}. {marke} {self.problem}\n\n"
            f"**Datei:** `{self.datei}`, Zeile {self.zeile}  \n"
            f"**Herkunft:** {herkunft}\n\n"
            f"```python\n{self.zitat}\n```\n\n"
            f"**Auswirkung:** {self.auswirkung}\n\n"
            f"**Vorschlag:** {self.vorschlag}\n"
        )


@dataclass
class Pruefbericht:
    befunde: List[Befund] = field(default_factory=list)
    dateien: int = 0
    zeilen: int = 0
    nicht_geprueft: List[str] = field(default_factory=list)
    verworfen: List[str] = field(default_factory=list)
    modell_abschnitte: int = 0
    # Der zweite Blick: nicht "was ist an dieser Zeile falsch", sondern
    # "trägt der Aufbau, ist er geprüft, und was fehlt an Fähigkeiten".
    architektur: Optional[Architekturbericht] = None

    def sortiert(self) -> List[Befund]:
        return sorted(self.befunde,
                      key=lambda b: (SCHWERE_REIHENFOLGE.get(b.schwere, 1),
                                     b.datei, b.zeile))

    def terminalfassung(self, breite: int = 3) -> List[str]:
        """Was im Terminal stehen soll - auch wenn nichts Dringendes dabei ist.

        Bei 32× 'gering' stand vorher nur die Zahl da. Das liest sich wie
        'nichts gefunden', obwohl 32 konkrete Stellen bekannt sind.
        """
        zeilen: List[str] = []
        wichtig = [b for b in self.sortiert() if b.schwere != "gering"]
        for nummer, befund in enumerate(wichtig, 1):
            herkunft = "" if befund.quelle == "statisch" else "  (Modell, unbestätigt)"
            zeilen.append(f"{nummer:2}. [{befund.schwere}] "
                          f"{befund.datei}:{befund.zeile}{herkunft}")
            zeilen.append(f"    {befund.problem}")
            zeilen.append(f"    → {befund.vorschlag}")

        gering = [b for b in self.sortiert() if b.schwere == "gering"]
        if gering:
            gruppen: Dict[str, List[Befund]] = {}
            for befund in gering:
                gruppen.setdefault(befund.problem, []).append(befund)
            if wichtig:
                zeilen.append("")
            zeilen.append(f"Kleinkram ({len(gering)}):")
            for problem, treffer in sorted(gruppen.items(), key=lambda p: -len(p[1])):
                stellen = ", ".join(f"{b.datei}:{b.zeile}" for b in treffer[:breite])
                if len(treffer) > breite:
                    stellen += f" … (+{len(treffer) - breite})"
                zeilen.append(f"  {len(treffer):2}× {problem}")
                zeilen.append(f"      {stellen}")
        if not wichtig and not gering:
            zeilen.append("Keine Befunde. Das heißt: die geprüften Muster trafen nicht zu –")
            zeilen.append("nicht, dass der Code fehlerfrei ist.")

        if self.architektur is not None:
            zeilen.append("")
            zeilen.append("─── Aufbau und Fähigkeiten " + "─" * 30)
            zeilen.append("")
            zeilen.extend(_arch.terminalfassung(self.architektur))
        return zeilen

    def kurzfassung(self) -> str:
        nach_schwere: Dict[str, int] = {}
        for b in self.befunde:
            nach_schwere[b.schwere] = nach_schwere.get(b.schwere, 0) + 1
        teile = [f"{anzahl}× {name}" for name, anzahl in
                 sorted(nach_schwere.items(), key=lambda p: SCHWERE_REIHENFOLGE.get(p[0], 1))]
        kern = (f"{len(self.befunde)} Befunde in {self.dateien} Dateien "
                f"({self.zeilen} Zeilen)" + (f": {', '.join(teile)}" if teile else "."))
        if self.architektur is not None:
            kern += "\n" + self.architektur.kurzfassung()
        return kern

    def als_markdown(self, titel: str = "Code-Prüfung") -> str:
        heute = dt.date.today().isoformat()
        zeilen = [
            "---",
            f'titel: "{titel}"',
            "tags:\n  - code\n  - pruefung\n  - selbstpruefung",
            f"datum: {heute}",
            "---",
            "",
            f"# {titel}",
            "",
            self.kurzfassung(),
            "",
        ]
        sortierte = self.sortiert()
        wichtig = [b for b in sortierte if b.schwere != "gering"]
        nebensache = [b for b in sortierte if b.schwere == "gering"]

        if wichtig:
            zeilen.append("## Was zuerst angesehen werden sollte")
            zeilen.append("")
            for nummer, befund in enumerate(wichtig, 1):
                zeilen.append(befund.als_markdown(nummer))
        elif not nebensache:
            zeilen.append("Keine Befunde. Das heißt nicht fehlerfrei – "
                          "es heißt, dass die geprüften Muster nicht zutrafen.")
            zeilen.append("")

        # Kleinkram gebündelt: dreißig gleich aussehende Einzelblöcke liest
        # niemand, und sie verdecken die Befunde, auf die es ankommt.
        if nebensache:
            gruppen: Dict[str, List[Befund]] = {}
            for befund in nebensache:
                gruppen.setdefault(befund.problem, []).append(befund)
            zeilen.append("")
            zeilen.append(f"## Kleinkram ({len(nebensache)}) – gebündelt")
            zeilen.append("")
            for problem, treffer in sorted(gruppen.items(), key=lambda p: -len(p[1])):
                stellen = ", ".join(f"`{b.datei}:{b.zeile}`" for b in treffer[:12])
                if len(treffer) > 12:
                    stellen += f" … (+{len(treffer) - 12})"
                zeilen.append(f"- **{problem}** ({len(treffer)}×): {stellen}")
                zeilen.append(f"  - {treffer[0].vorschlag}")
            zeilen.append("")

        if self.architektur is not None:
            zeilen.append("")
            zeilen.append(_arch.als_markdown(self.architektur))
            zeilen.append("")

        if self.verworfen:
            zeilen.append("## Verworfen (Zitat stimmte nicht mit dem Quelltext überein)")
            zeilen.append("")
            for eintrag in self.verworfen:
                zeilen.append(f"- {eintrag}")
            zeilen.append("")

        zeilen.append("## Nicht geprüft")
        zeilen.append("")
        if self.nicht_geprueft:
            for eintrag in self.nicht_geprueft:
                zeilen.append(f"- {eintrag}")
        else:
            zeilen.append("- (nichts)")
        zeilen.append("")
        zeilen.append(f"*Erzeugt am {heute}. Statische Befunde sind vom Parser belegt; "
                      f"Modellbefunde nur, wenn das Zitat im Quelltext steht.*")
        return "\n".join(zeilen)


# ===================================================================== statisch
class _Besucher(ast.NodeVisitor):
    """Sucht Muster, die im Betrieb nachweislich Ärger machen."""

    def __init__(self, datei: str, quelltext: List[str]):
        self.datei = datei
        self.zeilen = quelltext
        self.befunde: List[Befund] = []
        # Stapel der gerade offenen except-Klauseln, um zu wissen, ob ein
        # Aufruf abgesichert ist.
        self._faenge: List[bool] = []

    def _faengt_oserror(self) -> bool:
        return any(self._faenge)

    def _zitat(self, zeile: int) -> str:
        if 1 <= zeile <= len(self.zeilen):
            return self.zeilen[zeile - 1].strip()
        return ""

    def _melden(self, zeile: int, problem: str, auswirkung: str, vorschlag: str,
                schwere: str = "mittel") -> None:
        self.befunde.append(Befund(
            datei=self.datei, zeile=zeile, zitat=self._zitat(zeile),
            problem=problem, auswirkung=auswirkung, vorschlag=vorschlag,
            schwere=schwere, quelle="statisch",
        ))

    # -------------------------------------------------- verschluckte Fehler
    def visit_Try(self, node: ast.Try) -> None:
        """Ein verschluckter Fehler wiegt so schwer wie das, was im try steht.

        `except Exception: pass` um einen optionalen Sprachausgabe-Versuch ist
        Absicht und richtig. Dieselbe Zeile um einen Schreibvorgang herum ist
        der Bauplan für eine falsche Erfolgsmeldung. Ohne diese Unterscheidung
        meldet die Prüfung vierzig gleich aussehende Treffer, und der eine, auf
        den es ankommt, geht darin unter.
        """
        schreibend, gewicht = self._schreibt(node.body)
        for handler in node.handlers:
            nur_pass = (len(handler.body) == 1
                        and isinstance(handler.body[0], (ast.Pass, ast.Continue)))
            if handler.type is None:
                self._melden(
                    handler.lineno, "Nacktes except fängt auch Strg+C und SystemExit ab",
                    "Der Abbruch durch den Benutzer wird verschluckt; das Programm "
                    "lässt sich an dieser Stelle nicht mehr beenden.",
                    "Auf `except Exception:` einschränken – oder auf die Ausnahme, "
                    "mit der hier wirklich zu rechnen ist.",
                    schwere="hoch")
            elif nur_pass and schreibend and gewicht == "hoch":
                self._melden(
                    handler.lineno,
                    f"Schreibender Aufruf ({schreibend}) scheitert stillschweigend",
                    "Der Aufrufer sieht Erfolg, obwohl nichts geschrieben wurde – "
                    "genau die Klasse von Fehler, die als falsche Erfolgsmeldung endet.",
                    "Grund festhalten: protokollieren, im Rückgabewert vermerken oder "
                    "als ToolResult(False, …) zurückgeben.",
                    schwere="hoch")
            elif nur_pass and self._begruendet(handler):
                # Der Rat lautet "wenn beabsichtigt: kurzer Kommentar". Dann muss
                # der Prüfer den Kommentar auch gelten lassen - sonst erzieht er
                # dazu, seine eigenen Meldungen zu ignorieren.
                pass
            elif nur_pass and schreibend:
                self._melden(
                    handler.lineno,
                    f"Aufräum-/Ordneraufruf ({schreibend}) scheitert stillschweigend",
                    "Kein falscher Erfolg, aber es bleibt etwas liegen bzw. fehlt – "
                    "und der Grund ist nirgends festgehalten.",
                    "Wenn beabsichtigt: kurzer Kommentar. Sonst den Grund vermerken.",
                    schwere="mittel")
            elif nur_pass:
                self._melden(
                    handler.lineno, "Fehler wird ohne Vermerk übergangen",
                    "Vermutlich Absicht (optionaler Schritt). Falls nicht, verschwindet "
                    "die Ursache hier spurlos.",
                    "Wenn beabsichtigt: kurzer Kommentar, warum. Sonst den Grund "
                    "wenigstens protokollieren.",
                    schwere="gering")
            elif handler.name and not self._name_benutzt(handler):
                self._melden(
                    handler.lineno,
                    f"Ausnahme '{handler.name}' wird gefangen, aber nie ausgewertet",
                    "Die Fehlerursache geht verloren; in der Meldung steht später nur, "
                    "DASS etwas schiefging, nicht warum.",
                    f"`{handler.name}` in die Meldung aufnehmen oder protokollieren.",
                    schwere="gering")

        # Für alles im try-Block merken, ob hier ein OSError aufgefangen würde.
        self._faenge.append(any(_faengt_os(h) for h in node.handlers))
        for anweisung in node.body:
            self.visit(anweisung)
        self._faenge.pop()
        for rest in list(node.handlers) + list(node.orelse) + list(node.finalbody):
            self.visit(rest)

    # Aufrufe, bei denen ein verschluckter Fehler ECHTEN Datenverlust bedeutet.
    INHALT_SCHREIBEN = {
        "write_text", "write_bytes", "writelines", "copy2", "copyfile",
        "move", "rename", "save_local",
    }
    # Aufräumen und Ordnerpflege: ärgerlich, aber kein falscher Erfolg.
    NEBEN_SCHREIBEN = {"mkdir", "unlink", "rmdir", "rmtree", "touch", "fsync"}
    # Bewusst NICHT in den Listen: write, flush, dump, replace. Die gibt es
    # genauso auf Strings, Puffern und dicts - `str.replace(",", ".")` als
    # Schreibvorgang zu melden, macht den ganzen Bericht unglaubwürdig.

    def _begruendet(self, handler: ast.ExceptHandler) -> bool:
        """Steht bei diesem except ein Kommentar, der das Schweigen erklärt?"""
        # Der Kommentar steht meist ZWISCHEN `except …:` und `pass`, kann aber
        # auch davor oder auf derselben Zeile stehen.
        ende = handler.body[0].lineno if handler.body else handler.lineno
        for nummer in range(handler.lineno - 1, ende + 1):
            if 1 <= nummer <= len(self.zeilen) and "#" in self.zeilen[nummer - 1]:
                return True
        return False

    def _schreibt(self, koerper: Sequence[ast.stmt]) -> Tuple[str, str]:
        """(Name des schreibenden Aufrufs, Gewicht) - oder ('', '')."""
        nebensache = ""
        for knoten in koerper:
            for kind in ast.walk(knoten):
                if not isinstance(kind, ast.Call):
                    continue
                name = _aufrufname(kind.func)
                kurz = name.rsplit(".", 1)[-1]
                if kurz in self.INHALT_SCHREIBEN:
                    return name or kurz, "hoch"
                if name == "os.replace":          # atomares Ersetzen einer Datei
                    return name, "hoch"
                if kurz in self.NEBEN_SCHREIBEN and not nebensache:
                    nebensache = name or kurz
        return (nebensache, "mittel") if nebensache else ("", "")

    def _name_benutzt(self, node: ast.ExceptHandler) -> bool:
        for kind in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(kind, ast.Name) and kind.id == node.name:
                return True
        return False

    # ------------------------------------------- veränderliche Vorgabewerte
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for vorgabe in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
            if isinstance(vorgabe, (ast.List, ast.Dict, ast.Set)):
                self._melden(
                    vorgabe.lineno,
                    f"Veränderlicher Vorgabewert in '{node.name}'",
                    "Die Liste bzw. das Dict wird EINMAL angelegt und über alle Aufrufe "
                    "hinweg geteilt; Einträge aus einem früheren Aufruf tauchen später "
                    "unerwartet wieder auf.",
                    "`= None` als Vorgabe und im Rumpf `if x is None: x = []`.",
                    schwere="hoch")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    # --------------------------------------------------- Aufrufe mit Tücken
    def visit_Call(self, node: ast.Call) -> None:
        name = _aufrufname(node.func)
        schluessel = {kw.arg for kw in node.keywords if kw.arg}

        # Binärmodus ("rb"/"wb") verträgt gar kein encoding - das zu fordern
        # wäre ein TypeError. Also nur Textmodus prüfen.
        binaer = any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and "b" in a.value for a in node.args[1:2]) or any(
            kw.arg == "mode" and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str) and "b" in kw.value.value
            for kw in node.keywords)
        if name in ("open",) and not binaer and "encoding" not in schluessel and node.args:
            # Auf deutschem Windows ist die Vorgabe cp1252 - Umlaute und
            # jedes fremde Zeichen brechen dann beim Lesen.
            self._melden(
                node.lineno, "open() ohne encoding",
                "Die Kodierung richtet sich nach dem System. Auf deutschem Windows "
                "ist das cp1252 – eine UTF-8-Notiz mit Umlauten oder Emoji löst dort "
                "einen UnicodeDecodeError aus, auf dem Entwicklungsrechner nie.",
                'encoding="utf-8" mitgeben (beim Lesen fremder Dateien zusätzlich '
                'errors="replace").',
                schwere="hoch")

        # subprocess.run wirft FileNotFoundError, wenn es das Programm nicht
        # gibt. Das ist ein OSError - `except subprocess.SubprocessError`
        # fängt es NICHT. Genau daran ist /tempo gestorben, als Windows 11
        # `wmic` entfernt hatte, und der Prüfer hat es damals übersehen.
        if name in _EXTERNE_AUFRUFE and not self._faengt_oserror():
            self._melden(
                node.lineno, f"{name} ohne Absicherung gegen ein fehlendes Programm",
                "Fehlt das Programm auf dem Zielrechner, wirft der Aufruf "
                "FileNotFoundError. Ein `except subprocess.SubprocessError` fängt "
                "das NICHT – es ist ein OSError. Das Programm stürzt dann mit "
                "einem Traceback ab, statt die Funktion einfach auszulassen.",
                "In try/except mit OSError (oder FileNotFoundError) einpacken – "
                "shutil.which() davor reicht nicht, das Programm kann zwischen "
                "Prüfung und Aufruf verschwinden.",
                schwere="hoch")

        if name in ("subprocess.run", "subprocess.call", "subprocess.check_output") \
                and "timeout" not in schluessel:
            self._melden(
                node.lineno, f"{name} ohne timeout",
                "Hängt das aufgerufene Programm, hängt der Agent mit – ohne Meldung "
                "und ohne Weg zurück.",
                "timeout=… setzen und TimeoutExpired abfangen.",
                schwere="mittel")

        if name.endswith("Popen") and "stdout" not in schluessel \
                and "stderr" not in schluessel:
            self._melden(
                node.lineno, "Popen ohne Umleitung von stdout/stderr",
                "Die Ausgabe des Unterprozesses landet mitten in der Terminaloberfläche "
                "und zerreißt die Darstellung.",
                "stdout/stderr auf PIPE oder DEVNULL setzen.",
                schwere="gering")

        self.generic_visit(node)

    # ------------------------------------------------------------ Vergleiche
    def visit_Compare(self, node: ast.Compare) -> None:
        for operator, rechts in zip(node.ops, node.comparators):
            if isinstance(operator, (ast.Eq, ast.NotEq)) \
                    and isinstance(rechts, ast.Constant) and rechts.value is None:
                self._melden(
                    node.lineno, "Vergleich mit == None statt is None",
                    "Eine Klasse mit eigenem __eq__ kann hier ein anderes Ergebnis "
                    "liefern als erwartet.",
                    "`is None` bzw. `is not None` verwenden.",
                    schwere="gering")
        self.generic_visit(node)

    # ------------------------------------------------------------- assert
    def visit_Assert(self, node: ast.Assert) -> None:
        self._melden(
            node.lineno, "assert als Laufzeitprüfung",
            "Mit `python -O` werden asserts entfernt – die Prüfung fällt genau dann "
            "aus, wenn optimiert gestartet wird.",
            "Für echte Prüfungen `if not …: raise` bzw. eine Fehlermeldung verwenden.",
            schwere="gering")
        self.generic_visit(node)


# Aufrufe, die ein externes Programm starten - und scheitern, wenn es fehlt.
_EXTERNE_AUFRUFE = {
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen", "os.execvp", "os.spawnv",
}
# Ausnahmen, die FileNotFoundError einschließen.
_OS_AUSNAHMEN = {"OSError", "FileNotFoundError", "EnvironmentError", "IOError",
                 "Exception", "BaseException", "PermissionError"}


def _faengt_os(handler: ast.ExceptHandler) -> bool:
    """Fängt diese except-Klausel ein fehlendes Programm ab?"""
    if handler.type is None:
        return True                     # nacktes except fängt alles
    kandidaten = (handler.type.elts if isinstance(handler.type, ast.Tuple)
                  else [handler.type])
    for knoten in kandidaten:
        name = _aufrufname(knoten).rsplit(".", 1)[-1]
        if name in _OS_AUSNAHMEN:
            return True
    return False


def _aufrufname(knoten: ast.AST) -> str:
    if isinstance(knoten, ast.Name):
        return knoten.id
    if isinstance(knoten, ast.Attribute):
        return f"{_aufrufname(knoten.value)}.{knoten.attr}"
    return ""


def statisch_pruefen(pfad: Path, anzeige: str = "") -> Tuple[List[Befund], Optional[str]]:
    """Ein Modul prüfen. Zweiter Rückgabewert ist ein Grund, falls es nicht ging."""
    try:
        quelltext = pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"{anzeige or pfad.name}: nicht lesbar ({exc})"
    try:
        baum = ast.parse(quelltext, filename=str(pfad))
    except SyntaxError as exc:
        return [], f"{anzeige or pfad.name}: Syntaxfehler in Zeile {exc.lineno}"
    besucher = _Besucher(anzeige or pfad.name, quelltext.splitlines())
    besucher.visit(baum)
    return besucher.befunde, None


# ======================================================================= Modell
ABSCHNITT_PROMPT = """Prüfe diesen Ausschnitt auf ECHTE Fehler.

{datei}, Zeilen {von}–{bis}:

```python
{code}
```

Suche nur nach: Fehler, der verschluckt wird; Erfolg, der ohne Prüfung gemeldet \
wird; Sonderfall (leere Liste, None, fehlender Schlüssel), der in den falschen \
Zweig führt; Wert, der ungeprüft weiterverwendet wird.
Stil, Namen und Formatierung sind KEIN Fehler.

Melde NUR, was du im Ausschnitt SIEHST. Keine Vermutung, keine Rückfrage.
Verboten sind Formulierungen wie "es ist nicht klar, ob", "könnte", "stelle
sicher, dass", "überprüfe, ob" – wenn du es nachsehen müsstest, ist es kein
Befund. Auch kein Befund: eine Zeile, die lediglich etwas definiert, importiert
oder zuweist, ohne dass daran ein Fehler sichtbar wird.

Antworte mit reinem JSON, sonst nichts:
{{"befunde": [{{"zeile": <Nummer>, "zitat": "<die Zeile wörtlich, ohne Nummer>", \
"problem": "<1 Satz>", "auswirkung": "<1 Satz>", "vorschlag": "<1 Satz>", \
"schwere": "hoch|mittel|gering"}}]}}

Findest du nichts Belegbares: {{"befunde": []}}
Höchstens 2 Befunde. Nur was du in diesem Ausschnitt SIEHST."""


class CodePruefer:
    """Steuert die Prüfung - Reihenfolge und Auswahl liegen hier, nicht im Modell."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg

    # ------------------------------------------------------------- Dateien
    def quelldateien(self) -> List[Tuple[Path, str]]:
        """Alle freigegebenen Quelldateien als (Pfad, Anzeigename).

        Doppelte werden übersprungen - nicht nur gleiche Pfade, sondern auch
        gleicher INHALT. Bei Josh lag der Code sowohl im Projektordner als auch
        in einem Unterordner: 36 Dateien, 23 471 Zeilen, jede Zahl doppelt.
        Schlimmer noch, das Budget der Modellprüfung ging für dieselbe Datei
        zweimal drauf (Abschnitt 1/6 und 2/6 waren identisch).
        """
        import hashlib

        gefunden: List[Tuple[Path, str]] = []
        gesehen_pfade = set()
        gesehen_inhalte: Dict[str, str] = {}
        self.doppelte: List[str] = []

        for wurzel in self.cfg.code_root_list:
            if not wurzel.is_dir():
                continue
            for datei in sorted(wurzel.rglob("*.py")):
                if not datei.is_file():
                    continue
                teile = datei.relative_to(wurzel).parts
                if any(t.startswith(".") or t in ("__pycache__", "vector_store",
                                                  "node_modules", "venv", ".venv",
                                                  "build", "dist", "site-packages")
                       for t in teile):
                    continue
                if datei in gesehen_pfade:
                    continue
                gesehen_pfade.add(datei)
                anzeige = datei.relative_to(wurzel).as_posix()
                try:
                    fingerabdruck = hashlib.sha1(datei.read_bytes()).hexdigest()
                except OSError:
                    fingerabdruck = ""
                if fingerabdruck and fingerabdruck in gesehen_inhalte:
                    self.doppelte.append(f"{anzeige} = {gesehen_inhalte[fingerabdruck]}")
                    continue
                if fingerabdruck:
                    gesehen_inhalte[fingerabdruck] = anzeige
                gefunden.append((datei, anzeige))
                if len(gefunden) >= self.cfg.max_code_files:
                    return gefunden
        return gefunden

    # ------------------------------------------------------------ Durchlauf
    def pruefen(self, mit_modell: bool = False, abschnitte: int = 6,
                fortschritt: Optional[Callable[[str], None]] = None,
                mit_architektur: bool = True) -> Pruefbericht:
        melden = fortschritt or (lambda _t: None)
        bericht = Pruefbericht()
        alle_dateien = self.quelldateien()
        # Tests bilden den Aufbau nicht, sie belegen ihn. Deshalb gehen sie
        # getrennt in die Architekturprüfung: ihre Namensverwendung entscheidet
        # darüber, welches Werkzeug als geprüft gilt.
        dateien = [(p, a) for p, a in alle_dateien if not ist_testdatei(a)]
        testdateien = [(p, a) for p, a in alle_dateien if ist_testdatei(a)]
        if not dateien:
            bericht.nicht_geprueft.append(
                "Keine Quelldateien gefunden – CODE_ROOTS prüfen "
                f"(aktuell: {', '.join(str(w) for w in self.cfg.code_root_list)})")
            return bericht

        doppelte = getattr(self, "doppelte", [])
        if doppelte:
            # Sichtbar machen statt still übergehen: Eine doppelte Codebasis
            # ist selbst ein Befund - man pflegt sonst die falsche Kopie.
            melden(f"{len(doppelte)} inhaltsgleiche Doppel übersprungen "
                   f"(z. B. {doppelte[0]})")
            bericht.nicht_geprueft.append(
                f"{len(doppelte)} Dateien sind inhaltsgleiche Kopien und wurden nur "
                f"einmal geprüft: {', '.join(doppelte[:5])}"
                + (f" … (+{len(doppelte) - 5})" if len(doppelte) > 5 else "")
                + ". Zwei Kopien derselben Datei heißt: Änderungen an der einen "
                  "wirken nicht. CODE_ROOTS auf genau einen Ordner setzen.")
        if testdateien:
            # `assert` ist in einem Test die richtige Konstruktion, nicht der
            # Fehler, den visit_Assert meint. Vorher versank der Bericht in
            # hunderten solcher Meldungen.
            bericht.nicht_geprueft.append(
                f"{len(testdateien)} Testdateien wurden nicht auf Defektmuster geprüft "
                "(dort ist `assert` beabsichtigt); sie zählen aber als Nachweis, "
                "welche Werkzeuge geprüft sind.")
        melden(f"{len(dateien)} Quelldateien, {len(testdateien)} Tests – "
               f"statische Prüfung …")
        for pfad, anzeige in dateien:
            befunde, grund = statisch_pruefen(pfad, anzeige)
            if grund:
                bericht.nicht_geprueft.append(grund)
                continue
            bericht.dateien += 1
            try:
                bericht.zeilen += sum(1 for _ in pfad.open(encoding="utf-8",
                                                           errors="replace"))
            except OSError:
                pass
            bericht.befunde.extend(befunde)
        melden(f"statisch: {len(bericht.befunde)} Befunde")

        if mit_architektur:
            melden("Aufbau vermessen (Größe, Verzweigungen, Kopplung, Fähigkeiten) …")
            try:
                bericht.architektur = architektur_pruefen(dateien, testdateien, self.cfg)
            except Exception as exc:      # darf die Defektprüfung nie mitreißen
                bericht.nicht_geprueft.append(f"Architekturprüfung fehlgeschlagen: {exc}")
            else:
                melden(bericht.architektur.kurzfassung())

        if not mit_modell:
            bericht.nicht_geprueft.append(
                "Inhaltliche Prüfung durch das Modell – nicht angefordert "
                "(mit --tief bzw. /codereview tief)")
            return bericht

        ausgewaehlt = self._abschnitte_waehlen(dateien, bericht, abschnitte)
        bericht.modell_abschnitte = len(ausgewaehlt)
        for nummer, (pfad, anzeige, von, bis) in enumerate(ausgewaehlt, 1):
            melden(f"Modellprüfung {nummer}/{len(ausgewaehlt)}: {anzeige} {von}–{bis}")
            neue, verworfen, grund = self._abschnitt_pruefen(pfad, anzeige, von, bis)
            bericht.befunde.extend(neue)
            bericht.verworfen.extend(verworfen)
            if grund:
                bericht.nicht_geprueft.append(f"{anzeige} {von}–{bis}: {grund}")

        geprueft = {(a, v, b) for _, a, v, b in ausgewaehlt}
        offen = len(dateien) - len({a for a, _, _ in geprueft})
        if offen > 0:
            bericht.nicht_geprueft.append(
                f"{offen} Dateien wurden nur statisch geprüft – für die inhaltliche "
                f"Prüfung war das Abschnittsbudget ({abschnitte}) erschöpft.")
        bericht.befunde = _entdoppeln(bericht.befunde)
        return bericht

    # --------------------------------------------------------- Auswahllogik
    def _abschnitte_waehlen(self, dateien: Sequence[Tuple[Path, str]],
                            bericht: Pruefbericht,
                            budget: int) -> List[Tuple[Path, str, int, int]]:
        """Wählt die verdächtigsten Abschnitte - deterministisch, ohne Modell.

        Vorrang haben Stellen um statische Befunde herum: dort ist erwiesenermaßen
        etwas im Argen, und das Modell kann den Zusammenhang beurteilen. Danach
        die größten Dateien, weil dort die meiste ungeprüfte Logik liegt.
        """
        fenster = 100
        # Höchstens zwei Abschnitte je Datei. Ohne diese Schranke wandert das
        # ganze Budget in die größte Datei - beim ersten echten Lauf gingen
        # alle sechs Abschnitte nach tools.py, und dreizehn andere Module
        # wurden inhaltlich überhaupt nicht angesehen.
        je_datei = max(1, min(2, budget))
        kandidaten: List[Tuple[Path, str, int, int]] = []
        gesehen = set()
        zaehler: Dict[str, int] = {}
        pfad_zu = {anzeige: pfad for pfad, anzeige in dateien}

        def merken(pfad: Path, anzeige: str, von: int) -> bool:
            block = (anzeige, von // fenster)
            if block in gesehen or zaehler.get(anzeige, 0) >= je_datei:
                return False
            gesehen.add(block)
            zaehler[anzeige] = zaehler.get(anzeige, 0) + 1
            kandidaten.append((pfad, anzeige, von, von + fenster))
            return True

        # Runde 1: je Datei mit statischen Befunden die auffälligste Stelle,
        # reihum - damit die Breite gewinnt und nicht die Dateigröße.
        nach_datei: Dict[str, List[int]] = {}
        for befund in bericht.befunde:
            nach_datei.setdefault(befund.datei, []).append(befund.zeile)
        reihenfolge = sorted(nach_datei.items(), key=lambda p: -len(p[1]))

        for runde in range(je_datei):
            for anzeige, zeilen in reihenfolge:
                pfad = pfad_zu.get(anzeige)
                if pfad is None:
                    continue
                sortiert = sorted(zeilen)
                if runde >= len(sortiert):
                    continue
                for zeile in sortiert[runde:]:
                    if merken(pfad, anzeige, max(1, zeile - fenster // 2)):
                        break
                if len(kandidaten) >= budget:
                    return kandidaten

        # Runde 2: Dateien ohne statischen Befund, größte zuerst
        for pfad, anzeige in sorted(dateien, key=lambda p: -p[0].stat().st_size):
            merken(pfad, anzeige, 1)
            if len(kandidaten) >= budget:
                break
        return kandidaten[:budget]

    # ------------------------------------------------------- ein Abschnitt
    def _abschnitt_pruefen(self, pfad: Path, anzeige: str, von: int, bis: int):
        """Eine Frage, eine Antwort, danach Abgleich mit dem echten Quelltext."""
        try:
            alle = pfad.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return [], [], f"nicht lesbar ({exc})"
        bis = min(bis, len(alle))
        if von > len(alle):
            return [], [], "Bereich liegt hinter dem Dateiende"
        ausschnitt = "\n".join(f"{von + i}| {z}" for i, z in enumerate(alle[von - 1:bis]))

        prompt = ABSCHNITT_PROMPT.format(datei=anzeige, von=von, bis=bis, code=ausschnitt)
        try:
            antwort = self.agent.llm.chat(
                [{"role": "system", "content":
                  "Du prüfst Python-Code. Du antwortest ausschließlich mit JSON."},
                 {"role": "user", "content": prompt}],
                temperature=0.1, format_json=True, num_predict=600,
            )
        except Exception as exc:
            return [], [], f"Modell nicht erreichbar ({exc})"

        roh = _json_lesen(antwort)
        if roh is None:
            return [], [], "Antwort war kein verwertbares JSON"

        angenommen: List[Befund] = []
        verworfen: List[str] = []
        for eintrag in (roh.get("befunde") or [])[:2]:
            if not isinstance(eintrag, dict):
                continue
            try:
                zeile = int(eintrag.get("zeile") or 0)
            except (TypeError, ValueError):
                zeile = 0
            zitat = str(eintrag.get("zitat") or "").strip()
            problem = str(eintrag.get("problem") or "").strip()
            if not zitat or not problem or zeile < 1:
                verworfen.append(f"{anzeige}: unvollständiger Befund ohne Zitat oder Zeile")
                continue

            echte_zeile = _zeile_finden(alle, zitat, zeile)
            if echte_zeile is None:
                # Genau hier wird Erfundenes aussortiert, bevor es im Bericht steht.
                verworfen.append(
                    f"{anzeige}:{zeile} – Zitat \"{zitat[:60]}\" steht so nicht im Quelltext")
                continue

            quelltextzeile = alle[echte_zeile - 1].strip()
            grund = _taugt_nicht(quelltextzeile, problem,
                                 str(eintrag.get("auswirkung") or ""))
            if grund:
                verworfen.append(f"{anzeige}:{echte_zeile} – {grund}")
                continue

            angenommen.append(Befund(
                datei=anzeige, zeile=echte_zeile,
                zitat=quelltextzeile,
                problem=problem,
                auswirkung=str(eintrag.get("auswirkung") or "").strip() or "(nicht genannt)",
                vorschlag=str(eintrag.get("vorschlag") or "").strip() or "(nicht genannt)",
                # Ein Modellbefund ist unbestätigt und darf nie über einem vom
                # Parser belegten stehen. Beim ersten echten Lauf standen zwei
                # falsche Modellbefunde ganz oben auf "hoch" - das entwertet
                # die ganze Liste.
                schwere="mittel" if _schwere_normieren(eintrag.get("schwere")) == "hoch"
                        else _schwere_normieren(eintrag.get("schwere")),
                quelle="modell",
            ))
        return angenommen, verworfen, None


# ====================================================================== Helfer
def _schwere_normieren(wert) -> str:
    text = str(wert or "").strip().lower()
    if text in ("hoch", "high", "kritisch", "critical"):
        return "hoch"
    if text in ("gering", "low", "niedrig", "minor"):
        return "gering"
    return "mittel"


# Formulierungen, mit denen ein Modell Unsicherheit als Befund verkauft.
UNSICHER = re.compile(
    r"\b(ist\s+nicht\s+klar|unklar|k[öo]nnte|m[öo]glicherweise|vermutlich|"
    r"eventuell|vielleicht|scheint|stelle\s+sicher|stellen\s+sie\s+sicher|"
    r"[üu]berpr[üu]fe[n]?\s*,?\s*ob|falls\s+n[öo]tig|might|could|unclear|"
    r"make\s+sure)\b",
    re.IGNORECASE,
)
# Zeilen, an denen ein Laufzeitfehler schlicht nicht auftreten kann.
HARMLOS = re.compile(r"^\s*(#|\"\"\"|'''|import\s|from\s+\S+\s+import\s|@|"
                     r"def\s|class\s|else\s*:|try\s*:|pass\s*$|\)\s*$|\}\s*$)")


# Reine Zuweisung an ein Attribut/eine Variable, oder Anhängen an eine Sammlung.
ZUWEISUNG = re.compile(
    r"^\s*[\w.\[\]\"']+\s*(=|\+=|-=)\s*[^=]"
    r"|^\s*[\w.]+\.(add|append|update|extend|discard)\s*\(")
# Behauptungen, die auf einer solchen Zeile keinen Sinn ergeben.
FEHLERBEHAUPTUNG = re.compile(
    r"\b(fehler|exception|verschluck\w*|abgefangen|ungeprüft|ungeprueft|"
    r"ohne\s+Prüfung|ohne\s+Pruefung|rückgabewert|rueckgabewert|"
    r"error|swallow\w*|unchecked)\b",
    re.IGNORECASE,
)


def _taugt_nicht(quelltextzeile: str, problem: str, auswirkung: str) -> str:
    """Gibt einen Verwerfungsgrund zurück - oder '' , wenn der Befund taugt.

    Das Zitat zu prüfen reicht nicht: Die Zeile kann echt sein und die Aussage
    darüber trotzdem Unsinn. "Es ist nicht klar, ob TOOL_NAMES eine Menge ist"
    stand beim ersten Lauf über einer Zeile, die eine Mengennotation ENTHÄLT.
    """
    text = f"{problem} {auswirkung}"
    if UNSICHER.search(text):
        return f"Vermutung statt Befund: \"{problem[:70]}\""
    if len(problem.strip()) < 15:
        return "Befund ohne Aussage"
    if HARMLOS.match(quelltextzeile):
        return (f"Zeile trägt keinen Laufzeitfehler "
                f"(`{quelltextzeile[:50]}`): \"{problem[:60]}\"")

    # Eine schlichte Zuweisung oder ein Anhängen an eine Sammlung kann keinen
    # Fehler verschlucken und nichts ungeprüft lassen. Beide Modellbefunde, die
    # beim ersten echten --tief-Lauf durchkamen, waren genau das:
    #   self.status = "deaktiviert"   -> "Status ohne Prüfung gesetzt"
    #   gesehen.add(datei)            -> "kein Fall, wo entfernt würde"
    if ZUWEISUNG.match(quelltextzeile) and FEHLERBEHAUPTUNG.search(text):
        return (f"Behauptung passt nicht zur Zeile "
                f"(`{quelltextzeile[:50]}` ist eine Zuweisung): \"{problem[:60]}\"")
    return ""


def _normieren(zeile: str) -> str:
    """Zum Vergleich: Zeilennummer weg, Leerraum vereinheitlichen."""
    ohne_nummer = re.sub(r"^\s*\d+\s*[|:]\s?", "", str(zeile))
    return " ".join(ohne_nummer.split())


def _zeile_finden(alle: Sequence[str], zitat: str, vermutet: int) -> Optional[int]:
    """Prüft, ob das Zitat wirklich im Quelltext steht - und wo.

    Erst an der genannten Stelle, dann in der Nachbarschaft, dann in der
    ganzen Datei. Findet sich nichts, ist der Befund erfunden.
    """
    ziel = _normieren(zitat)
    if not ziel:
        return None
    if 1 <= vermutet <= len(alle) and _normieren(alle[vermutet - 1]) == ziel:
        return vermutet
    for abstand in range(1, 6):
        for kandidat in (vermutet - abstand, vermutet + abstand):
            if 1 <= kandidat <= len(alle) and _normieren(alle[kandidat - 1]) == ziel:
                return kandidat
    for nummer, zeile in enumerate(alle, 1):
        if _normieren(zeile) == ziel:
            return nummer
    return None


def _json_lesen(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        daten = json.loads(text)
        return daten if isinstance(daten, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        daten = json.loads(match.group(0))
        return daten if isinstance(daten, dict) else None
    except ValueError:
        return None


def _entdoppeln(befunde: Sequence[Befund]) -> List[Befund]:
    """Gleiche Stelle, gleicher Punkt: der statische Befund gewinnt."""
    nach_stelle: Dict[Tuple[str, int], Befund] = {}
    rest: List[Befund] = []
    for befund in befunde:
        schluessel = (befund.datei, befund.zeile)
        vorhanden = nach_stelle.get(schluessel)
        if vorhanden is None:
            nach_stelle[schluessel] = befund
        elif vorhanden.quelle == "modell" and befund.quelle == "statisch":
            nach_stelle[schluessel] = befund
        elif vorhanden.quelle == befund.quelle:
            rest.append(befund)
    return list(nach_stelle.values()) + rest
