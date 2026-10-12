"""Zerlegt agent.py in das Paket jarvis/gespraech/ - nach demselben Muster.

Wie bei tools.py: geschnitten wird nach dem Syntaxbaum, und danach wird
geprueft, dass jede Methode genau einmal verwendet wurde. Der Unterschied ist
das Risiko - agent.py ist der Pfad, den JEDE Frage nimmt. Deshalb bleibt hier
noch strenger, was war: dieselben Methoden, dieselben Namen, keine einzige
Zeile umformuliert.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

WURZEL = pathlib.Path("/root/zeus/jarvis_second_brain")
QUELLE = pathlib.Path("/root/zeus/agent_original.py")   # der Stand vor der Aufteilung
PAKET = WURZEL / "jarvis" / "gespraech"

text = QUELLE.read_text(encoding="utf-8")
zeilen = text.split("\n")
baum = ast.parse(text)


def schnitt(a: int, b: int) -> str:
    return "\n".join(zeilen[a - 1:b])


def block(knoten) -> tuple:
    """Zeilenbereich einschliesslich der Kommentare darueber."""
    start = knoten.lineno - 1
    if getattr(knoten, "decorator_list", None):
        start = min(d.lineno for d in knoten.decorator_list) - 1
    # Nach oben laufen, solange dort Kommentare stehen. Wurde KEINER
    # aufgenommen, bleibt es bei der def-Zeile selbst: 'i + 1' waere sonst die
    # Zeile davor - und die ist im Zweifel 'class JarvisAgent:'. Genau dieser
    # Fehler um eins hat beim ersten Lauf eine zweite, leere Klasse erzeugt,
    # die alle Mixins verdeckte.
    i = start - 1
    genommen = False
    while i >= 0:
        roh = zeilen[i].strip()
        if roh.startswith("#"):
            i -= 1
            genommen = True
            continue
        if not roh and i > 0 and zeilen[i - 1].strip().startswith("#"):
            i -= 1
            continue
        break
    return ((i + 1) if genommen else (start + 1)), knoten.end_lineno


klasse = next(k for k in baum.body
              if isinstance(k, ast.ClassDef) and k.name == "JarvisAgent")
methoden = {m.name: block(m) for m in klasse.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}

GRUPPEN = {
    "agent": [
        "__init__", "kern_starten", "kern_anhalten", "detect_capabilities",
        "native_tools", "tool_mode", "structure", "sync", "ask",
        "_sitzung_melden", "sitzung_abschliessen", "schrittmelder_setzen",
    ],
    "prompt": [
        "_gedaechtnisblock", "_gedaechtnis_melden", "system_prompt",
        "_fehlerbericht", "_build_context", "_extract_folder_filter",
        "_auf_budget_kuerzen",
    ],
    "werkzeuglauf": [
        "_tool_loop", "_werkzeugliste", "_laufzeitnamen", "_run_tool",
        "_aufruf_signatur", "_bekannte_werkzeuge", "_ausdrueckliche_rufe",
    ],
    "bericht": [
        "_tatsachen_anhaengen", "_zeichnungen_anhaengen",
        "_leere_antwort_erklaeren", "_verbindungshinweis",
    ],
    "ordner": [
        "_ordner_aus_text", "_structure_pfade", "resolve_target_folder",
        "_llm_pick_folder",
    ],
    "schreibweg": [
        "_plan_melder", "_plan_durchfuehren", "_plan_antwort", "write_note",
        "summarize_for_note",
    ],
}

verteilt = [n for g in GRUPPEN.values() for n in g]
if sorted(verteilt) != sorted(methoden):
    print("ABBRUCH – Aufteilung passt nicht.")
    print("  nicht zugeordnet:", sorted(set(methoden) - set(verteilt)))
    print("  gibt es nicht   :", sorted(set(verteilt) - set(methoden)))
    print("  doppelt         :", sorted({n for n in verteilt if verteilt.count(n) > 1}))
    sys.exit(1)

belegt = {}
for name, (a, b) in methoden.items():
    for z in range(a, b + 1):
        if z in belegt:
            print(f"ABBRUCH – Zeile {z}: '{belegt[z]}' und '{name}'.")
            sys.exit(1)
        belegt[z] = name

# Was ausserhalb der Klasse steht
klassen_ausser = [(k.name, *block(k)) for k in baum.body
                  if isinstance(k, ast.ClassDef) and k.name != "JarvisAgent"]
print(f"{len(methoden)} Methoden, {len(belegt)} Zeilen, keine Ueberschneidung.")
for g, n in GRUPPEN.items():
    print(f"  {g:<14} {len(n):>3} Methoden  "
          f"{sum(methoden[x][1]-methoden[x][0]+1 for x in n):>5} Zeilen")
print("Klassen ausserhalb:", klassen_ausser)

pathlib.Path("/root/zeus/agent_plan.json").write_text(json.dumps(
    {"methoden": {k: list(v) for k, v in methoden.items()}, "gruppen": GRUPPEN},
    ensure_ascii=False, indent=1), encoding="utf-8")
print("Plan geschrieben.")
