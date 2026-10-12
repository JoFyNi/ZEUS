"""Die Bruecke zu den Slash-Befehlen - dieselben wie im Terminal, nicht nachgebaut.

Hiess bis eben `web/befehle.py`. Die eigene Architekturpruefung hat den Namen
angemeckert, und zu Recht: Es gab dann ZWEI Module namens `befehle.py`
(`jarvis/befehle.py` = der Katalog, `jarvis/web/befehle.py` = diese Bruecke).
Der Zyklenfinder meldete daraufhin ui -> befehle -> ui, und ein Mensch haette
sich beim Lesen genauso vertan.

Der naheliegende Weg wäre gewesen, `/hilfe`, `/werkzeuge`, `/lernen` und die
übrigen sechzig Befehle für die Oberfläche ein zweites Mal zu schreiben. Das
wäre falsch: Zwei Fassungen desselben Befehls driften auseinander, und die
zweite ist immer die schlechtere. Beim ersten Entwurf der Oberfläche gab es
gar keine - genau deshalb liefen `/help` und `/werkzeuge` dort ins Leere.

Stattdessen läuft `JarvisConsole._command` unverändert, nur mit einer
Rich-Konsole, die in einen Puffer statt ins Terminal schreibt. Was
herauskommt, ist der fertig gesetzte Text - Tabellen, Rahmen, Bäume und
alles - und der geht so in die Oberfläche.

Damit gilt: Jeder Befehl, den es im Terminal gibt, gibt es hier. Auch jeder,
der später dazukommt, ohne dass hier etwas geändert werden muss.
"""
from __future__ import annotations

import contextlib
import io
from typing import List, Optional

from ..config import settings

# Befehle, die ein echtes Terminal brauchen. Sie werden nicht stillschweigend
# ignoriert, sondern beantwortet - ein Befehl, der scheinbar nichts tut, ist
# schlimmer als einer, der sagt, warum er hier nicht geht.
NUR_TERMINAL = {
    "cls": "Der Bildschirm wird im Fenster über den Knopf oben rechts geleert.",
    "ansicht": "Die mitlaufende Anzeige gibt es nur im Terminal – hier zeichnet "
               "die Oberfläche ohnehin fortlaufend neu.",
    "live": "Die mitlaufende Anzeige gibt es nur im Terminal.",
    "hoeren": "Die Spracheingabe läuft über das Terminal. Starte Zeus dafür mit "
              "`python jarvis_run.py` ohne --ui.",
    "ende": "Zum Beenden das Fenster schließen.",
    "exit": "Zum Beenden das Fenster schließen.",
    "quit": "Zum Beenden das Fenster schließen.",
    "q": "Zum Beenden das Fenster schließen.",
}

BREITE = 104          # Zeichen. Passt zur Chatspalte im Fenster.


class Befehlsbruecke:
    """Führt einen Slash-Befehl aus und gibt seine Ausgabe als Text zurück."""

    def __init__(self, agent, cfg=settings):
        self.cfg = cfg
        self.agent = agent
        self._konsole = None
        self._puffer: Optional[io.StringIO] = None
        self._ui = None

    # ------------------------------------------------------------- Aufbau
    def _aufbauen(self):
        """Die Textoberfläche erst beim ersten Befehl bauen - sie kostet Zeit."""
        if self._ui is not None:
            return self._ui
        from ..ui import JarvisConsole

        self._puffer = io.StringIO()
        try:
            from rich.console import Console
            # force_terminal=False: keine ANSI-Fluchtfolgen im Puffer. Farben
            # gehen dabei verloren - dafür ist der Text sofort brauchbar, und
            # die Oberfläche setzt ihre eigenen Farben.
            self._konsole = Console(file=self._puffer, width=BREITE,
                                    force_terminal=False, soft_wrap=False,
                                    legacy_windows=False, no_color=True)
        except ImportError:
            # Ohne Rich druckt out() schlicht mit print(). Dann wird stdout
            # abgefangen - dieselben Befehle, nur ohne Tabellenrahmen.
            self._konsole = None
        self._ui = JarvisConsole(self.cfg, agent=self.agent,
                                 ausgabe=self._konsole, eingabe=False,
                                 terminal=False)
        return self._ui

    # ---------------------------------------------------------- Ausführen
    def kennt(self, name: str) -> bool:
        """Gibt es diesen Befehl? Antwort aus der Hilfetabelle, nicht geraten."""
        return name.lower() in self.namen()

    @staticmethod
    def namen() -> List[str]:
        """Alle Befehlsnamen - aus dem Katalog, nicht aus der Hilfetabelle.

        Vorher wurden sie mit einem regulaeren Ausdruck aus HELP gefischt.
        Das lief, solange HELP von Hand gepflegt war; seit der Katalog
        (`jarvis/befehle.py`) die Quelle ist, wird er direkt gefragt. Eine
        Liste, drei Verwender: Terminal, Bruecke, Oberflaeche.
        """
        from ..befehle import nach_name

        return sorted(set(nach_name()) | set(NUR_TERMINAL))

    def ausfuehren(self, zeile: str) -> dict:
        """Einen Befehl ausführen. Gibt {ok, text, beenden} zurück."""
        zeile = (zeile or "").strip()
        if not zeile.startswith("/"):
            return {"ok": False, "text": "Kein Befehl.", "beenden": False}

        name = zeile[1:].split(maxsplit=1)[0].lower() if len(zeile) > 1 else ""
        if name in NUR_TERMINAL:
            return {"ok": True, "text": NUR_TERMINAL[name], "beenden": False,
                    "nur_terminal": True}

        try:
            ui = self._aufbauen()
        except Exception as exc:                        # noqa: BLE001
            return {"ok": False, "beenden": False,
                    "text": f"Die Befehlsebene ließ sich nicht starten: {exc}"}

        self._puffer.seek(0)
        self._puffer.truncate(0)
        try:
            if self._konsole is None:
                with contextlib.redirect_stdout(self._puffer):
                    beenden = bool(ui._command(zeile))
            else:
                beenden = bool(ui._command(zeile))
        except Exception as exc:                        # noqa: BLE001
            # Ein Befehl darf die Oberfläche nicht mitreißen - dieselbe Regel
            # wie im Terminal, wo jeder Slash-Befehl in einer Schutzschicht
            # läuft.
            import traceback
            return {"ok": False, "beenden": False,
                    "text": (self._puffer.getvalue()
                             + f"\n[Fehler in /{name}] {type(exc).__name__}: {exc}\n"
                             + traceback.format_exc()[-800:])}
        text = self._puffer.getvalue().rstrip()
        if not text:
            text = f"/{name} ausgeführt (keine Ausgabe)."
        return {"ok": True, "text": text, "beenden": beenden}

    # Nach einem Befehl kann sich die letzte Antwort geändert haben
    # (/merke, /quellen). Die Oberfläche fragt sie darüber ab.
    @property
    def letzte_antwort(self):
        return getattr(self._ui, "last_answer", None)

    def antwort_uebernehmen(self, antwort) -> None:
        """Die Antwort aus dem Fenster auch der Befehlsebene bekannt machen.

        Ohne das sagt `/merke` nach einer im Fenster gestellten Frage
        "Es gibt noch keine Antwort zum Speichern" - obwohl sie da oben steht.
        """
        if self._ui is not None:
            self._ui.last_answer = antwort
