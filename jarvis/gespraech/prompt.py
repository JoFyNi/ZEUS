"""Was das Modell zu lesen bekommt.

Der Systemprompt ist der einzige Ort, an dem Persona, Regeln,
Struktur, Gedaechtnis, Werkzeuge und Kontext zusammenkommen - und
der einzige, an dem sie um denselben begrenzten Platz
konkurrieren. Deshalb liegen die Budgetrechnung und das Kuerzen
hier und nicht verstreut: Wer an einer Stelle mehr hineinschreibt,
nimmt es woanders weg, und das soll man sehen koennen.

Die Lehre aus v5 steht in `_auf_budget_kuerzen`: Ollama schneidet
einen zu langen Prompt still am ANFANG ab - dort, wo die
Anweisungen stehen.
"""
from __future__ import annotations

import json

from typing import List, Optional, Sequence, Tuple

from ..index import Hit
from ..learning import BESTAETIGT
from ..sprache import auf_last_kuerzen, textlast
from ..tools import (
    tool_reference,
)
from ..vault import fold

from .texte import FOLDER_HINT_RE, GEDAECHTNIS_ANTEIL, _fragt_nach_fehlern


# So viel muss dem Prompt nach Abzug der Werkzeugschemas mindestens bleiben,
# sonst stehen weder Anweisungen noch Gespraech darin. Unterschreitet die
# Konfiguration das, ist nicht der Prompt zu gross, sondern NUM_CTX zu klein.
MINDESTPLATZ = 6000


def _faktzeile(fakt) -> str:
    """'[2026-09-05, vorläufig] Text (ersetzt: ...)' - Herkunft und Stand."""
    stand = {BESTAETIGT: "bestätigt"}.get(getattr(fakt, "status", ""), "vorläufig")
    tag = getattr(fakt, "tag", "") or (getattr(fakt, "zeit", "") or "")[:10]
    kopf = f"[{tag}, {stand}] " if tag else f"[{stand}] "
    ersetzt = getattr(fakt, "ersetzt", "")
    return kopf + fakt.text + (f" (ersetzt die frühere Angabe: {ersetzt})" if ersetzt else "")


class PromptTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""
    # Einmal gemessen, dann gemerkt - die Schemas aendern sich zur Laufzeit nicht.
    _werkzeug_bytes = None



    # ------------------------------------------------------------- Gedächtnis
    def _gedaechtnisblock(self, frage: str) -> str:
        """Lektionen und Sitzungsfakten aus EINEM gemeinsamen Zeichenbudget.

        Warum gemeinsam: Der Systemprompt ist gedeckelt (siehe
        `prompt_char_budget`). Zwei getrennte Kontingente heißt in der Praxis,
        dass beide zusammen den Notizkontext verdrängen - und der ist das,
        wofür der Agent überhaupt da ist. Ein Budget zwingt zur Entscheidung.

        Reihenfolge des Weichens (unwichtigstes zuerst): vorläufige
        Sitzungsfakten → vorläufige Lektionen → Startauszug → Notizkontext.
        Bestätigtes weicht zuletzt: Es hat sich mehrfach bewährt, während eine
        Hypothese noch nichts bewiesen hat.

        Was nicht hineinpasst, verschwindet nicht stillschweigend: Die Zahl
        steht in `letzter_kontext`, in `/status` und als Warnung im Protokoll.
        Genau das stille Abschneiden hat schon einmal wie Vergesslichkeit ohne
        Ursache ausgesehen.
        """
        if self.gedaechtnis_aus:
            return ""

        budget = int(self.cfg.prompt_char_budget * GEDAECHTNIS_ANTEIL)
        lernen_an = bool(self.cfg.learning_enabled)
        sitzung_an = bool(getattr(self.cfg, "session_memory", False))
        # Die Sitzungsfakten kommen ueber einen Vektorabruf. Auf den Wegen
        # RECHERCHE und WERKZEUG gibt es keinen (gespraech/wege.py) - auch
        # nicht durch diese Hintertuer.
        if getattr(self, "_ohne_vektorabruf", False):
            sitzung_an = False

        lektionen = (self.lernwerk.relevante_lektionen_voll(frage)
                     if lernen_an else [])
        fakten = self.sitzung.passende_fakten(frage) if sitzung_an else []
        auszug = self.sitzung.startauszug() if sitzung_an else ""

        # (Rang, Länge, Art, Nutzlast). Kleiner Rang = weicht später.
        posten: List[tuple] = []
        for eintrag in lektionen:
            posten.append((0 if eintrag.status == BESTAETIGT else 2,
                           len(eintrag.text) + 3, "lektion", eintrag))
        for fakt in fakten:
            posten.append((1 if fakt.status == BESTAETIGT else 3,
                           len(fakt.text) + 3, "fakt", fakt))

        genommen: List[tuple] = []
        verbraucht = 0
        ausgelassen = {"lektionen": 0, "fakten": 0, "auszug": 0}
        for rang, laenge, art, nutzlast in sorted(posten, key=lambda p: p[0]):
            if verbraucht + laenge > budget:
                ausgelassen["lektionen" if art == "lektion" else "fakten"] += 1
                continue
            verbraucht += laenge
            genommen.append((art, nutzlast))

        # Der Startauszug bekommt, was übrig bleibt: Er ist der größte Posten
        # und zugleich der entbehrlichste - was darin steht, findet der Abruf
        # pro Frage ohnehin noch einmal.
        rest = budget - verbraucht
        if auszug and len(auszug) > rest:
            ausgelassen["auszug"] = len(auszug) - max(0, rest)
            auszug = (auszug[:rest].rsplit("\n", 1)[0] + "\n- […]"
                      if rest > 200 else "")

        genommene_lektionen = [n for a, n in genommen if a == "lektion"]
        genommene_fakten = [n for a, n in genommen if a == "fakt"]
        # Bewährung nur für das, was TATSÄCHLICH im Prompt stand. Vorher wurde
        # eine Lektion auch dann als "verwendet" gebucht, wenn das Kürzen sie
        # hinterher wieder herauswarf - das verfälscht ihre Bilanz.
        if lernen_an:
            self.lernwerk.lektion_verwendet([e.text for e in genommene_lektionen])
        if sitzung_an:
            self.sitzung.fakt_verwendet(genommene_fakten)

        if isinstance(self.letzter_kontext, dict):
            self.letzter_kontext["lektionen"] = len(genommene_lektionen)
            self.letzter_kontext["sitzungsfakten"] = len(genommene_fakten)
            self.letzter_kontext["gedaechtnis_zeichen"] = verbraucht + len(auszug)
            self.letzter_kontext["gedaechtnis_ausgelassen"] = dict(ausgelassen)
        self._gedaechtnis_melden(ausgelassen, budget)

        teile: List[str] = []
        if genommene_lektionen:
            teile.append("\nGELERNTE REGELN (aus früheren Korrekturen von "
                         f"{self.cfg.user_title} – halte dich daran):\n"
                         + "\n".join(f"- {e.text}" for e in genommene_lektionen)
                         + "\n")
        if genommene_fakten:
            # Bis zum 12.10.2026 stand hier "belegt" - fuer Saetze, die ein
            # Modell aus dem Verlauf verdichtet hat und die zum groessten Teil
            # noch 'vorlaeufig' sind. Jetzt steht bei jedem, woher er kommt und
            # wie weit er sich bewaehrt hat; und was er ersetzt hat.
            teile.append("\nAUS FRÜHEREN SITZUNGEN (vom Modell aus dem Verlauf verdichtet; "
                         "Datum und Stand stehen dabei – 'bestätigt' hat sich bewährt, "
                         "'vorläufig' nicht. Nicht erneut erfragen, nicht ungefragt "
                         "aufzählen; widerspricht eine neue Aussage, gilt die neue):\n"
                         + "\n".join(f"- {_faktzeile(f)}" for f in genommene_fakten) + "\n")
        if auszug:
            teile.append(f"\nANKNÜPFUNG AN DIE LETZTE SITZUNG:\n{auszug}\n")
        return "".join(teile)

    def _gedaechtnis_melden(self, ausgelassen: dict, budget: int) -> None:
        """Ausgelassene Erinnerungen ins Protokoll - je Lage nur einmal."""
        if not any(ausgelassen.values()):
            return
        marke = (ausgelassen["lektionen"], ausgelassen["fakten"],
                 bool(ausgelassen["auszug"]))
        if getattr(self, "_gedaechtnis_gemeldet", None) == marke:
            return
        self._gedaechtnis_gemeldet = marke
        teile = []
        if ausgelassen["fakten"]:
            teile.append(f"{ausgelassen['fakten']} vorläufige Sitzungsfakten")
        if ausgelassen["lektionen"]:
            teile.append(f"{ausgelassen['lektionen']} Lektionen")
        if ausgelassen["auszug"]:
            teile.append(f"{ausgelassen['auszug']} Zeichen Anknüpfung")
        try:
            self.tools.log.warnung(
                "Kontextbudget: " + " und ".join(teile)
                + f" passten nicht in {budget} Zeichen Gedächtnis.", "gedaechtnis")
        except Exception:
            pass


    # --------------------------------------------------------------- Persona
    def system_prompt(self, context: str = "", structure_focus: bool = False,
                      with_tools: bool = False, frage: str = "") -> str:
        s = self.cfg
        base = f"""Du bist {s.assistant_name}, der Wissens-Agent von {s.user_title} –
ruhig, präzise, trocken-höflich.

SPRACHE: Du antwortest AUSSCHLIESSLICH auf {s.language}. Auch wenn Notizen,
Fachbegriffe oder Werkzeugausgaben englisch sind, bleibt deine Antwort auf
{s.language}. Du redest {s.user_title} gelegentlich mit "{s.user_title}" an,
ohne es zu übertreiben.

Die Obsidian-Vault ist DEIN Arbeitsspeicher und Lernmedium. Du liest, schreibst,
strukturierst und verknüpfst darin eigenständig – sie ist nicht bloß Nachschlagewerk,
sondern das Gedächtnis, an dem du dich weiterentwickelst.

BEGRIFFE: "Vault" ist IMMER diese Obsidian-Notizsammlung – nie HashiCorp Vault,
kein Passwort-Safe, kein Tresor. "Notiz" ist eine Markdown-Datei darin,
"Fertigkeit" ein von dir hinterlegter Ablauf aus vorhandenen Werkzeugen.
Ist ein Wort mehrdeutig, gilt die Bedeutung aus diesem System, nicht die
bekanntere aus deinem Weltwissen.

WOHER DIE ANTWORT KOMMT - das entscheidest du VOR dem ersten Werkzeugaufruf:
* Fragen zu SEINEM Wissen, seinen Projekten, seinen Notizen  ->  notizen_suchen
* Fragen zur Welt: Produkte, Hersteller, Systemvoraussetzungen, Preise,
  Nachrichten, alles Aktuelle  ->  web_suchen, danach web_lesen
* Fragen zu DIESEM Programm  ->  code_suchen, quellcode_lesen

Sagt er "online", "im Internet", "recherchiere", "schau nach" oder fragt nach
etwas, das nicht aus seinen Notizen stammen kann, dann ist notizen_suchen die
FALSCHE Wahl. Seine Vault enthaelt keine Herstellerangaben.

WENN DU ETWAS NICHT KANNST, SAG ES. Greif NIE zum naechstaehnlichen Werkzeug
und melde Erfolg. Am 21.09. wurde dreimal nach einer Onlinerecherche gefragt;
dreimal wurde stattdessen die Vault durchsucht und "Erledigt" gemeldet. Das
ist die schlimmste Antwort, die du geben kannst - schlimmer als "ich kann das
nicht", weil der Nutzer dir glaubt und es erst Tage spaeter merkt.

REGELN:
1. Antworte auf Basis der Notizen. Was dort nicht steht, kennzeichnest du als eigene
   Einschätzung ("Nicht in Ihren Notizen belegt, aber …").
2. Nenne Notizen im Fließtext als [[Notizname]].
3. Kein Füllgeschwätz. Bei Widersprüchen in den Notizen: benenne sie.
4. Wirkt ein Auszug abgeschnitten oder unvollständig, lade die Notiz mit
   notiz_lesen nach, bevor du antwortest. Rate nie den fehlenden Teil.

VAULT-STRUKTUR (immer aktuell):
{self.structure.overview()}
"""
        if with_tools:
            base += f"""
WERKZEUGE – WICHTIG:
Du kannst die Vault tatsächlich verändern. Behaupte NIEMALS, etwas angelegt,
geändert oder verschoben zu haben, ohne das passende Werkzeug aufgerufen zu haben.
Eine Antwort wie "Ich habe die Notiz erstellt" ohne Werkzeugaufruf ist ein Fehler.

Arbeitsweise:
- Erst prüfen (notizen_suchen / notiz_lesen / struktur_anzeigen), dann handeln.
- Neue Erkenntnis, die es noch nicht gibt → notiz_erstellen.
- Ergänzung zu Vorhandenem → notiz_ergaenzen statt neuer Notiz.
- Nach dem Anlegen sinnvoll verknüpfen (verwandte_finden → notizen_verknuepfen).
- Beschreibe zum Schluss in einem Satz, was du tatsächlich getan hast.
"""
            if not self.native_tools:
                base += f"""
Dieses Modell hat keine native Werkzeug-Schnittstelle. Um ein Werkzeug zu benutzen,
antwortest du mit NICHTS ALS diesem JSON-Block:

```json
{{"werkzeug": "notiz_erstellen", "parameter": {{"titel": "…", "inhalt": "…"}}}}
```

Du erhältst dann das Ergebnis und machst weiter. Wenn kein Werkzeug nötig ist,
antworte einfach normal in Prosa (ohne JSON-Block).

VERFÜGBARE WERKZEUGE:
{tool_reference()}
"""
        # --- Verbindliche Regeln aus dem Regelordner ---
        # Ganz oben und wörtlich. Sie stehen in der Vault, sind dort von Hand
        # änderbar und für den Agenten schreibgeschützt.
        try:
            from ..regeln import promptblock
            base += promptblock(self.cfg)
        except Exception:
            # Ein unlesbarer Regelordner darf keine Anfrage verhindern. Der
            # Zustand ist über /regeln sichtbar.
            pass

        # --- Selbstauskunft: Fragt der Nutzer nach Fehlschlägen, soll die Antwort
        #     auf echten Daten beruhen statt auf Vermutungen des Modells. ---
        if frage and _fragt_nach_fehlern(frage):
            base += "\n" + self._fehlerbericht()

        # --- Gedächtnis: Lektionen und Sitzungsfakten aus EINEM Budget ---
        # gedaechtnis_aus schaltet beides für den A/B-Vergleich im Messstand ab.
        # Ohne diesen Schalter misst die Variante "ohne Gedächtnis" nichts.
        gedaechtnis = self._gedaechtnisblock(frage or context[:400])
        base += gedaechtnis

        if with_tools and self.cfg.code_root_list:
            wurzeln = ", ".join(str(w) for w in self.cfg.code_root_list[:2])
            base += f"""
EIGENER QUELLCODE:
Du kannst deinen eigenen Code LESEN: code_dateien (Überblick), code_suchen
(Fundstellen), quellcode_lesen (Datei oder Zeilenbereich), codebasis_importieren
(Code als Notizen ablegen, dann findet ihn auch die Suche).
Verzeichnis: {wurzeln}
Du kannst den Code NICHT ändern - beschreibe Verbesserungen konkret mit Datei und
Zeilennummer, damit {s.user_title} sie umsetzen kann.
"""

        if with_tools and self.cfg.self_extension and not self.gedaechtnis_aus:
            eigene = self.fertigkeiten.uebersicht()
            if isinstance(self.letzter_kontext, dict):
                self.letzter_kontext["fertigkeiten"] = len(self.fertigkeiten.alle)
            if not eigene.startswith("(noch"):
                base += f"\nEIGENE FERTIGKEITEN (selbst gebaut, wie Werkzeuge aufrufbar):\n{eigene}\n"
            verhalten_uebersicht = self.verhalten.uebersicht()
            if not verhalten_uebersicht.startswith("(noch"):
                base += ("\nEIGENE VERHALTENSFERTIGKEITEN (ändern, WIE du antwortest; "
                         "nicht aufrufbar, sie greifen von selbst):\n"
                         f"{verhalten_uebersicht}\n")
            if self._fertigkeit_gewuenscht:
                # Ohne diese Ansage antwortet das Modell mit einer BESCHREIBUNG,
                # wie man die Fertigkeit bauen könnte, statt sie zu bauen. Genau
                # das ist passiert: eine Tabelle mit Ratschlägen, und angelegt
                # war nichts.
                if self._fertigkeit_art == "verhalten":
                    # Am 04.09. wurde aus genau diesem Wunsch der Versuch, eine
                    # Werkzeugkette zu bauen, dann eine Notiz, dann eine
                    # Erklärung, warum es nicht geht. Es ging nicht, weil es die
                    # passende Form nicht gab. Jetzt gibt es sie - und das
                    # Modell wird ausdrücklich darauf gestoßen.
                    base += (
                        "\nDER NUTZER WILL EINE VERHALTENSFERTIGKEIT ANLEGEN – er "
                        "beschreibt, WIE du antworten sollst, nicht was du an der "
                        "Vault tun sollst. Rufe dafür verhalten_definieren(...) auf – "
                        "jetzt, in dieser Antwort.\n"
                        "Nimm NICHT fertigkeit_definieren: Das ist eine Werkzeugkette "
                        "und kann kein Antwortverhalten ausdrücken.\n"
                        "Lege auch KEINE Notiz an und erkläre nicht, wie man es machen "
                        "könnte. Als 'ausloeser' nimm die Wörter, die in solchen "
                        "Anfragen wirklich vorkommen – die Wörter aus SEINER Anfrage "
                        "sind der beste Anfang. Die 'anleitung' schreibst du in der "
                        "Befehlsform: was konkret zu tun ist, nicht was gut wäre.\n")
                else:
                    base += (
                        "\nDER NUTZER WILL EINE FERTIGKEIT ANLEGEN. Rufe dafür "
                        "fertigkeit_definieren(...) auf – jetzt, in dieser Antwort. "
                        "Erkläre NICHT, wie man es machen könnte, und lege dafür "
                        "auch keine Notiz an: Eine Fertigkeit ist ein ausführbarer "
                        "Ablauf, keine Beschreibung. Fehlt dir ein Parameter, "
                        "wähle einen sinnvollen und sage hinterher, welchen.\n"
                        "Geht es dem Nutzer in Wahrheit darum, WIE du antwortest "
                        "(darstellen, formulieren, zeigen), nimm stattdessen "
                        "verhalten_definieren(...).\n")
            base += (
                "\nWiederkehrende Abläufe darfst du dir als Fertigkeit merken: "
                "fertigkeit_definieren(name, beschreibung, parameter, schritte).\n"
                "REGELN FÜR SCHRITTE (werden geprüft, falsche Fertigkeiten werden abgelehnt):\n"
                "1. Jeder Platzhalter ist entweder ein deklarierter Parameter oder "
                "{ergebnisN} mit N = Nummer eines FRÜHEREN Schritts. {ergebnis} ohne Zahl "
                "und Verweise nach vorn sind ungültig.\n"
                "2. {ergebnisN} enthält TEXT. Es gehört in inhalt/text – niemals in "
                "name/titel/ziel. Eine Notiz, die wie eine Trefferliste heißt, gibt es nicht.\n"
                "3. Namen von Notizen kommen aus Parametern, nicht aus Suchergebnissen.\n"
                "Beispiel:\n"
                '  parameter: [thema]\n'
                '  1. notizen_suchen(frage="{thema}", anzahl=8)\n'
                '  2. notiz_erstellen(titel="Übersicht {thema}", inhalt="{ergebnis1}")\n'
                '  3. verwandte_finden(name="Übersicht {thema}")\n'
                "Anlegen ist NICHT Ausführen: nach fertigkeit_definieren ist noch nichts "
                "passiert, und es gibt keine automatische Wiederholung. Behaupte beides nie.\n"
                "\nZWEI ARTEN VON FERTIGKEIT – nimm die passende:\n"
                "  fertigkeit_definieren  TUT etwas an der Vault (suchen, anlegen, "
                "verknüpfen). Eine Folge von Werkzeugaufrufen.\n"
                "  verhalten_definieren   ÄNDERT, WIE du antwortest (darstellen, "
                "formulieren, zeigen, kürzen). Kein Werkzeugaufruf, sondern eine "
                "Anleitung, die bei passenden Stichwörtern von selbst greift.\n"
                "Beschreibt der Nutzer eine Gewohnheit für die Antwort, ist es das "
                "Zweite – auch wenn er 'Skill' sagt.\n"
            )

        # Ausgelöste Verhaltensfertigkeiten. Sie stehen bewusst NACH den
        # Werkzeugregeln und VOR dem Kontext: Sie sollen die Form der Antwort
        # bestimmen, aber nie den Notizkontext aus dem Budget drängen - das
        # eigene Budget in verhalten.py deckelt sie dafür.
        if with_tools and self.cfg.self_extension and not self.gedaechtnis_aus:
            block = self.verhalten.promptblock(frage or "")
            if block:
                base += block
                if isinstance(self.letzter_kontext, dict):
                    self.letzter_kontext["verhalten"] = [
                        f.name for f in self.verhalten.passend(frage or "")]

        # Wie der Gesprächsverlauf zu lesen ist. Ohne diese Ansage suchte das
        # Modell nach etwas, das drei Zeilen weiter oben im selben Prompt
        # steht - und behauptete bei einer Anschlussfrage regelmäßig, dies sei
        # die erste Nachricht.
        base += (
            "\nGESPRÄCHSVERLAUF:\n"
            "Die Nachrichten vor der aktuellen Frage sind dieses Gespräch. Sie sind "
            "Tatsachen, keine Vermutungen: Was dort steht, wurde wirklich gesagt.\n"
            "- Suche NIEMALS in der Vault nach etwas, das im Verlauf steht. Die Vault "
            "kennt nur abgelegte Notizen, nicht das laufende Gespräch.\n"
            "- Sage NIE, dies sei die erste Nachricht oder du hättest keinen Verlauf, "
            "solange oben Nachrichten stehen.\n"
            "- Bezieht sich die Frage auf etwas eben Besprochenes ('die Notiz', 'so wie "
            "gesagt'), nimm es von dort.\n")

        offen = self.aufgaben.fuer_prompt() if with_tools else ""

        # --- Arbeitsgedächtnis (kontextwahl.py): Vorgaben, Bezüge, Widersprüche,
        #     frühere Gesprächsstellen - und auf dem Weg VAULT die Beleglage.
        #     Es steht VOR dem Notizkontext: Gekürzt wird von hinten, und eine
        #     Vorgabe des Nutzers wiegt schwerer als der fünfte Suchtreffer.
        arbeit = getattr(self, "_arbeit", None)
        if (arbeit is not None and frage and not arbeit.beleg
                and getattr(self._runde(), "weg", "") == "vault"):
            self._beleglage_setzen(arbeit, frage, [
                context, gedaechtnis, offen,
                *(str(m.get("content") or "") for m in self.history),
                *(f"{t.zug.frage} {t.zug.antwort}" for t in arbeit.erinnerungen)])
        base += self._arbeitsblock()

        if offen:
            base += f"\n{offen}\n"

        if structure_focus:
            base += (
                "\nDie Frage betrifft die Struktur der Vault. Antworte anhand des Ordnerbaums; "
                "erfinde keine Ordner.\n"
            )
        if context:
            base += f"\nKONTEXT AUS DEN NOTIZEN:\n{context}\n"

        # Harte Grenze: ein Prompt über dem Kontextfenster wird von Ollama
        # stillschweigend beschnitten - dann fehlen zuerst die Anweisungen.
        budget = self.cfg.prompt_char_budget
        if len(base) > budget:
            from ..vault import trim_at_boundary

            kopf, _, rest = base.partition("KONTEXT AUS DEN NOTIZEN:\n")
            if rest:
                base = kopf + "KONTEXT AUS DEN NOTIZEN:\n" + trim_at_boundary(
                    rest, max(1000, budget - len(kopf))
                )
            else:
                base = trim_at_boundary(base, budget)
        return base

    def _fehlerbericht(self) -> str:
        """Was ist zuletzt tatsächlich schiefgegangen - aus dem Protokoll, nicht geraten."""
        zeilen = ["EIGENE FEHLSCHLÄGE (aus dem Protokoll – nutze diese Fakten für "
                  "deine Antwort, spekuliere nicht):"]
        gefunden = False

        if self.last_plan and self.last_plan.fehlgeschlagen:
            gefunden = True
            for eintrag in self.last_plan.fehlgeschlagen[:6]:
                zeile = f"- {eintrag['titel']}: {eintrag['fehler']}"
                if eintrag.get("vorschlaege"):
                    zeile += f" (tatsächlich vorhanden: {', '.join(eintrag['vorschlaege'][:4])})"
                zeilen.append(zeile)

        for beobachtung in reversed(getattr(self.lernwerk, "beobachtungen", [])[-8:]):
            if beobachtung.fehlschlaege or beobachtung.abgebrochen:
                gefunden = True
                zeilen.append(f"- Auf „{beobachtung.frage[:70]}“: "
                              f"{beobachtung.fehlschlaege} Fehlschlag/Fehlschläge"
                              + (f", abgebrochen: {beobachtung.abgebrochen}"
                                 if beobachtung.abgebrochen else ""))

        if not gefunden:
            return ""
        zeilen.append(
            "Häufigster Grund: Ein Notizname im Plan entsprach keiner echten Datei. "
            "Namen werden inzwischen vor der Ausführung gegen den Bestand aufgelöst; "
            "bleibt einer unauflösbar, wird die Operation mit Vorschlägen abgelehnt."
        )
        return "\n".join(zeilen) + "\n"

    def _build_context(self, hits: Sequence[Hit], structural: bool, frage: str = "") -> str:
        """Baut den Kontext aus VOLLSTÄNDIGEN Abschnitten statt aus Fragmenten."""
        parts: List[str] = []
        if structural:
            parts.append("### Ordnerbaum\n" + self.structure.tree())

        # An num_ctx gekoppelt: ein Prompt über dem Kontextfenster wird von
        # Ollama beschnitten und das Modell verliert seine Anweisungen.
        budget = min(self.cfg.max_context_chars, int(self.cfg.prompt_char_budget * 0.7))
        used = sum(len(p) for p in parts)
        gesehen: set = set()

        for i, hit in enumerate(hits, 1):
            rel = hit.metadata.get("rel_path", "")
            text, kapitel = self.index.expand_hit(hit, frage)
            # Nach der Erweiterung entdoppeln: Stichwort- und Vektortreffer
            # landen oft im selben Kapitel und würden es sonst zweimal belegen.
            schluessel = (rel, kapitel or text[:80])
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)

            block = (
                f"### Quelle {i}: {hit.label}"
                + (f" › {kapitel}" if kapitel and kapitel not in hit.label else "")
                + f"\nDatei: {rel}\n{text.strip()}\n"
            )
            if used + len(block) > budget:
                if used == 0:  # erste Quelle passt nicht -> gekürzt aufnehmen
                    from ..vault import trim_at_boundary

                    parts.append(trim_at_boundary(block, budget))
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts) if parts else "(keine passenden Notizen gefunden)"


    # ---------------------------------------------------------------- Kontext
    def _extract_folder_filter(self, question: str) -> Tuple[str, Optional[str]]:
        """'@Deep Learning Wie …' → Ordner 'Deep Learning', Frage 'Wie …'.

        Der Ordnername kann mehrere Wörter haben, die Frage schließt direkt an,
        und nichts trennt beides. Deshalb wird vom LÄNGSTEN Wortpräfix nach dem
        '@' rückwärts gesucht, bis eines auf einen echten Ordner passt - das
        ist die einzige Grenze, die es hier wirklich gibt.

        Vorher nahm ein Ausdruck nur das erste Wort ('Deep') und verließ sich
        darauf, dass die Ähnlichkeitsfunktion es schon auf 'Deep Learning'
        ziehen würde. Das tat sie, weil ein Einwortname als Teilmenge pauschal
        0,85 bekam - dieselbe Regel, die 'ARVIS Architektur Overview' auf
        'Architektur' zog. Mit ihr fiel auch diese Krücke weg, und das ist
        richtig so: Der Ordner steht buchstäblich da und muss nicht geraten
        werden.
        """
        match = FOLDER_HINT_RE.search(question)
        if not match:
            return question, None
        rest = question[match.start() + 1:]
        woerter = rest.split()
        if not woerter:
            return question, None

        bekannt = {fold(pfad): pfad for pfad in self.structure.folder_paths}
        bekannt.update({fold(pfad.rsplit("/", 1)[-1]): pfad
                        for pfad in self.structure.folder_paths})

        # Höchstens fünf Wörter - danach ist es die Frage, nicht der Ordner.
        for laenge in range(min(5, len(woerter)), 0, -1):
            kandidat = " ".join(woerter[:laenge])
            treffer = bekannt.get(fold(kandidat))
            if treffer:
                frage = " ".join(woerter[laenge:]).strip()
                vorher = question[:match.start()].strip()
                return (f"{vorher} {frage}".strip() or question), treffer

        # Kein Ordner trägt diesen Namen: dann wie bisher ungefähr suchen,
        # aber nur mit dem, was vor der Frage steht.
        raw = woerter[0]
        candidates = self.structure.match_folder(raw, threshold=0.5)
        folder = candidates[0][0] if candidates else raw
        cleaned = FOLDER_HINT_RE.sub("", question, count=1).strip()
        return cleaned or question, folder

    def _werkzeug_aufschlag(self, mit_werkzeugen: bool) -> int:
        """Wie viele Zeichen die Werkzeugschemas im Prompt belegen.

        Der Anlass ist gemessen, nicht geschaetzt. Im Protokoll vom 18.09.
        ging JEDE Anfrage mit 37.929 Bytes an /api/chat - bei NUM_CTX=8192,
        also rund 27.000 Bytes Platz. 40 Prozent Ueberhang, jedes Mal.

        Die Ursache: _auf_budget_kuerzen deckelte die NACHRICHTEN auf
        prompt_char_budget, danach haengte llm.py die 39 Werkzeugschemas an
        den Payload - 17.226 Bytes, die niemand mitzaehlte. Ollama bekam
        num_ctx ausdruecklich mitgeschickt und schnitt den Ueberhang ab:
        vorn, wo die Anweisungen und der Gespraechsverlauf stehen.

        Sichtbar wurde das als zwei scheinbar verschiedene Fehler - der Agent
        folgte dem Gespraech nicht mehr, und bei der groessten Anfrage brach
        der Strom mitten drin ab ("Verbindungsproblem: EOF"), obwohl der
        Server mit 200 geantwortet hatte.
        """
        if not mit_werkzeugen:
            return 0
        if self._werkzeug_bytes is None:
            try:
                from ..tools import TOOL_SCHEMAS
                self._werkzeug_bytes = len(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))
            except Exception:
                self._werkzeug_bytes = 0
        return self._werkzeug_bytes

    def _budget_warnen(self, aufschlag: int, budget: int) -> None:
        """Einmal je Sitzung sagen, dass Werkzeuge und Kontext nicht zusammenpassen."""
        if getattr(self, "_budget_gewarnt", False):
            return
        self._budget_gewarnt = True
        noetig = int((aufschlag + 14000) / 3.3 / 0.65)
        text = (f"Die {len(TOOL_NAMES) if 'TOOL_NAMES' in globals() else 'vorhandenen'} "
                f"Werkzeugschemas belegen {aufschlag} Zeichen, das Prompt-Budget umfasst "
                f"aber nur {budget}. Anweisungen und Gespraechsverlauf werden dadurch "
                f"beschnitten. Abhilfe in der .env: NUM_CTX auf mindestens {noetig} "
                f"und MAX_CONTEXT_CHARS entsprechend erhoehen.")
        melden = getattr(self, "melden", None)
        if callable(melden):
            melden(text)
        else:
            log = getattr(self, "protokoll", None)
            if log is not None and hasattr(log, "warnung"):
                log.warnung(text, quelle="prompt")

    def _auf_budget_kuerzen(self, messages: List[dict],
                            mit_werkzeugen: bool = False) -> Tuple[List[dict], int]:
        """Hält den GANZEN Nachrichtenstapel unter prompt_char_budget.

        HISTORY_TURNS zählt Züge, nicht Zeichen. Fünf ausführliche Antworten
        sprengen damit jedes Kontextfenster - und Ollama schneidet dann still
        am ANFANG ab, wo die Anweisungen stehen. Genau das war die Ursache der
        englischen Antworten in v5 (ENTWICKLUNG.md). Der Systemprompt wird
        gedeckelt, der Stapel bisher nicht.

        Die Reihenfolge beim Kürzen ist der eigentliche Punkt:

        1. Der Systemprompt bleibt - er trägt Persona, Regeln und Werkzeuge.
        2. Die aktuelle Frage bleibt - ohne sie ist der Aufruf sinnlos.
        3. Aus der Mitte fallen die ÄLTESTEN Züge zuerst, paarweise (Frage
           samt zugehöriger Antwort). Ein Frageturn ohne seine Antwort liest
           sich wie eine unbeantwortete Frage.
        4. Erst wenn selbst Systemprompt und Frage zusammen zu groß sind, wird
           der Systemprompt von HINTEN gekürzt - vorn stehen die Anweisungen.

        Zurück kommt der gekürzte Stapel und die Zahl der ausgelassenen
        Nachrichten. Die wird dem Nutzer gemeldet, nicht verschwiegen.
        """
        roh = max(2000, int(getattr(self.cfg, "prompt_char_budget", 14000)))

        # Die Werkzeugschemas fahren im selben Kontextfenster mit, auch wenn
        # sie nicht in `messages` stehen. Sie gehen also vom Budget ab.
        #
        # ABER: Sie duerfen den Gespraechsverlauf nicht auffressen. Bei 39
        # Werkzeugen und NUM_CTX=8192 belegen die Schemas allein mehr als das
        # ganze Budget - dann bliebe fuer Anweisungen und Verlauf nichts, und
        # der Agent verlaere den Faden bei JEDER Frage. Das ist keine Kuerzung
        # mehr, das ist eine Fehlkonfiguration. Also wird der Abzug gedeckelt
        # und der Nutzer bekommt es gesagt, statt es stillschweigend zu
        # erleiden.
        # Der Abzug ist voll, solange danach genug fuer Anweisungen und
        # Verlauf bleibt. Erst wenn das Fenster dafuer zu klein ist, wird
        # gedeckelt - dann stimmt die Rechnung zwar nicht mehr, aber ein
        # Agent ohne Anweisungen waere endgueltig unbrauchbar. Gemeldet wird
        # es in dem Fall, denn es ist eine Fehlkonfiguration und keine
        # Kuerzung: Bei NUM_CTX=8192 belegen 39 Werkzeugschemas allein mehr
        # als das ganze nutzbare Fenster.
        aufschlag = self._werkzeug_aufschlag(mit_werkzeugen)
        if aufschlag and roh - aufschlag < MINDESTPLATZ:
            self._budget_warnen(aufschlag, roh)
            aufschlag = max(0, roh - MINDESTPLATZ)
        budget = max(2000, roh - aufschlag)

        # Gemessen wird in "lateinischen Zeichen" (sprache.textlast): fuer
        # deutschen Text genau die Zeichenzahl wie bisher, fuer dichter
        # tokenisierte Schriften (CJK, Kyrillisch ...) entsprechend mehr. Sonst
        # passt ein chinesischer Verlauf "ins Budget" und sprengt trotzdem das
        # Kontextfenster - mit genau dem stillen Abschneiden vorn, um das es
        # hier geht.
        def laenge(stapel: Sequence[dict]) -> int:
            return sum(textlast(m.get("content") or "") for m in stapel)

        if laenge(messages) <= budget:
            return messages, 0

        # Frueher stand hier zusaetzlich `or len(messages) < 3`. Damit blieb
        # der allererste Zug eines Gespraechs UNGEKUERZT - genau der, bei dem
        # der Systemprompt mit allen abgerufenen Notizen am groessten ist und
        # noch kein Verlauf danebensteht. Gemessen am 18.09.: 15.710 Zeichen
        # Nachrichten bei 8.400 Budget, also doppelt so viel wie erlaubt, und
        # das noch vor dem ersten Werkzeugaufruf.
        if len(messages) < 3:
            system, frage = messages[0], messages[-1]
            erlaubt = max(500, budget - textlast(frage.get("content") or ""))
            if textlast(system.get("content") or "") > erlaubt:
                system = dict(system)
                system["content"] = auf_last_kuerzen(system.get("content") or "", erlaubt)
            return [system, frage] if len(messages) > 1 else [system], 0

        system, mitte, frage = messages[0], [dict(m) for m in messages[1:-1]], messages[-1]
        frage_laenge = textlast(frage.get("content") or "")
        ausgelassen = 0

        # Die Aufteilung ist der eigentliche Punkt, und sie war frueher falsch
        # herum. Der Systemprompt traegt die abgerufenen Notizen; er wuchs mit
        # jeder Frage, zu der die Suche viel fand, und verdraengte dann den
        # GESAMTEN Gespraechsverlauf (`mitte = []`). Genau so verliert ein
        # Agent mitten im Gespraech den Faden: Der Nutzer fragt dreimal
        # dasselbe und bekommt dreimal eine Antwort auf etwas anderes.
        #
        # Notizen liefert die Suche jederzeit neu. Den letzten Zug des
        # Gespraechs nicht. Also bekommt das Gespraech zuerst seinen Platz,
        # und der Systemprompt nimmt, was uebrig bleibt.
        frei = max(0, budget - frage_laenge)
        reserve = min(laenge(mitte), frei // 2)

        # Von HINTEN aufsammeln: der juengste Zug zuerst, denn auf ihn bezieht
        # sich die aktuelle Frage.
        behalten: List[dict] = []
        belegt = 0
        for eintrag in reversed(mitte):
            gross = textlast(eintrag.get("content") or "")
            if belegt + gross <= reserve:
                behalten.insert(0, eintrag)
                belegt += gross
            elif not behalten and reserve > 200:
                # Selbst der juengste Zug passt nicht - angeschnitten behalten
                # statt ganz verlieren. Aus einem Bruchstueck laesst sich der
                # Bezug noch herstellen, aus nichts nicht.
                eintrag["content"] = (
                    auf_last_kuerzen(eintrag.get("content") or "", reserve - 1).rstrip() + "…")
                behalten.insert(0, eintrag)
                belegt = reserve
                ausgelassen += 1
                break
            else:
                break

        ausgelassen += max(0, len(mitte) - len(behalten))
        mitte = behalten

        # Erst jetzt der Systemprompt, auf das was bleibt. Gekuerzt wird von
        # HINTEN: vorn stehen Sprache, Regeln und Werkzeuge, hinten die
        # abgerufenen Notizen - und die sind das Entbehrliche.
        erlaubt = max(500, budget - frage_laenge - belegt)
        if textlast(system.get("content") or "") > erlaubt:
            system = dict(system)
            system["content"] = auf_last_kuerzen(system.get("content") or "", erlaubt)
        return [system] + mitte + [frage], ausgelassen
