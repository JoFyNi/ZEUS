"""Der Agent selbst: Aufbau, Faehigkeiten, und der Weg einer Frage.

`ask()` ist die Hauptschlagader des Systems - jede Frage aus dem
Terminal und aus der Oberflaeche laeuft hier durch. Was sie der
Reihe nach tut, steht in der Codekarte unter 'Der Weg einer Frage'.

Alles Umfangreiche daran liegt in den Nachbardateien und wird hier
eingemischt: der Prompt in prompt.py, die Werkzeugschleife in
werkzeuglauf.py, der Tatsachenbericht in bericht.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..config import settings
from ..index import KnowledgeIndex, SyncReport
from ..aufgaben import Aufgabenliste
from ..learning import Lernwerk
from ..llm import OllamaClient, OllamaError
from ..planner import (
    PlanErgebnis, WritePlanner, looks_like_skill_request,
    looks_like_write_request, skill_art,
)
from ..sitzung import Sitzungsgedaechtnis
from ..skills import Fertigkeiten
from ..sprache import aufbereiten
from ..verhalten import Verhaltensfertigkeiten
from ..tools import (
    VaultTools,
)
from ..vault import VaultStructure

from .antwort import Answer, ToolCallRecord
from .texte import (
    STRUCTURE_PATTERNS, VERAENDERNDE_WERKZEUGE, _ist_kurze_bestaetigung,
    _ist_wiederholung,
)

from .bericht import BerichtTeil
from .direkt import DirektTeil
from .kontextwahl import KontextwahlTeil
from .laufbericht import Laufbericht, Runde
from .pruefung import PruefungTeil
from .triage import Weg
from .wege import WegeTeil
from .ordner import OrdnerTeil
from .prompt import PromptTeil
from .schreibweg import SchreibwegTeil
from .werkzeuglauf import WerkzeuglaufTeil


class JarvisAgent(PromptTeil, WerkzeuglaufTeil, BerichtTeil, OrdnerTeil,
                  SchreibwegTeil, DirektTeil, WegeTeil, KontextwahlTeil,
                  PruefungTeil):
    """Der Agent. Die Teile daneben sind nach Aufgabe getrennt, nicht
    nach Zufall - siehe die Modulkoepfe.

    Reihenfolge der Basisklassen: Sie ueberschneiden sich nicht (das
    Aufteilungsskript prueft es), aber die Reihenfolge soll auch dann
    noch stimmen, wenn jemand das vergisst.
    """

    def __init__(self, cfg=settings, index: Optional[KnowledgeIndex] = None):
        self.cfg = cfg
        self.llm = OllamaClient(cfg)
        self.index = index or KnowledgeIndex(cfg)
        self.history: List[dict] = []
        self.tools = VaultTools(self.index, cfg, folder_resolver=self.resolve_target_folder)
        self.planner = WritePlanner(self, cfg)
        self.lernwerk = Lernwerk(self, cfg)
        self.sitzung = Sitzungsgedaechtnis(self, cfg)
        self.fertigkeiten = Fertigkeiten(self.tools, cfg)
        # Die zweite Fertigkeitsart: keine Werkzeugkette, sondern eine
        # Haltung, die bei passendem Anlass in den Prompt kommt.
        self.verhalten = Verhaltensfertigkeiten(self.tools, cfg)
        self.aufgaben = Aufgabenliste(self.tools, cfg)
        # Vom Messstand gesetzt: unterdrückt Lektionen und Fertigkeiten im
        # Prompt, damit sich ihr Beitrag überhaupt messen lässt.
        self.gedaechtnis_aus = False
        # Schaltet den gesamten Wissensabruf ab (Kontrollgruppe im Messstand).
        self.wissen_aus = False
        self.letzter_kontext: dict = {}
        # Nur für die laufende Anfrage: Der Nutzer will eine Fertigkeit.
        self._fertigkeit_gewuenscht = False
        # Welche ART Fertigkeit gemeint ist: 'ablauf' oder 'verhalten'.
        self._fertigkeit_art = "ablauf"
        self.last_plan: Optional[PlanErgebnis] = None
        self._native_tools: Optional[bool] = None
        self._capabilities: Optional[List[str]] = None
        # Wiederholungssperre je Auftrag: Signatur -> Anzahl, Signatur -> Meldung
        self._aufrufe: Dict[str, int] = {}
        self._letzte_meldung: Dict[str, str] = {}
        # Wird von kern.aufbau() gesetzt, sobald der Betriebssystem-Kern
        # hochgefahren ist. Bleibt None, wenn KERN_AKTIV=false steht - dann
        # verhaelt sich Zeus genau wie vorher. Der Agent FRAGT den Kern
        # (Rechte, Auftraege), er haengt nicht von ihm ab.
        self.kern = None
        # Was in den letzten Runden WIRKLICH geschah - fuer die Messung und
        # fuer "warum hast du mir nicht geantwortet?" (siehe laufbericht.py).
        self.laufbericht = Laufbericht()
        self.letzte_runde: Optional[Runde] = None
        self._rundenfrage = ""
        # Das Arbeitsgedaechtnis der letzten Frage (kontextwahl.py) - fluechtig.
        self.letztes_arbeitsgedaechtnis = None


    # ------------------------------------------------------------------- Kern
    def kern_starten(self):
        """Faehrt den Betriebssystem-Kern hoch und meldet die Module an.

        Bewusst nicht im Konstruktor: Ein Agent muss sich auch ohne Kern bauen
        lassen - jede Testreihe tut genau das, und der Messstand baut Dutzende.
        """
        from ..kern.aufbau import kern_fuer
        if self.kern is None:
            self.kern = kern_fuer(self)
        return self.kern

    def kern_anhalten(self) -> None:
        if self.kern is not None:
            self.kern.anhalten()
            self.kern = None


    # --------------------------------------------------------- Fähigkeiten
    def detect_capabilities(self, force: bool = False) -> List[str]:
        """Fragt EINMAL ab, was das Modell kann. Ollama meldet fehlenden
        Werkzeug-Support nicht als Fehler - es ignoriert 'tools' einfach."""
        if self._capabilities is None or force:
            self._capabilities = self.llm.model_capabilities()
            if self.cfg.native_tools == "ja":
                self._native_tools = True
            elif self.cfg.native_tools == "nein":
                self._native_tools = False
            else:
                self._native_tools = "tools" in self._capabilities
        return self._capabilities

    @property
    def native_tools(self) -> bool:
        if self._native_tools is None:
            self.detect_capabilities()
        return bool(self._native_tools)

    @property
    def tool_mode(self) -> str:
        if self._native_tools is None:
            self.detect_capabilities()
        return "nativ" if self._native_tools else "json-protokoll"


    # ------------------------------------------------------------------ Setup
    @property
    def structure(self) -> VaultStructure:
        if self.index.structure is None:
            self.index.refresh_structure()
        return self.index.structure  # type: ignore[return-value]

    def sync(self, force: bool = False, progress: Optional[Callable[[str], None]] = None) -> SyncReport:
        return self.index.sync(force=force, progress=progress)


    # ----------------------------------------------------------------- Fragen
    def ask(
        self,
        question: str,
        stream_cb: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[ToolCallRecord], None]] = None,
        k: Optional[int] = None,
        use_tools: Optional[bool] = None,
    ) -> Answer:
        """Eine Frage, eine Runde. Die Runde wird gezaehlt und abgelegt -
        auf JEDEM Rueckweg, deshalb diese Huelle um `_frage_bearbeiten`.

        Stufe 1 (sprache.py) laeuft hier: Die Eingabe kommt kanonisch (NFC)
        weiter, damit "Ü" aus zwei Codepunkten dieselben Muster trifft wie
        "Ü" aus einem. Sonst bleibt sie Zeichen fuer Zeichen, wie sie war.
        """
        self._eingang = aufbereiten(question)
        question = self._eingang.text
        runde = Runde(eingabe=question)
        self.letzte_runde = runde
        self._rundenfrage = question
        self._weg_werkzeuge = None          # der Weg setzt sie neu
        self._weg_schritte = None
        self._arbeit = None                 # das Arbeitsgedaechtnis dieser Frage
        antwort: Optional[Answer] = None
        try:
            antwort = self._frage_bearbeiten(question, stream_cb, on_tool, k, use_tools)
            return antwort
        finally:
            if not runde.endete_mit:
                text = (getattr(antwort, "text", "") or "").strip()
                kind = getattr(antwort, "kind", "")
                runde.endete_mit = ("fehler" if kind == "fehler" else
                                    "notbremse" if text.startswith("Erledigt:") else
                                    "prosa" if text else "leer")
            runde.phase = ""
            # Jeder Zug ins Episodengedaechtnis - auf jedem Rueckweg, denn
            # auch ein Gruss oder ein Planbericht kann spaeter gemeint sein.
            self._zug_ablegen(question, antwort, runde.weg_text)
            # Zum Nachsehen (/status, Messung): was diese Frage zu sehen bekam.
            self.letztes_arbeitsgedaechtnis = self._arbeit
            self._arbeit = None
            self._weg_werkzeuge = None
            self._weg_schritte = None
            self._ohne_vektorabruf = False
            self.laufbericht.ablegen(runde)

    def _frage_bearbeiten(
        self,
        question: str,
        stream_cb: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[ToolCallRecord], None]] = None,
        k: Optional[int] = None,
        use_tools: Optional[bool] = None,
    ) -> Answer:
        import time as _time

        begonnen = _time.time()
        question, folder_filter = self._extract_folder_filter(question)
        structural = bool(STRUCTURE_PATTERNS.search(question))
        tools_on = self.cfg.tools_enabled if use_tools is None else use_tools
        self.tools.lauf_zuruecksetzen()   # Datei-Limit gilt pro Auftrag, nicht pro Sitzung
        # Wiederholungssperre gilt pro Auftrag: Was hier scheitert, soll im
        # SELBEN Auftrag nicht nochmal identisch versucht werden - beim
        # nächsten Auftrag darf es aber wieder probiert werden.
        self._aufrufe.clear()
        self._letzte_meldung.clear()

        # --- Selbst denken: Rückmeldung des Nutzers als Messwert verwerten ---
        gelernt = ""
        if self.cfg.learning_enabled:
            bewertung = self.lernwerk.rueckmeldung_erkennen(question)
            if bewertung:
                lektion = self.lernwerk.rueckmeldung_verarbeiten(question, bewertung)
                if lektion:
                    gelernt = lektion

        # --- Triage: welcher Weg, BEVOR die Vault durchsucht wird ---
        # Frueher lief der Vektorabruf hier bedingungslos, fuer jede Eingabe -
        # auch fuer "Hallo" und fuer "reflektiere deine Aktionen". Siehe
        # triage.py fuer die Vorrangregeln.
        wahl = self._triage_waehlen(question, tools_on)
        # --- Arbeitsgedaechtnis: Bezuege, Vorgaben, Widersprueche, Erinnerungen ---
        # Nach der Triage, weil der Weg bestimmt, ob ein Vektorabruf erlaubt
        # ist; vor jedem Prompt, weil jeder Weg es braucht (kontextwahl.py).
        self._arbeit = self._arbeitsgedaechtnis_bauen(question, wahl.weg)
        self._runde_vermerken(self._arbeit)
        if wahl.weg is Weg.DIREKT:
            antwort = self._direkt_antworten(question, wahl, stream_cb, gelernt)
            if wahl.folge is Weg.SCHREIBEN and tools_on and self.cfg.planner_enabled:
                # Kombinierter Auftrag ("reflektiere ... und notiere das"):
                # ERST die Antwort, DANN die Notiz - nie umgekehrt, und nie
                # die Notiz anstelle der Antwort.
                antwort = self._danach_schreiben(question, antwort, folder_filter,
                                                 on_tool)
            if self.cfg.learning_enabled:
                self.lernwerk.beobachten(question, antwort, _time.time() - begonnen, "")
            return antwort

        # RECHERCHE braucht das Netz. Ist keins da, ist "kann ich nicht" die
        # richtige Antwort - NIE ersatzweise die Vault (werkzeuge/netz.py).
        if wahl.weg is Weg.RECHERCHE and not self.netz_pruefen():
            return self._ohne_netz_antworten(question, folder_filter)
        # Welche Werkzeuge der Weg erlaubt, und wie viele Schritte.
        self._weg_einrichten(wahl)
        ohne_abruf = wahl.weg in (Weg.RECHERCHE, Weg.WERKZEUG) and not wahl.ausdruecklich
        self._ohne_vektorabruf = ohne_abruf

        if ohne_abruf:
            # Die Antwort steht nicht in den Notizen (RECHERCHE) oder kommt
            # aus EINEM Werkzeug (WERKZEUG). Ein Abruf waere Aufwand - und
            # bei RECHERCHE die Einladung, doch wieder die Vault zu fragen.
            hits = []
            context = ""
        elif self.wissen_aus:
            # Kontrollgruppe für den Messstand: keine Vault, kein Abruf. Das
            # Modell antwortet allein aus seinen Gewichten. Ohne diese Variante
            # lässt sich nicht belegen, dass der Messstand überhaupt etwas
            # misst - wenn "blind" genauso gut abschneidet wie "voll", prüfen
            # die Aufgaben nicht das Wissen, sondern etwas anderes.
            hits = []
            context = ""
        else:
            # Ein aufgeloester Bezug ("was steht in der Notiz?" -> [[X]]) geht
            # mit in die Suche - sonst sucht sie nach "Notiz" und findet alles
            # ausser X. Das Modell bekommt weiterhin die Frage, wie sie war.
            zusatz = self._arbeit.suchzusatz() if self._arbeit else []
            suchfrage = " ".join([question, *zusatz]) if zusatz else question
            hits = self.index.search(suchfrage, k=k or self.cfg.top_k, folder=folder_filter)
            context = self._build_context(hits, structural=structural, frage=suchfrage)
        # Was tatsächlich im Prompt landete - der Messstand liest das aus,
        # um zu prüfen, ob sich zwei Varianten überhaupt unterschieden haben.
        self.letzter_kontext = {
            "quellen": len(hits),
            "kontext_zeichen": len(context),
            "lektionen": 0,
            "fertigkeiten": 0,
        }

        # --- Ausdrücklich benanntes Werkzeug: [auto_verlinken] ---
        # Steht ein Werkzeugname in eckigen Klammern, hat der Nutzer sich
        # entschieden. Das geht NICHT ans Modell - sonst passiert wieder das,
        # was am 01.09. im Protokoll steht: Er hielt "[auto_verlinken]" für ein
        # Zitat und erklärte, das Werkzeug gebe es nicht.
        if tools_on:
            antwort = self._ausdrueckliche_rufe(question, hits, folder_filter,
                                                context, on_tool, stream_cb)
            if antwort is not None:
                if self.cfg.learning_enabled:
                    self.lernwerk.beobachten(question, antwort, _time.time() - begonnen, "")
                return antwort

        # --- Schreibauftrag? Dann nicht auf die Tool-Disziplin des Modells hoffen,
        #     sondern Plan erzwingen, ausführen und die Ausführung prüfen. ---
        if tools_on and self.cfg.planner_enabled:
            if _ist_wiederholung(question) and self.last_plan and self.last_plan.fehlgeschlagen:
                return self._plan_antwort(
                    self.planner.fehlende_wiederholen(self.last_plan, self._plan_melder(on_tool)),
                    question, hits, folder_filter,
                )
            # Eine FERTIGKEIT ist keine Notiz. Diese Unterscheidung muss vor
            # dem Schreibplaner fallen - danach sieht "erstelle …" für ihn
            # zwangsläufig nach notiz_erstellen aus, weil er nur Aktionen an
            # der Vault kennt.
            #
            # "dann erstelle sie" allein enthält kein Fertigkeit-Signalwort -
            # looks_like_skill_request verfehlt es, looks_like_write_request
            # greift wegen "erstelle" trotzdem, und der Schreibplaner legt
            # (ohne jeden Bezug zur besprochenen Fertigkeit) irgendeine Notiz
            # an. Genau das geschah am 03.09.: aus "dann erstelle sie" wurde
            # die Notiz "Aufbau und Fähigkeiten 2026-09-10". Deshalb zählt
            # eine kurze Bestätigung AUCH dann als Fertigkeit-Wunsch, wenn die
            # vorige eigene Antwort erkennbar von einer Fertigkeit handelte.
            bezog_sich_auf_fertigkeit = (
                _ist_kurze_bestaetigung(question) and self.history
                and self.history[-1].get("role") == "assistant"
                and re.search(r"fertigkeit|skill", self.history[-1].get("content") or "",
                              re.IGNORECASE)
            )
            if looks_like_skill_request(question) or bezog_sich_auf_fertigkeit:
                self._fertigkeit_gewuenscht = True
                # Zwei Arten, zwei Werkzeuge. Welches gemeint ist, entscheidet
                # der Planer an den Wörtern des Auftrags - nicht das Modell.
                self._fertigkeit_art = skill_art(question)
            elif looks_like_write_request(question):
                antwort = self._plan_durchfuehren(question, self._planer_kontext(context),
                                                  hits, folder_filter, on_tool)
                if antwort is not None:
                    return antwort

        messages: List[dict] = [
            {"role": "system",
             "content": self.system_prompt(context, structural, with_tools=tools_on,
                                           frage=question)}
        ]
        messages.extend(self.history[-2 * self.cfg.history_turns :])
        messages.append({"role": "user", "content": question})
        # HISTORY_TURNS zählt Züge, nicht Zeichen. Fünf ausführliche Antworten
        # sprengen damit jedes Kontextfenster - und Ollama schneidet dann still
        # am ANFANG ab, wo die Anweisungen stehen (siehe ENTWICKLUNG.md, v5).
        # Deshalb hier eine zweite, harte Schranke in Zeichen.
        messages, ausgelassen = self._auf_budget_kuerzen(messages, mit_werkzeugen=tools_on)

        calls: List[ToolCallRecord] = []
        try:
            if tools_on:
                text, calls = self._tool_loop(messages, on_tool, stream_cb)
            else:
                text = self.llm.chat(messages, stream_cb=stream_cb)
        except OllamaError as exc:
            antwort = Answer(text=self._verbindungshinweis(exc), hits=hits,
                             folder_filter=folder_filter, kind="fehler", tool_calls=calls)
            if self.cfg.learning_enabled:
                self.lernwerk.beobachten(question, antwort, _time.time() - begonnen, str(exc))
            return antwort

        # Der im Code erzeugte Tatsachenbericht steht ÜBER der Modellprosa.
        # Ein kleines Modell schreibt sonst "erfolgreich verschoben", obwohl das
        # Werkzeug ok=False gemeldet hat - der Nutzer glaubt es und merkt es erst
        # Tage später.
        text = self._tatsachen_anhaengen(text, calls)
        if wahl.weg is Weg.WERKZEUG and not wahl.ausdruecklich:
            text = self._werkzeug_fehlschlag_anhaengen(text, calls)
        elif wahl.weg is Weg.RECHERCHE:
            text = self._recherche_belegen(text, calls)
        # Stufe 6: nachrechnen, Grenzen, Quellen, Herkunft (pruefung.py).
        text = self._antwort_pruefen(text, question, wahl.weg, calls)
        text = self._gedaechtnisluecke_melden(text, question)

        abbruch = self.llm.last_warning
        if abbruch:
            text = (text or "").rstrip() + f"\n\n*Hinweis: {abbruch}*"
        if not (text or "").strip():
            # Die Oberfläche zeigte für eine leere Antwort bisher "…" - der
            # Nutzer sah dreimal hintereinander drei Punkte und wusste nicht,
            # ob der Server tot ist, das Modell schweigt oder etwas abgestürzt
            # ist. Lieber ein Satz, der sagt, was tatsächlich passiert ist.
            text = self._leere_antwort_erklaeren(calls)
        if ausgelassen:
            # Stillschweigend vergessen ist die schlechteste Möglichkeit: Der
            # Nutzer bezieht sich auf etwas, das der Agent nicht mehr hat, und
            # bekommt eine Antwort, die daneben liegt, ohne zu erfahren warum.
            text = ((text or "").rstrip()
                    + f"\n\n*Kontext knapp: {ausgelassen} ältere Nachricht(en) wurden "
                      f"ausgelassen, damit die Anweisungen im Prompt bleiben. "
                      f"Was länger her ist, frag bitte noch einmal nach.*")
        if gelernt:
            text = f"*Verstanden – gemerkt: {gelernt}*\n\n" + (text or "")

        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-2 * self.cfg.history_turns :]

        antwort = Answer(
            text=text, hits=hits, folder_filter=folder_filter,
            kind="struktur" if structural else "rag", tool_calls=calls,
        )
        # Maßstab für beide Gedächtnisse: ob die verändernden Werkzeuge
        # geglückt sind - nicht, was das Modell über sich behauptet.
        veraendernd = [c for c in calls if c.werkzeug in VERAENDERNDE_WERKZEUGE]
        geglueckt = bool(not abbruch and (not veraendernd
                                          or all(c.ergebnis.ok for c in veraendernd)))
        if self.cfg.learning_enabled:
            self.lernwerk.beobachten(question, antwort, _time.time() - begonnen, abbruch)
            self.lernwerk.lektionen_bewerten(geglueckt)
        self.sitzung.bewerten(geglueckt)
        self.sitzung.zug_vermerken(question, text)
        if self.sitzung.faellig():
            # Periodisch verdichten: Ohne das geht bei einem Absturz oder einem
            # zugeklappten Terminal die ganze Sitzung verloren, weil /ende nie
            # erreicht wird. Ein Fehlschlag bleibt folgenlos für die Antwort.
            self.sitzung.verdichten("periodisch", self._sitzung_melden)
        return antwort

    def _sitzung_melden(self, text: str) -> None:
        try:
            self.tools.log.info(text, "sitzung")
        except Exception:
            pass

    def sitzung_abschliessen(self) -> Optional[Path]:
        """Beim Beenden verdichten. Darf nie eine Ausnahme nach oben lassen -
        ein Programm, das sich wegen des Gedächtnisses nicht beenden lässt,
        ist schlimmer als eines, das eine Sitzung vergisst."""
        try:
            return self.sitzung.verdichten("Sitzungsende", self._sitzung_melden)
        except Exception as exc:
            self.sitzung.letzter_fehler = f"Abschluss misslang: {exc}"
            return None

    def schrittmelder_setzen(self, melder) -> None:
        """Wer erfahren will, WELCHER innere Schritt gerade läuft.

        melder(werkzeug, schritt) - beides Zeichenketten. Die Oberfläche lässt
        damit den Stern des laufenden Werkzeugs Schritt für Schritt aufleuchten.
        """
        self.tools.on_schritt = melder
