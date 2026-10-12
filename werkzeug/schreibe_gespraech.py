"""Schreibt jarvis/gespraech/ aus agent.py."""
from __future__ import annotations

import json
import pathlib

WURZEL = pathlib.Path("/root/zeus/jarvis_second_brain")
QUELLE = pathlib.Path("/root/zeus/agent_original.py")   # der Stand vor der Aufteilung
PAKET = WURZEL / "jarvis" / "gespraech"

zeilen = QUELLE.read_text(encoding="utf-8").split("\n")
plan = json.loads(pathlib.Path("/root/zeus/agent_plan.json").read_text(encoding="utf-8"))
methoden = {k: tuple(v) for k, v in plan["methoden"].items()}
gruppen = plan["gruppen"]


def schnitt(a: int, b: int) -> str:
    return "\n".join(zeilen[a - 1:b])


KOPF = '''from __future__ import annotations

import ast
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..config import settings
from ..index import Hit, KnowledgeIndex, SyncReport
from ..aufgaben import Aufgabenliste
from ..learning import BESTAETIGT, Lernwerk
from ..llm import OllamaClient, OllamaError, ToolsUnsupported
from ..planner import (PlanErgebnis, WritePlan, WritePlanner,
                      looks_like_skill_request, looks_like_write_request,
                      skill_art)
from ..sitzung import Sitzungsgedaechtnis
from ..skills import Fertigkeiten
from ..verhalten import Verhaltensfertigkeiten
from .. import werkzeugruf
from ..tools import (
    SCHREIBENDE_WERKZEUGE,
    TOOL_NAMES,
    TOOL_PARAMS,
    ToolResult,
    VaultTools,
    ollama_tool_definitions,
    tool_reference,
)
from ..vault import (Note, VaultStructure, fold, parse_note, safe_filename,
                     similarity)

from .antwort import Answer, FolderDecision, ToolCallRecord
from .aufrufe import (_argumente_lesen, _aufruf_aus_text,
                      _fertigkeit_json_aus_text, _parse_action)
from .texte import (ACTION_RE, AUTOMATIK_BEHAUPTUNG, ERFOLGS_BEHAUPTUNG,
                    FEHLERFRAGE, FOLDER_HINT_RE, GEDAECHTNIS_ANTEIL,
                    NUR_DEFINIERENDE_WERKZEUGE, STRUCTURE_PATTERNS,
                    VERAENDERNDE_WERKZEUGE, _WERKZEUG_HINWEISE,
                    _behauptet_erfolg, _extract_context, _fragt_nach_fehlern,
                    _ist_kurze_bestaetigung, _ist_wiederholung)
'''

BESCHREIBUNG = {
    "agent": ("Der Agent selbst: Aufbau, Faehigkeiten, und der Weg einer Frage.\n\n"
              "`ask()` ist die Hauptschlagader des Systems - jede Frage aus dem\n"
              "Terminal und aus der Oberflaeche laeuft hier durch. Was sie der\n"
              "Reihe nach tut, steht in der Codekarte unter 'Der Weg einer Frage'.\n\n"
              "Alles Umfangreiche daran liegt in den Nachbardateien und wird hier\n"
              "eingemischt: der Prompt in prompt.py, die Werkzeugschleife in\n"
              "werkzeuglauf.py, der Tatsachenbericht in bericht.py."),
    "prompt": ("Was das Modell zu lesen bekommt.\n\n"
               "Der Systemprompt ist der einzige Ort, an dem Persona, Regeln,\n"
               "Struktur, Gedaechtnis, Werkzeuge und Kontext zusammenkommen - und\n"
               "der einzige, an dem sie um denselben begrenzten Platz\n"
               "konkurrieren. Deshalb liegen die Budgetrechnung und das Kuerzen\n"
               "hier und nicht verstreut: Wer an einer Stelle mehr hineinschreibt,\n"
               "nimmt es woanders weg, und das soll man sehen koennen.\n\n"
               "Die Lehre aus v5 steht in `_auf_budget_kuerzen`: Ollama schneidet\n"
               "einen zu langen Prompt still am ANFANG ab - dort, wo die\n"
               "Anweisungen stehen."),
    "werkzeuglauf": ("Die Werkzeugschleife: vom Modellwunsch zum echten Aufruf.\n\n"
                     "Zwei Wege, weil nicht jedes Modell Werkzeuge kann: die native\n"
                     "Schnittstelle und das JSON-Protokoll als Rueckfallebene. Dazu\n"
                     "die Erkennung eines Aufrufs, den das Modell als TEXT\n"
                     "hingeschrieben hat, statt ihn zu taetigen - der haeufigste\n"
                     "Fall bei mittelgrossen Modellen und die Ursache dafuer, dass\n"
                     "eine Fertigkeit im Chat stand statt in der Vault.\n\n"
                     "Hier sitzt auch die Wiederholungssperre: Der zweite\n"
                     "buchstabengleiche Aufruf im selben Auftrag wird beantwortet\n"
                     "statt ausgefuehrt."),
    "bericht": ("Der Tatsachenbericht - was WIRKLICH passiert ist.\n\n"
                "Der wichtigste Grundsatz des Projekts in Codeform: Was der Code\n"
                "gemessen hat, steht ueber dem, was das Modell behauptet. Ein\n"
                "kleines Modell ueberliest ein 'ok: false' und schreibt trotzdem\n"
                "'erfolgreich verschoben'; der Bericht darunter widerspricht ihm\n"
                "mit den echten Rueckgabewerten."),
    "ordner": ("In welchen Ordner eine Notiz gehoert.\n\n"
               "Vier gewichtete Signale und eine Begruendung, die im Frontmatter\n"
               "landet. Die Begruendung ist kein Schmuck: Sie ist der einzige Weg,\n"
               "eine falsche Einsortierung spaeter zu verstehen statt nur zu\n"
               "aergern."),
    "schreibweg": ("Der deterministische Schreibweg und die Notizerzeugung.\n\n"
                   "Die Bruecke zum Planer: Absicht erkennen, Plan anfordern,\n"
                   "ausfuehren lassen, Ergebnis als Antwort formen."),
}

KLASSE = {
    "prompt": "PromptTeil",
    "werkzeuglauf": "WerkzeuglaufTeil",
    "bericht": "BerichtTeil",
    "ordner": "OrdnerTeil",
    "schreibweg": "SchreibwegTeil",
}

PAKET.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------- antwort.py
(PAKET / "antwort.py").write_text(
    '"""Die Datentypen einer Antwort: Werkzeugaufruf, Antwort, Ordnerentscheidung.\n\n'
    "Klein und ohne Abhaengigkeit ausser den Werkzeugen - damit jede andere\n"
    "Datei sie importieren kann, ohne einen Ring zu bauen.\n"
    '"""\nfrom __future__ import annotations\n\n'
    "from dataclasses import dataclass, field\n"
    "from typing import Dict, List, Optional, Sequence\n\n"
    "from ..index import Hit\n"
    "from ..tools import SCHREIBENDE_WERKZEUGE, ToolResult\n"
    "from ..vault import Note\n\n\n"
    + schnitt(105, 160) + "\n", encoding="utf-8")

# --------------------------------------------------------------- aufrufe.py
(PAKET / "aufrufe.py").write_text(
    '"""Einen Werkzeugaufruf aus Modellprosa herauslesen.\n\n'
    "Der Anlass ist einer der aeltesten Befunde des Projekts: Ein Modell ohne\n"
    "Werkzeug-Schnittstelle - und oft genug auch eines mit - schreibt den\n"
    "Aufruf als TEXT hin, statt ihn zu taetigen. Bis das erkannt wurde, sah\n"
    "der Nutzer eine huebsche Beschreibung dessen, was haette geschehen\n"
    "sollen, und in der Vault war nichts.\n\n"
    "Deshalb ein eigener, nachsichtiger Leser: Er nimmt Codezaeune, einfache\n"
    "und doppelte Anfuehrungszeichen, fehlende Kommata, Python- statt\n"
    "JSON-Wahrheitswerte - und er lehnt ab, was nur ein BEISPIEL ist. Der\n"
    "Unterschied zwischen 'ruf X so auf' und 'ich rufe X auf' ist genau der\n"
    "zwischen Erklaerung und Handlung.\n"
    '"""\nfrom __future__ import annotations\n\n'
    "import json\nimport re\nfrom typing import Dict, List, Optional, Set, Tuple\n\n"
    "from ..tools import TOOL_NAMES, TOOL_PARAMS\n\n\n"
    + schnitt(1764, 2061) + "\n", encoding="utf-8")

# ----------------------------------------------------------------- texte.py
(PAKET / "texte.py").write_text(
    '"""Muster und Konstanten - alles, was erkannt statt gerechnet wird.\n\n'
    "Jeder regulaere Ausdruck hier ist aus einem konkreten Fehlverhalten\n"
    "entstanden, und ueber jedem steht, aus welchem. Wer einen lockert, sollte\n"
    "den Fall darueber lesen: Er kommt zurueck.\n"
    '"""\nfrom __future__ import annotations\n\n'
    "import re\n\n"
    "from ..tools import SCHREIBENDE_WERKZEUGE\n\n\n"
    + schnitt(41, 103) + "\n\n\n"
    + schnitt(2064, 2108) + "\n", encoding="utf-8")

# ------------------------------------------------------------ Die Mixin-Teile
for gruppe, namen in gruppen.items():
    teile = [f'"""{BESCHREIBUNG[gruppe]}\n"""\n', KOPF, "\n"]
    if gruppe == "agent":
        continue                       # kommt unten als eigene Datei
    teile.append(f"\nclass {KLASSE[gruppe]}:\n"
                 f'    """Wird in JarvisAgent eingemischt - siehe agent.py."""\n\n')
    for name in namen:
        a, b = methoden[name]
        teile.append(schnitt(a, b) + "\n\n")
    (PAKET / f"{gruppe}.py").write_text("".join(teile).rstrip() + "\n", encoding="utf-8")

# ----------------------------------------------------------------- agent.py
teile = [f'"""{BESCHREIBUNG["agent"]}\n"""\n', KOPF,
         "\nfrom .bericht import BerichtTeil\n"
         "from .ordner import OrdnerTeil\n"
         "from .prompt import PromptTeil\n"
         "from .schreibweg import SchreibwegTeil\n"
         "from .werkzeuglauf import WerkzeuglaufTeil\n\n",
         "\nclass JarvisAgent(PromptTeil, WerkzeuglaufTeil, BerichtTeil, OrdnerTeil,\n"
         "                  SchreibwegTeil):\n"
         '    """Der Agent. Die Teile daneben sind nach Aufgabe getrennt, nicht\n'
         "    nach Zufall - siehe die Modulkoepfe.\n\n"
         "    Reihenfolge der Basisklassen: Sie ueberschneiden sich nicht (das\n"
         "    Aufteilungsskript prueft es), aber die Reihenfolge soll auch dann\n"
         "    noch stimmen, wenn jemand das vergisst.\n"
         '    """\n\n']
for name in gruppen["agent"]:
    a, b = methoden[name]
    teile.append(schnitt(a, b) + "\n\n")
(PAKET / "agent.py").write_text("".join(teile).rstrip() + "\n", encoding="utf-8")

print("geschrieben:", ", ".join(sorted(p.name for p in PAKET.glob("*.py"))))
