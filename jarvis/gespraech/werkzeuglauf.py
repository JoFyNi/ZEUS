"""Die Werkzeugschleife: vom Modellwunsch zum echten Aufruf.

Zwei Wege, weil nicht jedes Modell Werkzeuge kann: die native
Schnittstelle und das JSON-Protokoll als Rueckfallebene. Dazu
die Erkennung eines Aufrufs, den das Modell als TEXT
hingeschrieben hat, statt ihn zu taetigen - der haeufigste
Fall bei mittelgrossen Modellen und die Ursache dafuer, dass
eine Fertigkeit im Chat stand statt in der Vault.

Hier sitzt auch die Wiederholungssperre: Der zweite
buchstabengleiche Aufruf im selben Auftrag wird beantwortet
statt ausgefuehrt.
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional, Set, Tuple

from ..llm import OllamaError, ToolsUnsupported
from .. import werkzeugruf
from ..sprache import textlast
from ..tools import (
    TOOL_NAMES,
    TOOL_PARAMS,
    ToolResult,
    ollama_tool_definitions,
)

from .antwort import Answer, ToolCallRecord
from .antwortpflicht import (SCHLUSSRUNDE_ANWEISUNG, ehrlicher_hinweis,
                             ist_prosa_antwort)
from .laufbericht import Runde
from .schleifenschutz import Schleifenschutz
from .aufrufe import _aufruf_aus_text, _fertigkeit_json_aus_text, _parse_action
from .arbeitsplan import Arbeitsplan, braucht_plan, plan_erzeugen  # noqa: F401
from .texte import _WERKZEUG_HINWEISE, _extract_context


_NACHFASSEN = (
    "Der Auftrag ist noch nicht erledigt - es stehen Schritte offen. Frage "
    "NICHT um Erlaubnis: Der Auftrag liegt bereits vor, du sollst ihn "
    "ausfuehren. Rufe jetzt das Werkzeug fuer den naechsten offenen Schritt "
    "auf. Erst wenn alle Schritte erledigt sind, fasst du in Prosa zusammen."
)


def _fuer_menschen(meldung: str) -> str:
    """Streicht aus einer Werkzeugmeldung, was nur das MODELL angeht.

    Werkzeugmeldungen tragen Anweisungen ("SAG DEM NUTZER, dass ...",
    "Behaupte nichts ueber das Ergebnis"). Die sind richtig - aber sie sind an
    das Modell gerichtet. Am 21.09. standen sie woertlich in der Antwort an
    Josh, weil die Notfallzusammenfassung die Meldungen einfach aneinander-
    haengte. Was er zu lesen bekam, war eine Handlungsanweisung an jemand
    anderen.
    """
    text = str(meldung or "")
    for anfang in ("Sag dem Nutzer", "SAG DEM NUTZER", "Behaupte nichts",
                   "Such nicht", "Kennst du eine konkrete Adresse",
                   "Kennst du die", "Derselbe Aufruf", "Ändere die Parameter",
                   "Weitere Umformulierungen", "Für Einzelheiten"):
        stelle = text.find(anfang)
        if stelle > 0:
            text = text[:stelle]
    return text.strip(" .;") or str(meldung or "")[:120]


def _schlussbericht(calls) -> str:
    """NOTBREMSE. Der Protokollauszug, wenn wirklich nichts anderes ging.

    Frueher war das die gewoehnliche Rueckfallantwort: "Erledigt: 2 Treffer
    fuer ...; Inhalt von 'Dokumentation'". Das ist ein Protokollauszug, keine
    Antwort - und er kam am Anlass viermal hintereinander. Seit der
    Antwortpflicht (antwortpflicht.py) steht davor die Schlussrunde ohne
    Werkzeuge und danach der ehrliche Hinweis. Hierher kommt ein Lauf nur
    noch, wenn selbst der Hinweis nicht gebaut werden konnte; jeder Aufruf
    wird in `Runde.notbremse` gezaehlt und soll im Testsatz 0 bleiben.
    """
    erfolgreich = [c for c in calls if c.ergebnis.ok]
    if erfolgreich:
        return ("Erledigt: "
                + "; ".join(_fuer_menschen(c.ergebnis.meldung)
                            for c in erfolgreich[-4:]))
    return _fehlschlag_erklaeren(calls)


def _fehlschlag_erklaeren(calls) -> str:
    """Warum KEIN Werkzeug etwas geliefert hat - in einem lesbaren Satz."""
    gruende = []
    for c in calls[-3:]:
        grund = _fuer_menschen(c.ergebnis.meldung)
        if grund and grund not in gruende:
            gruende.append(grund)
    netz = any(c.werkzeug in ("web_suchen", "web_lesen") for c in calls)
    if netz:
        return ("Die Recherche ist **nicht** geglückt – ich habe nichts "
                "herausgefunden und sage dir lieber nichts, als etwas zu "
                "erfinden.\n\nGrund: " + "; ".join(gruende)
                + "\n\nMeine Quellen sind Kurzantworten und Wikipedia; für "
                  "Herstellerangaben reicht das nicht. Nenn mir die Adresse "
                  "der richtigen Seite, dann lese ich sie dir aus.")
    return ("Kein Werkzeug hat ein Ergebnis geliefert. Grund: "
            + "; ".join(gruende))


def _ist_ergebnis(eintrag: dict) -> bool:
    return (eintrag.get("role") == "tool"
            or str(eintrag.get("content") or "").startswith("WERKZEUG-ERGEBNIS"))


def _verdichten(text: str, laenge: int) -> str:
    """Kurzfassung eines Werkzeugergebnisses. Der Anfang traegt Werkzeug-
    meldung und Notizname ("Inhalt von 'X'") - er bleibt stehen."""
    if len(text) <= laenge + 20:
        return text
    return text[:laenge] + " …[gekürzt]"


class WerkzeuglaufTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    # Einmal gebaut, dann gemerkt. None bis zum ersten schreibenden Aufruf.
    _verifikation = None
    _speicher = None            # ADR-001, erst bei einem Arbeitsplan
    _speicher_fehler = ""
    # Das gemeinsame Werkzeugtor. None heisst: Der Kern ist nicht
    # hochgefahren - dann laeuft der alte Weg, siehe `_ohne_tor`.
    _werkzeugtor = None


    # ---------------------------------------------------------- Werkzeuglauf
    def _schleife_kuerzen(self, arbeit: List[dict]) -> List[dict]:
        """Haelt den wachsenden Werkzeug-Stapel im Kontextfenster.

        Die Rangfolge beim Kuerzen, wichtigstes zuletzt:

            1. fruehere Gespraechszuege (aelteste zuerst) - fallen weg
            2. Werkzeugergebnisse DIESER Runde - werden VERDICHTET, nie
               entfernt (Kurzfassung mit Notizname), aelteste zuerst
            3. Systemprompt und Frage - bleiben immer ganz

        Die fruehere Fassung schnitt von vorn und warf dabei auch Ergebnisse
        der laufenden Runde weg. Das Modell verlor, was es gerade geholt hatte,
        und las dieselbe Notiz erneut - M3 im Auftrag, dreimal "Identischer
        Aufruf von notiz_lesen". Die Ergebnisse der laufenden Runde sind das
        Einzige, was die Runde erarbeitet hat.

        Der Systemprompt wird nie angeschnitten: Genau das tat Ollama, wenn
        der Prompt zu lang war (Kuerzung am Prompt-Anfang, 18.09.).
        """
        roh = max(2000, int(getattr(self.cfg, "prompt_char_budget", 14000)))
        aufschlag = self._werkzeug_aufschlag(True)
        platz = max(2000, roh - min(aufschlag, int(roh * 0.4)))

        def gross(stapel):
            # Schaetzung in "lateinischen Zeichen" - siehe sprache.textlast
            return sum(textlast(m.get("content") or "") for m in stapel)

        if gross(arbeit) <= platz or len(arbeit) < 3:
            return arbeit

        # Wo beginnt die laufende Runde? An der Frage dieser Runde - per
        # Identitaet gesucht, weil Kuerzen die Positionen verschiebt.
        frage_msg = getattr(self, "_rundenfrage_msg", None)
        stelle = next((i for i, m in enumerate(arbeit) if m is frage_msg), None)
        if stelle is None or stelle == 0:
            stelle = 1              # alter Aufbau: [system, frage, ...]
        system = arbeit[0]
        verlauf = list(arbeit[1:stelle])
        frage = arbeit[stelle]
        laufend = [dict(m) for m in arbeit[stelle + 1:]]

        def stapel():
            return [system, *verlauf, frage, *laufend]

        # 1. Fruehere Zuege, aelteste zuerst - und nie mit einer Antwort
        #    ohne ihre Frage beginnen.
        while verlauf and gross(stapel()) > platz:
            verlauf.pop(0)
            while verlauf and verlauf[0].get("role") != "user":
                verlauf.pop(0)

        # 2. Ergebnisse der laufenden Runde verdichten, aelteste zuerst; erst
        #    maessig, dann stark. Entfernt wird keines.
        for laenge in (600, 160):
            for eintrag in laufend:
                if gross(stapel()) <= platz:
                    break
                if _ist_ergebnis(eintrag):
                    eintrag["content"] = _verdichten(eintrag.get("content") or "", laenge)
        return stapel()

    # --------------------------------------------------------- Antwortpflicht
    def _runde(self) -> Runde:
        """Die laufende Runde - oder eine Wegwerfrunde, wenn keine laeuft.

        Tests rufen `_tool_loop` auch ohne `ask()`; dann gibt es nichts zu
        berichten, aber auch keinen Grund abzustuerzen.
        """
        runde = getattr(self, "letzte_runde", None)
        if runde is None:
            runde = Runde(eingabe="")
            self.letzte_runde = runde
        return runde

    def _schlussrunde(self, arbeit: List[dict], calls: List[ToolCallRecord],
                      stream_cb=None) -> Tuple[str, str]:
        """EINE Runde ohne Werkzeugdefinitionen - das Modell kann nur antworten.

        Zaehlt nicht gegen MAX_TOOL_STEPS. Schreibt das Modell trotzdem einen
        Aufruf hin, wird er NICHT ausgefuehrt: Er gilt als "keine Prosa", und
        der Nutzer bekommt den ehrlichen Hinweis.
        Gibt (text, endete_mit) zurueck.
        """
        runde = self._runde()
        frage = ""
        for m in reversed(arbeit):
            if m.get("role") == "user" and not str(m.get("content") or "").startswith(
                    "WERKZEUG-ERGEBNIS"):
                frage = str(m.get("content") or "")
                break
        frage = getattr(self, "_rundenfrage", "") or frage
        grund = ""
        text = ""
        vorher = runde.phase
        runde.phase = "schlussrunde"
        try:
            gekuerzt = self._schleife_kuerzen(list(arbeit))
            text = self.llm.chat(
                gekuerzt + [{"role": "user",
                             "content": SCHLUSSRUNDE_ANWEISUNG.format(frage=frage)}],
                stream_cb=stream_cb) or ""
        except OllamaError as exc:
            grund = f"die Schlussrunde erreichte das Modell nicht: {exc}"
        finally:
            runde.phase = vorher
        if ist_prosa_antwort(text, self._laufzeitnamen()):
            return text, "schlussrunde"
        try:
            erfolgreich = [c for c in calls if c.ergebnis.ok]
            hinweis = ehrlicher_hinweis(
                len(calls), len(erfolgreich), frage, grund,
                fehlschlag="" if erfolgreich else _fehlschlag_erklaeren(calls))
            return hinweis, "hinweis"
        except Exception:
            runde.notbremse += 1
            return _schlussbericht(calls), "notbremse"

    def _plan_aufstellen(self, messages: List[dict]):
        """Zerlegt den Auftrag EINMAL, bevor das erste Werkzeug laeuft.

        Nur fuer mehrstufige Auftraege. Fuer "was heisst EOF" waere ein Plan
        ein Modellaufruf und Prompt-Platz fuer nichts.
        """
        frage = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                frage = m.get("content") or ""
                break
        if not frage:
            return None
        try:
            from ..planner import looks_like_write_request
            schreibt = looks_like_write_request(frage)
        except Exception:
            schreibt = False
        if not braucht_plan(frage, schreibt):
            return None
        plan = plan_erzeugen(frage, self.llm, set(self._laufzeitnamen()),
                             melden=getattr(self, "melden", None))
        if plan is not None:
            self._melden_plan(plan)
            # ADR-001: Persistiert wird am ARBEITSPLAN, nicht an ask(). Eine
            # einstufige Frage hat nichts wiederaufzunehmen; hier dagegen
            # kann eine Aufgabe auf Freigabe warten, einen Neustart
            # ueberleben und spaeter beantworten, woran gearbeitet wurde.
            self._aufgabe_anlegen(plan, frage)
        return plan

    # ------------------------------------------------------- Aufgabenspeicher
    @property
    def speicher(self):
        """Der persistente Aufgabenspeicher - erst beim ersten Bedarf gebaut."""
        if getattr(self, "_speicher", None) is None:
            try:
                from ..kern.aufgabenspeicher import Aufgabenspeicher
                self._speicher = Aufgabenspeicher(
                    self.cfg.work_dir / "aufgaben.sqlite3")
            except Exception as exc:
                # Ein kaputter Speicher darf keinen Auftrag verhindern. Er
                # meldet sich und tritt beiseite - dieselbe Regel wie bei der
                # Git-Historie in werkzeuge/basis.py.
                self._speicher_fehler = f"{type(exc).__name__}: {exc}"
                self._speicher = False
        return self._speicher or None

    def _aufgabe_anlegen(self, plan, frage: str) -> None:
        """Legt Aufgabe und Kindschritte an. Scheitert nie laut."""
        speicher = self.speicher
        if speicher is None:
            return
        try:
            titel = (plan.schritte[0].beschreibung if plan.schritte else frage)[:120]
            aufgabe = speicher.anlegen(
                titel=f"{frage[:80]}…" if len(frage) > 80 else frage,
                beschreibung=titel, auftrag=frage, prioritaet=3)
            speicher.wechseln(aufgabe.id, "planung", "Arbeitsplan steht")
            for schritt in plan.schritte:
                speicher.anlegen(titel=schritt.beschreibung[:200],
                                 eltern_id=aufgabe.id, auftrag=frage,
                                 daten={"werkzeug": schritt.erwartet,
                                        "nummer": schritt.nummer})
            speicher.wechseln(aufgabe.id, "bereit", "Schritte angelegt")
            speicher.wechseln(aufgabe.id, "laeuft", "Ausführung beginnt")
            plan.aufgabe_id = aufgabe.id
        except Exception as exc:
            melden = getattr(self, "melden", None)
            if callable(melden):
                melden(f"Aufgabe nicht persistiert: {type(exc).__name__}: {exc}")

    def _aufgabe_abschliessen(self, plan) -> None:
        """Bucht den Ausgang der Aufgabe, wenn die Schleife endet."""
        speicher = self.speicher
        kennung = getattr(plan, "aufgabe_id", "") if plan else ""
        if speicher is None or not kennung:
            return
        try:
            aufgabe = speicher.holen(kennung)
            if aufgabe is None or not aufgabe.offen:
                return
            if plan.fertig:
                speicher.wechseln(kennung, "pruefung", "alle Schritte erledigt")
                speicher.wechseln(kennung, "fertig", "Auftrag abgeschlossen")
            else:
                # NICHT als gescheitert wegwerfen: Offene Schritte sind der
                # Grund, aus dem der Speicher ueberhaupt existiert. Neuplanung
                # heisst, dass ein spaeterer Lauf hier ansetzen kann.
                speicher.fehlschlag(kennung, plan.offene_melden()[:300]
                                    or "Schleife endete mit offenen Schritten")
        except Exception as exc:
            melden = getattr(self, "melden", None)
            if callable(melden):
                melden(f"Aufgabenstand nicht gebucht: {type(exc).__name__}: {exc}")

    def _melden_plan(self, plan) -> None:
        """Den Plan sichtbar machen - der Nutzer soll sehen, was vorgehabt wird."""
        melden = getattr(self, "melden", None)
        if callable(melden):
            melden("Arbeitsplan: " + " | ".join(
                f"{s.nummer}. {s.beschreibung}" for s in plan.schritte))

    def _tool_loop(
        self, messages: List[dict], on_tool: Optional[Callable[[ToolCallRecord], None]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, List[ToolCallRecord]]:
        calls: List[ToolCallRecord] = []
        arbeit = list(messages)
        letzte_antwort = ""
        plan = self._plan_aufstellen(messages)
        self._plan = plan               # fuer den Abschlussbericht
        nachfassen = 0                  # wie oft schon auf den Plan gestossen
        # Die Abbruchzaehlung der Netzsuche gilt JE AUFTRAG. Ohne diesen
        # Rueckstellung blieb sie gesetzt und sperrte auch die naechste,
        # voellig andere Frage.
        self.tools._netz_fehlschlaege = 0
        runde = self._runde()
        runde.phase = "schleife"
        # Die Frage dieser Runde - `_schleife_kuerzen` erkennt an ihr, wo die
        # laufende Runde beginnt, deren Ergebnisse es nie wegwirft.
        self._rundenfrage_msg = messages[-1] if messages else None
        self._schutz = Schleifenschutz(
            schwelle=getattr(self.cfg, "such_aehnlichkeit", 0.6),
            deckel=getattr(self.cfg, "max_abweisungen", 3))
        grenze = max(1, self._weg_schritte or self.cfg.max_tool_steps)

        # Ein Durchlauf, in dem NUR abgewiesen wurde, kostet keinen Schritt
        # (Schleifenschutz). Die harte Obergrenze der Durchlaeufe haelt die
        # Schleife trotzdem endlich: Schritte + Abweisungsdeckel + Reserve.
        schritte = 0
        vorher = None                   # (Aufrufe, Abweisungen) vor dem Durchlauf
        for _ in range(grenze + self._schutz.deckel + 2):
            if vorher is not None:
                lief = len(calls) > vorher[0]
                nur_abgewiesen = runde.abgewiesen > vorher[1] and not lief
                if not nur_abgewiesen:
                    schritte += 1
                if schritte >= grenze or self._schutz.erschoepft:
                    break
            vorher = (len(calls), runde.abgewiesen)
            # Die Schleife haengte bisher nur an und kuerzte nie. Gemessen am
            # 18.09.: 32.846 Zeichen vor dem ersten Aufruf, 46.934 nach dem
            # fuenften - bei 13.517 nutzbaren. Ollama schneidet den Ueberhang
            # VORN ab, also dort, wo die Anweisungen und die zuerst gelesenen
            # Notizen stehen. Das Modell verlor damit genau das Ergebnis, das
            # es gerade geholt hatte, und las dieselbe Notiz erneut - die
            # Endlosschleife aus dem Protokoll ("Identischer Aufruf ...").
            arbeit = self._schleife_kuerzen(arbeit)

            # Der Plan faehrt als LETZTE Nachricht mit, nicht als Teil von
            # `arbeit`: So steht er immer unmittelbar vor der Generierung,
            # ueberlebt jedes Kuerzen und sammelt sich nicht Runde fuer Runde
            # im Verlauf an.
            anfrage = arbeit if plan is None else (
                arbeit + [{"role": "user", "content": plan.als_anweisung()}])

            if self.native_tools:
                try:
                    msg = self.llm.chat_message(anfrage, tools=self._werkzeugliste(),
                                                stream_cb=stream_cb)
                except ToolsUnsupported:
                    # Modell kann keine Werkzeuge -> auf JSON-Protokoll umstellen
                    self._native_tools = False
                    arbeit[0] = {
                        "role": "system",
                        "content": self.system_prompt(
                            _extract_context(messages[0]["content"]), with_tools=True
                        ),
                    }
                    continue

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    inhalt = msg.get("content", "") or ""
                    # Hat das Modell den Aufruf als Text hingeschrieben, statt
                    # ihn zu tätigen? Dann ausführen statt als Antwort zeigen.
                    geschrieben = _aufruf_aus_text(inhalt, self._laufzeitnamen())
                    if not geschrieben and self.cfg.self_extension:
                        # Fertigkeitsdefinition als blosses JSON, ohne
                        # fertigkeit_definieren(...)-Hülle - siehe
                        # _fertigkeit_json_aus_text().
                        fert = _fertigkeit_json_aus_text(inhalt)
                        if fert:
                            geschrieben = ("fertigkeit_definieren", fert)
                    if geschrieben:
                        name, args = geschrieben
                        record, gelaufen = self._aufruf_im_lauf(name, args, on_tool)
                        if gelaufen:
                            calls.append(record)
                            if plan is not None:
                                plan.vermerken(name, record.ergebnis.ok,
                                               record.ergebnis.meldung)
                        arbeit.append({"role": "assistant", "content": inhalt})
                        arbeit.append({
                            "role": "user",
                            "content": f"WERKZEUG-ERGEBNIS von {name}: "
                                       f"{record.ergebnis.to_model()}\n"
                                       "Beantworte damit die Frage in Prosa.",
                        })
                        continue
                    letzte_antwort = inhalt or letzte_antwort
                    # Prosa statt Werkzeugaufruf beendete die Schleife bisher
                    # IMMER - auch mitten im Plan. Am 18.09. las Zeus beide
                    # Notizen und schrieb dann "Die Zusammenfuehrung ist
                    # vorbereitet. Soll ich direkt loslegen?", statt sie zu
                    # schreiben. Der Auftrag lag vor, die Rueckfrage war
                    # keine. Steht der Plan noch offen, wird einmal darauf
                    # gestossen, bevor aufgegeben wird.
                    if plan is not None and not plan.fertig and nachfassen < 2:
                        nachfassen += 1
                        arbeit.append({"role": "assistant", "content": inhalt})
                        arbeit.append({"role": "user", "content": _NACHFASSEN})
                        continue
                    break

                arbeit.append({"role": "assistant", "content": msg.get("content", ""),
                               "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", {})
                    parse_fehler = ""
                    roh = args if isinstance(args, str) else ""
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError as exc:
                            # NICHT still auf {} zurückfallen. Genau das führte
                            # bei fertigkeit_definieren dazu, dass 'schritte'
                            # als leer ankam und die Meldung "Kein gültiger
                            # Schritt übrig" lautete - ohne jede Spur, dass in
                            # Wahrheit das JSON der Argumente kaputt war (z. B.
                            # weil ein langes 'beschreibung'-Feld mit
                            # Sonderzeichen die Ausgabe des Modells zerriss).
                            parse_fehler = str(exc)
                            args = {}
                    if not isinstance(args, dict):
                        parse_fehler = parse_fehler or (
                            f"Argumente sind kein Objekt, sondern {type(args).__name__}")
                        args = {}

                    if parse_fehler:
                        ergebnis = ToolResult(
                            False,
                            f"Argumente für '{name}' waren kein gültiges JSON: {parse_fehler}. "
                            f"Halte einzelne Textfelder kürzer oder ohne Sonderzeichen und "
                            f"versuche denselben Aufruf erneut.",
                            {"rohtext_anfang": roh[:300]} if roh else {},
                        )
                        record = ToolCallRecord(werkzeug=name, parameter={}, ergebnis=ergebnis)
                        if on_tool:
                            on_tool(record)
                        calls.append(record)
                        if plan is not None:
                            plan.vermerken(name, False, ergebnis.meldung)
                    else:
                        record, gelaufen = self._aufruf_im_lauf(name, args, on_tool)
                        if gelaufen:
                            calls.append(record)
                            if plan is not None:
                                plan.vermerken(name, record.ergebnis.ok,
                                               record.ergebnis.meldung)
                    arbeit.append({"role": "tool", "name": name,
                                   "content": record.ergebnis.to_model()})
                # Ist der Plan abgearbeitet, endet die Schleife nach der
                # naechsten Runde von selbst - die Anweisung sagt dem Modell
                # dann ausdruecklich, kein Werkzeug mehr zu rufen und
                # zusammenzufassen. Frueher lief sie stur bis MAX_TOOL_STEPS.
                continue

            # --- Rückfallebene: JSON-Protokoll ---
            msg = self.llm.chat_message(anfrage)
            raw = msg.get("content", "") or ""
            if not _parse_action(raw) and stream_cb and raw:
                stream_cb(raw)
            aktion = _parse_action(raw)
            if not aktion and self.cfg.self_extension:
                fert = _fertigkeit_json_aus_text(raw)
                if fert:
                    aktion = ("fertigkeit_definieren", fert)
            if not aktion:
                letzte_antwort = raw or letzte_antwort
                if plan is not None and not plan.fertig and nachfassen < 2:
                    nachfassen += 1
                    arbeit.append({"role": "assistant", "content": raw})
                    arbeit.append({"role": "user", "content": _NACHFASSEN})
                    continue
                break
            name, args = aktion
            record, gelaufen = self._aufruf_im_lauf(name, args, on_tool)
            if gelaufen:
                calls.append(record)
                if plan is not None:
                    plan.vermerken(name, record.ergebnis.ok, record.ergebnis.meldung)
            arbeit.append({"role": "assistant", "content": raw})
            arbeit.append({
                "role": "user",
                "content": f"WERKZEUG-ERGEBNIS von {name}: {record.ergebnis.to_model()}\n"
                           "Mach weiter oder fasse in Prosa zusammen, was du getan hast.",
            })

        # Antwortpflicht: Steht nach der Schleife keine Prosa da, folgt die
        # Schlussrunde - statt des Protokollauszugs, der hier frueher stand.
        runde = self._runde()
        if ist_prosa_antwort(letzte_antwort, self._laufzeitnamen()):
            runde.endete_mit = runde.endete_mit or "prosa"
        elif calls or letzte_antwort or runde.abgewiesen:
            # Auch eine Runde aus lauter ABGEWIESENEN Aufrufen braucht die
            # Schlussrunde - sonst endete sie mit "Das Modell hat nichts
            # geantwortet", obwohl es nur das Falsche wollte.
            letzte_antwort, runde.endete_mit = self._schlussrunde(
                arbeit, calls, stream_cb)

        # Was der Plan NICHT geschafft hat, wird gesagt. Frueher endete ein
        # Lauf mit "Erledigt: ..." und zaehlte auf, was geglueckt war - was
        # offen blieb, stand nirgends. Der Nutzer hielt die Aufgabe fuer
        # erledigt und merkte es Tage spaeter.
        self._aufgabe_abschliessen(plan)

        if plan is not None and not plan.fertig:
            offen = plan.offene_melden()
            if offen:
                letzte_antwort = (letzte_antwort or "").rstrip() + (
                    f"\n\n**Nicht vollständig.** {offen}")
        return letzte_antwort, calls

    def _werkzeugliste(self) -> List[dict]:
        """Eingebaute Werkzeuge plus die Fertigkeiten, die sich der Agent gebaut hat."""
        werkzeuge = ollama_tool_definitions()

        # Ein Werkzeug, das in diesem Auftrag zweimal nichts geliefert hat,
        # wird WEGGENOMMEN statt hoeflich abgeraten. Am 21.09. stand in der
        # Ergebnismeldung ausdruecklich "weitere Umformulierungen helfen
        # nicht" - das Modell formulierte danach noch viermal um. Eine Bitte
        # an das Modell ist keine Schranke; eine fehlende Werkzeugdefinition
        # schon.
        if getattr(self.tools, "_netz_fehlschlaege", 0) >= 2:
            werkzeuge = [w for w in werkzeuge
                         if w.get("function", {}).get("name") != "web_suchen"]

        if not self.cfg.self_extension:
            return self._nach_weg_filtern(werkzeuge)
        werkzeuge.append({
            "type": "function",
            "function": {
                "name": "fertigkeit_definieren",
                "description": "Legt einen wiederkehrenden Ablauf als neue Fertigkeit AN "
                               "(führt ihn nicht aus). Schritte sind Aufrufe vorhandener "
                               "Werkzeuge. Platzhalter: deklarierte Parameter wie {thema}, "
                               "oder {ergebnisN} für den Text aus einem früheren Schritt. "
                               "{ergebnisN} darf nur in inhalt/text stehen, nie in "
                               "name/titel. Beispiel: "
                               "['notizen_suchen(frage=\"{thema}\", anzahl=8)', "
                               "'notiz_erstellen(titel=\"Übersicht {thema}\", "
                               "inhalt=\"{ergebnis1}\")'].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "kurzer Name ohne Leerzeichen"},
                        "beschreibung": {"type": "string", "description": "wozu die Fertigkeit dient"},
                        "parameter": {"type": "array", "items": {"type": "string"},
                                      "description": "Namen der Platzhalter"},
                        "schritte": {"type": "array", "items": {"type": "string"},
                                     "description": "Werkzeugaufrufe in Reihenfolge"},
                    },
                    "required": ["name", "beschreibung", "schritte"],
                },
            },
        })
        werkzeuge.append({
            "type": "function",
            "function": {
                "name": "verhalten_definieren",
                "description": "Legt eine VERHALTENSFERTIGKEIT an: eine Anleitung, die "
                               "ändert, WIE geantwortet wird, sobald eine Anfrage eines "
                               "der Stichwörter enthält. Führt nichts aus und ändert "
                               "nichts an der Vault. Dafür nehmen, wenn der Nutzer eine "
                               "Gewohnheit für die Darstellung, Formulierung oder Länge "
                               "der Antwort beschreibt - auch wenn er 'Skill' sagt.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "kurzer Name ohne Leerzeichen"},
                        "beschreibung": {"type": "string",
                                         "description": "ein Satz: was sie bewirkt"},
                        "ausloeser": {
                            "type": "array", "items": {"type": "string"},
                            "description": "mindestens zwei Stichwoerter, die in solchen "
                                           "Anfragen wirklich vorkommen; je drei Zeichen "
                                           "oder laenger"},
                        "anleitung": {
                            "type": "string",
                            "description": "Befehlsform: was konkret zu tun ist, wenn sie "
                                           "greift. Kein Konjunktiv."},
                        "beispiel": {"type": "string",
                                     "description": "optional: ein Anwendungsfall"},
                    },
                    "required": ["name", "beschreibung", "ausloeser", "anleitung"],
                },
            },
        })
        for fertigkeit in self.fertigkeiten.alle.values():
            werkzeuge.append({
                "type": "function",
                "function": {
                    "name": fertigkeit.name,
                    "description": f"[eigene Fertigkeit] {fertigkeit.beschreibung}",
                    "parameters": {
                        "type": "object",
                        "properties": {p: {"type": "string"} for p in fertigkeit.parameter},
                        "required": list(fertigkeit.parameter),
                    },
                },
            })
        return self._nach_weg_filtern(werkzeuge)

    # ------------------------------------------------------ Weg und Schranke
    # Vom Weg der Triage gesetzt, je Runde (siehe agent.ask). None heisst:
    # keine Einschraenkung - so wie vor der Triage.
    _weg_werkzeuge: Optional[Set[str]] = None
    _weg_schritte: Optional[int] = None
    # Je `_tool_loop` neu: was diese Runde schon geholt hat, und ihre Frage.
    _schutz: Optional[Schleifenschutz] = None
    _rundenfrage_msg: Optional[dict] = None

    def _nach_weg_filtern(self, werkzeuge: List[dict]) -> List[dict]:
        """Nur die Werkzeuge, die der Weg dieser Runde erlaubt.

        Eine fehlende Definition ist eine Schranke, eine Bitte im Prompt ist
        keine (siehe den Fall web_suchen oben). Das engt nur ein: Was hier
        steht, geht trotzdem durch das Werkzeugtor.
        """
        erlaubt = getattr(self, "_weg_werkzeuge", None)
        if erlaubt is None:
            return werkzeuge
        return [w for w in werkzeuge
                if w.get("function", {}).get("name") in erlaubt]

    def _vor_ausfuehrung_abweisen(self, name: str, args: dict) -> str:
        """Grund, einen Aufruf NICHT auszufuehren - oder ''.

        Sitzt VOR `_run_tool` und damit vor dem Werkzeugtor. Es kann nur
        ablehnen, nie etwas erlauben, was das Tor verbietet.
        """
        erlaubt = getattr(self, "_weg_werkzeuge", None)
        if erlaubt is not None and name not in erlaubt:
            runde = self._runde()
            return (f"'{name}' ist auf dem Weg {runde.weg.upper() or '?'} nicht "
                    f"verfuegbar. Erlaubt sind hier: {', '.join(sorted(erlaubt)) or 'keine'}. "
                    f"Beantworte die Frage mit dem, was du hast.")
        schutz = getattr(self, "_schutz", None)
        if schutz is not None:
            return schutz.pruefen(name, args)
        return ""

    def _aufruf_im_lauf(self, name: str, args: dict, on_tool):
        """Ein Aufruf aus der Schleife: erst pruefen, dann ausfuehren.

        Gibt (record, ausgefuehrt) zurueck. Ein abgewiesener Aufruf wird dem
        Modell beantwortet, zaehlt aber NICHT als Werkzeugaufruf - er steht
        weder im Tatsachenbericht noch in der Antwort.
        """
        args = args if isinstance(args, dict) else {}
        grund = self._vor_ausfuehrung_abweisen(name, args)
        if not grund:
            record = self._run_tool(name, args, on_tool)
            schutz = getattr(self, "_schutz", None)
            if schutz is not None:
                schutz.vermerken(name, args, record.ergebnis)
            return record, True
        self._runde().abgewiesen += 1
        melden = getattr(self, "_abweisung_gemeldet", None)
        if callable(melden):
            melden(name, args)
        record = ToolCallRecord(werkzeug=name, parameter=args,
                                ergebnis=ToolResult(False, grund, {"abgewiesen": True}))
        return record, False

    def _laufzeitnamen(self) -> Set[str]:
        """Werkzeugnamen, die es nur zur Laufzeit gibt.

        Das sind 'fertigkeit_definieren' und alles, was sich der Agent selbst
        gebaut hat. TOOL_NAMES kennt sie nicht - deshalb hielt die Prosa-
        Erkennung einen fertig hingeschriebenen fertigkeit_definieren(...)
        für Fließtext und zeigte ihn dem Nutzer als Antwort an, statt ihn
        auszuführen.
        """
        if not getattr(self.cfg, "self_extension", False):
            return set()
        namen = {"fertigkeit_definieren", "verhalten_definieren"}
        try:
            namen.update(self.fertigkeiten.alle.keys())
        except Exception:
            pass
        return namen

    def _nachpruefen(self, name: str, args: dict, ergebnis):
        """Sieht unabhängig nach, ob die gemeldete Änderung stattgefunden hat.

        Drei Ausgänge, und die Unterscheidung ist der ganze Punkt:

            bestanden       Der Erfolg bleibt Erfolg, die Prüfung steht dabei.
            nicht prüfbar   Der Erfolg bleibt stehen, aber unbestätigt.
            durchgefallen   Aus ok=True wird ok=False - mit Begründung.

        Das Umdrehen ist Absicht. Eine Erfolgsmeldung, die einer Gegenprobe
        nicht standhält, ist schlimmer als ein Fehler: Der Nutzer glaubt sie
        und merkt es Tage später.
        """
        if not getattr(ergebnis, "ok", False):
            return ergebnis
        try:
            if self._verifikation is None:
                from ..kern.verifikation import Verifikation
                self._verifikation = Verifikation(self.tools, self.cfg)
            befund = self._verifikation.pruefen(name, args, ergebnis)
        except Exception as exc:
            # Eine kaputte Prüfung darf niemals eine geglückte Änderung
            # verwerfen. Sie meldet sich und tritt beiseite.
            melden = getattr(self, "melden", None)
            if callable(melden):
                melden(f"Verifikation übersprungen: {type(exc).__name__}: {exc}")
            return ergebnis

        daten = dict(ergebnis.daten or {})
        daten["verifikation"] = befund.urteil
        if befund.urteil == "durchgefallen":
            daten["pruefbefunde"] = befund.befunde
            return ToolResult(
                False,
                f"{ergebnis.meldung} ABER DIE GEGENPROBE SCHEITERT: "
                f"{'; '.join(befund.befunde)}. Melde das NICHT als Erfolg.",
                daten)
        if befund.geprueft:
            daten["geprueft"] = befund.geprueft
        return ToolResult(True, ergebnis.meldung, daten)

    def _run_tool(self, name: str, args: dict, on_tool) -> ToolCallRecord:
        args = args if isinstance(args, dict) else {}

        # Wiederholungssperre. Ohne sie ruft ein kleines Modell denselben
        # fehlschlagenden Aufruf beliebig oft auf ("Notiz '' nicht gefunden",
        # fünfmal hintereinander) und kommt nie zum Ziel. Der zweite Versuch
        # wird nicht mehr ausgeführt, sondern beantwortet - mit dem Hinweis,
        # dass sich etwas ändern muss.
        signatur = self._aufruf_signatur(name, args)
        vorher = self._aufrufe.get(signatur)
        if vorher is not None:
            self._aufrufe[signatur] = vorher + 1
            ergebnis = ToolResult(
                False,
                f"Identischer Aufruf von '{name}' – bereits versucht, Ergebnis war: "
                f"{self._letzte_meldung.get(signatur, 'Fehler')} "
                f"Derselbe Aufruf führt zum selben Ergebnis. Ändere die Parameter "
                f"oder nimm ein anderes Werkzeug.",
                {"wiederholung": vorher + 1,
                 "hinweis": _WERKZEUG_HINWEISE.get(name, ""),
                 "verfuegbar": sorted(TOOL_NAMES)},
            )
            # Auch der abgeblockte Versuch zählt: Nur so wird sichtbar, dass
            # das Modell festhängt, statt dass die Liste bei "1 Versuch" bleibt.
            self.aufgaben.aus_fehlschlag(name, args, ergebnis.meldung)
            self._runde().abgewiesen += 1
            record = ToolCallRecord(werkzeug=name, parameter=args, ergebnis=ergebnis)
            if on_tool:
                on_tool(record)
            return record
        self._aufrufe[signatur] = 1

        # ---------------------------------------------------- Werkzeugtor
        # Seit dem Umbau geht JEDER Werkzeugaufruf durch dieselbe Tuer -
        # auch der aus dem Gespraech. Das Tor prueft Recht, Freigabe und
        # Idempotenz, fuehrt aus und sieht unabhaengig nach, ob wirklich
        # geschehen ist, was gemeldet wird. Vorher hatte diese Schleife
        # ihren eigenen Weg, und auf dem wurde die Rechtestufe NIE geprueft:
        # Auf Stufe 0 ("lesen - aendert nichts") lief notiz_loeschen durch.
        #
        # `unbeaufsichtigt=False`, weil hier ein Mensch davorsitzt und den
        # Auftrag gerade erteilt hat. Ihn nach jeder Loeschung zu fragen,
        # die er selbst verlangt hat, macht die Frage wertlos.
        tor = getattr(self, "_werkzeugtor", None)
        if tor is not None:
            try:
                ergebnis = tor.ausfuehren(name, args, unbeaufsichtigt=False)
            except Exception as exc:
                ergebnis = ToolResult(
                    False, f"'{name}' konnte nicht ausgefuehrt werden: "
                           f"{type(exc).__name__}: {exc}", {})
        else:
            ergebnis = self._ohne_tor(name, args)
            ergebnis = self._nachpruefen(name, args, ergebnis)
            try:
                from .. import nutzung
                nutzung.vermerken(name, ergebnis.ok, self.cfg)
            except Exception:
                # Beiwerk. Eine kaputte Strichliste darf keinen Auftrag
                # abbrechen.
                pass

        self._letzte_meldung[signatur] = ergebnis.meldung[:160]
        # Fehlschläge werden festgehalten, damit sie nicht im Gesprächsverlauf
        # verschwinden - abgehakt wird später, wenn dieselbe Sache klappt.
        if not ergebnis.ok:
            self.aufgaben.aus_fehlschlag(name, args, ergebnis.meldung)
        else:
            self.aufgaben.erledigt_melden(name, args, ergebnis.meldung)

        record = ToolCallRecord(werkzeug=name, parameter=args, ergebnis=ergebnis)
        self._runde().werkzeug_vermerken(name, ergebnis.ok)
        if on_tool:
            on_tool(record)
        return record

    def werkzeugtor_setzen(self, tor) -> None:
        """Haengt das Werkzeugtor ein. Wird vom AufgabenModul gerufen.

        Bleibt es ungesetzt, laeuft die Schleife wie vor dem Umbau: eigene
        Ausfuehrung, eigene Verifikation, keine Rechtepruefung. Das ist die
        Rueckfallebene fuer den Fall, dass der Kern nicht hochkommt - und
        nicht der Normalfall.
        """
        self._werkzeugtor = tor

    def _ohne_tor(self, name: str, args: dict):
        """Der alte Weg - nur noch, wenn kein Tor da ist."""
        if name == "fertigkeit_definieren" and self.cfg.self_extension:
            return self.fertigkeiten.definieren(
                name=args.get("name", ""),
                beschreibung=args.get("beschreibung", ""),
                parameter=args.get("parameter") or [],
                schritte=args.get("schritte") or [],
            )
        if name == "verhalten_definieren" and self.cfg.self_extension:
            return self.verhalten.definieren(
                name=args.get("name", ""),
                beschreibung=args.get("beschreibung", ""),
                ausloeser=args.get("ausloeser") or [],
                anleitung=args.get("anleitung", ""),
                beispiel=args.get("beispiel", ""),
            )
        if name in self.fertigkeiten.alle:
            return self.fertigkeiten.ausfuehren(name, args)
        return self.tools.ausfuehren(name, args)

    @staticmethod
    def _aufruf_signatur(name: str, args: dict) -> str:
        """Erkennungsmerkmal eines Aufrufs - für die Wiederholungssperre."""
        try:
            fest = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            fest = str(sorted((args or {}).items()))
        return f"{name}|{fest}"

    def _bekannte_werkzeuge(self) -> List[str]:
        """Werkzeuge und eigene Fertigkeiten - für ihn ist beides ein Werkzeug."""
        namen = list(TOOL_NAMES)
        if getattr(self.cfg, "self_extension", False):
            namen.append("fertigkeit_definieren")
            try:
                namen.extend(self.fertigkeiten.alle.keys())
            except Exception:                            # noqa: BLE001
                pass
        return sorted(set(namen))

    def _ausdrueckliche_rufe(self, frage: str, hits, folder_filter: str,
                             context: str, on_tool, stream_cb) -> Optional[Answer]:
        """Führt `[werkzeug]`-Rufe aus. None heißt: es waren keine drin."""
        bekannte = self._bekannte_werkzeuge()
        rufe = werkzeugruf.finden(frage, bekannte)
        if not rufe:
            return None

        rest = werkzeugruf.text_ohne_rufe(frage, rufe)
        ordner = folder_filter or self._ordner_aus_text(rest)
        calls: List[ToolCallRecord] = []
        zeilen: List[str] = []

        for ruf in rufe:
            if not ruf.bekannt:
                # NIE behaupten, es gebe das Werkzeug nicht, ohne zu sagen,
                # was es stattdessen gibt. Genau diese Auskunft hat im
                # Protokoll gefehlt.
                nah = werkzeugruf.aehnliche(ruf.name, bekannte)
                zeilen.append(
                    f"**`{ruf.name}` gibt es nicht.**"
                    + (f" Gemeint sein könnte: "
                       + ", ".join(f"`{n}`" for n in nah) + "." if nah else "")
                    + f" Insgesamt kennt er {len(bekannte)} Werkzeuge – `/werkzeuge` "
                      f"listet sie.")
                continue
            parameter = TOOL_PARAMS.get(ruf.name, ())
            args = werkzeugruf.argumente_ergaenzen(ruf, parameter,
                                                   ordner=ordner, rest=rest)
            record = self._run_tool(ruf.name, args, on_tool)
            calls.append(record)

        if not calls:
            return Answer(text="\n\n".join(zeilen), hits=hits,
                          folder_filter=folder_filter, kind="werkzeug", tool_calls=calls)

        # Das Modell fasst NUR zusammen, was die Werkzeuge geliefert haben.
        # Es entscheidet nicht mehr, ob und was aufgerufen wird - das ist
        # bereits geschehen.
        ergebnisse = "\n\n".join(
            f"### {c.werkzeug}({', '.join(f'{k}={v}' for k, v in (c.parameter or {}).items())})\n"
            f"{'OK' if c.ergebnis.ok else 'FEHLGESCHLAGEN'}: {c.ergebnis.to_model()}"
            for c in calls)
        auftrag = (
            f"Der Nutzer hat diese Werkzeuge ausdrücklich angefordert. Sie sind "
            f"bereits gelaufen. Berichte in Prosa, WAS dabei herauskam - knapp, "
            f"ohne Wiederholung der Rohdaten, und ohne etwas zu erfinden, das "
            f"nicht im Ergebnis steht.\n\n"
            + (f"Zusätzlicher Auftrag des Nutzers: {rest}\n\n" if rest else "")
            + f"ERGEBNISSE:\n{ergebnisse}")
        try:
            text = self.llm.chat([
                {"role": "system", "content": self.system_prompt(context, False,
                                                                 with_tools=False)},
                {"role": "user", "content": auftrag},
            ], stream_cb=stream_cb)
        except OllamaError as exc:
            text = self._verbindungshinweis(exc)

        if zeilen:
            text = "\n\n".join(zeilen) + "\n\n" + (text or "")
        text = self._tatsachen_anhaengen(text, calls)
        self.history.append({"role": "user", "content": frage})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-2 * self.cfg.history_turns:]
        return Answer(text=text or "", hits=hits, folder_filter=folder_filter,
                      kind="werkzeug", tool_calls=calls)
