"""Der Weg DIREKT: antworten, ohne die Vault zu durchsuchen.

Fuer Gruesse, Meta-Fragen, Reflexion ueber das eigene Verhalten und Rueckfragen
zur eigenen letzten Antwort. Deren Antwort steht nie in den Notizen, sondern
im Gespraech - im Verlauf und im Laufbericht der letzten Runden.

Am Anlass (claude/PROMPT_Antwortpflicht_und_Triage.md, §1) bat der Nutzer
um eine Reflexion darueber, warum er keine Antwort bekam. Es wurden 13 Notizen
gelesen, und wieder kam keine. Eine Meta-Frage mit Vektorabruf zu beantworten
ist nicht nur teuer: Der Abruf zieht das Modell in die Notizen und damit weg
von der Frage.

Was DIREKT bekommt - und warum genau das:

    Verlauf         gekuerzt nach den bestehenden Regeln (_auf_budget_kuerzen)
    Laufbericht     deterministisch aus den Zaehlern (laufbericht.py). Ohne ihn
                    kann "warum hast du nicht geantwortet?" nur geraten werden.
    Selbstbild      aus Konfiguration und Werkzeugregister abgeleitet, nicht
                    handgepflegt - eine gepflegte Liste veraltet beim ersten
                    neuen Werkzeug.

Keine Werkzeuge, kein Abruf. Ein Gruss allein bekommt eine feste Antwort ohne
Modell (Strategie REGEL der Weiche).
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..llm import OllamaError
from ..tools import NUR_LESENDE_WERKZEUGE, SCHREIBENDE_WERKZEUGE
from .antwort import Answer
from .antwortpflicht import ist_prosa_antwort
from .triage import TriageErgebnis, Weg

_DANK = re.compile(r"^\s*(danke|dankesch(oe|ö)n|vielen\s+dank|merci)\b", re.IGNORECASE)
_ABSCHIED = re.compile(r"^\s*(tsch(ue|ü)ss|ciao|bis\s+(dann|sp(ae|ä)ter)|gute\s+nacht)\b",
                       re.IGNORECASE)
_GRUSS = re.compile(r"^\s*(hallo|hi|hey|moin|servus|guten\s+(morgen|tag|abend))\b"
                    r"[\s,.!?]*$", re.IGNORECASE)

# Wozu jeder Weg da ist - fuer das Selbstbild. Aus dem Enum abgeleitet, damit
# ein neuer Weg hier auffaellt (KeyError im Test), statt still zu fehlen.
_WEGE = {
    Weg.DIREKT: "Gruesse, Fragen ueber mich und Rueckfragen - ohne Suche, ohne Werkzeuge",
    Weg.VAULT: "Fragen zu deinen Notizen - suchen, lesen, dann zusammenfassen",
    Weg.RECHERCHE: "Weltwissen - nur ueber das Netz, nie ersatzweise aus der Vault",
    Weg.SCHREIBEN: "Notizen anlegen oder aendern - ueber den Schreibplaner",
    Weg.WERKZEUG: "eine einzelne Auskunft (zaehlen, Struktur, Verweise) - ein Aufruf",
}

_DIREKT_REGELN = """Du antwortest jetzt DIREKT: ohne Notizsuche und ohne Werkzeuge.
Die Frage betrifft dich selbst, dein Verhalten oder deine letzte Antwort. Die
Antwort steht im Gespraechsverlauf und im Laufbericht unten - nicht in der Vault.

REGELN:
1. Antworte in ganzen Saetzen. Kein "Erledigt:", keine Liste von Notiznamen.
2. Was du ueber dein eigenes Vorgehen sagst, muss im LAUFBERICHT stehen. Er ist
   aus der Telemetrie gebaut, nicht nacherzaehlt. Erfinde keine Schritte.
3. Zeigt der Laufbericht, dass eine Runde ohne Antwort endete, sag das offen,
   nenne den Grund aus dem Bericht und beantworte - wenn der Verlauf es
   hergibt - die urspruengliche Frage jetzt.
4. Weisst du etwas nicht, sag es. Rate nicht."""


class DirektTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    _triage_obj = None

    # ------------------------------------------------------------ Triage
    @property
    def triage(self):
        """Der Adapter auf Aufgabenbewerter und Strategieweiche.

        Laeuft der Kern, werden SEINE Instanzen benutzt - eine Einordnung,
        nicht zwei. Ohne Kern baut der Adapter eigene, regelbasierte.
        """
        if self._triage_obj is None:
            from .triage import Triage
            kern = getattr(self, "kern", None)
            self._triage_obj = Triage(
                cfg=self.cfg,
                bewerter=getattr(kern, "bewerter", None),
                weiche=getattr(kern, "weiche", None))
        return self._triage_obj

    def _triage_waehlen(self, question: str, tools_on: bool) -> TriageErgebnis:
        """Fragt die Triage und vermerkt die Wahl in der laufenden Runde.

        Die Signale fuer die Vorrangregeln 1 und 3 kommen aus denselben
        Mustern, die ask() schon vorher benutzte - `werkzeugruf.finden`,
        `looks_like_write_request`, `_ist_wiederholung`. Die Triage liest sie
        nur ab, statt sie nachzubauen.
        """
        from .. import werkzeugruf
        from ..planner import looks_like_skill_request, looks_like_write_request
        from .texte import _ist_kurze_bestaetigung, _ist_wiederholung

        ausdruecklich = bool(tools_on and werkzeugruf.finden(
            question, self._bekannte_werkzeuge()))
        verlauf = bool(self.history and self.history[-1].get("role") == "assistant")
        # "ja, mach das" / "ok" nach einem eigenen Vorschlag ist eine
        # Zustimmung zu einer Handlung, kein Gruss. Sie lief vor der Triage in
        # die Werkzeugschleife mit allen Werkzeugen (bzw. in den Fertigkeit-
        # Zweig) - und genau dorthin soll sie weiter: Weg SCHREIBEN, der
        # unveraenderte alte Pfad.
        bestaetigt = bool(verlauf and _ist_kurze_bestaetigung(question))
        schreibt = bool(looks_like_write_request(question)
                        or looks_like_skill_request(question))
        wiederholung = bool(_ist_wiederholung(question) and self.last_plan
                            and self.last_plan.fehlgeschlagen)
        try:
            wahl = self.triage.einordnen(
                question, verlauf=verlauf, ausdrueckliche_rufe=ausdruecklich,
                schreibauftrag=schreibt, wiederholung=wiederholung,
                bestaetigung=bestaetigt)
        except Exception as exc:                          # noqa: BLE001
            wahl = TriageErgebnis(Weg.VAULT, f"Triage warf {type(exc).__name__}",
                                  "rueckfall")
        runde = self._runde()
        runde.weg = wahl.weg.value
        runde.folge = wahl.folge.value if wahl.folge else ""
        runde.quelle = wahl.quelle
        runde.begruendung = wahl.begruendung
        return wahl

    def _danach_schreiben(self, question: str, antwort: Answer, folder_filter,
                          on_tool) -> Answer:
        """Der zweite Teil eines kombinierten Auftrags: die Notiz.

        Ueber den unveraenderten Schreibplaner. Als Kontext bekommt er die
        gerade gegebene Antwort - "notiere das" meint genau sie.
        """
        runde = self._runde()
        runde.phase = "schreiben"
        try:
            geschrieben = self._plan_durchfuehren(question, antwort.text, [],
                                                  folder_filter, on_tool)
        finally:
            runde.phase = ""
        if geschrieben is None:
            return Answer(text=antwort.text + "\n\n---\n*Die Notiz dazu konnte "
                          "ich nicht anlegen: Der Schreibplaner fand keinen "
                          "ausfuehrbaren Plan.*", hits=[], kind="direkt")
        text = antwort.text.rstrip() + "\n\n---\n" + (geschrieben.text or "")
        # _plan_antwort hat Frage und Planbericht schon in den Verlauf
        # geschrieben; der DIREKT-Zug davor steht dort auch. Zusammenlegen,
        # sonst stuende dieselbe Frage zweimal im Verlauf.
        if (len(self.history) >= 4 and self.history[-2].get("content") == question
                and self.history[-4].get("content") == question):
            del self.history[-4:-2]
        if self.history and self.history[-1].get("role") == "assistant":
            self.history[-1]["content"] = text
        runde.endete_mit = "direkt"
        return Answer(text=text, hits=[], kind="direkt",
                      tool_calls=list(geschrieben.tool_calls or []))

    # ------------------------------------------------------------ Antwort
    def _direkt_antworten(self, question: str, triage: TriageErgebnis,
                          stream_cb=None, gelernt: str = "") -> Answer:
        """Antwort ohne Abruf und ohne Werkzeuge. Endet immer mit Text."""
        runde = self._runde()
        text = self._feste_antwort(question)
        if not text:
            runde.phase = "direkt"
            messages: List[dict] = [
                {"role": "system", "content": self._direkt_prompt()}]
            messages.extend(self.history[-2 * self.cfg.history_turns:])
            messages.append({"role": "user", "content": question})
            messages, _ = self._auf_budget_kuerzen(messages, mit_werkzeugen=False)
            try:
                text = self.llm.chat(messages, stream_cb=stream_cb) or ""
            except OllamaError as exc:
                return Answer(text=self._verbindungshinweis(exc), hits=[],
                              kind="fehler")
            finally:
                runde.phase = ""
            if not (text or "").strip():
                # Leer ist ein eigener Fall mit eigener Abhilfe (Denkbudget,
                # Server) - dieselbe Erklaerung wie auf jedem anderen Weg.
                text = self._leere_antwort_erklaeren([])
            elif not ist_prosa_antwort(text, self._laufzeitnamen()):
                # Auch DIREKT darf nicht leer oder als Aufrufzeile enden. Was
                # dann bleibt, ist die Wahrheit aus den Zaehlern.
                text = ("Darauf habe ich gerade keine sinnvolle Antwort gefunden. "
                        "Was ich sicher sagen kann, steht im Laufbericht:\n\n"
                        + self.laufbericht.text())
            else:
                text = self._antwort_pruefen(text, question, Weg.DIREKT, [])
                text = self._gedaechtnisluecke_melden(text, question)
        runde.endete_mit = "direkt"
        if gelernt:
            text = f"*Verstanden – gemerkt: {gelernt}*\n\n" + text
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-2 * self.cfg.history_turns:]
        try:
            self.sitzung.zug_vermerken(question, text)
        except Exception:
            pass
        return Answer(text=text, hits=[], kind="direkt", tool_calls=[])

    def _feste_antwort(self, text: str) -> str:
        """Gruss, Dank, Abschied: eine feste Antwort, kein Modellaufruf."""
        titel = getattr(self.cfg, "user_title", "") or ""
        if _DANK.match(text) and len(text.split()) <= 4:
            return "Gern geschehen."
        if _ABSCHIED.match(text) and len(text.split()) <= 4:
            return f"Bis bald, {titel}." if titel else "Bis bald."
        if _GRUSS.match(text):
            return (f"Hallo {titel}. " if titel else "Hallo. ") + \
                "Was steht an – eine Frage zu deinen Notizen, etwas Neues " \
                "anlegen oder etwas nachschlagen?"
        return ""

    def _direkt_prompt(self) -> str:
        name = getattr(self.cfg, "assistant_name", "Zeus")
        titel = getattr(self.cfg, "user_title", "")
        # Das Arbeitsgedaechtnis gilt auch hier: "Antworte immer auf Englisch"
        # aus Zug 1 gilt fuer die Rueckfrage in Zug 9 genauso.
        return (f"Du bist {name}, der Wissensagent von {titel}.\n\n"
                + _DIREKT_REGELN
                + "\n\nSELBSTBILD (aus Konfiguration und Werkzeugregister):\n"
                + self.selbstbild()
                + "\n\nLAUFBERICHT DER LETZTEN RUNDEN (aus der Telemetrie):\n"
                + self.laufbericht.text()
                + self._arbeitsblock())

    def selbstbild(self) -> str:
        """Faehigkeiten, Wege, Grenzen - abgeleitet, nicht gepflegt."""
        cfg = self.cfg
        try:
            notizen = len(self.index.notes)
        except Exception:
            notizen = 0
        netz = bool(getattr(cfg, "web_enabled", True))
        zeilen = [
            f"- Modell: {getattr(cfg, 'llm_model', '?')}; Werkzeugmodus: "
            f"{'nativ' if self._native_tools else 'JSON-Protokoll' if self._native_tools is False else 'noch nicht ermittelt'}",
            f"- Vault: {notizen} Notizen",
            f"- Werkzeuge: {len(NUR_LESENDE_WERKZEUGE)} lesende, "
            f"{len(SCHREIBENDE_WERKZEUGE)} schreibende; Internet "
            f"{'erlaubt' if netz else 'abgeschaltet (WEB_ENABLED=false)'}",
            f"- Rechtestufe {getattr(cfg, 'rechte_stufe', '?')}; hoechstens "
            f"{getattr(cfg, 'max_tool_steps', '?')} Werkzeugschritte je Frage, danach "
            f"eine Schlussrunde ohne Werkzeuge",
            "- Wege, zwischen denen vor jeder Antwort gewaehlt wird:",
        ]
        zeilen += [f"    {weg.value.upper()}: {_WEGE[weg]}" for weg in Weg]
        zeilen.append("- Grenzen: kein Zeitplaner (nichts laeuft von selbst "
                      "wieder), keine Aenderung am eigenen Code, jede Aenderung "
                      "an der Vault geht durch das Werkzeugtor.")
        zeilen.append("- Gedaechtnis: Verlauf der letzten "
                      f"{getattr(cfg, 'history_turns', '?')} Zuege woertlich; aeltere Zuege "
                      "im Episodengedaechtnis (Abruf nach Stichwort, Auswahl - nicht "
                      "vollstaendig); verdichtete Fakten in _Sitzungen/; gelernte Regeln "
                      "in _Lernen/. Gewichte des Modells aendern sich nie.")
        return "\n".join(zeilen)
