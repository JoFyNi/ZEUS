"""Das Werkzeugtor: die EINE Stelle, durch die jeder Werkzeugaufruf geht.

Vorher gab es drei Wege zu einem Werkzeug, und jeder machte etwas anderes:

    gespraech/werkzeuglauf._run_tool   Wiederholungssperre, Verifikation,
                                       Strichliste - aber keine Rechtepruefung
    skills.Fertigkeiten.ausfuehren     ruft tools.ausfuehren direkt
    kern/module.AutonomieModul         ruft tools.ausfuehren direkt

Drei Wege heissen drei Gelegenheiten, eine Schranke zu vergessen, und
mindestens eine war vergessen: Die Rechtestufe hat bis zum 21.09. NIE einen
Werkzeugaufruf aufgehalten (siehe werkzeugrechte.py, Modulkopf).

Das Tor macht daraus einen Weg. Was es der Reihe nach tut, und warum in
genau dieser Reihenfolge:

    0. VERTRAG     Gehoert das ueberhaupt zu DIESER Aufgabe? Der engste
                   Rahmen zuerst - und einer, der nur einengen kann.
    1. RECHT       Darf das ueberhaupt sein? Vor dem Budget, weil eine
                   verbotene Handlung nicht einmal Budget kosten soll.
    2. BUDGET      Ist noch Luft? Vor der Freigabe, damit ein Mensch nicht
                   etwas genehmigt, das danach am Budget scheitert.
    3. FREIGABE    Braucht es einen Menschen? Vor der Idempotenzpruefung,
                   denn eine genehmigte Wirkung darf gemerkt sein, eine
                   ungefragte nie.
    4. IDEMPOTENZ  Ist das schon geschehen? Direkt vor der Ausfuehrung -
                   spaeter waere es zu spaet, frueher unnoetig.
    5. AUSFUEHREN
    6. VERIFIKATION Hat es wirklich stattgefunden? Unabhaengig nachgesehen.
    7. BUCHEN      Telemetrie, Wirkung merken, Ereignis melden.

Die Reihenfolge ist kein Geschmack. Jede Vertauschung erzeugt einen Fall, in
dem entweder etwas ohne Erlaubnis geschieht oder etwas Genehmigtes doppelt
laeuft.

Was das Tor NICHT tut: Es entscheidet nicht, OB ein Werkzeug sinnvoll ist.
Das ist Sache des Planers und des Modells. Das Tor sagt nur, ob es sein DARF
und ob es getan hat, was es behauptet.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..werkzeuge.ergebnis import ToolResult
from .aufgabenspeicher import RISIKO_HOCH, RISIKO_MITTEL, RISIKO_RANG
from .budget import Budget, BudgetErschoepft
from .werkzeugrechte import Werkzeugart, merkmal, wirkungsschluessel


class FreigabeNoetig(RuntimeError):
    """Ein Mensch muss zustimmen, bevor dieser Aufruf laufen darf.

    Wird geworfen statt zurueckgegeben, und zwar mit Absicht: Ein Aufrufer,
    der einen `ToolResult` bekommt, macht weiter. Ein Aufrufer, der eine
    Ausnahme bekommt, kann nicht versehentlich weitermachen. Bei einer
    Handlung, die auf einen Menschen wartet, ist das der Unterschied
    zwischen Warten und Loeschen.
    """

    def __init__(self, werkzeug: str, frage: str, risiko: str,
                 schluessel: str = "", freigabe_id: str = "") -> None:
        self.werkzeug = werkzeug
        self.frage = frage
        self.risiko = risiko
        self.schluessel = schluessel
        self.freigabe_id = freigabe_id
        super().__init__(frage)


class Werkzeugtor:
    """Rechte, Budget, Freigabe, Idempotenz, Ausfuehrung, Verifikation."""

    def __init__(self, tools, cfg, *, rechte=None, speicher=None,
                 fertigkeiten=None, verhalten=None, bus=None,
                 melden: Optional[Callable[[str], None]] = None) -> None:
        self.tools = tools
        self.cfg = cfg
        self.rechte = rechte
        self.speicher = speicher
        self.fertigkeiten = fertigkeiten
        self.verhalten = verhalten
        self.bus = bus
        self.melden = melden
        self._verifikation = None
        # Ab welchem Risiko unbeaufsichtigt gefragt wird. Voreinstellung
        # 'hoch': Im Gespraech sitzt ein Mensch davor und hat den Auftrag
        # gerade erteilt; unbeaufsichtigt sitzt niemand davor.
        self.freigabeschwelle = str(
            getattr(cfg, "freigabe_ab_risiko", RISIKO_HOCH) or RISIKO_HOCH)

    # ------------------------------------------------------------ Verifikation
    @property
    def verifikation(self):
        if self._verifikation is None:
            from .verifikation import Verifikation
            self._verifikation = Verifikation(self.tools, self.cfg)
        return self._verifikation

    # --------------------------------------------------------------- Einstieg
    def ausfuehren(self, name: str, parameter: Optional[dict] = None, *,
                   aufgabe_id: Optional[str] = None,
                   wirkungsbereich: Optional[str] = None,
                   budget: Optional[Budget] = None,
                   vertrag=None,
                   unbeaufsichtigt: bool = False,
                   akteur: str = "zeus",
                   schritt: int = 0) -> ToolResult:
        """Fuehrt ein Werkzeug aus - oder sagt begruendet, warum nicht.

        Wirft `FreigabeNoetig`, wenn ein Mensch zustimmen muss, und
        `BudgetErschoepft`, wenn kein Budget mehr da ist. Beide sind keine
        Fehler, sondern Zustaende, die der Aufrufer behandeln MUSS - deshalb
        Ausnahmen und keine stillen Rueckgabewerte.
        """
        parameter = dict(parameter or {})
        eigenschaft = merkmal(name, self.fertigkeiten)
        begonnen = time.time()

        # --- 0. Vertrag ---------------------------------------------------
        # Der engste Rahmen zuerst. Ein Vertrag ENGT NUR EIN - sein Ja
        # bedeutet ausdruecklich nicht "erlaubt", sondern nur "gehoert zu
        # dieser Aufgabe". Danach fragen Recht, Budget und Freigabe
        # unveraendert weiter.
        #
        # Der Fall, den das abfaengt: Am 21.09. hatte ein Schreibauftrag
        # Zugriff auf alles, was die Rechtestufe hergab - und legte beim
        # Auftrag "Loesche die Notiz X" eine neue Notiz dieses Namens an.
        # Ein Vertrag haette 'notiz_erstellen' dort gar nicht erst in der
        # Hand gehabt.
        if vertrag is not None:
            erlaubt, grund = vertrag.werkzeug_erlaubt(name)
            if not erlaubt:
                abgelehnt = ToolResult(
                    False, f"{grund} Der Aufruf ist NICHT gelaufen.",
                    {"vertrag": "ausserhalb", "werkzeug": name})
                self._buchen(aufgabe_id, "werkzeug", name, parameter, abgelehnt,
                             begonnen, schritt, urteil="ausserhalb_vertrag")
                return abgelehnt

        # --- 1. Recht -----------------------------------------------------
        verweigert = self._rechte_pruefen(eigenschaft, parameter)
        if verweigert is not None:
            self._buchen(aufgabe_id, "werkzeug", name, parameter, verweigert,
                         begonnen, schritt, urteil="verweigert")
            return verweigert

        # --- 2. Budget ----------------------------------------------------
        if budget is not None:
            budget.pruefen("werkzeugaufrufe")

        # --- 3. Freigabe --------------------------------------------------
        schluessel = wirkungsschluessel(name, parameter, eigenschaft)
        if self._freigabe_noetig(eigenschaft, unbeaufsichtigt):
            entschieden = self._freigabe_stand(aufgabe_id, schluessel or
                                               f"{name}|<ohne schluessel>")
            if entschieden is None:
                self._freigabe_erbitten(aufgabe_id, eigenschaft, parameter,
                                        schluessel or f"{name}|<ohne schluessel>")
            if entschieden is False:
                abgelehnt = ToolResult(
                    False,
                    f"'{name}' wurde nicht freigegeben. Der Aufruf ist "
                    f"unterblieben. Nimm einen anderen Weg oder frage nach, "
                    f"was stattdessen geschehen soll.",
                    {"freigabe": "abgelehnt", "werkzeug": name})
                self._buchen(aufgabe_id, "werkzeug", name, parameter, abgelehnt,
                             begonnen, schritt, urteil="abgelehnt")
                return abgelehnt

        # --- 4. Idempotenz ------------------------------------------------
        # Der Merkschluessel traegt den WIRKUNGSBEREICH - im Regelfall das
        # Ziel, ersatzweise die Aufgabe. Beides ist bewusst gewaehlt:
        #
        # * NICHT global. Sonst waere "lege Notiz X an" nach dem ersten Mal
        #   fuer immer gesperrt, und die Meldung "ist schon geschehen" waere
        #   bei einem neuen Auftrag des Nutzers schlicht falsch.
        # * NICHT je Aufgabe. Eine Neuplanung auf Zielebene legt NEUE
        #   Aufgaben an; mit der Aufgabenkennung im Schluessel faenden die
        #   eine leere Merkliste vor und taeten alles noch einmal. Genau so
        #   gemessen: Nach einem Absturz stand die erste Notiz zweimal in
        #   der Vault, als "MerkzettelEins" und "MerkzettelEins_2".
        bereich = wirkungsbereich or aufgabe_id
        merkschluessel = f"{bereich}|{schluessel}" if (bereich and schluessel) else ""
        gemerkt = self._wirkung_nachsehen(merkschluessel, aufgabe_id)
        if gemerkt is not None:
            self._buchen(aufgabe_id, "werkzeug", name, parameter, gemerkt,
                         begonnen, schritt, urteil="schon_geschehen")
            return gemerkt

        # --- 5. Ausfuehren ------------------------------------------------
        try:
            ergebnis = self._roh_ausfuehren(name, parameter)
        except Exception as exc:
            # Ein werfendes Werkzeug ist ein Fehlschlag, kein Absturz des
            # Systems. Der Aufrufer bekommt ein Ergebnis, mit dem er planen
            # kann - eine durchgereichte Ausnahme reisst dagegen die ganze
            # Aufgabe mit, auch wenn nur ein Schritt daneben ging.
            ergebnis = ToolResult(
                False, f"'{name}' hat einen Fehler geworfen: "
                       f"{type(exc).__name__}: {exc}",
                {"ausnahme": type(exc).__name__})
            self._buchen(aufgabe_id, "werkzeug", name, parameter, ergebnis,
                         begonnen, schritt, fehler=str(exc))
            if budget is not None:
                budget.buchen("werkzeugaufrufe")
            return ergebnis

        if budget is not None:
            budget.buchen("werkzeugaufrufe")
            budget.buchen("zeichen", len(str(getattr(ergebnis, "meldung", ""))))

        # --- 6. Verifikation ----------------------------------------------
        ergebnis, urteil = self._nachpruefen(name, parameter, ergebnis)

        # --- 7. Buchen ----------------------------------------------------
        if ergebnis.ok and merkschluessel and eigenschaft.veraendert:
            self._wirkung_merken(merkschluessel, name, ergebnis, aufgabe_id)
        self._buchen(aufgabe_id, "werkzeug", name, parameter, ergebnis,
                     begonnen, schritt, urteil=urteil)
        return ergebnis

    # ------------------------------------------------------------------ Recht
    def _rechte_pruefen(self, eigenschaft, parameter: dict) -> Optional[ToolResult]:
        if self.rechte is None:
            return None
        zweck = f"{eigenschaft.name}({', '.join(sorted(parameter))[:80]})"
        if self.rechte.verlangen(eigenschaft.recht, zweck):
            return None
        return ToolResult(
            False,
            f"'{eigenschaft.name}' ist auf der aktuellen Rechtestufe nicht "
            f"erlaubt. {self.rechte.grund(eigenschaft.recht)} Der Aufruf ist "
            f"NICHT gelaufen - behaupte nichts anderes.",
            {"recht": eigenschaft.recht, "verweigert": True,
             "stufe": getattr(self.rechte, "stufe", None)})

    # --------------------------------------------------------------- Freigabe
    def _freigabe_noetig(self, eigenschaft, unbeaufsichtigt: bool) -> bool:
        """Nur unbeaufsichtigt, und nur ab der eingestellten Risikoschwelle.

        Im Gespraech sitzt ein Mensch davor, der den Auftrag gerade erteilt
        hat - ihn nach jeder Loeschung erneut zu fragen, die er selbst
        verlangt hat, macht die Frage wertlos. Unbeaufsichtigt ist es genau
        umgekehrt.
        """
        if not unbeaufsichtigt:
            return False
        schwelle = RISIKO_RANG.get(self.freigabeschwelle, 2)
        return eigenschaft.rang >= schwelle

    def _freigabe_stand(self, aufgabe_id: Optional[str],
                        schluessel: str) -> Optional[bool]:
        if not (self.speicher and aufgabe_id):
            return None
        try:
            eintrag = self.speicher.freigabe_stand(aufgabe_id, schluessel)
        except Exception:
            return None
        return None if eintrag is None else bool(eintrag.get("erteilt"))

    def _freigabe_erbitten(self, aufgabe_id: Optional[str], eigenschaft,
                           parameter: dict, schluessel: str) -> None:
        frage = (f"'{eigenschaft.name}' soll laufen "
                 f"({', '.join(f'{k}={str(v)[:40]}' for k, v in list(parameter.items())[:3])}). "
                 f"Das ist eine {eigenschaft.wirkung}e Handlung mit Risiko "
                 f"'{eigenschaft.risiko}'. Freigeben?")
        kennung = ""
        if self.speicher and aufgabe_id:
            try:
                kennung = self.speicher.freigabe_erbitten(
                    aufgabe_id, frage, risiko=eigenschaft.risiko,
                    moeglichkeiten=["ja", "nein"], schluessel=schluessel)
            except Exception as exc:
                self._sagen(f"Freigabe konnte nicht hinterlegt werden: {exc}")
        self._ereignis("freigabe.erbeten", werkzeug=eigenschaft.name,
                       aufgabe=aufgabe_id, risiko=eigenschaft.risiko)
        raise FreigabeNoetig(eigenschaft.name, frage, eigenschaft.risiko,
                             schluessel, kennung)

    # ------------------------------------------------------------- Idempotenz
    def _wirkung_nachsehen(self, schluessel: str,
                           aufgabe_id: Optional[str]) -> Optional[ToolResult]:
        if not (schluessel and self.speicher):
            return None
        try:
            eintrag = self.speicher.wirkung_gemerkt(schluessel)
        except Exception:
            return None
        if not eintrag:
            return None
        gemerkt = eintrag.get("ergebnis") or {}
        return ToolResult(
            True,
            f"{gemerkt.get('meldung') or 'Bereits erledigt.'} "
            f"(Diese Wirkung ist schon eingetreten - der Aufruf wurde NICHT "
            f"wiederholt.)",
            {**(gemerkt.get("daten") or {}), "schon_geschehen": True,
             "zuerst_am": eintrag.get("zeit")})

    def _wirkung_merken(self, schluessel: str, name: str, ergebnis: ToolResult,
                        aufgabe_id: Optional[str]) -> None:
        if not self.speicher:
            return
        try:
            self.speicher.wirkung_merken(
                schluessel, name,
                {"meldung": ergebnis.meldung, "daten": ergebnis.daten},
                aufgabe_id)
        except Exception as exc:
            self._sagen(f"Wirkung nicht gemerkt: {exc}")

    # ------------------------------------------------------------ Ausfuehren
    def _roh_ausfuehren(self, name: str, parameter: dict) -> ToolResult:
        """Der eigentliche Aufruf - drei Arten Werkzeug, eine Stelle."""
        if (name == "fertigkeit_definieren" and self.fertigkeiten is not None
                and getattr(self.cfg, "self_extension", False)):
            return self.fertigkeiten.definieren(
                name=parameter.get("name", ""),
                beschreibung=parameter.get("beschreibung", ""),
                parameter=parameter.get("parameter") or [],
                schritte=parameter.get("schritte") or [])
        if (name == "verhalten_definieren" and self.verhalten is not None
                and getattr(self.cfg, "self_extension", False)):
            return self.verhalten.definieren(
                name=parameter.get("name", ""),
                beschreibung=parameter.get("beschreibung", ""),
                ausloeser=parameter.get("ausloeser") or [],
                anleitung=parameter.get("anleitung", ""),
                beispiel=parameter.get("beispiel", ""))
        if self.fertigkeiten is not None:
            try:
                bekannt = name in (self.fertigkeiten.alle or {})
            except Exception:
                bekannt = False
            if bekannt:
                return self.fertigkeiten.ausfuehren(name, parameter)
        return self.tools.ausfuehren(name, parameter)

    # ---------------------------------------------------------- Verifikation
    def _nachpruefen(self, name: str, parameter: dict, ergebnis: ToolResult):
        """Dreht `ok=True` um, wenn die Gegenprobe scheitert.

        Das Umdrehen ist der ganze Zweck. Eine Erfolgsmeldung, die einer
        unabhaengigen Pruefung nicht standhaelt, ist schlimmer als ein
        Fehler: Der Nutzer glaubt sie und merkt es Tage spaeter.
        """
        if not getattr(ergebnis, "ok", False):
            return ergebnis, ""
        try:
            befund = self.verifikation.pruefen(name, parameter, ergebnis)
        except Exception as exc:
            self._sagen(f"Verifikation uebersprungen: {type(exc).__name__}: {exc}")
            return ergebnis, "nicht_pruefbar"

        daten = dict(ergebnis.daten or {})
        daten["verifikation"] = befund.urteil
        if befund.urteil == "durchgefallen":
            daten["pruefbefunde"] = befund.befunde
            return ToolResult(
                False,
                f"{ergebnis.meldung} ABER DIE GEGENPROBE SCHEITERT: "
                f"{'; '.join(befund.befunde)}. Melde das NICHT als Erfolg.",
                daten), befund.urteil
        if befund.geprueft:
            daten["geprueft"] = befund.geprueft
        return ToolResult(True, ergebnis.meldung, daten), befund.urteil

    # ---------------------------------------------------------------- Buchen
    def _buchen(self, aufgabe_id, art: str, name: str, parameter: dict,
                ergebnis: ToolResult, begonnen: float, schritt: int,
                urteil: str = "", fehler: str = "") -> None:
        dauer = time.time() - begonnen
        if self.speicher and aufgabe_id:
            try:
                self.speicher.lauf_vermerken(
                    aufgabe_id, art, name=name,
                    eingabe=", ".join(f"{k}={str(v)[:60]}"
                                      for k, v in list(parameter.items())[:6]),
                    ausgabe=str(ergebnis.meldung)[:600], ok=bool(ergebnis.ok),
                    urteil=urteil, dauer=dauer, fehler=fehler, schritt=schritt)
            except Exception:
                pass
        self._ereignis("werkzeug.gelaufen", werkzeug=name, ok=bool(ergebnis.ok),
                       urteil=urteil, aufgabe=aufgabe_id, dauer=round(dauer, 3))
        # Strichliste fuer die Nutzungsstatistik. Beiwerk: Ihr Ausfall darf
        # keinen Auftrag abbrechen.
        try:
            from .. import nutzung
            nutzung.vermerken(name, bool(ergebnis.ok), self.cfg)
        except Exception:
            pass

    def _ereignis(self, name: str, /, **daten) -> None:
        # Der Schraegstrich ist kein Schmuck: Ohne ihn kollidiert ein
        # Nutzlastfeld namens 'name' mit dem Parameter, und der TypeError
        # faellt an einer Stelle an, die mit dem Ereignis nichts zu tun hat.
        # Genau das ist beim Zeitplan passiert.
        if self.bus is not None:
            try:
                self.bus.melden(name, quelle="werkzeugtor", **daten)
            except Exception:
                pass

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass
