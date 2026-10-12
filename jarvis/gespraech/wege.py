"""Die Wege VAULT, RECHERCHE und WERKZEUG: was jeder darf und wie er endet.

Die Triage (triage.py) waehlt den Weg; hier wird er eingerichtet. Jeder Weg
ENGT EIN - welche Werkzeuge das Modell ueberhaupt angeboten bekommt, wie
viele Schritte es hat. Erlauben kann ein Weg nichts: Was er zulaesst, geht
weiter durch Rechte, Freigabe und Werkzeugtor.

    VAULT       lesende Werkzeuge ohne Netz; danach Synthesepflicht
                (die Schlussrunde verlangt den Inhalt, nicht Notiznamen)
    RECHERCHE   nur web_suchen/web_lesen; vorher Netzpruefung - ohne Netz
                "kann ich nicht", NIE ersatzweise die Vault
    WERKZEUG    lesende Werkzeuge ohne Suche, genau EIN Schritt, dann Antwort; scheitert
                der Aufruf, steht das in der Antwort
    SCHREIBEN   keine Einschraenkung - der unveraenderte alte Pfad
    DIREKT      gar keine Werkzeuge (direkt.py)
"""
from __future__ import annotations

import socket
import time
from typing import Sequence

from ..tools import NUR_LESENDE_WERKZEUGE
from .antwort import Answer, ToolCallRecord
from .triage import TriageErgebnis, Weg

NETZWERKZEUGE = frozenset({"web_suchen", "web_lesen"})
LESEND_OHNE_NETZ = frozenset(NUR_LESENDE_WERKZEUGE) - NETZWERKZEUGE
# Eine EINZELNE deterministische Auskunft (zaehlen, Struktur, Verweise)
# braucht keine semantische Suche - die waere ein Vektorabruf durch die
# Hintertuer, und ihr Ergebnis ist eine Trefferliste, keine Auskunft.
AUSKUNFT = LESEND_OHNE_NETZ - {"notizen_suchen", "verwandte_finden", "code_suchen"}

# Wohin die Netzpruefung klopft - dieselbe Quelle, die web_suchen benutzt.
# Nur ein TCP-Verbindungsaufbau, es wird nichts gesendet.
_NETZ_ZIEL = ("api.duckduckgo.com", 443)
_NETZ_FRIST = 2.0
_NETZ_MERKEN = 60.0          # Sekunden, so lange gilt ein Ergebnis


class WegeTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    _netz_stand = None       # (zeitpunkt, erreichbar)
    _ohne_vektorabruf = False   # je Runde, von ask() gesetzt

    def _weg_einrichten(self, wahl: TriageErgebnis) -> None:
        """Setzt Werkzeugauswahl und Schrittzahl fuer diese Runde."""
        if wahl.ausdruecklich or wahl.weg is Weg.SCHREIBEN:
            self._weg_werkzeuge, self._weg_schritte = None, None
        elif wahl.weg is Weg.RECHERCHE:
            self._weg_werkzeuge, self._weg_schritte = set(NETZWERKZEUGE), None
        elif wahl.weg is Weg.WERKZEUG:
            self._weg_werkzeuge, self._weg_schritte = set(AUSKUNFT), 1
        else:                                   # VAULT
            self._weg_werkzeuge, self._weg_schritte = set(LESEND_OHNE_NETZ), None

    # ------------------------------------------------------------ Netz
    def netz_pruefen(self) -> bool:
        """Ist Netz da? Abgeschaltet zaehlt als nein. Gemerkt fuer eine Minute."""
        if not getattr(self.cfg, "web_enabled", True):
            return False
        stand = self._netz_stand
        if stand and time.time() - stand[0] < _NETZ_MERKEN:
            return stand[1]
        try:
            with socket.create_connection(_NETZ_ZIEL, timeout=_NETZ_FRIST):
                erreichbar = True
        except OSError:
            erreichbar = False
        self._netz_stand = (time.time(), erreichbar)
        return erreichbar

    def _ohne_netz_antworten(self, question: str, folder_filter) -> Answer:
        """Die ehrliche Antwort, wenn Recherche verlangt ist und kein Netz da.

        Kein Modellaufruf, kein Abruf. Bietet der Nutzer danach an, dass
        Vault-Wissen reicht, ist das eine neue Runde mit neuer Triage.
        """
        grund = ("abgeschaltet (WEB_ENABLED=false)"
                 if not getattr(self.cfg, "web_enabled", True)
                 else "gerade nicht erreichbar")
        text = (f"Dafür bräuchte ich Netzzugang, und der ist {grund}. In deinen "
                f"Notizen suche ich ersatzweise nicht – dort stehen solche "
                f"Angaben nicht, und eine Vault-Suche als Recherche auszugeben "
                f"wäre falsch. Wenn dir reicht, was in deinen Notizen steht, "
                f"sag es, dann sehe ich dort nach.")
        runde = self._runde()
        runde.endete_mit = "hinweis"
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-2 * self.cfg.history_turns:]
        return Answer(text=text, hits=[], folder_filter=folder_filter,
                      kind="recherche", tool_calls=[])

    def _recherche_belegen(self, text: str, calls: Sequence[ToolCallRecord]) -> str:
        """Weg RECHERCHE: Lief keine erfolgreiche Netzsuche, steht das da.

        Gemessen am echten Modell (Messlauf zu Stufe 4): Es antwortete ohne
        einen einzigen Werkzeugaufruf "Ich habe im Internet ... gesucht".
        Dieselbe Regel wie beim Tatsachenbericht - gemessen schlaegt
        behauptet: Ob recherchiert wurde, entscheidet das Protokoll der
        Aufrufe, nicht die Prosa.
        """
        if any(c.werkzeug in NETZWERKZEUGE and c.ergebnis.ok for c in calls):
            return text
        return ((text or "").rstrip()
                + "\n\n*Hinweis: In dieser Runde lief keine erfolgreiche "
                  "Netzsuche. Was oben steht, stammt nicht aus einer Recherche, "
                  "sondern aus dem Modellwissen – bitte entsprechend prüfen.*")

    # -------------------------------------------------------- WERKZEUG
    def _werkzeug_fehlschlag_anhaengen(self, text: str,
                                       calls: Sequence[ToolCallRecord]) -> str:
        """Weg WERKZEUG: Ist der eine Aufruf gescheitert, sagt die Antwort das.

        Deterministisch aus dem Ergebnis, wie der Tatsachenbericht - ein
        Modell, das "hier ist die Anzahl" schreibt, obwohl das Werkzeug
        scheiterte, bekommt hier widersprochen. Kein zweiter Versuch mit
        einem anderen Werkzeug.
        """
        gescheitert = [c for c in calls if not c.ergebnis.ok]
        if not gescheitert:
            return text
        c = gescheitert[0]
        meldung = str(c.ergebnis.meldung or "").split(". ")[0][:200].rstrip(". ")
        return ((text or "").rstrip()
                + f"\n\n*Das Werkzeug `{c.werkzeug}` ist gescheitert: {meldung}.*")
