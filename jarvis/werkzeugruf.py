"""`[werkzeug]` in der Eingabe heißt: DIESES Werkzeug, jetzt, ohne Rückfrage.

Der Anlass steht wörtlich in einem Protokoll vom 01.09.: Auf `[auto_verlinken]`
antwortete Zeus mit

    "In den mir vorliegenden Werkzeugergebnissen taucht kein Funktions- oder
     Modulname `auto_verlinken` auf."

und auf `[verwandte_finden]` sogar

    "das ist kein Werkzeug, das ich hier aufrufen kann"

Beides ist **falsch** - beide Werkzeuge stehen im Katalog, `auto_verlinken`
sogar seit v6. Die eckigen Klammern waren reine Oberflächenkosmetik: Die
Eingabe ging als gewöhnlicher Text ans Modell, das Modell hielt den Namen für
ein Zitat und philosophierte darüber.

Die Antwort darauf ist dieselbe wie bei allem anderen in diesem Projekt:
**Die Schrittfolge gehört in Python, nicht in den Prompt.** Wer ein Werkzeug in
eckige Klammern setzt, hat sich entschieden. Das wird hier erkannt und
ausgeführt, bevor das Modell die Eingabe überhaupt sieht.

Vier Formen:

    [auto_verlinken]                       - ohne Argumente
    [auto_verlinken(ordner="_System/Code")]  - mit Argumenten
    [notiz_lesen(name=Planer)]             - Anführungszeichen sind optional
    [meine_fertigkeit]                     - eigene Fertigkeiten zählen mit

Abgegrenzt wird gegen `[[Wikilinks]]`: doppelte Klammern sind Notizverweise
und werden nicht angefasst.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# Einfache eckige Klammer, KEINE doppelte. Der Name muss wie ein
# Werkzeugname aussehen; alles andere ist gewöhnlicher Text in Klammern.
RUF_RE = re.compile(
    r"(?<!\[)\[([a-zäöüß_][a-zäöüß0-9_]{2,})\s*(\(([^\[\]]*)\))?\](?!\])",
    re.IGNORECASE,
)


class Werkzeugruf:
    """Ein ausdrücklich gewünschter Werkzeugaufruf aus der Eingabe."""

    __slots__ = ("name", "argumente", "roh", "bekannt")

    def __init__(self, name: str, argumente: Dict[str, str], roh: str, bekannt: bool):
        self.name = name
        self.argumente = argumente
        self.roh = roh
        self.bekannt = bekannt

    def __repr__(self) -> str:                            # pragma: no cover
        return f"Werkzeugruf({self.name!r}, {self.argumente!r}, bekannt={self.bekannt})"


def _argumente_lesen(rumpf: str) -> Dict[str, str]:
    """`ordner="_System/Code", anzahl=8` → {"ordner": "...", "anzahl": "8"}.

    Anführungszeichen sind optional - beim Tippen setzt sie niemand.
    """
    args: Dict[str, str] = {}
    if not rumpf or not rumpf.strip():
        return args
    for schluessel, wert in re.findall(
            r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'
            r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^,]*)', rumpf):
        roh = wert.strip()
        if len(roh) >= 2 and roh[0] in "\"'" and roh[-1] == roh[0]:
            roh = roh[1:-1]
        args[schluessel] = roh.strip()
    return args


def finden(text: str, bekannte: Sequence[str]) -> List[Werkzeugruf]:
    """Alle `[werkzeug]`-Rufe im Text, in der Reihenfolge des Vorkommens.

    `bekannte` sind die Namen, die es wirklich gibt - Werkzeuge UND eigene
    Fertigkeiten. Ein unbekannter Name wird trotzdem zurückgegeben, aber mit
    bekannt=False: Der Nutzer soll erfahren, dass es das nicht gibt, statt
    eine erfundene Erklärung zu bekommen.
    """
    namen = {n.lower() for n in bekannte}
    rufe: List[Werkzeugruf] = []
    for treffer in RUF_RE.finditer(text or ""):
        name = treffer.group(1)
        rufe.append(Werkzeugruf(
            name=name,
            argumente=_argumente_lesen(treffer.group(3) or ""),
            roh=treffer.group(0),
            bekannt=name.lower() in namen,
        ))
    return rufe


def text_ohne_rufe(text: str, rufe: Sequence[Werkzeugruf]) -> str:
    """Die Eingabe ohne die Klammerausdrücke - das ist der eigentliche Auftrag.

    "alle code dateien [auto_verlinken]" wird zu "alle code dateien". Genau
    dieser Rest sagt, WORAUF sich der Aufruf beziehen soll.
    """
    rest = text or ""
    for ruf in rufe:
        rest = rest.replace(ruf.roh, " ")
    return re.sub(r"\s{2,}", " ", rest).strip()


def aehnliche(name: str, bekannte: Sequence[str], anzahl: int = 4) -> List[str]:
    """Namen, die dem gesuchten nahekommen - für eine ehrliche Fehlermeldung."""
    import difflib

    name = (name or "").lower()
    treffer = [n for n in bekannte if name in n.lower() or n.lower() in name]
    treffer += [n for n in difflib.get_close_matches(name, list(bekannte), n=anzahl,
                                                     cutoff=0.6) if n not in treffer]
    return treffer[:anzahl]


def argumente_ergaenzen(ruf: Werkzeugruf, parameter: Sequence[str],
                        ordner: str = "", rest: str = "") -> Dict[str, str]:
    """Fehlende Argumente aus dem Satz drumherum füllen - aber nur belegbar.

    "alle code dateien [auto_verlinken]" nennt kein `ordner=`, meint aber
    eindeutig einen. Wurde im Satz ein Ordner erkannt, wird er eingesetzt.
    Geraten wird nichts: Ohne erkannten Ordner bleibt der Parameter leer, und
    das Werkzeug entscheidet selbst, was sein Standard ist.
    """
    args = dict(ruf.argumente)
    if ordner and "ordner" in parameter and "ordner" not in args:
        args["ordner"] = ordner
    # Werkzeuge mit einem einzigen Textparameter (frage, thema, begriff …)
    # bekommen den Rest des Satzes - sonst müsste man ihn doppelt tippen.
    for feld in ("frage", "thema", "begriff", "suchbegriff"):
        if feld in parameter and feld not in args and rest:
            args[feld] = rest
            break
    return args
