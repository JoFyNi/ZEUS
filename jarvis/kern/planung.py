"""Vom Ziel zum Aufgabengraphen - einmal zerlegen, in Python Buch fuehren.

`gespraech/arbeitsplan.py` kann das bereits, aber nur fuer die Dauer einer
Antwort: Sein `Arbeitsplan` lebt im Arbeitsspeicher, und wenn die Antwort
fertig ist, ist er weg. Fuer eine Aufgabe, die auf eine Freigabe wartet oder
einen Neustart ueberlebt, reicht das nicht.

Hier entsteht stattdessen ein GRAPH aus persistenten Aufgaben:

    Ziel "Marktanalyse erstellen"
     └── Aufgabe (art=ziel)              die Wurzel, wartet auf ihre Kinder
          ├── Schritt 1  notizen_suchen  bereit
          ├── Schritt 2  notiz_lesen     wartet auf 1
          └── Schritt 3  notiz_erstellen wartet auf 2

Zwei Dinge daran sind Absicht und keine Bequemlichkeit:

1. **Abhaengigkeiten statt Reihenfolge.** Ein Schritt ist nicht deshalb der
   naechste, weil er Nummer 3 traegt, sondern weil seine Vorbedingungen
   erfuellt sind. Nur so laesst sich spaeter etwas parallel ausfuehren, ohne
   die Zerlegung anzufassen - und nur so kann ein Neuplanungsschritt sich
   zwischen zwei bestehende haengen.

2. **Werkzeug UND Parameter, wenn moeglich.** Ein Schritt, der nur
   "recherchiere das Thema" heisst, zwingt den Laeufer, zur Laufzeit erneut
   das Modell zu fragen - jedes Mal mit dem Risiko, dass es etwas anderes
   antwortet als beim Zerlegen. Ein Schritt mit fertigem Aufruf ist
   deterministisch, wiederholbar und ohne Modell testbar. Beides ist
   zugelassen; das erste ist die Rueckfallebene, nicht der Normalfall.

Die Pruefung des Modellvorschlags passiert VOLLSTAENDIG in Python: Gibt es
das Werkzeug? Kennt es diese Parameter? Ist die Abhaengigkeit ein Schritt,
den es gibt? Ein erfundener Werkzeugname im Plan fuehrt sonst den Laeufer
in die Irre, und zwar dauerhaft - der Plan steht ja in der Datenbank.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence

from .aufgabenspeicher import ART_SCHRITT, BEREIT
# Planschritt und Zerlegung stehen in plantypen.py - sonst bauen Zerleger
# und Fachagenten einen Importring. Warum das mehr ist als Kosmetik, steht
# im Kopf jener Datei.
from .plantypen import Planschritt, Zerlegung

# Mehr als das ist kein Plan mehr, sondern ein Programm - und es passt weder
# ins Kontextfenster noch in einen Kopf.
MAX_SCHRITTE = 8


_ZERLEGE_PROMPT = """Zerlege dieses Ziel in 2 bis {max_schritte} ausfuehrbare \
Schritte.

ZIEL: {ziel}
GEWUENSCHTES ERGEBNIS: {ergebnis}
{bedingungen}
Verfuegbare Werkzeuge (nur diese, keine anderen):
{werkzeuge}

Regeln:
- Jeder Schritt ist EINE Handlung mit EINEM Werkzeug.
- Lesen kommt vor Verarbeiten, Verarbeiten vor Schreiben.
- Gib "parameter" so vollstaendig an, wie du kannst. Was du nicht weisst,
  laesst du weg - rate nicht.
- "braucht" nennt die Nummern der Schritte, deren Ergebnis dieser Schritt
  benoetigt. Unabhaengige Schritte bekommen eine leere Liste.
- "erwartung" ist EIN Satz: Woran erkennt man, dass der Schritt geklappt hat?
- Keine Schritte fuer Dinge, die das Ziel nicht verlangt.

Antworte AUSSCHLIESSLICH mit JSON in dieser Form:
{{"schritte": [{{"beschreibung": "...", "werkzeug": "notizen_suchen",
"parameter": {{"frage": "..."}}, "erwartung": "...", "braucht": []}}]}}
"""

_ALTERNATIVE_PROMPT = """Ein Schritt ist gescheitert. Finde EINEN anderen Weg.

ZIEL: {ziel}
GESCHEITERTER SCHRITT: {schritt}
VERSUCHTES WERKZEUG: {werkzeug} mit {parameter}
WAS SCHIEFGING: {fehler}
BEREITS ERFOLGLOS VERSUCHT: {gescheitert}

Verfuegbare Werkzeuge:
{werkzeuge}

Regeln:
- Wiederhole NICHT denselben Aufruf mit denselben Parametern - das ergibt
  dasselbe Ergebnis.
- Aendere entweder das Werkzeug oder die Parameter deutlich.
- Fuehrt kein Weg zum Ziel, gib "schritte": [] zurueck. Das ist eine
  gueltige Antwort und besser als ein aussichtsloser Versuch.

Antworte AUSSCHLIESSLICH mit JSON:
{{"begruendung": "warum der alte Weg nicht ging",
"schritte": [{{"beschreibung": "...", "werkzeug": "...", "parameter": {{}},
"erwartung": "...", "braucht": []}}]}}
"""


class Zerleger:
    """Zerlegt Ziele in Schritte und findet Alternativen fuer gescheiterte.

    Braucht ein Modell, kommt aber ohne aus: Ohne erreichbares Modell liefert
    `zerlegen` eine Rueckfall-Zerlegung mit einem einzigen Schritt. Das ist
    bewusst so - ein System, das ohne sein Sprachmodell gar nichts mehr tut,
    hat das Sprachmodell zum Grundsystem gemacht (siehe kern/module.py).
    """

    def __init__(self, llm=None, werkzeugnamen: Optional[Sequence[str]] = None,
                 parameternamen: Optional[Dict[str, Sequence[str]]] = None,
                 melden=None, agenten=None) -> None:
        self.llm = llm
        self._namen = set(werkzeugnamen or ())
        self._parameter = {k: set(v) for k, v in (parameternamen or {}).items()}
        self.melden = melden
        if not self._namen:
            try:
                from ..tools import TOOL_NAMES, TOOL_PARAMS
                self._namen = set(TOOL_NAMES)
                self._parameter = {k: set(v) for k, v in TOOL_PARAMS.items()}
            except Exception:
                pass
        # Die Fachagenten. Sie sind KEIN zweiter Ausfuehrer, sondern
        # Zerlegungswissen - siehe agenten.py. Wer sie abschalten will,
        # uebergibt agenten=False.
        if agenten is False:
            self.agenten = None
        elif agenten is not None:
            self.agenten = agenten
        else:
            try:
                from .agenten import Agentenregister
                self.agenten = Agentenregister(werkzeugnamen=self._namen)
            except Exception:
                self.agenten = None

    # --------------------------------------------------- Der Werkzeugweg
    # Absicht -> fertiger Werkzeugaufruf. Das ist der Fast Path aus §20 und
    # der einzige Grund, warum die Einordnung ueberhaupt etwas einspart:
    # Ohne ihn faellt eine Zaehlfrage in die Rueckfall-Zerlegung, bekommt
    # einen Schritt OHNE Werkzeug - und der fragt dann doch das Modell.
    #
    # Gemessen am 22.09. vor diesem Zusatz: "Wie viele Notizen habe ich?"
    # war als 'werkzeug' geroutet und loeste trotzdem einen Modellaufruf
    # aus. Eine Strategie, die niemand befolgt, spart nichts.
    _WERKZEUGWEG = {
        "zaehlen": lambda thema: [
            Planschritt(beschreibung="Bestand aufnehmen",
                        werkzeug="struktur_anzeigen", parameter={},
                        erwartung="Ordner und Anzahl liegen vor")],
        "abruf": lambda thema: [
            Planschritt(beschreibung=f"Nach '{thema}' suchen",
                        werkzeug="notizen_suchen",
                        parameter={"frage": thema, "anzahl": 8},
                        erwartung="Treffer aus der Vault liegen vor")],
        # `lesen` ist der einzige Eintrag mit einer Bedingung: `notiz_lesen`
        # braucht einen NAMEN, und aus "Lies mir die Notiz Verifikation vor"
        # laesst sich kein verlaesslicher Name schneiden. Bleibt ein ganzer
        # Satz uebrig, wird gesucht statt geraten - Suchen findet vielleicht
        # nichts, Raten oeffnet die falsche Datei.
        "lesen": lambda thema: [
            Planschritt(beschreibung=f"'{thema}' lesen",
                        werkzeug="notiz_lesen", parameter={"name": thema},
                        erwartung="Der Inhalt liegt vor")
            if len(thema.split()) <= 4 else
            Planschritt(beschreibung=f"Notiz zu '{thema}' finden",
                        werkzeug="notizen_suchen",
                        parameter={"frage": thema, "anzahl": 5},
                        erwartung="Die gemeinte Notiz liegt vor")],
        "wartung": lambda thema: [
            Planschritt(beschreibung="Verweise pruefen",
                        werkzeug="links_pruefen",
                        parameter={"reparieren": False},
                        erwartung="Die Liste der toten Verweise liegt vor")],
    }

    def werkzeugweg(self, ziel, absicht: str) -> Optional[Zerlegung]:
        """Ein reiner Werkzeugweg fuer diese Absicht - oder None.

        Bewusst ein bis zwei Schritte und kein Plan: Was sich in einem
        Werkzeugaufruf beantworten laesst, braucht keinen Aufgabengraphen.
        """
        bauen = self._WERKZEUGWEG.get(str(absicht))
        if bauen is None:
            return None
        thema = self._thema(getattr(ziel, "beschreibung", str(ziel)))
        schritte = self._pruefen([
            {"beschreibung": s.beschreibung, "werkzeug": s.werkzeug,
             "parameter": s.parameter, "erwartung": s.erwartung,
             "braucht": s.braucht} for s in bauen(thema)])
        if not schritte:
            return None
        return Zerlegung(schritte=schritte, quelle="werkzeugweg",
                         begruendung=f"'{absicht}' ist ohne Modell loesbar")

    @staticmethod
    def _thema(text: str) -> str:
        """Der Gegenstand eines Auftrags, ohne die Hoeflichkeitsfloskeln."""
        kurz = re.sub(
            r"^\s*(bitte\s+)?(wie\s*viele?|anzahl|z(ae|ä)hl\w*|zeig(e)?\s+mir|"
            r"zeig(e)?|find(e)?|such(e)?|lies|lese|liste)\s+", "",
            str(text or "").strip(), flags=re.IGNORECASE)
        kurz = re.sub(r"^(die|der|das|den|meine[nrs]?|alle)\s+", "", kurz,
                      flags=re.IGNORECASE)
        kurz = re.sub(r"^(mir|mich|uns|dir)\s+", "", kurz, flags=re.IGNORECASE)
        kurz = re.sub(r"^(die|der|das|den|meine[nrs]?|alle)\s+", "", kurz,
                      flags=re.IGNORECASE)
        kurz = re.sub(r"^(notiz(en)?|dateien?|seiten?)\s+(zu|ueber|über)?\s*", "",
                      kurz, flags=re.IGNORECASE)
        kurz = re.split(r"[?!.]", kurz)[0].strip()
        # Die Vorsilbe trennbarer Verben steht am Satzende und gehoert nicht
        # zum Gegenstand: "... Verifikation vor" -> "Verifikation".
        kurz = re.sub(r"\s+(vor|an|auf|raus|aus)$", "", kurz,
                      flags=re.IGNORECASE).strip()
        return kurz[:90] or str(text)[:90]

    # ------------------------------------------------------------- Zerlegen
    def zerlegen(self, ziel, strategie: Optional[dict] = None) -> Zerlegung:
        """Ein Ziel in Schritte. Liefert immer etwas Verwendbares.

        `strategie` kommt von der Strategieweiche. Sie entscheidet hier nur
        eines, aber das ist der ganze Punkt: ob das Modell ueberhaupt
        gefragt werden darf.
        """
        beschreibung = getattr(ziel, "beschreibung", str(ziel))
        vorgabe = (getattr(ziel, "daten", None) or {}).get("schritte")
        if isinstance(vorgabe, list) and vorgabe:
            # Ein Ziel, das seine Schritte mitbringt - so arbeiten Zeitplan,
            # Ablauf und Regelwerk. Diese Schritte sind bereits geprueft
            # worden, als der Ablauf gespeichert wurde.
            geprueft = self._pruefen(vorgabe)
            if geprueft:
                return Zerlegung(schritte=geprueft, quelle="vorgabe")

        # Der Werkzeugweg noch vor dem Fachagenten: Was ein einziger
        # Werkzeugaufruf beantwortet, braucht weder Agent noch Modell.
        # Die Strategie steht am ZIEL - der Parameter ist nur die kurze
        # Hand des Laeufers. Wer den Zerleger anders aufruft (Ablauf,
        # Zeitplan, Test), soll dieselbe Entscheidung bekommen; sonst gilt
        # sie je nach Aufrufweg oder eben nicht.
        daten = (getattr(ziel, "daten", None) or {})
        if strategie is None:
            strategie = daten.get("strategie")
        gewaehlt = str((strategie or {}).get("strategie") or "")
        befund = daten.get("befund") or {}
        if gewaehlt in ("werkzeug", "regel"):
            direkt = self.werkzeugweg(ziel, befund.get("absicht") or "")
            if direkt is not None:
                return direkt

        # Ein Fachagent vor dem Modell: Er zerlegt ohne Modellaufruf und
        # immer gleich. Nur wenn keiner deutlich passt, wird gefragt -
        # dieselbe Arbeitsteilung wie ueberall in diesem Projekt (Python
        # entscheidet, was sich entscheiden laesst; das Modell den Rest).
        if self.agenten is not None:
            try:
                vom_agenten = self.agenten.zerlegen(ziel)
            except Exception as exc:
                self._sagen(f"Fachagent uebersprungen: {exc}")
                vom_agenten = None
            if vom_agenten is not None:
                geprueft = self._pruefen([
                    {"beschreibung": s.beschreibung, "werkzeug": s.werkzeug,
                     "parameter": s.parameter, "erwartung": s.erwartung,
                     "braucht": s.braucht} for s in vom_agenten.schritte])
                if geprueft:
                    return Zerlegung(schritte=geprueft,
                                     begruendung=vom_agenten.begruendung,
                                     quelle=vom_agenten.quelle)

        if gewaehlt in ("werkzeug", "regel"):
            # Die Weiche hat entschieden, dass hier kein Modell noetig ist.
            # Dann wird auch keines gefragt - sonst waere die Entscheidung
            # eine Notiz ohne Wirkung.
            self._sagen(f"Strategie '{gewaehlt}': ohne Modell zerlegt")
            return Zerlegung(
                schritte=[Planschritt(beschreibung=str(beschreibung)[:160])],
                begruendung=f"Strategie '{gewaehlt}' - kein Modellaufruf",
                quelle="rueckfall")

        roh = self._fragen(_ZERLEGE_PROMPT.format(
            max_schritte=MAX_SCHRITTE,
            ziel=str(beschreibung)[:800],
            ergebnis=str(getattr(ziel, "ergebnis", "") or "(nicht genannt)")[:400],
            bedingungen=self._bedingungen(ziel),
            werkzeuge=self._werkzeugliste()))
        daten = _json_finden(roh or "")
        schritte = self._pruefen((daten or {}).get("schritte"))
        if schritte:
            return Zerlegung(schritte=schritte,
                             begruendung=str((daten or {}).get("begruendung") or ""),
                             quelle="modell")

        # Rueckfall: ein einziger Schritt, der das Ziel als Auftrag traegt.
        # Er ist nicht schoen, aber er ist ehrlich - und der Laeufer kann ihn
        # zur Laufzeit mit dem Modell aufloesen.
        return Zerlegung(schritte=[Planschritt(beschreibung=str(beschreibung)[:160])],
                         begruendung="ohne Modell zerlegt", quelle="rueckfall")

    # ---------------------------------------------------------- Alternative
    def alternative(self, ziel, aufgabe, fehler: str,
                    gescheitert: Optional[Sequence[str]] = None) -> Zerlegung:
        """Ein anderer Weg fuer einen gescheiterten Schritt.

        `gescheitert` sind die bereits erfolglosen Aufrufe. Sie stehen im
        Prompt, weil ein Modell sonst zuverlaessig denselben Vorschlag noch
        einmal macht - genau das Muster aus dem Protokoll vom 18.09.
        """
        daten = getattr(aufgabe, "daten", None) or {}
        roh = self._fragen(_ALTERNATIVE_PROMPT.format(
            ziel=str(getattr(ziel, "beschreibung", "") or aufgabe.auftrag)[:400],
            schritt=str(aufgabe.titel)[:200],
            werkzeug=daten.get("werkzeug") or "(keines)",
            parameter=json.dumps(daten.get("parameter") or {}, ensure_ascii=False)[:300],
            fehler=str(fehler)[:400],
            gescheitert="; ".join(list(gescheitert or [])[:6]) or "(nichts)",
            werkzeuge=self._werkzeugliste()))
        antwort = _json_finden(roh or "")
        schritte = self._pruefen((antwort or {}).get("schritte"))
        return Zerlegung(schritte=schritte,
                         begruendung=str((antwort or {}).get("begruendung") or ""),
                         quelle="modell" if schritte else "rueckfall")

    # --------------------------------------------------------------- Speichern
    def eintragen(self, speicher, ziel, zerlegung: Zerlegung,
                  wurzel_id: Optional[str] = None,
                  generation: int = 1) -> List[str]:
        """Schreibt die Zerlegung als Aufgabengraphen in den Speicher.

        Gibt die Kennungen der Schritte zurueck, in Reihenfolge. Ein Schritt
        ohne Abhaengigkeit startet auf BEREIT, alle anderen blockiert - der
        Speicher gibt sie frei, sobald ihre Vorbedingung fertig ist.

        `generation` zaehlt die Zerlegungen derselben Wurzel. Eine
        Neuplanung laesst die alten Kinder als BELEG stehen und legt neue
        an; ohne die Nummer koennte die Wurzel beide Saetze nicht
        auseinanderhalten und wuerde ewig auf die abgebrochenen warten.
        """
        kennungen: List[str] = []
        for nummer, schritt in enumerate(zerlegung.schritte, start=1):
            benoetigt = [kennungen[n - 1] for n in schritt.braucht
                         if 1 <= n <= len(kennungen)]
            aufgabe = speicher.anlegen(
                titel=schritt.beschreibung[:200],
                beschreibung=schritt.erwartung,
                eltern_id=wurzel_id,
                ziel_id=getattr(ziel, "id", None),
                prioritaet=getattr(ziel, "prioritaet", 5),
                auftrag=getattr(ziel, "auftrag", "") or getattr(ziel, "beschreibung", ""),
                art=ART_SCHRITT,
                benoetigt=benoetigt,
                daten={**schritt.als_daten(nummer), "generation": int(generation)})
            if not benoetigt and aufgabe.zustand != BEREIT:
                speicher.wechseln(aufgabe.id, BEREIT, "keine Vorbedingung", "planer")
            kennungen.append(aufgabe.id)
        return kennungen

    # ------------------------------------------------------------------ Innen
    def _fragen(self, prompt: str) -> str:
        """Erst das schnelle Modell, dann das grosse. Nie eine Ausnahme nach oben.

        Der zweite Versuch ist kein Luxus: Am 18.09. zeigte FAST_MODEL auf ein
        Modell, das auf dem Server gar nicht lag. Ollama antwortete mit 404,
        der Plan entfiel lautlos, und die Schleife lief genau in die
        Wiederholung, die der Plan verhindern soll.
        """
        if self.llm is None:
            return ""
        nachricht = [{"role": "user", "content": prompt}]
        for schnell in (True, False):
            try:
                roh = self.llm.chat(nachricht, temperature=0.0,
                                    format_json=True, schnell=schnell)
            except TypeError:
                try:
                    roh = self.llm.chat(nachricht)
                except Exception as exc:
                    self._sagen(f"Zerlegung nicht moeglich: {exc}")
                    return ""
            except Exception as exc:
                if schnell:
                    continue
                self._sagen(f"Zerlegung nicht moeglich: {exc}")
                return ""
            if (roh or "").strip():
                return roh
        return ""

    def _pruefen(self, rohschritte) -> List[Planschritt]:
        """Modellvorschlag gegen die Wirklichkeit pruefen. Vollstaendig in Python."""
        if not isinstance(rohschritte, list):
            return []
        raus: List[Planschritt] = []
        for eintrag in rohschritte[:MAX_SCHRITTE]:
            if isinstance(eintrag, str):
                eintrag = {"beschreibung": eintrag}
            if not isinstance(eintrag, dict):
                continue
            beschreibung = str(eintrag.get("beschreibung")
                               or eintrag.get("schritt") or "").strip()
            beschreibung = re.sub(r"^\s*\d+[.)]\s*", "", beschreibung)
            werkzeug = str(eintrag.get("werkzeug") or "").strip()
            if werkzeug and werkzeug not in self._namen:
                # Ein erfundener Werkzeugname im Plan fuehrt den Laeufer
                # dauerhaft in die Irre - der Plan steht in der Datenbank.
                # Lieber ohne Vorschlag als mit einem falschen.
                self._sagen(f"Werkzeug '{werkzeug}' gibt es nicht - Schritt "
                            f"bleibt ohne Vorgabe.")
                werkzeug = ""
            if not beschreibung and werkzeug:
                beschreibung = f"{werkzeug} ausfuehren"
            if len(beschreibung) < 4:
                continue
            parameter = eintrag.get("parameter")
            parameter = dict(parameter) if isinstance(parameter, dict) else {}
            if werkzeug:
                bekannt = self._parameter.get(werkzeug)
                if bekannt:
                    # Unbekannte Felder wegwerfen statt durchreichen: Das
                    # Werkzeug wuerde sie ignorieren, aber sie stuenden im
                    # Plan und saehen aus, als waeren sie wirksam.
                    fremd = [k for k in parameter if k not in bekannt]
                    for k in fremd:
                        parameter.pop(k, None)
            braucht = eintrag.get("braucht")
            braucht = [int(b) for b in braucht
                       if isinstance(b, (int, float, str)) and str(b).strip().isdigit()] \
                if isinstance(braucht, list) else []
            # Ein Schritt, der auf sich selbst oder auf einen spaeteren
            # wartet, blockiert fuer immer. Nummer ist hier 1-basiert und
            # entspricht der Position, die dieser Schritt bekommen wird.
            eigene = len(raus) + 1
            braucht = sorted({b for b in braucht if 1 <= b < eigene})
            raus.append(Planschritt(
                beschreibung=beschreibung[:160], werkzeug=werkzeug,
                parameter=parameter,
                erwartung=str(eintrag.get("erwartung") or "")[:200],
                braucht=braucht))
        return raus

    def _werkzeugliste(self) -> str:
        zeilen = []
        for name in sorted(self._namen)[:60]:
            felder = sorted(self._parameter.get(name, ()))
            zeilen.append(f"- {name}({', '.join(felder)})" if felder
                          else f"- {name}()")
        return "\n".join(zeilen)

    @staticmethod
    def _bedingungen(ziel) -> str:
        werte = list(getattr(ziel, "bedingungen", None) or [])
        if not werte:
            return ""
        return "RANDBEDINGUNGEN:\n" + "\n".join(f"- {w}" for w in werte[:8]) + "\n"

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass


def _json_finden(text: str):
    """Das erste vollstaendige JSON-Objekt aus einer Modellantwort."""
    text = str(text or "")
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    while start >= 0:
        tiefe = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                tiefe += 1
            elif text[i] == "}":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None
