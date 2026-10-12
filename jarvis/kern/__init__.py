"""Der Kern: Zeus als Betriebssystem statt als Bündel von Modulen.

Bis hierher war Zeus eine Sammlung: `agent.py` kannte `tools.py`, `ui.py`
kannte `agent.py`, `autolauf.py` kannte `planner.py`. Das funktioniert,
solange alles nacheinander abläuft und von einer Eingabe angestoßen wird.

Es funktioniert NICHT mehr, sobald etwas gleichzeitig laufen soll, sobald
etwas ohne Eingabe geschehen soll, und sobald ein Teil ausfallen darf, ohne
den Rest mitzureißen. Genau das steht jetzt an. Deshalb dieser Kern - mit
vier Stücken und einer klaren Regel dazwischen:

    bus.py         Wer was erfährt. Module reden über Ereignisse, nicht
                   über Methodenaufrufe - sonst kennt am Ende wieder jeder
                   jeden.
    modul.py       Was ein Modul ist: Name, Abhängigkeiten, Start, Stopp,
                   Gesundheitsauskunft. Ein Modul, das beim Start scheitert,
                   gilt als gestört - der Kern läuft weiter.
    auftrag.py     Was Arbeit ist: eine Einheit mit Zustand, Priorität,
                   Versuchen und einem Abbruchschalter.
    scheduler.py   Wer wann arbeitet. Mehrere Arbeiter, aber nur einer
                   schreibt in die Vault.
    rechte.py      Was Zeus darf - und wie er sich mehr verdient, ohne es
                   sich selbst zu geben.
    kernel.py      Der Kern selbst: Module anmelden, hochfahren, herunter-
                   fahren, Auskunft geben.

Die Regel dazwischen: **Der Kern kennt keine Fachlogik.** Er weiß nichts von
Notizen, Ollama oder Fertigkeiten. Er startet Module, verteilt Aufträge und
gibt Auskunft. Alles Inhaltliche steht in den Modulen. Wer das aufweicht,
bekommt in einem halben Jahr denselben Klumpen zurück, nur mit mehr Ebenen.
"""
from .ablauf import Ablauffehler, Ablaufwerk
from .agenten import Agentenregister, Fachagent
from .aufgabenspeicher import (Aufgabe, Aufgabenspeicher, Ziel, Zustandsfehler,
                               BEREIT, FERTIG, GESCHEITERT, LAEUFT, NEU,
                               NEUPLANUNG, PRUEFUNG, WARTET_AUF_FREIGABE,
                               ZIEL_AKTIV, ZIEL_ERREICHT, ZIEL_GESCHEITERT)
from .ausfuehrung import Aufgabenlaeufer
from .budget import Budget, BudgetErschoepft
from .kontext import Kontext, Kontextwerk
from .plantypen import Planschritt, Zerlegung
from .planung import Zerleger
from .regelwerk import Regel, Regelwerk
from .sandbox import Sandkasten, Sandkastenlauf
from .warteschlange import Arbeiterstube
from .werkzeugrechte import Werkzeugart, Werkzeugmerkmal, merkmal
from .werkzeugtor import FreigabeNoetig, Werkzeugtor
from .zeitplan import Zeitgeber
from .auftrag import Auftrag, Auftragszustand
from .bus import Bus, Ereignis
from .kernel import Zeus
from .modul import Befund, Modul, Modulzustand
from .rechte import RECHTESTUFEN, Rechteverwaltung
from .scheduler import Auftragsplaner
from .verifikation import Pruefergebnis, Verifikation

__all__ = [
    "Ablauffehler", "Ablaufwerk", "Agentenregister", "Arbeiterstube",
    "Aufgabenlaeufer", "Budget", "BudgetErschoepft", "Fachagent",
    "FreigabeNoetig", "Kontext", "Kontextwerk", "Planschritt", "Regel", "Regelwerk", "Sandkasten",
    "Sandkastenlauf", "Werkzeugart", "Werkzeugmerkmal", "Werkzeugtor",
    "Zeitgeber", "Zerlegung", "Zerleger", "Ziel", "ZIEL_AKTIV",
    "ZIEL_ERREICHT", "ZIEL_GESCHEITERT", "merkmal",
    "Aufgabe", "Aufgabenspeicher", "Zustandsfehler",
    "Pruefergebnis", "Verifikation",
    "BEREIT", "FERTIG", "GESCHEITERT", "LAEUFT", "NEU", "NEUPLANUNG",
    "PRUEFUNG", "WARTET_AUF_FREIGABE",
    "Auftrag", "Auftragszustand", "Auftragsplaner", "Befund", "Bus",
    "Ereignis", "Modul", "Modulzustand", "RECHTESTUFEN", "Rechteverwaltung",
    "Zeus",
]
