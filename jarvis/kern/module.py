"""Die Module, mit denen der Kern die vorhandenen Teile führt.

Wichtig ist, was hier NICHT passiert: Kein Modul schreibt Fachlogik neu. Der
Agent bleibt der Agent, die Werkzeuge bleiben die Werkzeuge, das
Sitzungsgedächtnis bleibt, wo es ist. Ein Modul ist eine Fassung um etwas
Vorhandenes - sie meldet es am Kern an, gibt Auskunft über seinen Zustand und
hängt es an den Ereignisbus.

Der Gewinn steckt genau darin und nirgends sonst:

* Ein Teil, das nicht hochkommt, ist benannt statt still kaputt. Bisher
  scheiterte etwa der Index beim Start mit einer Ausnahme mitten im Hochlauf.
* Wartung, Verdichtung und Autolauf laufen über den Planer, also mit
  Vault-Sperre, Abbruch, Wiederholung und Protokoll - statt irgendwo als
  freilaufender Thread.
* Der Zustand ist an einer Stelle abfragbar: `/kern`.

Die Module in der Reihenfolge, in der sie hochfahren:

    gedaechtnis     Vault, Index, Sitzung - alles andere braucht es
    verarbeitung    Ollama. Ausdrücklich NICHT wesentlich (siehe unten)
    fertigkeiten    beide Arten, Ablauf und Verhalten
    chat            der Agent selbst
    autonomie       Wartung, Verdichten, Autolauf - nur ab Rechtestufe 2
    selbstentwicklung  Vorschläge am eigenen Code, in Quarantäne
"""
from __future__ import annotations

import time
from typing import Optional

from .auftrag import HINTERGRUND, NORMAL
from .modul import Befund, Modul


class GedaechtnisModul(Modul):
    """Vault, Index und Sitzungsgedächtnis - die Grundlage von allem."""

    name = "gedaechtnis"
    beschreibung = "Vault, Index und Sitzungsgedächtnis"
    wesentlich = True

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.letzter_abgleich = 0.0

    def _starten(self, kern):
        # Der Abgleich gehört hierher und nicht in den Agenten: Wenn die Vault
        # nicht lesbar ist, soll das beim Hochfahren als gestörtes Modul
        # dastehen und nicht als Ausnahme beim ersten Suchen.
        bericht = self.agent.sync()
        self.letzter_abgleich = time.time()
        kern.bus.melden("gedaechtnis.abgeglichen", quelle=self.name,
                        notizen=len(self.agent.index.notes),
                        bericht=str(bericht)[:120])

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        notizen = len(self.agent.index.notes)
        zahlen = {
            "notizen": notizen,
            "ordner": len(getattr(self.agent.structure, "folder_paths", []) or []),
            "abgleich_vor": f"{int(time.time() - self.letzter_abgleich)}s",
        }
        if notizen == 0:
            # Eine leere Vault ist kein Fehler, aber fast immer ein falsch
            # gesetzter Pfad - und das sieht man sonst erst daran, dass jede
            # Antwort nichts findet.
            return Befund(False, f"Der Index ist leer. Zeigt OBSIDIAN_VAULT wirklich "
                                 f"auf die Vault? ({self.agent.cfg.vault_path})", zahlen)
        return Befund(True, f"{notizen} Notizen im Index", zahlen)


class VerarbeitungsModul(Modul):
    """Ollama - Recherche und Formulierung, ausdrücklich nicht das Grundsystem.

    Josh hat das am 04.09. so vorgegeben, und es deckt sich mit dem, was das
    Projekt seit v4 gelernt hat: Alles, was sicher sein muss, läuft
    deterministisch in Python - Planer, Werkzeugprüfung, Fertigkeitsvalidierung,
    Layout. Das Modell recherchiert, formuliert und schlägt vor.

    Deshalb ist dieses Modul NICHT `wesentlich`. Ist Ollama nicht erreichbar,
    läuft Zeus weiter: Suchen, Lesen, Zeichnen, Listen, Wartung, alle
    Slash-Befehle. Nur die formulierten Antworten fehlen. Ein System, das ohne
    sein Sprachmodell gar nicht erst startet, hat das Sprachmodell zum
    Grundsystem gemacht - genau das soll es nicht sein.
    """

    name = "verarbeitung"
    beschreibung = "Ollama für Recherche und Formulierung (nicht das Grundsystem)"
    wesentlich = False
    abhaengig_von = ("gedaechtnis",)

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.erreichbar = False
        self.modell = ""
        self.letzter_fehler = ""

    def _starten(self, kern):
        self.modell = getattr(self.agent.cfg, "llm_model", "")
        try:
            # model_capabilities() wirft nicht, sondern gibt bei Nichterreichen
            # eine leere Liste zurück - deshalb wird HIER auf leer geprüft und
            # nicht auf eine Ausnahme gewartet. Genau diese Art stiller
            # Rückgabe hat in v4 dazu geführt, dass ein Modell ohne
            # Werkzeugunterstützung monatelang trotzdem 'tools' bekam.
            faehig = list(self.agent.llm.model_capabilities() or [])
            self.erreichbar = bool(faehig)
            if self.erreichbar:
                kern.bus.melden("verarbeitung.bereit", quelle=self.name,
                                modell=self.modell, faehigkeiten=faehig)
            else:
                self.letzter_fehler = "keine Auskunft von /api/show"
                kern.bus.melden("verarbeitung.nicht_erreichbar", quelle=self.name,
                                modell=self.modell, grund=self.letzter_fehler)
        except Exception as exc:
            # NICHT werfen: Das Modul soll laufen und "nicht erreichbar"
            # melden. Ein gestörtes Modul würde alles Abhängige mitreißen -
            # und abhängig ist hier nichts, weil nichts abhängig sein soll.
            self.erreichbar = False
            self.letzter_fehler = f"{type(exc).__name__}: {exc}"
            kern.bus.melden("verarbeitung.nicht_erreichbar", quelle=self.name,
                            modell=self.modell, grund=self.letzter_fehler)

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        zahlen = {"modell": self.modell, "host": getattr(self.agent.cfg, "host", "")}
        if not self.erreichbar:
            return Befund(False, f"Ollama nicht erreichbar ({self.letzter_fehler}). "
                                 f"Zeus läuft weiter – ohne formulierte Antworten.",
                          zahlen)
        return Befund(True, f"Modell {self.modell} erreichbar", zahlen)


class FertigkeitenModul(Modul):
    """Beide Arten Fertigkeit - Abläufe und Verhalten."""

    name = "fertigkeiten"
    beschreibung = "Selbstgebaute Abläufe und Verhaltensregeln"
    abhaengig_von = ("gedaechtnis",)

    def __init__(self, agent):
        super().__init__()
        self.agent = agent

    def _starten(self, kern):
        self.agent.fertigkeiten.laden()
        self.agent.verhalten.laden()
        kaputt = list(self.agent.fertigkeiten.defekt) + list(self.agent.verhalten.defekt)
        if kaputt:
            # Gesperrte Fertigkeiten sind kein Startfehler - sie werden ja
            # gerade nicht ausgeführt. Aber sie gehören gemeldet, sonst
            # wundert man sich, warum ein Aufruf "unbekannt" heißt.
            kern.bus.melden("fertigkeiten.gesperrt", quelle=self.name, namen=kaputt)

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        ablauf = self.agent.fertigkeiten
        verhalten = self.agent.verhalten
        zahlen = {"ablauf": len(ablauf.alle), "verhalten": len(verhalten.alle),
                  "gesperrt": len(ablauf.defekt) + len(verhalten.defekt)}
        if zahlen["gesperrt"]:
            namen = list(ablauf.defekt) + list(verhalten.defekt)
            return Befund(False, f"{zahlen['gesperrt']} gesperrt: {', '.join(namen[:3])}"
                                 f" – Grund mit /fertigkeiten bzw. /verhalten", zahlen)
        return Befund(True, f"{zahlen['ablauf']} Abläufe, {zahlen['verhalten']} Verhalten",
                      zahlen)


class ChatModul(Modul):
    """Der Agent - das Gespräch."""

    name = "chat"
    beschreibung = "Das Gespräch: Fragen, Werkzeuge, Antworten"
    wesentlich = True
    abhaengig_von = ("gedaechtnis", "fertigkeiten")

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.fragen = 0
        self.werkzeugfehler = 0

    def _starten(self, kern):
        # Der Agent bekommt den Kern, damit er Arbeit einreihen kann statt
        # selbst Threads zu starten. Mehr Kopplung soll es nicht geben.
        self.agent.kern = kern
        self.horchen("auftrag.gescheitert", self._auftrag_gescheitert)

    def _anhalten(self) -> None:
        # Beim Herunterfahren die Sitzung verdichten - sonst ist der letzte
        # Gesprächsabschnitt beim nächsten Start weg. Das ist der häufigste
        # Weg, auf dem Sitzungsgedächtnis verloren geht.
        try:
            if getattr(self.agent.cfg, "session_memory", False):
                self.agent.sitzung.verdichten(grund="Herunterfahren")
        except Exception:
            pass

    def _auftrag_gescheitert(self, ereignis) -> None:
        # Ein gescheiterter Hintergrundauftrag gehört auf die Aufgabenliste,
        # sonst verschwindet er im Protokoll. Dieselbe Regel wie bei
        # fehlgeschlagenen Werkzeugaufrufen im Gespräch.
        try:
            self.agent.aufgaben.aus_fehlschlag(
                f"auftrag:{ereignis.daten.get('art', '?')}",
                {"auftrag": ereignis.daten.get("auftrag")},
                str(ereignis.daten.get("grund", ""))[:200])
        except Exception:
            pass

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        offen = 0
        try:
            offen = len(self.agent.aufgaben.offene())
        except Exception:
            pass
        return Befund(True, "bereit", {"verlauf": len(self.agent.history) // 2,
                                       "offene_punkte": offen})


class AutonomieModul(Modul):
    """Was ohne Eingabe geschieht - und nur ab Rechtestufe 2.

    Die Rechteprüfung ist der Kern dieses Moduls. Bisher hing unbeaufsichtigtes
    Arbeiten daran, ob jemand `--autolauf` startete. Jetzt hängt es an einer
    Stufe, die in der `.env` steht - und die Zeus sich nicht selbst geben kann.
    """

    name = "autonomie"
    beschreibung = "Wartung, Verdichtung und Autolauf im Hintergrund"
    abhaengig_von = ("gedaechtnis", "chat")

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.geplant = []
        self.abgelehnt = ""

    def _starten(self, kern):
        takt = int(getattr(self.agent.cfg, "wartung_alle", 0) or 0)
        if takt <= 0:
            self.abgelehnt = "abgeschaltet (WARTUNG_ALLE=0)"
            return
        if not kern.rechte.verlangen("wartung", "regelmäßige Vault-Wartung"):
            # Kein Startfehler. Das Modul läuft und sagt, warum es nichts tut -
            # das ist der Unterschied zwischen "kaputt" und "darf nicht".
            self.abgelehnt = kern.rechte.grund("wartung")
            return
        auftrag = kern.planer.auftrag(
            "wartung", "Vault prüfen und Links reparieren", self._wartung,
            prioritaet=HINTERGRUND, braucht_vault=True,
            faellig_ab=time.time() + takt, wiederholen_alle=takt, quelle=self.name)
        self.geplant.append(auftrag.id)

    def _wartung(self, auftrag):
        auftrag.haltepunkt()
        ergebnis = self.agent.tools.ausfuehren("vault_wartung", {"reparieren": True})
        auftrag.haltepunkt()
        if getattr(self.agent.cfg, "session_memory", False) and self.agent.sitzung.faellig():
            self.agent.sitzung.verdichten(grund="Wartung")
        # Die Bilanz zählt für die Bewährung. Nur so kann aus "es lief 20-mal
        # gut" später ein Antrag auf mehr Rechte werden - mit Zahlen.
        if self.kern:
            self.kern.rechte.vermerken(bool(ergebnis.ok))
        return {"ok": ergebnis.ok, "meldung": ergebnis.meldung}

    def gesundheit(self) -> Befund:
        grund = super().gesundheit()
        if not grund.ok:
            return grund
        if self.abgelehnt:
            return Befund(True, f"ruht: {self.abgelehnt}", {"auftraege": 0})
        return Befund(True, "Wartung eingeplant", {"auftraege": len(self.geplant)})
