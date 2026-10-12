"""Einen Werkzeugaufruf aus Modellprosa herauslesen.

Der Anlass ist einer der aeltesten Befunde des Projekts: Ein Modell ohne
Werkzeug-Schnittstelle - und oft genug auch eines mit - schreibt den
Aufruf als TEXT hin, statt ihn zu taetigen. Bis das erkannt wurde, sah
der Nutzer eine huebsche Beschreibung dessen, was haette geschehen
sollen, und in der Vault war nichts.

Deshalb ein eigener, nachsichtiger Leser: Er nimmt Codezaeune, einfache
und doppelte Anfuehrungszeichen, fehlende Kommata, Python- statt
JSON-Wahrheitswerte - und er lehnt ab, was nur ein BEISPIEL ist. Der
Unterschied zwischen 'ruf X so auf' und 'ich rufe X auf' ist genau der
zwischen Erklaerung und Handlung.
"""
from __future__ import annotations

import ast
import json
import re
from typing import List, Optional, Set, Tuple

from ..tools import TOOL_NAMES
# ACTION_RE liegt bei den uebrigen Mustern in texte.py - dort steht
# auch, aus welchem Fehlverhalten jedes einzelne entstanden ist.
from .texte import ACTION_RE


# ------------------------------------------------------------------ Helfer
# Ein Aufruf darf lang sein: fertigkeit_definieren() trägt den kompletten
# Quelltext einer Fertigkeit im Parameter "code". 400 Zeichen waren dafür
# lächerlich wenig - genau daran ist der Auftrag "erstelle eine Fertigkeit"
# gescheitert. 40000 ist großzügig, aber immer noch eine Grenze.
_AUFRUF_MAX = 40000

# ```python\n ... \n``` - der Block, in den Modelle Quelltext packen.
_ZAUN_RE = re.compile(r"^```[A-Za-z0-9_+\-]*[ \t]*\r?\n(.*?)\r?\n?```$", re.DOTALL)

# Wörter, mit denen ein Modell einen Aufruf als BEISPIEL ankündigt. Steht so
# etwas im Fließtext vor dem Codeblock, wird nicht ausgeführt.
_NUR_BEISPIEL = re.compile(
    r"\b(?:zum beispiel|beispielsweise|z\.\s?b\.|etwa so|so etwas wie|"
    r"würdest?|wuerdest?|könntest?|koenntest?|hättest?|haettest?|"
    r"beispiel|syntax|schema)\b",
    re.IGNORECASE,
)


def _zaun_entfernen(text: str) -> str:
    """Nimmt einen umschließenden ```-Block ab, sonst bleibt der Text wie er ist."""
    kandidat = (text or "").strip()
    treffer = _ZAUN_RE.match(kandidat)
    return treffer.group(1).strip() if treffer else kandidat.strip("`").strip()


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _zeichenkette_lesen(rumpf: str, i: int, zeichen: str) -> Tuple[str, int]:
    """Eine einfach begrenzte Zeichenkette ab Position i (i zeigt aufs Anführungszeichen)."""
    n = len(rumpf)
    i += 1
    teile: List[str] = []
    while i < n and rumpf[i] != zeichen:
        if rumpf[i] == "\\" and i + 1 < n:
            teile.append(_ESCAPES.get(rumpf[i + 1], rumpf[i + 1]))
            i += 2
            continue
        teile.append(rumpf[i])
        i += 1
    return "".join(teile), i + 1


def _rohwert_lesen(rumpf: str, i: int) -> Tuple[object, int]:
    """Ein unquotierter Wert bis zum Komma auf oberster Klammerebene."""
    n, tiefe, start = len(rumpf), 0, i
    while i < n:
        z = rumpf[i]
        if z in "([{":
            tiefe += 1
        elif z in ")]}":
            if tiefe == 0:
                break
            tiefe -= 1
        elif z == "," and tiefe == 0:
            break
        i += 1
    wert: object = rumpf[start:i].strip()
    # Unquotierte Werte dürfen Listen, Zahlen oder Wahrheitswerte sein.
    # fertigkeit_definieren() erwartet schritte=[...] - als Zeichenkette wäre
    # die Fertigkeit ein einziger unbrauchbarer Schritt.
    text = str(wert)
    if text[:1] in "[{-0123456789" or text in ("True", "False", "None"):
        try:
            wert = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            pass
    return wert, i


def _wert_lesen(rumpf: str, i: int) -> Tuple[object, int]:
    """Wert ab Position i - dreifach quotiert, einfach quotiert oder roh."""
    n = len(rumpf)
    if rumpf.startswith(('"""', "'''"), i):
        zeichen = rumpf[i:i + 3]
        ende = rumpf.find(zeichen, i + 3)
        return (rumpf[i + 3:ende if ende != -1 else n],
                (ende + 3) if ende != -1 else n)
    if i < n and rumpf[i] in "\"'":
        return _zeichenkette_lesen(rumpf, i, rumpf[i])
    return _rohwert_lesen(rumpf, i)


def _schluessel_lesen(rumpf: str, i: int) -> Tuple[str, int]:
    """Überspringt Trenner und liest den nächsten Parameternamen samt '='."""
    n = len(rumpf)
    while i < n and (rumpf[i].isspace() or rumpf[i] == ","):
        i += 1
    start = i
    while i < n and (rumpf[i].isalnum() or rumpf[i] == "_"):
        i += 1
    name = rumpf[start:i]
    while i < n and rumpf[i].isspace():
        i += 1
    if not name or i >= n or rumpf[i] != "=":
        # Positionsargument oder Schrott - ab hier ist nichts mehr sicher
        # zuzuordnen, also lieber abbrechen als raten.
        return "", n
    i += 1
    while i < n and rumpf[i] in " \t":
        i += 1
    return name, i


def _argumente_lesen(rumpf: str) -> dict:
    """Zerlegt 'name="a", code=\"\"\"...\"\"\"' in ein Dict.

    Ein Zeichen-Scanner statt eines regulären Ausdrucks, weil der Wert selbst
    Kommas, Klammern und Zeilenumbrüche enthält - der komplette Quelltext einer
    Fertigkeit zum Beispiel. Der alte Ausdruck ([^,()]+) brach beim ersten
    Komma im Code ab und lieferte Bruchstücke.
    """
    args: dict = {}
    i, n = 0, len(rumpf)
    while i < n:
        schluessel, i = _schluessel_lesen(rumpf, i)
        if not schluessel or i >= n:
            break
        args[schluessel], i = _wert_lesen(rumpf, i)
        while i < n and rumpf[i] in " \t\r\n":
            i += 1
        if i < n and rumpf[i] == ",":
            i += 1
    return args


def _aufruf_aus_text(text: str,
                     zusatznamen: Optional[Set[str]] = None) -> Optional[Tuple[str, dict]]:
    """Erkennt einen Werkzeugaufruf, den das Modell als PROSA geschrieben hat.

    Manchmal ruft ein Modell das Werkzeug nicht auf, sondern schreibt den
    Aufruf hin:

        notiz_lesen(name="_System/Code/Pruefungen/Code-Prüfung 2026-08-25")

    Der Werkzeugkanal bleibt dabei leer, die Schleife hält das für die fertige
    Antwort und zeigt dem Nutzer eine Zeile Quelltext. Für ihn sieht das aus,
    als hätte der Agent aufgegeben - dabei stand die Absicht vollständig da.

    Zwei Formen werden angenommen:

    1. Der Aufruf ist die GANZE Antwort - auch über viele Zeilen. Genau das
       kam bei "erstelle eine Fertigkeit" vor: fertigkeit_definieren() mit
       dem kompletten Quelltext im code-Parameter. Die alte Fassung verwarf
       jede Antwort mit einem Zeilenumbruch und ließ den Auftrag ins Leere
       laufen.
    2. Die Antwort endet mit EINEM ```-Block, der nur aus diesem Aufruf
       besteht ("Ich lege sie an:\\n```\\nfertigkeit_definieren(...)\\n```").

    Nicht angenommen wird ein Aufruf mitten im Fließtext ("dafür nimmst du
    notiz_lesen(...)") und ein Codeblock, den die Prosa davor ausdrücklich
    als Beispiel ankündigt.

    zusatznamen: Werkzeuge, die es nur zur Laufzeit gibt - vor allem
    'fertigkeit_definieren' und die selbstgebauten Fertigkeiten. Sie stehen
    nicht in TOOL_NAMES; ohne sie wurde genau der Aufruf verworfen, mit dem
    sich der Agent eine neue Fertigkeit anlegen wollte.
    """
    erlaubt = set(TOOL_NAMES) | set(zusatznamen or ())
    roh = (text or "").strip()
    if not roh:
        return None

    kandidaten: List[str] = [_zaun_entfernen(roh)]

    # Fall 2: Prosa, dann ein abschließender Codeblock.
    zaeune = list(re.finditer(r"```[A-Za-z0-9_+\-]*[ \t]*\r?\n(.*?)\r?\n?```",
                              roh, re.DOTALL))
    if len(zaeune) == 1 and roh.endswith("```"):
        vorspann = roh[:zaeune[0].start()]
        if not _NUR_BEISPIEL.search(vorspann):
            kandidaten.append(zaeune[0].group(1).strip())

    for kandidat in kandidaten:
        if not kandidat or len(kandidat) > _AUFRUF_MAX:
            continue
        treffer = re.fullmatch(r"([a-z_][a-z0-9_]*)\s*\((.*)\)\s*", kandidat, re.DOTALL)
        if not treffer:
            continue
        name = treffer.group(1)
        if name not in erlaubt:
            continue
        args = _argumente_lesen(treffer.group(2))
        if args:
            return name, args
    return None


def _json_objekt_finden(text: str) -> Optional[str]:
    """Der erste vollständige {...}-Block in text - klammertief gezählt,
    Anführungszeichen respektiert (ein '{' oder '}' MITTEN in einer
    'beschreibung' darf die Zählung nicht durcheinanderbringen)."""
    start = text.find("{")
    if start == -1:
        return None
    tiefe = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            tiefe += 1
        elif ch == "}":
            tiefe -= 1
            if tiefe == 0:
                return text[start:i + 1]
    return None


# Felder, deren gemeinsames Auftreten mit 'schritte' eine Fertigkeitsdefinition
# eindeutig genug erkennbar macht - kein Notiz-Frontmatter oder Werkzeug-
# ergebnis trägt zufällig dieselbe Kombination.
_FERTIGKEIT_STUETZEN = {"name", "fertigkeit", "beschreibung", "description", "parameter"}


def _fertigkeit_json_aus_text(text: str) -> Optional[dict]:
    """Erkennt eine Fertigkeitsdefinition, die als BLOSSES JSON geschrieben
    wurde - ohne fertigkeit_definieren(...)-Hülle und ohne echten Aufruf.

    Der Anlass steht wörtlich im Protokoll vom 03.09.: Auf "erstelle diese
    Fertigkeit" antwortete das Modell mit genau dem Argumente-Objekt
    ({"name": …, "beschreibung": …, "parameter": […], "schritte": […]}),
    als wäre das Zeigen der Struktur schon die Ausführung. _aufruf_aus_text
    erkennt das nicht, weil die Hülle 'fertigkeit_definieren(...)' fehlt -
    das Modell hat nur die Argumente geliefert, nie den Funktionsnamen davor.
    Ohne diese Erkennung blieb die Antwort folgenlos: keine Fertigkeit, keine
    Fehlermeldung, nichts - und "wurde die Fertigkeit erstellt?" musste mit
    "Nein" beantwortet werden, ohne dass klar wurde, warum.
    """
    roh = (text or "").strip()
    if not roh:
        return None
    kandidaten = [_zaun_entfernen(roh)]
    block = _json_objekt_finden(roh)
    if block:
        kandidaten.append(block)

    for kandidat in kandidaten:
        if not kandidat or len(kandidat) > _AUFRUF_MAX:
            continue
        try:
            daten = json.loads(kandidat)
        except ValueError:
            continue
        if not isinstance(daten, dict):
            continue
        schritte = daten.get("schritte")
        if not isinstance(schritte, list) or not schritte:
            continue
        if not (_FERTIGKEIT_STUETZEN & set(daten.keys())):
            continue
        return {
            "name": daten.get("name") or daten.get("fertigkeit") or "",
            "beschreibung": daten.get("beschreibung") or daten.get("description") or "",
            "parameter": daten.get("parameter") or [],
            "schritte": schritte,
        }
    return None


def _parse_action(text: str) -> Optional[Tuple[str, dict]]:
    """Findet einen Werkzeugaufruf im JSON-Protokoll (Rückfallebene)."""
    kandidaten: List[str] = [m.group(1) for m in ACTION_RE.finditer(text or "")]
    if not kandidaten:
        stripped = (text or "").strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            kandidaten = [stripped]
        else:
            match = re.search(r"\{[^{}]*\"werkzeug\"\s*:.*\}", text or "", re.DOTALL)
            if match:
                kandidaten = [match.group(0)]
    for kandidat in kandidaten:
        try:
            data = json.loads(kandidat)
        except ValueError:
            continue
        name = data.get("werkzeug") or data.get("tool") or data.get("name")
        if not name or str(name).lower() in ("keine", "none", "null"):
            continue
        params = data.get("parameter") or data.get("parameters") or data.get("arguments") or {}
        if not isinstance(params, dict):
            params = {}
        return str(name), params
    return None
