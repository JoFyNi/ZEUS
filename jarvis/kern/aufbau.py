"""Der Zusammenbau: welche Module in welcher Rolle am Kern hängen.

Diese Datei ist bewusst die einzige Stelle, an der Kern und Fachlogik
einander sehen. `kernel.py` kennt keine Notiz, `agent.py` kennt keinen
Scheduler - hier werden sie zusammengesteckt. Wer ein Modul hinzufügt, ändert
genau eine Datei.

Warum der Kern nicht im Konstruktor des Agenten entsteht: Ein Agent muss sich
auch ohne Kern bauen lassen. Jede Testreihe tut das, der Messstand baut
Dutzende davon, und keiner davon will Arbeiterthreads. Der Kern ist etwas,
das ein Programm HOCHFÄHRT, nicht etwas, das ein Objekt mitbringt.
"""
from __future__ import annotations

from .aufgabenmodul import AufgabenModul
from .kernel import Zeus
from .module import (AutonomieModul, ChatModul, FertigkeitenModul,
                     GedaechtnisModul, VerarbeitungsModul)
from .selbst import SelbstentwicklungsModul


def kern_fuer(agent) -> Zeus:
    """Baut den Kern für diesen Agenten, meldet die Module an und startet."""
    cfg = agent.cfg
    # `rechte_stufe` wird bewusst OHNE `or 1` gelesen. Mit ihm wurde die Null
    # - also die Lesestufe, die nichts aendern darf - stillschweigend zu
    # Stufe 1, und damit zu Schreibrechten. Eine Schranke, die bei ihrer
    # schaerfsten Einstellung aufgeht, ist schlimmer als keine: Wer
    # RECHTE_STUFE=0 setzt, verlaesst sich darauf.
    stufe = getattr(cfg, "rechte_stufe", 1)
    try:
        stufe = int(stufe)
    except (TypeError, ValueError):
        stufe = 1
    kern = Zeus(cfg=cfg,
                arbeiter=int(getattr(cfg, "kern_arbeiter", 2) or 2),
                rechte_ablage=cfg.work_dir / "rechte.json",
                rechte_stufe=stufe)

    # Ausnahmen von Ereignis-Zuhörern gehören ins Betriebsprotokoll und nicht
    # ins Nichts - sonst ist der Bus die erste Stelle im ganzen System, an der
    # ein Fehler still verschwindet.
    try:
        from ..protokoll import protokoll
        kern.bus.fehlermelder = lambda text: protokoll().fehler(text, "kern")
    except Exception:
        pass

    kern.anmelden(GedaechtnisModul(agent))
    kern.anmelden(VerarbeitungsModul(agent))
    kern.anmelden(FertigkeitenModul(agent))
    kern.anmelden(ChatModul(agent))
    # Der Aufgabenstapel kommt NACH dem Chat: Er haengt das gemeinsame
    # Werkzeugtor in die Gespraechsschleife, und die muss dafuer stehen.
    kern.anmelden(AufgabenModul(agent))
    kern.anmelden(AutonomieModul(agent))
    kern.anmelden(SelbstentwicklungsModul(agent))

    kern.starten()
    return kern
