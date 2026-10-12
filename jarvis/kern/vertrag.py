"""Der Aufgabenvertrag: Was gilt, BEVOR gearbeitet wird.

Die Regel dahinter ist §11, und sie ist unbequemer, als sie klingt:

    Erfolg muss vorher definiert werden.

Bisher entschied sich das hinterher. `_ziel_pruefen` sah am Ende nach, ob
Artefakte entstanden waren - eine Pruefung, die erst existiert, wenn die
Arbeit vorbei ist, und die deshalb nur feststellen kann, was ohnehin
geschehen ist. "Alle Schritte gelaufen" wurde damit zu "Ziel erreicht", und
das sind zwei verschiedene Aussagen.

Ein Vertrag dreht die Reihenfolge um. Er sagt VOR dem ersten Schritt:

    Woran erkennt man, dass es fertig ist?
    Was darf dafuer benutzt werden - und was ausdruecklich nicht?
    Wie viel darf es kosten?
    Wann wird eskaliert, wann abgebrochen?

Die eine Eigenschaft, die alles traegt
--------------------------------------
Ein Vertrag ENGT EIN. Er kann nie mehr erlauben, als ohnehin erlaubt ist.

Das ist keine Feinheit, sondern der Unterschied zwischen einer zusaetzlichen
Sicherung und einem Loch. Wuerde ein Vertrag Rechte VERGEBEN, waere er ein
zweiter Weg an der Rechteverwaltung vorbei - und der Vertrag entsteht zum
Teil aus einer Modellantwort. Ein Modell, das sich selbst Rechte
ausstellen kann, hat keine Rechte mehr, sondern Wuensche.

Deshalb gilt ueberall:

    Erlaubt = Rechtestufe  UND  Vertrag  UND  Werkzeugtor

Das Werkzeugtor bleibt die letzte Instanz. Der Vertrag sitzt davor und sagt
frueher nein - nie spaeter ja.

Woher die Erfolgskriterien kommen
---------------------------------
Aus dem BEFUND, nicht aus einer Modellantwort. Wer eine Notiz anlegen laesst,
will am Ende eine Datei, die es gibt und die nicht leer ist - das laesst
sich ableiten und mechanisch pruefen. Wo sich nichts mechanisch pruefen
laesst (eine Zusammenfassung ist gut oder nicht), sagt der Vertrag das
ausdruecklich, statt eine Pruefung vorzutaeuschen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .bewertung import (ABRUF, ANALYSE, Befund, FRAGE, GESPRAECH, LESEN, META,
                        SCHREIBEN, UMBAUEN, WARTUNG, ZAEHLEN)
from .budget import Budget

# Pruefweisen. Der Unterschied zwischen den ersten beiden und der letzten
# ist der ganze Punkt: "nicht pruefbar" ist eine ehrliche Angabe, kein
# Versagen - und sie verhindert, dass ein unbelegtes Ergebnis als bestaetigt
# durchgeht.
DATEI_DA = "datei_vorhanden"
DATEI_WEG = "datei_entfernt"
ZAHL_DA = "zahl_geliefert"
TREFFER_DA = "treffer_geliefert"
SCHEMA = "schema_erfuellt"
NICHT_PRUEFBAR = "nicht_mechanisch_pruefbar"


@dataclass
class Erfolgskriterium:
    """Eine Bedingung, die am Ende stimmen muss - mit ihrer Pruefweise."""
    text: str
    pruefweise: str = NICHT_PRUEFBAR
    erfuellt: Optional[bool] = None          # None = noch nicht geprueft
    befund: str = ""

    @property
    def mechanisch(self) -> bool:
        return self.pruefweise != NICHT_PRUEFBAR

    def zeile(self) -> str:
        marke = {True: "✔", False: "✖", None: "○"}[self.erfuellt]
        text = f"{marke} {self.text}"
        if not self.mechanisch:
            text += "   (nicht mechanisch pruefbar)"
        if self.befund:
            text += f"   – {self.befund}"
        return text


@dataclass
class Vertrag:
    """Der verbindliche Rahmen einer Aufgabe (§10)."""
    ziel: str
    erfolg: List[Erfolgskriterium] = field(default_factory=list)
    benoetigt: List[str] = field(default_factory=list)
    erlaubt: List[str] = field(default_factory=list)    # Werkzeugnamen
    verboten: List[str] = field(default_factory=list)   # Werkzeugnamen
    risiko: float = 0.0
    budget: Budget = field(default_factory=Budget)
    eskaliert_wenn: List[str] = field(default_factory=list)
    bricht_ab_wenn: List[str] = field(default_factory=list)
    signatur: str = ""
    geschlossen: float = field(default_factory=time.time)

    # ------------------------------------------------------ Werkzeugfrage
    def werkzeug_erlaubt(self, name: str) -> Tuple[bool, str]:
        """Darf dieses Werkzeug im Rahmen DIESER Aufgabe laufen?

        Gibt (ja/nein, Grund) zurueck. Das Nein ist eine ZUSAETZLICHE
        Schranke - ein Ja bedeutet hier ausdruecklich NICHT, dass es
        erlaubt ist: Danach fragt immer noch das Werkzeugtor.
        """
        if name in self.verboten:
            return False, (f"'{name}' ist in dieser Aufgabe ausdruecklich "
                           f"ausgeschlossen (Vertrag).")
        if self.erlaubt and name not in self.erlaubt:
            return False, (f"'{name}' gehoert nicht zu den fuer diese Aufgabe "
                           f"vorgesehenen Werkzeugen ({', '.join(sorted(self.erlaubt)[:6])}"
                           f"{' …' if len(self.erlaubt) > 6 else ''}).")
        return True, ""

    # --------------------------------------------------------- Erfuellung
    @property
    def offen(self) -> List[Erfolgskriterium]:
        return [k for k in self.erfolg if k.erfuellt is not True]

    @property
    def erfuellt(self) -> bool:
        """Sind alle MECHANISCH pruefbaren Kriterien bestaetigt?

        Nicht pruefbare Kriterien halten den Abschluss nicht auf - sie
        werden aber auch nicht als erfuellt gezaehlt. Der Unterschied steht
        im Bericht, damit niemand eine Bestaetigung liest, die es nicht gibt.
        """
        return all(k.erfuellt is True for k in self.erfolg if k.mechanisch)

    @property
    def unbelegt(self) -> List[Erfolgskriterium]:
        return [k for k in self.erfolg if not k.mechanisch]

    def vermerken(self, pruefweise: str, erfuellt: bool, befund: str = "") -> None:
        for kriterium in self.erfolg:
            if kriterium.pruefweise == pruefweise:
                kriterium.erfuellt = erfuellt
                kriterium.befund = befund

    # ------------------------------------------------------------ Ausgabe
    def als_dict(self) -> dict:
        return {
            "ziel": self.ziel, "risiko": round(self.risiko, 2),
            "signatur": self.signatur,
            "erfolg": [{"text": k.text, "pruefweise": k.pruefweise,
                        "erfuellt": k.erfuellt, "befund": k.befund}
                       for k in self.erfolg],
            "benoetigt": list(self.benoetigt),
            "erlaubt": sorted(self.erlaubt), "verboten": sorted(self.verboten),
            "budget": self.budget.als_dict(),
            "eskaliert_wenn": list(self.eskaliert_wenn),
            "bricht_ab_wenn": list(self.bricht_ab_wenn),
        }

    @classmethod
    def aus_dict(cls, daten: dict) -> "Vertrag":
        """Einen Vertrag aus seiner gespeicherten Form zurueckholen.

        Gebraucht wird das nach einem NEUSTART. Ein Vertrag, der nur im
        Arbeitsspeicher lebt, gilt genau bis zum naechsten Absturz - und
        danach liefe die Aufgabe ohne ihren Rahmen weiter. Das waere die
        schlechteste Sorte Schranke: eine, die genau dann fehlt, wenn der
        Zustand ohnehin unsicher ist.

        Wiederhergestellt wird, was zum DURCHSETZEN noetig ist. Das Budget
        wird bewusst neu aus den Einstellungen gebaut; sein Verbrauch steht
        an der Aufgabe, nicht am Vertrag.
        """
        daten = dict(daten or {})
        vertrag = cls(ziel=str(daten.get("ziel") or ""),
                      risiko=float(daten.get("risiko") or 0.0),
                      signatur=str(daten.get("signatur") or ""),
                      benoetigt=list(daten.get("benoetigt") or []),
                      erlaubt=list(daten.get("erlaubt") or []),
                      verboten=list(daten.get("verboten") or []),
                      eskaliert_wenn=list(daten.get("eskaliert_wenn") or []),
                      bricht_ab_wenn=list(daten.get("bricht_ab_wenn") or []))
        for roh in (daten.get("erfolg") or []):
            if not isinstance(roh, dict):
                continue
            vertrag.erfolg.append(Erfolgskriterium(
                text=str(roh.get("text") or ""),
                pruefweise=str(roh.get("pruefweise") or NICHT_PRUEFBAR),
                erfuellt=roh.get("erfuellt"),
                befund=str(roh.get("befund") or "")))
        return vertrag

    def bericht(self) -> str:
        zeilen = [f"Ziel: {self.ziel}", "", "Fertig ist es, wenn:"]
        zeilen += [f"  {k.zeile()}" for k in self.erfolg]
        zeilen += ["", f"Erlaubt:  {', '.join(sorted(self.erlaubt)) or '(alles, was die Rechtestufe hergibt)'}"]
        if self.verboten:
            zeilen.append(f"Verboten: {', '.join(sorted(self.verboten))}")
        zeilen.append(f"Budget:   {self.budget.bericht()}")
        if self.eskaliert_wenn:
            zeilen.append("Eskaliert, wenn: " + "; ".join(self.eskaliert_wenn))
        if self.bricht_ab_wenn:
            zeilen.append("Bricht ab, wenn: " + "; ".join(self.bricht_ab_wenn))
        return "\n".join(zeilen)


# ═══════════════════════════════════════════ Werkzeuge je Absicht ══
# Was eine Absicht ueberhaupt braucht. Der Vertrag engt damit auf das ein,
# was zur Aufgabe passt: Ein Zaehlauftrag hat keinen Grund, `notiz_loeschen`
# in der Hand zu haben - auch wenn die Rechtestufe es erlauben wuerde.
_WERKZEUGE_JE_ABSICHT = {
    ZAEHLEN:    {"struktur_anzeigen", "notizen_suchen", "code_dateien"},
    ABRUF:      {"notizen_suchen", "struktur_anzeigen", "verwandte_finden",
                 "notiz_lesen", "code_suchen"},
    LESEN:      {"notiz_lesen", "bloecke_anzeigen", "notizen_suchen",
                 "struktur_anzeigen"},
    FRAGE:      {"notizen_suchen", "notiz_lesen", "verwandte_finden",
                 "struktur_anzeigen", "web_suchen", "web_lesen"},
    SCHREIBEN:  {"notizen_suchen", "notiz_lesen", "struktur_anzeigen",
                 "notiz_erstellen", "notiz_ergaenzen", "abschnitt_einfuegen",
                 "listenpunkt_hinzufuegen", "tags_setzen", "ordner_anlegen",
                 "notizen_verknuepfen", "codeblock_hinzufuegen"},
    UMBAUEN:    {"notizen_suchen", "notiz_lesen", "struktur_anzeigen",
                 "notiz_verschieben", "notiz_loeschen", "notiz_umbenennen",
                 "notizen_zusammenfuehren", "ordner_umbenennen",
                 "ordner_anlegen", "notiz_erstellen"},
    ANALYSE:    {"notizen_suchen", "notiz_lesen", "verwandte_finden",
                 "struktur_anzeigen", "bloecke_anzeigen", "notiz_erstellen",
                 "notiz_ergaenzen", "abschnitt_ersetzen"},
    WARTUNG:    {"links_pruefen", "links_bereinigen", "vault_wartung",
                 "auto_verlinken", "struktur_anzeigen", "notizen_suchen"},
    META:       {"struktur_anzeigen"},
    GESPRAECH:  set(),
}

# Was NIE dazugehoert, egal welche Absicht - ausser die Absicht ist genau
# darauf gerichtet. `rueckgaengig` steht hier, weil ein zweiter Aufruf zwei
# Schritte zuruecknimmt statt einen; das gehoert in Menschenhand.
_IMMER_VERBOTEN = {"rueckgaengig"}


class Vertragswerk:
    """Schliesst Vertraege - aus dem Befund, nicht aus einer Modellantwort."""

    def __init__(self, cfg=None, werkzeugnamen=None, melden=None) -> None:
        self.cfg = cfg
        self.melden = melden
        self.werkzeugnamen = set(werkzeugnamen or ())
        if not self.werkzeugnamen:
            try:
                from ..tools import TOOL_NAMES
                self.werkzeugnamen = set(TOOL_NAMES)
            except Exception:
                self.werkzeugnamen = set()

    # ------------------------------------------------------------ Schliessen
    def schliessen(self, befund: Befund, *, ergebnis: str = "",
                   strategie: Optional[dict] = None,
                   schritte: Optional[list] = None) -> Vertrag:
        """Der Vertrag zu einem Befund - und, wenn vorhanden, zu seinem Plan.

        `schritte` ist der entscheidende zweite Eingang. Ohne ihn entsteht
        der Rahmen allein aus der ABSICHT, und dann macht jede
        Fehlklassifikation aus einer laufenden Aufgabe einen Abbruch.

        Genau das ist beim ersten Anlauf passiert: "Lege nacheinander zwei
        Merkzettel an" wurde nicht als Schreibauftrag erkannt - im Deutschen
        steht bei trennbaren Verben die Vorsilbe am Satzende, und das Muster
        suchte sie direkt hinter dem Verb. Der Vertrag verbot daraufhin
        `notiz_erstellen`, obwohl der Plan aus genau diesem Schritt bestand.

        Die Lehre steckt in der Reihenfolge: Ein Rahmen, der dem PLAN
        widerspricht, ist falsch, nicht streng. Was geplant wurde, gehoert
        in den Rahmen - eingeengt wird alles, was darueber hinausgeht.
        """
        vertrag = Vertrag(
            ziel=befund.ziel or befund.auftrag,
            risiko=befund.risiko,
            signatur=befund.signatur,
            benoetigt=list(befund.faehigkeiten),
            budget=self._budget(befund, strategie),
        )
        vertrag.erfolg = self._erfolg(befund, ergebnis)
        erlaubt = self._erlaubt(befund)
        verboten = self._verboten(befund)
        geplant = self._geplante_werkzeuge(schritte)
        if geplant:
            erlaubt |= geplant
            verboten -= geplant
        vertrag.erlaubt = sorted(erlaubt)
        vertrag.verboten = sorted(verboten)
        vertrag.eskaliert_wenn = self._eskalation(befund)
        vertrag.bricht_ab_wenn = self._abbruch(befund)
        return vertrag

    def _geplante_werkzeuge(self, schritte: Optional[list]) -> set:
        """Die Werkzeuge, die im Plan wirklich vorkommen."""
        namen = set()
        for schritt in (schritte or []):
            if isinstance(schritt, dict):
                name = str(schritt.get("werkzeug") or "").strip()
            else:
                name = str(getattr(schritt, "werkzeug", "") or "").strip()
            if name:
                namen.add(name)
        if self.werkzeugnamen:
            namen &= self.werkzeugnamen
        return namen

    def weiten(self, vertrag: Vertrag, schritte: Optional[list]) -> Vertrag:
        """Einen bestehenden Vertrag um die Werkzeuge des Plans ergaenzen.

        Gebraucht, wenn der Plan ERST NACH dem Vertrag entsteht - also immer
        dann, wenn ein Fachagent oder das Modell zerlegt. Der Vertrag steht
        vorher fest (§11), der Plan kommt danach; beide muessen
        zusammenpassen, sonst blockiert der eine den anderen.
        """
        geplant = self._geplante_werkzeuge(schritte)
        if not geplant:
            return vertrag
        dazu = geplant - set(vertrag.erlaubt)
        if dazu:
            vertrag.erlaubt = sorted(set(vertrag.erlaubt) | geplant)
            vertrag.verboten = sorted(set(vertrag.verboten) - geplant)
        return vertrag

    # ---------------------------------------------------------- Erfolg (§11)
    def _erfolg(self, befund: Befund, ergebnis: str) -> List[Erfolgskriterium]:
        """Woran man erkennt, dass es fertig ist - abgeleitet, nicht geraten."""
        kriterien: List[Erfolgskriterium] = []
        if ergebnis.strip():
            # Was der Nutzer selbst gesagt hat, steht oben - auch wenn es
            # sich nicht mechanisch pruefen laesst.
            kriterien.append(Erfolgskriterium(ergebnis.strip()[:200],
                                              NICHT_PRUEFBAR))

        if befund.absicht == ZAEHLEN:
            kriterien.append(Erfolgskriterium(
                "eine Zahl liegt vor, die aus dem Bestand stammt", ZAHL_DA))
        elif befund.absicht in (ABRUF, LESEN):
            kriterien.append(Erfolgskriterium(
                "es liegen benannte Treffer aus der Vault vor", TREFFER_DA))
        elif befund.absicht == SCHREIBEN:
            kriterien.append(Erfolgskriterium(
                "die Notiz existiert auf der Platte und ist nicht leer",
                DATEI_DA))
            kriterien.append(Erfolgskriterium(
                "ihr Inhalt beantwortet den Auftrag", NICHT_PRUEFBAR))
        elif befund.absicht == UMBAUEN:
            kriterien.append(Erfolgskriterium(
                "die betroffene Notiz liegt nicht mehr an ihrem alten Platz",
                DATEI_WEG))
            kriterien.append(Erfolgskriterium(
                "und es gibt eine Sicherung im Papierkorb", DATEI_DA))
        elif befund.absicht == WARTUNG:
            kriterien.append(Erfolgskriterium(
                "die Wartung meldet einen Stand, keine Ausnahme", SCHEMA))
        elif befund.absicht == ANALYSE:
            kriterien.append(Erfolgskriterium(
                "das Ergebnis nennt die Quellen, auf denen es beruht",
                SCHEMA))
            kriterien.append(Erfolgskriterium(
                "die Aussagen stimmen mit den Quellen ueberein", NICHT_PRUEFBAR))
        elif befund.absicht in (FRAGE, META, GESPRAECH):
            kriterien.append(Erfolgskriterium(
                "eine Antwort liegt vor", SCHEMA))
        if befund.schreiben_noetig:
            kriterien.append(Erfolgskriterium(
                "keine andere Notiz wurde dabei veraendert", NICHT_PRUEFBAR))
        return kriterien

    # -------------------------------------------------------- Werkzeugrahmen
    def _erlaubt(self, befund: Befund) -> set:
        erlaubt = set(_WERKZEUGE_JE_ABSICHT.get(befund.absicht, set()))
        if befund.netz_noetig:
            erlaubt |= {"web_suchen", "web_lesen"}
        if "code_read" in befund.faehigkeiten:
            erlaubt |= {"code_suchen", "code_dateien", "quellcode_lesen",
                        "code_auslesen"}
        # Nur Werkzeuge, die es wirklich gibt. Ein Vertrag, der ein
        # erfundenes Werkzeug erlaubt, sieht grosszuegig aus und ist wertlos.
        if self.werkzeugnamen:
            erlaubt &= self.werkzeugnamen
        return erlaubt

    def _verboten(self, befund: Befund) -> set:
        verboten = set(_IMMER_VERBOTEN)
        if befund.absicht != UMBAUEN:
            # Wer nicht umbauen soll, loescht auch nicht. Das ist der Fall
            # vom 21.09. in Vertragsform: Ein Schreibauftrag hatte damals
            # Zugriff auf alles, was die Rechtestufe hergab.
            verboten |= {"notiz_loeschen", "notiz_verschieben",
                         "notizen_zusammenfuehren", "ordner_umbenennen",
                         "notiz_ueberschreiben", "abschnitt_loeschen"}
        if not befund.netz_noetig:
            verboten |= {"web_suchen", "web_lesen"}
        if self.werkzeugnamen:
            verboten &= self.werkzeugnamen
        return verboten

    # ------------------------------------------------------------- Budget
    def _budget(self, befund: Befund, strategie: Optional[dict]) -> Budget:
        """Was diese Aufgabe kosten darf - nach ihrer Groesse bemessen.

        Eine Zaehlfrage bekommt nicht dasselbe Kontingent wie eine
        Vergleichsanalyse. Ein pauschales Budget ist fuer die einfache
        Aufgabe zu gross (sie laeuft lange, bevor jemand merkt, dass sie
        haengt) und fuer die schwere zu klein.
        """
        budget = Budget.aus_config(self.cfg)
        faktor = 0.4 + 1.2 * befund.komplexitaet
        budget.werkzeugaufrufe = max(2, int(budget.werkzeugaufrufe * faktor))
        budget.modellaufrufe = max(1, int(budget.modellaufrufe * faktor))
        budget.laufzeit = max(20.0, budget.laufzeit * faktor)
        # Die Teiltoepfe MUESSEN mitwachsen. Sonst summieren sie sich unter
        # die Gesamtgrenze, und die kann dann gar nicht mehr greifen - eine
        # Schranke, die nie zuschlaegt, sieht aus wie eine und ist keine.
        budget.kleine_modellaufrufe = max(
            1, int(budget.kleine_modellaufrufe * faktor))
        budget.grosse_modellaufrufe = max(
            1, int(budget.grosse_modellaufrufe * faktor))

        rolle = (strategie or {}).get("rolle") or ""
        if befund.deterministisch_loesbar:
            # Ein Werkzeugweg braucht kein Modellbudget. Null heisst null -
            # und faellt damit sofort auf, wenn doch eines gerufen wird.
            budget.grosse_modellaufrufe = 0
            budget.kleine_modellaufrufe = max(1, budget.kleine_modellaufrufe // 6)
            budget.modellaufrufe = budget.kleine_modellaufrufe
        elif rolle in ("winzig", "klein"):
            # Klein geplant heisst: ein grosser Aufruf bleibt fuer die
            # Eskalation uebrig, mehr nicht.
            budget.grosse_modellaufrufe = 2
        return budget

    # ------------------------------------------------ Eskalation / Abbruch
    @staticmethod
    def _eskalation(befund: Befund) -> List[str]:
        gruende = ["die Verifikation eines Schritts faellt durch",
                   "zwei Versuche desselben Schritts scheitern"]
        if befund.abruf_noetig:
            gruende.append("die Quellen widersprechen sich")
        if befund.komplexitaet >= 0.5:
            gruende.append("der Plan waechst ueber seine geschaetzten Schritte")
        return gruende

    @staticmethod
    def _abbruch(befund: Befund) -> List[str]:
        gruende = ["ein Budget ist erschoepft",
                   "eine Freigabe wurde verweigert"]
        if befund.risiko >= 0.6:
            gruende.append("eine zerstoerende Handlung laesst sich nicht "
                           "verifizieren")
        return gruende
