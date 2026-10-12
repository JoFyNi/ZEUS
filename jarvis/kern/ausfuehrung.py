"""Die Agentenschleife: Ziel -> Kontext -> Plan -> Tun -> Sehen -> Pruefen -> Weiter.

Das ist der Kasten, der in Zeus bisher fehlte. Es gab eine Schleife - aber sie
sass in `gespraech/werkzeuglauf._tool_loop` und war an eine ANTWORT gebunden:
Sie begann, wenn jemand fragte, und endete, wenn die Antwort stand. Alles,
was laenger dauert als eine Antwort, konnte es deshalb nicht geben. Kein
Warten auf eine Freigabe, kein Fortsetzen nach einem Neustart, keine Arbeit
ohne Frage.

Hier laeuft dieselbe Schleife ueber PERSISTENTE Aufgaben. Ein Durchgang
(`bearbeiten`) macht genau EINEN Schritt und schreibt den Stand zurueck.
Das ist die entscheidende Eigenschaft: Zwischen zwei Durchgaengen darf der
Prozess sterben. Was gelaufen ist, steht in der Datenbank; was noch offen
ist, findet der naechste Start wieder.

    Ziel
     └─ Wurzelaufgabe       zerlegt, wartet auf ihre Kinder, prueft am Ende
         ├─ Schritt 1       Werkzeug -> Beobachtung -> Verifikation
         ├─ Schritt 2       gescheitert -> Neuplanung -> anderer Weg
         └─ Schritt 3       braucht Freigabe -> schlaeft, bis Josh antwortet

Drei Festlegungen, die aus dem bisherigen Verhalten kommen:

1. **Ein Durchgang, ein Schritt.** Nicht "arbeite die Aufgabe ab". Eine
   Methode, die eine ganze Aufgabe erledigt, kann nicht unterbrochen werden
   und haelt beim Absturz nichts fest.

2. **Fehlschlag fuehrt zu Neuplanung, nicht zum Ende.** Der Speicher zaehlt
   die Versuche (`fehlschlag`), der Zerleger sucht einen anderen Weg. Erst
   wenn beide Zaehler voll sind, ist Schluss - und dann landet die Aufgabe
   als toter Brief in der Ablage statt im Nichts.

3. **Das Modell entscheidet nie allein, ob etwas geklappt hat.** Zwischen
   Werkzeugergebnis und Aufgabenzustand steht die Verifikation. Ein Werkzeug,
   das `ok=True` meldet, ohne dass sich die Welt geaendert hat, bringt die
   Aufgabe NICHT auf 'fertig'. Das ist der Fall, den §47 des Auftrags
   ausdruecklich verlangt, und er wird in tests/test_agentenschleife.py
   genau so gefahren.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, List, Optional

from ..werkzeuge.ergebnis import ToolResult
from .aufgabenspeicher import (ABGEBROCHEN, ART_SCHRITT, ART_ZIEL, BEREIT,
                               FERTIG, GESCHEITERT, LAEUFT, NEUPLANUNG,
                               PAUSIERT, PLANUNG, PRUEFUNG,
                               WARTET_AUF_ABHAENGIGKEIT, WARTET_AUF_FREIGABE,
                               ZIEL_ERREICHT, ZIEL_GESCHEITERT, Zustandsfehler)
from .budget import Budget, BudgetErschoepft
from .kontext import Kontextwerk
from .planung import Zerleger
from .vertrag import Vertrag, Vertragswerk
from .werkzeugtor import FreigabeNoetig, Werkzeugtor

# Woran der Laeufer erkennt, dass ein Schritt erledigt ist, obwohl kein
# Werkzeug lief: Manche Schritte sind reine Denkschritte ("entscheide,
# welcher Ordner passt"). Sie bekommen kein Werkzeug und gelten als erledigt,
# sobald das Modell etwas Verwertbares geliefert hat.
_DENKSCHRITT = "denken"

# Platzhalter in Planparametern: {ergebnis1}, {auftrag}, {ziel}.
_PLATZHALTER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_AUFRUF_PROMPT = """Du fuehrst EINEN Schritt einer groesseren Aufgabe aus.

{kontext}

Verfuegbare Werkzeuge:
{werkzeuge}

Waehle GENAU EIN Werkzeug und die Parameter dafuer. Nichts erfinden: Wenn du
einen Notiznamen nicht kennst, suche ihn erst.

Antworte AUSSCHLIESSLICH mit JSON:
{{"werkzeug": "notizen_suchen", "parameter": {{"frage": "..."}}}}

Kommt fuer diesen Schritt kein Werkzeug infrage, antworte mit:
{{"werkzeug": "", "ergebnis": "was du stattdessen festgestellt hast"}}
"""


class Aufgabenlaeufer:
    """Arbeitet persistente Aufgaben ab - einen Schritt je Durchgang."""

    def __init__(self, speicher, tor: Werkzeugtor, *, zerleger: Optional[Zerleger] = None,
                 llm=None, cfg=None, bus=None, kontext=None,
                 index=None, sitzung=None, lernwerk=None,
                 bewerter=None, weiche=None,
                 melden: Optional[Callable[[str], None]] = None,
                 unbeaufsichtigt: bool = True) -> None:
        self.speicher = speicher
        self.tor = tor
        self.cfg = cfg if cfg is not None else getattr(tor, "cfg", None)
        self.llm = llm
        self.bus = bus
        self.kontext = kontext
        self.melden = melden
        self.unbeaufsichtigt = bool(unbeaufsichtigt)
        self.zerleger = zerleger or Zerleger(llm=llm, melden=melden)
        # Ohne uebergebenes Kontextwerk eines mit dem, was da ist. Fehlende
        # Quellen (kein Index, kein Sitzungsgedaechtnis) lassen einfach
        # Bloecke weg - sie sind kein Grund, den Schritt aufzuhalten.
        # Bewerter und Weiche sind optional: Ohne sie verhaelt sich der
        # Laeufer wie vor dem Umbau - er zerlegt und ruft das grosse Modell.
        # Das ist die Rueckfallebene, nicht der Normalfall.
        self.bewerter = bewerter
        self.weiche = weiche
        self.vertragswerk = Vertragswerk(cfg=self.cfg, melden=melden)
        self.kontext = kontext or Kontextwerk(
            speicher=speicher, index=index, sitzung=sitzung, lernwerk=lernwerk,
            platz=int(getattr(self.cfg, "kontext_platz", 6000) or 6000))

    # ------------------------------------------------------------------ Ziele
    def ziel_aufnehmen(self, beschreibung: str, *, ergebnis: str = "",
                       bedingungen: Optional[List[str]] = None,
                       prioritaet: int = 5, quelle: str = "nutzer",
                       auftrag: str = "", daten: Optional[dict] = None):
        """Nimmt ein Ziel entgegen und legt seine Wurzelaufgabe an.

        Hier und nur hier entsteht Arbeit. Terminal, Oberflaeche, Zeitplan,
        Ereignis und Ablauf gehen alle durch diese Tuer - das ist der
        Architekturgrundsatz aus §8 des Auftrags, und er ist der Grund,
        warum es nicht fuenf Agentenlogiken gibt.
        """
        # Erst einordnen, dann anlegen. Der Befund haengt am ZIEL und nicht
        # an der Aufgabe: Er beschreibt, was der Nutzer will, und das aendert
        # sich nicht, wenn ein Schritt scheitert und neu geplant wird.
        daten = dict(daten or {})
        befund = self._einordnen(beschreibung, daten)
        if befund is not None:
            daten["befund"] = befund.als_dict()
            # Der Vertrag entsteht VOR der ersten Handlung - das ist §11.
            # Er wird mitgespeichert, damit er einen Neustart ueberlebt:
            # Eine Schranke, die nach einem Absturz fehlt, fehlt genau dann,
            # wenn der Zustand ohnehin unsicher ist.
            try:
                vertrag = self.vertragswerk.schliessen(
                    befund, ergebnis=ergebnis,
                    strategie=daten.get("strategie"),
                    schritte=daten.get("schritte"))
                daten["vertrag"] = vertrag.als_dict()
                self._sagen(f"Vertrag: {len(vertrag.erfolg)} Erfolgskriterien, "
                            f"{len(vertrag.erlaubt)} Werkzeuge erlaubt, "
                            f"{len(vertrag.verboten)} ausgeschlossen")
            except Exception as exc:
                self._sagen(f"Kein Vertrag: {type(exc).__name__}: {exc}")

        ziel = self.speicher.ziel_anlegen(
            beschreibung, ergebnis=ergebnis, bedingungen=bedingungen,
            prioritaet=prioritaet, quelle=quelle,
            auftrag=auftrag or beschreibung, daten=daten)
        wurzel = self.speicher.anlegen(
            titel=beschreibung[:200], beschreibung=ergebnis,
            ziel_id=ziel.id, prioritaet=prioritaet,
            auftrag=auftrag or beschreibung, art=ART_ZIEL,
            daten={"quelle": quelle})
        self.speicher.wechseln(wurzel.id, BEREIT, "Ziel aufgenommen", quelle)
        self._ereignis("ziel.aufgenommen", ziel=ziel.id, aufgabe=wurzel.id,
                       beschreibung=beschreibung[:120], quelle=quelle)
        if befund is not None:
            # In die Telemetrie, nicht nur ins Log: "Warum lief das auf dem
            # grossen Modell?" laesst sich sonst hinterher nicht beantworten.
            self.speicher.lauf_vermerken(
                wurzel.id, "bewertung", name=befund.signatur,
                eingabe=beschreibung[:300],
                ausgabe="; ".join(befund.begruendung)[:600],
                urteil=befund.absicht, ziel_id=ziel.id)
            strategie = (daten.get("strategie") or {}).get("strategie")
            if strategie:
                self.speicher.lauf_vermerken(
                    wurzel.id, "routing", name=strategie,
                    ausgabe="; ".join(
                        (daten.get("strategie") or {}).get("begruendung") or []
                    )[:600], ziel_id=ziel.id)
        return ziel, wurzel

    def _einordnen(self, beschreibung: str, daten: dict):
        """Befund und Strategie bestimmen - beides darf ausfallen.

        Ein Bewerter, der wirft, duerfte kein Ziel verhindern. Er steht ganz
        vorne; was hier schiefgeht, nimmt alles mit.
        """
        if self.bewerter is None:
            return None
        try:
            befund = self.bewerter.bewerten(beschreibung)
        except Exception as exc:
            self._sagen(f"Bewertung uebersprungen: {type(exc).__name__}: {exc}")
            return None
        self._sagen(f"Eingeordnet als '{befund.absicht}' "
                    f"(Komplexitaet {befund.komplexitaet:.2f}, "
                    f"Risiko {befund.risiko:.2f})")
        if self.weiche is not None:
            try:
                entscheidung = self.weiche.waehlen(befund)
                daten["strategie"] = entscheidung.als_dict()
                self._sagen(f"Strategie: {entscheidung.zeile()}")
            except Exception as exc:
                self._sagen(f"Routing uebersprungen: {type(exc).__name__}: {exc}")
        return befund

    # ------------------------------------------------------------- Ein Schritt
    def bearbeiten(self, aufgabe) -> str:
        """Ein Durchgang fuer EINE Aufgabe. Gibt den neuen Zustand zurueck.

        Faengt jede Ausnahme ab und macht daraus einen Fehlschlag: Eine
        geworfene Ausnahme wuerde den Arbeiter mitnehmen, und der haelt
        gerade noch drei andere Aufgaben.
        """
        if aufgabe is None:
            return ""
        begonnen = time.time()
        try:
            if aufgabe.zustand == NEUPLANUNG:
                neu = self._neu_planen(aufgabe)
            elif aufgabe.art == ART_ZIEL:
                neu = self._wurzel_bearbeiten(aufgabe)
            else:
                neu = self._schritt_bearbeiten(aufgabe)
        except FreigabeNoetig as frage:
            # Das Tor hat die Aufgabe bereits schlafen gelegt und die Frage
            # hinterlegt. Hier bleibt nur, es zu melden.
            self._ereignis("aufgabe.wartet_auf_freigabe", aufgabe=aufgabe.id,
                           werkzeug=frage.werkzeug, risiko=frage.risiko)
            self._sagen(f"Wartet auf deine Freigabe: {frage.frage}")
            neu = WARTET_AUF_FREIGABE
        except BudgetErschoepft as ende:
            neu = self._budget_ende(aufgabe, ende)
        except Zustandsfehler as fehler:
            # Ein verbotener Wechsel ist ein Programmfehler, kein Betriebsfall.
            # Er gehoert laut gemeldet, darf aber den Arbeiter nicht toeten.
            self._sagen(f"Zustandsfehler bei '{aufgabe.titel[:50]}': {fehler}")
            self.speicher.lauf_vermerken(aufgabe.id, "fehler", name="zustand",
                                         ok=False, fehler=str(fehler))
            neu = aufgabe.zustand
        except Exception as exc:
            neu = self._unerwartet(aufgabe, exc)
        finally:
            try:
                self.speicher.freigeben(aufgabe.id)
            except Exception:
                pass
        self.speicher.lauf_vermerken(
            aufgabe.id, "durchgang", name=aufgabe.art,
            ausgabe=f"{aufgabe.zustand} -> {neu}",
            dauer=time.time() - begonnen, ziel_id=aufgabe.ziel_id)
        return neu

    # ------------------------------------------------------------- Wurzel
    def _wurzel_bearbeiten(self, aufgabe) -> str:
        """Die Zielaufgabe: zerlegen, warten, am Ende pruefen."""
        kinder = self._eigene_kinder(aufgabe)
        if not kinder:
            fertig = self._regelweg(aufgabe)
            if fertig is not None:
                return fertig
            return self._zerlegen(aufgabe)

        offen = [k for k in kinder if k.offen]
        if offen:
            # Kinder laufen noch. Zurueck ins Warten - der Speicher weckt
            # die Wurzel, sobald alle fertig sind (`blockaden_loesen`).
            self._abhaengig_machen(aufgabe, kinder)
            return WARTET_AUF_ABHAENGIGKEIT

        gescheitert = [k for k in kinder if k.zustand in (GESCHEITERT, ABGEBROCHEN)]
        if gescheitert:
            grund = "; ".join(f"{k.titel[:40]}: {k.fehler[:60]}" for k in gescheitert[:3])
            self.speicher.wechseln(aufgabe.id, NEUPLANUNG,
                                   f"Teilschritte gescheitert: {grund}"[:380])
            return NEUPLANUNG

        return self._ziel_abschliessen(aufgabe, kinder)

    def _regelweg(self, aufgabe) -> Optional[str]:
        """Der Fast Path aus §20: antworten, ohne irgendetwas zu planen.

        Ein Gruss und eine Frage nach den eigenen Faehigkeiten brauchen
        weder Aufgabengraph noch Modell - die Antwort steht im eigenen
        Zustand. Vor diesem Weg kostete "Hallo" gemessene drei
        Modellaufrufe: zwei fuer eine Zerlegung, die niemand brauchte, und
        einen fuer einen Schritt ohne Werkzeug.
        """
        if not aufgabe.ziel_id:
            return None
        ziel = self.speicher.ziel(aufgabe.ziel_id)
        if ziel is None:
            return None
        strategie = (ziel.daten or {}).get("strategie") or {}
        if strategie.get("strategie") != "regel":
            return None

        befund = (ziel.daten or {}).get("befund") or {}
        antwort = self._regelantwort(befund.get("absicht") or "", ziel)
        self.speicher.aktualisieren(aufgabe.id, ergebnis=antwort[:2000])
        self.speicher.lauf_vermerken(
            aufgabe.id, "regelweg", name=str(befund.get("absicht") or "?"),
            ausgabe=antwort[:400], ziel_id=ziel.id)
        self.speicher.wechseln(aufgabe.id, PRUEFUNG, "Regelweg")
        self.speicher.wechseln(aufgabe.id, FERTIG, "ohne Modell beantwortet")
        self.speicher.ziel_setzen(ziel.id, ZIEL_ERREICHT,
                                  "auf dem Regelweg beantwortet")
        self._ereignis("ziel.erreicht", ziel=ziel.id, aufgabe=aufgabe.id,
                       weg="regel")
        self._sagen(f"Ohne Modell beantwortet: {antwort[:70]}")
        return FERTIG

    def _regelantwort(self, absicht: str, ziel) -> str:
        """Die feste Antwort - aus dem eigenen Zustand, nicht erfunden."""
        if absicht == "gespraech":
            return "Hallo. Was soll ich tun?"
        if absicht == "meta":
            teile = []
            try:
                from ..werkzeuge import TOOL_NAMES
                teile.append(f"{len(TOOL_NAMES)} Werkzeuge")
            except Exception:
                pass
            try:
                zahlen = self.speicher.kennzahlen()
                teile.append(f"{zahlen.get('offen', 0)} offene Aufgaben")
            except Exception:
                pass
            return ("Ich arbeite an Zielen in deiner Vault: "
                    + ", ".join(teile) + ". "
                    "'/kern' zeigt den Zustand, '/werkzeuge' die Liste.")
        return "Beantwortet."

    def _zerlegen(self, aufgabe) -> str:
        """Aus dem Ziel einen Aufgabengraphen machen - einmal."""
        ziel = self.speicher.ziel(aufgabe.ziel_id) if aufgabe.ziel_id else None
        if ziel is None:
            # Eine Wurzel ohne Ziel ist ein Bruch, aber kein Grund
            # aufzugeben: Sie wird wie ein einzelner Schritt behandelt.
            self.speicher.aktualisieren(aufgabe.id, art=ART_SCHRITT)
            aufgabe.art = ART_SCHRITT
            return self._schritt_bearbeiten(aufgabe)

        return self._graph_bauen(aufgabe, ziel, "erste Zerlegung")

    def _graph_bauen(self, aufgabe, ziel, grund: str) -> str:
        begonnen = time.time()
        strategie = (ziel.daten or {}).get("strategie")
        try:
            zerlegung = self.zerleger.zerlegen(ziel, strategie=strategie)
        except TypeError:
            # Aelterer Zerleger ohne Strategie - dann eben ohne.
            zerlegung = self.zerleger.zerlegen(ziel)
        self.speicher.lauf_vermerken(
            aufgabe.id, "plan", name=zerlegung.quelle,
            eingabe=ziel.beschreibung[:400],
            ausgabe=" | ".join(s.beschreibung for s in zerlegung.schritte)[:1500],
            ok=not zerlegung.leer, dauer=time.time() - begonnen,
            ziel_id=ziel.id)
        if zerlegung.leer:
            self.speicher.wechseln(aufgabe.id, GESCHEITERT,
                                   "Der Auftrag liess sich nicht zerlegen.")
            self.speicher.toter_brief(aufgabe.id, "nicht zerlegbar")
            self.speicher.ziel_setzen(ziel.id, ZIEL_GESCHEITERT,
                                      "keine ausfuehrbare Zerlegung gefunden")
            return GESCHEITERT

        # PLANUNG ist der saubere Zwischenzustand, aber er ist aus LAEUFT
        # heraus nicht vorgesehen - eine Wurzel, die gerade beansprucht
        # wurde, LAEUFT ja bereits. Der Wechsel wird versucht und darf
        # ausbleiben; entscheidend ist der Ausgang unten.
        try:
            self.speicher.wechseln(aufgabe.id, PLANUNG, grund)
        except Zustandsfehler:
            pass
        generation = int((aufgabe.daten or {}).get("generation") or 0) + 1
        daten = dict(aufgabe.daten or {})
        daten["generation"] = generation
        self.speicher.aktualisieren(aufgabe.id, daten=daten)
        aufgabe.daten = daten
        # Der Vertrag stand vor dem Plan fest - jetzt muss er die Werkzeuge
        # kennen, die der Plan wirklich benutzt. Sonst blockiert ein Rahmen,
        # der aus der Absicht abgeleitet wurde, einen Plan, der aus
        # derselben Absicht entstanden ist.
        self._vertrag_weiten(ziel, zerlegung.schritte)

        kennungen = self.zerleger.eintragen(self.speicher, ziel, zerlegung,
                                            wurzel_id=aufgabe.id,
                                            generation=generation)
        self._sagen("Plan: " + " | ".join(
            f"{i}. {s.beschreibung}" for i, s in enumerate(zerlegung.schritte, 1)))
        self._ereignis("aufgabe.zerlegt", aufgabe=aufgabe.id, ziel=ziel.id,
                       schritte=len(kennungen))
        with self.speicher.einheit() as db:
            for kennung in kennungen:
                db.execute("INSERT OR IGNORE INTO abhaengigkeiten VALUES (?,?)",
                           (aufgabe.id, kennung))
        self.speicher.wechseln(aufgabe.id, WARTET_AUF_ABHAENGIGKEIT,
                               f"{len(kennungen)} Schritte angelegt")
        return WARTET_AUF_ABHAENGIGKEIT

    def _eigene_kinder(self, aufgabe) -> list:
        """Nur die Kinder der AKTUELLEN Zerlegung.

        Eine Neuplanung laesst die alten Kinder als Beleg stehen - man will
        hinterher sehen koennen, was versucht wurde. Ohne diesen Filter
        zaehlte die Wurzel sie weiter mit, faende darunter immer die
        abgebrochenen der letzten Runde und ginge sofort wieder in die
        Neuplanung. Ergebnis war eine Wurzel, die sich bis zum Anschlag
        selbst neu plante und dann aufgab, obwohl der neue Weg geklappt
        hatte. Genau so aufgetreten, deshalb steht es hier.
        """
        jetzt = int((aufgabe.daten or {}).get("generation") or 1)
        alle = self.speicher.kinder(aufgabe.id)
        return [k for k in alle
                if int((k.daten or {}).get("generation") or 1) == jetzt]

    def _abhaengig_machen(self, aufgabe, kinder) -> None:
        with self.speicher.einheit() as db:
            for kind in kinder:
                if kind.offen:
                    db.execute("INSERT OR IGNORE INTO abhaengigkeiten VALUES (?,?)",
                               (aufgabe.id, kind.id))
        try:
            self.speicher.wechseln(aufgabe.id, WARTET_AUF_ABHAENGIGKEIT,
                                   "Teilschritte laufen noch")
        except Zustandsfehler:
            pass

    def _ziel_abschliessen(self, aufgabe, kinder) -> str:
        """Alle Kinder fertig - jetzt wird das ZIEL geprueft, nicht der Plan.

        Der Unterschied traegt die ganze Last: "alle Schritte gelaufen" ist
        nicht dasselbe wie "das Ziel ist erreicht". Geprueft wird, was sich
        mechanisch pruefen laesst - die Artefakte, die entstehen sollten.
        """
        self.speicher.wechseln(aufgabe.id, PRUEFUNG, "alle Teilschritte fertig")
        ziel = self.speicher.ziel(aufgabe.ziel_id) if aufgabe.ziel_id else None
        befunde = self._ziel_pruefen(ziel, kinder)
        ergebnis = "\n".join(f"{k.titel}: {k.ergebnis[:120]}"
                             for k in kinder if k.ergebnis)[:2000]
        if befunde:
            self.speicher.aktualisieren(aufgabe.id, ergebnis=ergebnis,
                                        fehler="; ".join(befunde)[:600])
            self.speicher.wechseln(aufgabe.id, NEUPLANUNG,
                                   f"Zielpruefung: {'; '.join(befunde)[:300]}")
            return NEUPLANUNG
        self.speicher.aktualisieren(aufgabe.id, ergebnis=ergebnis)
        self.speicher.wechseln(aufgabe.id, FERTIG, "Ziel erreicht")
        if ziel is not None:
            self.speicher.ziel_setzen(ziel.id, ZIEL_ERREICHT,
                                      f"{len(kinder)} Schritte, alle bestanden")
        self._ereignis("ziel.erreicht", ziel=aufgabe.ziel_id, aufgabe=aufgabe.id)
        self._sagen(f"Ziel erreicht: {aufgabe.titel[:80]}")
        return FERTIG

    def _ziel_pruefen(self, ziel, kinder) -> List[str]:
        """Mechanische Gegenprobe auf ZIELebene. Gibt die Maengel zurueck.

        Geprueft wird ausschliesslich, was sich ohne Raten entscheiden
        laesst. Die erste Fassung schloss aus den Woertern des Ziels auf ein
        erwartetes Schriftstueck ("kommt 'Notiz' vor, muss eine Datei
        entstehen"). Das ging beim Satz "Entferne die Notiz X" prompt schief:
        Das Ziel wurde erreicht, die Pruefung verlangte trotzdem ein
        Artefakt, die Wurzel plante endlos neu. Ein Pruefer, der raet,
        prueft nichts - dieselbe Regel wie eine Ebene tiefer.

        Also nur noch zwei Fragen, und beide sind mechanisch:

        1. Hat ein Schritt eine Wirkung behauptet, die die Gegenprobe nicht
           bestaetigt hat?
        2. Sind die Artefakte, die entstanden sind, am Ende noch da? Das
           faengt den Fall, dass ein spaeterer Schritt wegraeumt, was ein
           frueherer angelegt hat.
        """
        befunde: List[str] = []
        for kind in kinder:
            if (kind.daten or {}).get("geprueft") == "durchgefallen":
                befunde.append(f"'{kind.titel[:50]}' hat die Gegenprobe nicht "
                               f"bestanden")
        wurzel = getattr(self.tor, "tools", None)
        wurzel = getattr(wurzel, "root", None)
        for kind in kinder:
            try:
                artefakte = self.speicher.artefakte(kind.id)
            except Exception:
                continue
            for eintrag in artefakte:
                if wurzel is None:
                    continue
                pfad = wurzel / str(eintrag.get("ort") or "")
                if not pfad.exists():
                    befunde.append(f"das Ergebnis '{eintrag.get('ort')}' ist "
                                   f"nicht mehr da")
        return befunde

    # -------------------------------------------------------------- Schritt
    def _schritt_bearbeiten(self, aufgabe) -> str:
        budget = self._budget(aufgabe)
        budget.pruefen()
        daten = dict(aufgabe.daten or {})
        werkzeug = str(daten.get("werkzeug") or "")
        parameter = daten.get("parameter")
        parameter = dict(parameter) if isinstance(parameter, dict) else {}

        if not werkzeug:
            werkzeug, parameter, notiz = self._aufruf_erfragen(aufgabe, budget)
            if not werkzeug:
                # Ein Denkschritt: kein Werkzeug, aber ein Ergebnis.
                self.speicher.aktualisieren(aufgabe.id, ergebnis=notiz[:2000])
                self.speicher.wechseln(aufgabe.id, PRUEFUNG, _DENKSCHRITT)
                self.speicher.wechseln(aufgabe.id, FERTIG, "Denkschritt erledigt")
                return FERTIG
            daten["werkzeug"] = werkzeug
            daten["parameter"] = parameter
            self.speicher.aktualisieren(aufgabe.id, daten=daten)
            aufgabe.daten = daten

        # Platzhalter aufloesen, BEVOR das Werkzeug laeuft. Ein Plan sagt
        # "schreibe {ergebnis1} in die Notiz" - ohne diesen Schritt stuende
        # genau das in der Datei. Gemessen im Abnahmetest: Der Schreibschritt
        # eines Fachagenten scheiterte, weil sein Inhaltsfeld unaufgeloest
        # blieb; die Meldung lautete "fehlt: inhalt", und die Ursache lag
        # zwei Ebenen daneben.
        parameter = self._platzhalter_fuellen(aufgabe, parameter)

        ergebnis = self.tor.ausfuehren(
            werkzeug, parameter, aufgabe_id=aufgabe.id,
            vertrag=self._vertrag(aufgabe),
            # Der Wirkungsbereich ist das ZIEL: Eine Neuplanung legt neue
            # Aufgaben an, aber es bleibt dieselbe Arbeit - und was darin
            # schon geschehen ist, darf nicht ein zweites Mal geschehen.
            wirkungsbereich=aufgabe.ziel_id or aufgabe.id,
            budget=budget, unbeaufsichtigt=self.unbeaufsichtigt,
            schritt=int(daten.get("nummer") or 0))
        self._budget_sichern(aufgabe, budget)

        if ergebnis.ok:
            return self._schritt_fertig(aufgabe, werkzeug, ergebnis, daten)
        return self._schritt_gescheitert(aufgabe, werkzeug, parameter, ergebnis)

    def _schritt_fertig(self, aufgabe, werkzeug: str, ergebnis, daten: dict) -> str:
        """Erfolg - aber erst nach der Pruefung, und die steht im Ergebnis.

        `ergebnis.ok` ist an dieser Stelle bereits das Urteil NACH der
        Verifikation: Das Werkzeugtor dreht ein `ok=True` um, wenn die
        Gegenprobe scheitert. Deshalb reicht hier die Abfrage - ein Werkzeug,
        das Erfolg behauptet, ohne etwas geaendert zu haben, kommt gar nicht
        bis hierher.
        """
        self.speicher.wechseln(aufgabe.id, PRUEFUNG, f"{werkzeug} gelaufen")
        daten["geprueft"] = (ergebnis.daten or {}).get("verifikation") or "nicht_pruefbar"
        self.speicher.aktualisieren(aufgabe.id, ergebnis=ergebnis.meldung[:2000],
                                    daten=daten, fehler="")
        aufgabe.daten = daten
        pfad = (ergebnis.daten or {}).get("pfad")
        if pfad and _veraendert(werkzeug, self.tor):
            # Ein entstandenes Artefakt gehoert verzeichnet, nicht nur
            # erwaehnt. Sonst ist "was ist dabei herausgekommen" nach einer
            # Woche nicht mehr zu beantworten.
            #
            # Nur bei VERAENDERNDEN Werkzeugen: `notiz_lesen` gibt den Pfad
            # der gelesenen Datei zurueck, und der wurde bis eben als
            # Artefakt verbucht. Im CLI-Lauf vom 21.09. stand deshalb die
            # Quellnotiz als Ergebnis eines Leseschritts in der Liste -
            # ein Ergebnis, das der Schritt gar nicht hervorgebracht hat.
            try:
                self.speicher.artefakt_ablegen(aufgabe.id, "notiz", str(pfad),
                                               aufgabe.titel[:200])
            except Exception:
                pass
        self.speicher.wechseln(aufgabe.id, FERTIG, "Schritt bestanden")
        self._ereignis("aufgabe.fertig", aufgabe=aufgabe.id, werkzeug=werkzeug,
                       ziel=aufgabe.ziel_id)
        return FERTIG

    def _schritt_gescheitert(self, aufgabe, werkzeug: str, parameter: dict,
                             ergebnis) -> str:
        """Fehlschlag buchen und den Speicher entscheiden lassen, wie es weitergeht."""
        gescheitert = list((aufgabe.daten or {}).get("gescheiterte_aufrufe") or [])
        marke = f"{werkzeug}({json.dumps(parameter, ensure_ascii=False, default=str)[:120]})"
        if marke not in gescheitert:
            gescheitert.append(marke)
        daten = dict(aufgabe.daten or {})
        daten["gescheiterte_aufrufe"] = gescheitert[-8:]
        self.speicher.aktualisieren(aufgabe.id, daten=daten)
        aufgabe.daten = daten
        self._ereignis("aufgabe.fehlschlag", aufgabe=aufgabe.id,
                       werkzeug=werkzeug, grund=ergebnis.meldung[:160])
        nachher = self.speicher.fehlschlag(aufgabe.id, ergebnis.meldung)
        if nachher.zustand == GESCHEITERT:
            self.speicher.toter_brief(aufgabe.id, ergebnis.meldung[:400],
                                      {"werkzeug": werkzeug, "parameter": parameter})
            self._sagen(f"Aufgegeben nach {nachher.versuche} Versuchen: "
                        f"{aufgabe.titel[:60]}")
        return nachher.zustand

    # ----------------------------------------------------------- Neuplanung
    def _neu_planen(self, aufgabe) -> str:
        """Der Kern von §15: aus einem Fehlschlag wird ein anderer Weg.

        Nicht derselbe Weg noch einmal - das waere die Wiederholung, die der
        Speicher ohnehin zaehlt. Hier wird das WERKZEUG oder die PARAMETER
        gewechselt, und die bereits erfolglosen Aufrufe stehen im Prompt,
        damit nicht wieder derselbe Vorschlag kommt.
        """
        budget = self._budget(aufgabe)
        try:
            budget.pruefen("neuplanungen")
        except BudgetErschoepft as ende:
            return self._budget_ende(aufgabe, ende)

        ziel = self.speicher.ziel(aufgabe.ziel_id) if aufgabe.ziel_id else None

        if aufgabe.art == ART_ZIEL:
            return self._ziel_neu_planen(aufgabe, ziel)

        daten = dict(aufgabe.daten or {})

        # --- Unterbrochen statt gescheitert -------------------------------
        # Eine Aufgabe kann auf zwei ganz verschiedenen Wegen hier landen:
        #
        #   a) Ein Werkzeug hat FEHLGESCHLAGEN. Dann zaehlt `fehlschlag()`
        #      einen Versuch, und derselbe Aufruf noch einmal ergaebe
        #      dasselbe Ergebnis - es braucht einen anderen Weg.
        #   b) Der Prozess ist ABGESTUERZT (oder der Arbeiter verschwand).
        #      Dann steht der Versuchszaehler auf null, und niemand weiss,
        #      wie weit der Schritt kam.
        #
        # Im Fall b) ist ein anderer Weg gar nicht gefragt: Der Schritt ist
        # fest geplant, und ihn zu wiederholen ist genau das Richtige. Das
        # geht nur, weil die Wirkungssperre im Werkzeugtor eine bereits
        # eingetretene Wirkung erkennt und NICHT noch einmal ausloest
        # (§33). Ohne sie waere das blinde Wiederholen die Gefahr, vor der
        # `wiederaufnehmen()` zu Recht warnt.
        #
        # Der Gewinn ist handfest: Nach einem Absturz laeuft die Arbeit
        # weiter, auch wenn gerade kein Sprachmodell erreichbar ist.
        if daten.get("werkzeug") and aufgabe.versuche == 0:
            self.speicher.lauf_vermerken(
                aufgabe.id, "neuplanung", name="wiederaufnahme",
                eingabe=str(aufgabe.fehler)[:300],
                ausgabe="fester Schritt - wird wiederholt, die Wirkungssperre "
                        "verhindert eine doppelte Wirkung",
                ziel_id=aufgabe.ziel_id)
            self.speicher.wechseln(aufgabe.id, BEREIT,
                                   "Wiederaufnahme nach Unterbrechung")
            self._ereignis("aufgabe.wiederaufgenommen", aufgabe=aufgabe.id,
                           werkzeug=daten.get("werkzeug"))
            return BEREIT
        begonnen = time.time()
        zerlegung = self.zerleger.alternative(
            ziel or aufgabe, aufgabe, aufgabe.fehler,
            gescheitert=daten.get("gescheiterte_aufrufe") or [])
        self.speicher.lauf_vermerken(
            aufgabe.id, "neuplanung", name=zerlegung.quelle,
            eingabe=str(aufgabe.fehler)[:400],
            ausgabe=" | ".join(s.beschreibung for s in zerlegung.schritte)[:1000],
            ok=not zerlegung.leer, dauer=time.time() - begonnen,
            ziel_id=aufgabe.ziel_id)

        if zerlegung.leer:
            self.speicher.wechseln(
                aufgabe.id, GESCHEITERT,
                f"kein anderer Weg gefunden: {aufgabe.fehler[:200]}")
            self.speicher.toter_brief(aufgabe.id,
                                      f"Neuplanung ohne Alternative: "
                                      f"{aufgabe.fehler[:300]}")
            return GESCHEITERT

        erster = zerlegung.schritte[0]
        daten["werkzeug"] = erster.werkzeug
        daten["parameter"] = dict(erster.parameter)
        daten["erwartung"] = erster.erwartung or daten.get("erwartung", "")
        daten["neuplanung"] = daten.get("neuplanung", 0) + 1
        self.speicher.aktualisieren(
            aufgabe.id, daten=daten,
            neuplanungen=aufgabe.neuplanungen + 1,
            titel=(erster.beschreibung[:200] or aufgabe.titel),
            # Der Versuchszaehler gehoert zum WEG, nicht zur Aufgabe: Ein
            # neuer Weg verdient seine eigenen Versuche. Ohne das Ruecksetzen
            # bekaeme die Alternative nur den Rest des alten Kontingents und
            # scheiterte an einer Zahl statt an der Sache.
            versuche=0)
        # Der Aufgabe im Arbeitsspeicher denselben Stand geben. Ohne das
        # schreibt `_budget_sichern` gleich darauf den ALTEN Datensatz
        # zurueck und macht die Alternative wieder zunichte - der Schritt
        # stand danach wieder auf dem Werkzeug, das eben gescheitert war.
        aufgabe.daten = daten
        budget.buchen("neuplanungen")
        self._budget_sichern(aufgabe, budget)
        self.speicher.wechseln(aufgabe.id, PLANUNG,
                               zerlegung.begruendung[:200] or "Alternative gefunden")
        self.speicher.wechseln(aufgabe.id, BEREIT, "anderer Weg steht")
        self._ereignis("aufgabe.neu_geplant", aufgabe=aufgabe.id,
                       werkzeug=erster.werkzeug, ziel=aufgabe.ziel_id)
        self._sagen(f"Neuer Weg fuer '{aufgabe.titel[:50]}': "
                    f"{erster.werkzeug or 'ohne Werkzeug'}")
        return BEREIT

    def _ziel_neu_planen(self, aufgabe, ziel) -> str:
        """Die Wurzel plant neu: gescheiterte Kinder weg, neue Zerlegung.

        Die alten Kinder werden NICHT geloescht - sie sind der Beleg, was
        versucht wurde. Sie werden nur aus den Abhaengigkeiten genommen,
        damit die Wurzel nicht auf etwas wartet, das nie kommt.
        """
        if ziel is None:
            self.speicher.wechseln(aufgabe.id, GESCHEITERT, "Ziel nicht auffindbar")
            return GESCHEITERT
        if aufgabe.neuplanungen >= aufgabe.max_neuplanungen:
            self.speicher.wechseln(
                aufgabe.id, GESCHEITERT,
                f"nach {aufgabe.neuplanungen} Neuplanungen aufgegeben")
            self.speicher.toter_brief(aufgabe.id, "Ziel nach Neuplanungen aufgegeben")
            self.speicher.ziel_setzen(ziel.id, ZIEL_GESCHEITERT,
                                      f"{aufgabe.neuplanungen} Neuplanungen ohne Erfolg")
            self._ereignis("ziel.gescheitert", ziel=ziel.id, aufgabe=aufgabe.id)
            return GESCHEITERT

        with self.speicher.einheit() as db:
            db.execute("DELETE FROM abhaengigkeiten WHERE aufgabe_id=?", (aufgabe.id,))
        for kind in self._eigene_kinder(aufgabe):
            if kind.offen:
                try:
                    self.speicher.wechseln(kind.id, ABGEBROCHEN,
                                           "Wurzel plant neu", "planer")
                except Zustandsfehler:
                    pass
        # Was schiefging, gehoert ins Ziel - sonst schlaegt die naechste
        # Zerlegung denselben Weg vor.
        gelernt = list((ziel.daten or {}).get("gescheitert") or [])
        gelernt.append(str(aufgabe.fehler)[:200])
        ziel.daten["gescheitert"] = gelernt[-5:]
        ziel.bedingungen = list(ziel.bedingungen) + [
            f"Das hat NICHT funktioniert: {g}" for g in gelernt[-3:]]
        self.speicher.aktualisieren(aufgabe.id,
                                    neuplanungen=aufgabe.neuplanungen + 1)
        return self._graph_bauen(aufgabe, ziel,
                                 f"Neuplanung {aufgabe.neuplanungen + 1}")

    # ----------------------------------------------------- Aufruf erfragen
    def _aufruf_erfragen(self, aufgabe, budget: Budget):
        """Ein Schritt ohne vorgegebenes Werkzeug - das Modell waehlt eines.

        Die Rueckfallebene, nicht der Normalfall. Alles, was der Zerleger
        vollstaendig planen konnte, kommt hier nie an.
        """
        if self.llm is None:
            # Ohne Modell kann dieser Schritt nicht DENKEN - aber er kann das
            # weiterreichen, was er vorgefunden hat, und dazusagen, dass
            # nichts verdichtet wurde. Eine blosse Entschuldigung waere im
            # naechsten Schritt der Inhalt einer Notiz gewesen; das Material
            # mit einem ehrlichen Vermerk ist brauchbar und luegt nicht.
            material = self._verlauf_text(aufgabe)
            return "", {}, ("*(ohne erreichbares Sprachmodell nicht verdichtet - "
                            "hier das Rohmaterial)*\n\n" + material)
        budget.pruefen("modellaufrufe")
        # Der Kontext wird ZUSAMMENGESTELLT, nicht zusammengeklebt: Ziel,
        # Schritt, Erwartung, Randbedingungen, bisherige Ergebnisse, bereits
        # Gescheitertes, Wissen aus der Vault und Gedaechtnis - in dieser
        # Rangfolge und innerhalb eines festen Platzes. Warum das mehr ist
        # als Bequemlichkeit, steht im Kopf von kontext.py: Ohne Schranke
        # waechst der Prompt ueber das Kontextfenster, Ollama schneidet
        # VORNE ab, und das Modell verliert genau die Anweisungen.
        ziel = self.speicher.ziel(aufgabe.ziel_id) if aufgabe.ziel_id else None
        gesammelt = self.kontext.fuer_schritt(aufgabe, ziel)
        self.speicher.lauf_vermerken(
            aufgabe.id, "kontext", name="zusammengestellt",
            ausgabe=gesammelt.bericht()[:400], ziel_id=aufgabe.ziel_id)

        prompt = _AUFRUF_PROMPT.format(
            kontext=gesammelt.text(),
            werkzeuge=self.zerleger._werkzeugliste())
        # DAS ist die Stelle, an der sich das Routing auszahlt. Vorher ging
        # jeder Schritt an das grosse Modell - gemessen zehn Sekunden, auch
        # fuer eine Entscheidung, die ein 3b-Modell in einer halben trifft.
        schnell, modellname = self._modellwahl(aufgabe)
        begonnen = time.time()
        try:
            roh = self.llm.chat([{"role": "user", "content": prompt}],
                                temperature=0.0, format_json=True,
                                schnell=schnell, modell=modellname)
        except TypeError:
            # Aelterer Client ohne Modellwahl - dann eben ohne.
            try:
                roh = self.llm.chat([{"role": "user", "content": prompt}],
                                    temperature=0.0, format_json=True)
            except Exception as exc:
                self.speicher.lauf_vermerken(aufgabe.id, "modell",
                                             name="aufrufwahl", ok=False,
                                             fehler=str(exc),
                                             dauer=time.time() - begonnen)
                return "", {}, f"Modell nicht erreichbar: {exc}"
        except Exception as exc:
            self.speicher.lauf_vermerken(aufgabe.id, "modell", name="aufrufwahl",
                                         ok=False, fehler=str(exc),
                                         dauer=time.time() - begonnen)
            return "", {}, f"Modell nicht erreichbar: {exc}"
        budget.buchen("modellaufrufe")
        budget.buchen("zeichen", len(prompt) + len(roh or ""))
        dauer = time.time() - begonnen
        self.speicher.lauf_vermerken(aufgabe.id, "modell",
                                     name=modellname or ("schnell" if schnell
                                                         else "gross"),
                                     eingabe=str(aufgabe.titel)[:200],
                                     ausgabe=str(roh)[:600], dauer=dauer)
        # Die gemessene Latenz zurueck ins Register - der Router entscheidet
        # beim naechsten Mal mit echten Zahlen statt mit Schaetzungen.
        if modellname and self.weiche is not None \
                and getattr(self.weiche, "register", None) is not None:
            try:
                self.weiche.register.vermerken(modellname, dauer, bool(roh))
            except Exception:
                pass
        from .planung import _json_finden
        antwort = _json_finden(roh or "") or {}
        werkzeug = str(antwort.get("werkzeug") or "").strip()
        parameter = antwort.get("parameter")
        parameter = dict(parameter) if isinstance(parameter, dict) else {}
        if werkzeug and werkzeug not in self.zerleger._namen:
            self._sagen(f"Modell schlug '{werkzeug}' vor - das gibt es nicht.")
            return "", {}, f"Das vorgeschlagene Werkzeug '{werkzeug}' gibt es nicht."
        return werkzeug, parameter, str(antwort.get("ergebnis") or roh or "")[:1500]

    def _platzhalter_fuellen(self, aufgabe, parameter: dict) -> dict:
        """Setzt `{ergebnisN}`, `{auftrag}` und `{ziel}` in die Parameter ein.

        Die Werte kommen aus den GESCHWISTERSCHRITTEN derselben Zerlegung,
        adressiert ueber ihre Nummer. Nicht aus dem Modellkontext: Ein Wert,
        den der Planer meinte, muss derselbe sein, den der Ausfuehrer
        einsetzt - sonst haengt das Ergebnis davon ab, was das Modell
        zwischenzeitlich noch gelesen hat.

        Ein Platzhalter ohne Wert wird ENTFERNT statt stehengelassen. Ein
        woertliches "{ergebnis2}" in einer Notiz ist eine stille Luege: Die
        Datei existiert, der Inhalt ist Unsinn, und die Verifikation sieht
        nur nach, ob etwas dasteht. Ein leeres Pflichtfeld faellt dagegen
        sofort auf, weil das Werkzeug es einfordert.
        """
        if not parameter or not any(isinstance(w, str) and "{" in w
                                    for w in parameter.values()):
            return parameter

        umgebung = {"auftrag": aufgabe.auftrag or aufgabe.titel,
                    "ziel": aufgabe.auftrag or aufgabe.titel,
                    "schritt": aufgabe.titel}
        if aufgabe.eltern_id:
            eigene = int((aufgabe.daten or {}).get("generation") or 1)
            for kind in self.speicher.kinder(aufgabe.eltern_id):
                daten = kind.daten or {}
                if int(daten.get("generation") or 1) != eigene:
                    continue
                nummer = daten.get("nummer")
                if nummer is None:
                    continue
                umgebung[f"ergebnis{int(nummer)}"] = kind.ergebnis or ""

        offen: List[str] = []

        def ersetzen(text: str) -> str:
            def eines(treffer):
                name = treffer.group(1)
                if name in umgebung:
                    return str(umgebung[name] or "")
                offen.append(name)
                return ""
            return _PLATZHALTER.sub(eines, text)

        gefuellt = {k: (ersetzen(v) if isinstance(v, str) else v)
                    for k, v in parameter.items()}
        if offen:
            # Nicht verschweigen: Ein Plan, der auf etwas verweist, das es
            # nicht gibt, ist falsch geplant - und das sieht man nur, wenn
            # es irgendwo steht.
            self.speicher.lauf_vermerken(
                aufgabe.id, "platzhalter", name=",".join(sorted(set(offen))[:6]),
                ausgabe="ohne Wert - aus den Parametern entfernt", ok=False)
        return gefuellt

    def _vertrag_weiten(self, ziel, schritte) -> None:
        """Den gespeicherten Vertrag um die geplanten Werkzeuge ergaenzen."""
        try:
            roh = (ziel.daten or {}).get("vertrag")
            if not roh:
                return
            vertrag = self.vertragswerk.weiten(Vertrag.aus_dict(roh), schritte)
            daten = dict(ziel.daten or {})
            daten["vertrag"] = vertrag.als_dict()
            ziel.daten = daten
            with self.speicher.einheit() as db:
                import json as _json
                db.execute("UPDATE ziele SET daten=?, geaendert=? WHERE id=?",
                           (_json.dumps(daten, ensure_ascii=False, default=str),
                            __import__("time").time(), ziel.id))
        except Exception as exc:
            self._sagen(f"Vertrag nicht geweitet: {type(exc).__name__}: {exc}")

    def _vertrag(self, aufgabe):
        """Der Vertrag dieser Aufgabe - aus dem Ziel geholt, nicht gemerkt.

        Aus dem SPEICHER und nicht aus einem Feld: So gilt er auch fuer
        einen Arbeiter, der die Aufgabe nach einem Neustart uebernimmt.
        """
        if not aufgabe.ziel_id:
            return None
        try:
            ziel = self.speicher.ziel(aufgabe.ziel_id)
            roh = (ziel.daten or {}).get("vertrag") if ziel else None
            return Vertrag.aus_dict(roh) if roh else None
        except Exception as exc:
            self._sagen(f"Vertrag nicht lesbar: {type(exc).__name__}: {exc}")
            return None

    def _modellwahl(self, aufgabe):
        """(schnell, modellname) fuer diesen Schritt - aus der Strategie.

        Die Strategie steht beim ZIEL, nicht beim Schritt: Sie wurde einmal
        gewaehlt und gilt, bis ein Reassessment sie aendert. Fehlt sie,
        bleibt es beim bisherigen Verhalten (grosses Modell).
        """
        if not aufgabe.ziel_id:
            return False, ""
        try:
            ziel = self.speicher.ziel(aufgabe.ziel_id)
        except Exception:
            return False, ""
        strategie = ((ziel.daten or {}).get("strategie") or {}) if ziel else {}
        rolle = str(strategie.get("rolle") or "")
        modellname = str(strategie.get("modell") or "")
        # Ein Modell auf einem ANDEREN Server kann dieser Client nicht rufen -
        # er spricht immer mit cfg.host. Lieber das grosse hier als ein
        # kleines dort, das nicht erreichbar ist.
        if strategie.get("fremder_host"):
            return False, ""
        return rolle in ("winzig", "klein"), modellname

    def _verlauf_text(self, aufgabe) -> str:
        """Was bisher geschah - knapp, aus der Telemetrie der Geschwister."""
        zeilen: List[str] = []
        if aufgabe.eltern_id:
            for kind in self.speicher.kinder(aufgabe.eltern_id):
                if kind.id == aufgabe.id or not kind.ergebnis:
                    continue
                zeilen.append(f"- {kind.titel[:60]}: {kind.ergebnis[:180]}")
        if aufgabe.fehler:
            zeilen.append(f"- zuletzt gescheitert: {aufgabe.fehler[:200]}")
        return "\n".join(zeilen[-8:]) or "(noch nichts)"

    # ----------------------------------------------------------- Budget
    def _budget(self, aufgabe) -> Budget:
        """Das Budget einer Aufgabe - aus den Einstellungen, mit dem
        bisherigen Verbrauch aus der Datenbank.

        Der Verbrauch muss mitfahren, sonst faengt jede Aufgabe nach einem
        Neustart wieder bei null an - und eine Aufgabe, die dreimal
        abgestuerzt ist, haette dreimal das volle Budget verbraucht, ohne
        dass es irgendwo stuende.
        """
        budget = Budget.aus_config(self.cfg)
        gespeichert = (aufgabe.daten or {}).get("budget") or {}
        if isinstance(gespeichert, dict):
            verbraucht = gespeichert.get("verbraucht")
            if isinstance(verbraucht, dict):
                budget.verbraucht = {k: float(v) for k, v in verbraucht.items()
                                     if isinstance(v, (int, float))}
            # Die LAUFZEIT zaehlt ab dem ersten Durchgang, nicht ab diesem.
            begonnen = gespeichert.get("begonnen")
            if isinstance(begonnen, (int, float)) and begonnen > 0:
                budget.begonnen = float(begonnen)
        return budget

    def _budget_sichern(self, aufgabe, budget: Budget) -> None:
        daten = dict(aufgabe.daten or {})
        daten["budget"] = {"verbraucht": dict(budget.verbraucht),
                           "begonnen": budget.begonnen}
        try:
            self.speicher.aktualisieren(aufgabe.id, daten=daten)
            aufgabe.daten = daten
        except Exception:
            pass

    def _budget_ende(self, aufgabe, ende: BudgetErschoepft) -> str:
        """Budget alle: ANHALTEN, nicht abbrechen.

        Der Unterschied ist die Entscheidung, die dem Menschen bleibt. Ein
        Abbruch verliert die Zwischenergebnisse; ein Anhalten laesst sie
        stehen, und er kann das Budget erhoehen oder die Aufgabe abschreiben.
        """
        self.speicher.aktualisieren(aufgabe.id, fehler=str(ende)[:400])
        try:
            self.speicher.wechseln(aufgabe.id, PAUSIERT, str(ende)[:300], "budget")
        except Zustandsfehler:
            return aufgabe.zustand
        self._ereignis("aufgabe.budget_alle", aufgabe=aufgabe.id,
                       groesse=ende.groesse, grenze=ende.grenze)
        self._sagen(f"'{aufgabe.titel[:50]}' angehalten: {ende}")
        return PAUSIERT

    # ----------------------------------------------------------- Unerwartet
    def _unerwartet(self, aufgabe, exc: Exception) -> str:
        text = f"{type(exc).__name__}: {exc}"
        self.speicher.lauf_vermerken(aufgabe.id, "fehler", name="durchgang",
                                     ok=False, fehler=text)
        self._sagen(f"Unerwartet bei '{aufgabe.titel[:50]}': {text}")
        try:
            nachher = self.speicher.fehlschlag(aufgabe.id, text)
        except Exception:
            return aufgabe.zustand
        if nachher.zustand == GESCHEITERT:
            self.speicher.toter_brief(aufgabe.id, text)
        return nachher.zustand

    # ---------------------------------------------------------------- Helfer
    def _ereignis(self, name: str, /, **daten) -> None:
        # Der Schraegstrich ist kein Schmuck: Ohne ihn kollidiert ein
        # Nutzlastfeld namens 'name' mit dem Parameter, und der TypeError
        # faellt an einer Stelle an, die mit dem Ereignis nichts zu tun hat.
        # Genau das ist beim Zeitplan passiert.
        if self.bus is not None:
            try:
                self.bus.melden(name, quelle="laeufer", **daten)
            except Exception:
                pass

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass


def _veraendert(werkzeug: str, tor) -> bool:
    """Hinterlaesst dieses Werkzeug etwas - oder liest es nur?"""
    try:
        from .werkzeugrechte import merkmal
        return merkmal(werkzeug, getattr(tor, "fertigkeiten", None)).veraendert
    except Exception:
        # Im Zweifel verzeichnen: Ein Artefakt zu viel ist ein Schoenheits-
        # fehler, ein fehlendes eine verlorene Spur.
        return True
