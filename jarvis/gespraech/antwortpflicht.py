"""Antwortpflicht: Eine Runde endet mit einer Antwort - nie mit einem Protokoll.

Der Anlass (claude/PROMPT_Antwortpflicht_und_Triage.md, §1): Auf "wa steht
an?" kam viermal hintereinander

    Erledigt: Inhalt von 'Aufgabenliste_Autonomie_NN_Gehirn'; 3 Treffer ...

Die Regel dagegen stand laengst im Systemprompt ("Erledigt melden ist der
schlimmste Fehler") und half nicht. Eine Regel, die nur im Prompt steht, ist
eine Bitte. Deshalb hier im Code:

1. `ist_prosa_antwort` entscheidet, was als Antwort zaehlt.
2. Endet die Werkzeugschleife ohne eine solche, folgt EINE Schlussrunde ohne
   Werkzeugdefinitionen - das Modell kann dann nur noch antworten.
3. Bringt auch die nichts, bekommt der Nutzer den Grund in einem Satz, nicht
   die Liste der Aufrufe.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .aufrufe import _aufruf_aus_text, _parse_action

SCHLUSSRUNDE_ANWEISUNG = (
    "Du hast keine Werkzeugaufrufe mehr. Beantworte die Frage des Nutzers "
    "aus dem, was du bereits geholt hast - in ganzen Saetzen, mit dem Inhalt "
    "selbst, nicht mit den Namen der gelesenen Notizen. Sag in einem Satz, "
    "was offen geblieben ist.\n\nDie Frage war: {frage}"
)

# Werkzeugmeldungen, wie `_schlussbericht` sie aneinanderhaengt. Besteht ein
# Text NUR daraus, ist er ein Protokollauszug.
_MELDUNG = re.compile(
    r"(Inhalt\s+von\s+'[^']*'|\d+\s+Treffer\s+f(ü|ue)r\s+'[^']*'|"
    r"Notiz\s+'[^']*'\s+\w+|\[\[[^\]]*\]\]|`[^`]*`)", re.IGNORECASE)

# Rohes Aufruf-JSON: {"name": ..., "arguments": ...} oder tool_calls.
_ROHAUFRUF = re.compile(
    r'^\s*[\[{].*"(name|function|tool_calls|werkzeug|aktion)"\s*:', re.DOTALL)


def ist_prosa_antwort(text: Optional[str],
                      werkzeugnamen: Iterable[str] = ()) -> bool:
    """Zaehlt dieser Text als Antwort an einen Menschen?

    Nein, wenn er leer ist, mit "Erledigt:" beginnt, ein roher Werkzeugaufruf
    ist oder nach Abzug von Werkzeug- und Notiznamen nichts uebrig bleibt.
    """
    roh = str(text or "").strip()
    if not roh:
        return False
    if roh.lower().startswith("erledigt:"):
        return False
    if _ROHAUFRUF.match(roh):
        return False
    namen = set(werkzeugnamen or ())
    try:
        if _parse_action(roh):
            return False
        if _aufruf_aus_text(roh, namen):
            return False
    except Exception:
        pass
    rest = _MELDUNG.sub(" ", roh)
    woerter = [w for w in re.findall(r"[^\W\d_]{2,}", rest)
               if w not in namen]
    return len(woerter) >= 1


def ehrlicher_hinweis(anzahl: int, erfolgreich: int, frage: str,
                      grund: str = "", fehlschlag: str = "") -> str:
    """Was der Nutzer liest, wenn auch die Schlussrunde keine Antwort brachte.

    Der GRUND, nicht die Aufrufliste. Die Liste steht auf Wunsch im
    Laufbericht ("was hast du gerade gemacht?").
    """
    frage = str(frage or "").strip()
    offen = f"die Antwort auf „{frage[:160]}“" if frage else "die Antwort"
    if not erfolgreich:
        text = "Ich bin zu keiner Antwort gekommen. " + (fehlschlag or
                                                        "Kein Werkzeug hat ein Ergebnis geliefert.")
    else:
        text = (f"Ich habe {anzahl} Werkzeugaufruf{'e' if anzahl != 1 else ''} "
                f"gebraucht und bin nicht zu einer Antwort gekommen. "
                f"Offen ist: {offen}.")
    if grund:
        text += f" ({grund})"
    return text + " Frag gern enger nach - dann setze ich gezielt an."
