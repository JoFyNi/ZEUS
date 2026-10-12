"""Der Tatsachenbericht - was WIRKLICH passiert ist.

Der wichtigste Grundsatz des Projekts in Codeform: Was der Code
gemessen hat, steht ueber dem, was das Modell behauptet. Ein
kleines Modell ueberliest ein 'ok: false' und schreibt trotzdem
'erfolgreich verschoben'; der Bericht darunter widerspricht ihm
mit den echten Rueckgabewerten.
"""
from __future__ import annotations

from typing import List, Sequence


from .antwort import ToolCallRecord
from .texte import (
    AUTOMATIK_BEHAUPTUNG, NUR_DEFINIERENDE_WERKZEUGE, VERAENDERNDE_WERKZEUGE,
    _behauptet_erfolg,
)


class BerichtTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    def _tatsachen_anhaengen(self, text: str, calls: Sequence[ToolCallRecord]) -> str:
        """Hängt an, was WIRKLICH passiert ist - und widerspricht dem Modell notfalls.

        Die Werkzeugergebnisse liegen dem Modell zwar vor, aber kleine Modelle
        überlesen ein 'ok: false' und schreiben trotzdem "erfolgreich verschoben".
        Deshalb wird der Bericht hier aus den echten Rückgabewerten gebaut.
        """
        # Eine Zeichnung gehört in die Antwort, nicht ins Werkzeugprotokoll.
        # Sie wird hier angehängt statt dem Modell überlassen: Ein Modell, das
        # ein Bild aus dem Werkzeugergebnis abschreibt, verliert Kästen,
        # verrutscht Linien und erfindet Knoten - und was der Nutzer sieht,
        # entspräche dann nicht mehr dem, was gerechnet wurde. Dasselbe
        # Grundmuster wie beim Tatsachenbericht: gemessen schlägt behauptet.
        text = self._zeichnungen_anhaengen(text, calls)

        veraendernd = [c for c in calls if c.werkzeug in VERAENDERNDE_WERKZEUGE]
        definiert = [c for c in calls
                     if c.werkzeug in NUR_DEFINIERENDE_WERKZEUGE and c.ergebnis.ok]

        # Eine angelegte Fertigkeit ist ein Bauplan, kein Ergebnis. Wenn das
        # Modell danach von durchgeführten Änderungen spricht, wird das hier
        # richtiggestellt - sonst glaubt man, es sei etwas passiert.
        if definiert and not veraendernd:
            namen = [str(c.parameter.get("name") or "?") for c in definiert]
            zeilen = ["**Tatsächlich ausgeführt:**"]
            for aufruf, benannt in zip(definiert, namen):
                zeilen.append(f"- ✔ Fertigkeit `{benannt}` wurde **angelegt**"
                              f" – {aufruf.ergebnis.meldung}")
                for zeile in (aufruf.ergebnis.daten.get("korrekturen") or []):
                    zeilen.append(f"    - korrigiert: {zeile}")
                probelauf = aufruf.ergebnis.daten.get("probelauf") or []
                if probelauf:
                    zeilen.append("    - Probelauf: " + " · ".join(str(p) for p in probelauf))
            zeilen.append("")
            zeilen.append(f"**An der Vault wurde dabei nichts geändert.** Die Fertigkeit "
                          f"läuft erst, wenn sie aufgerufen wird: "
                          f"`/fertigkeit {namen[0]}` oder \"führe {namen[0]} aus\".")
            if _behauptet_erfolg(text) or AUTOMATIK_BEHAUPTUNG.search(text or ""):
                zeilen.insert(0, "")
                zeilen.insert(0, f"> **Achtung, {self.cfg.user_title}: Der Text oben geht zu "
                                 f"weit.** Angelegt heißt nicht ausgeführt, und es gibt keine "
                                 f"automatische Wiederholung – ich habe keinen Zeitplaner.")
            return (text or "").rstrip() + "\n\n---\n" + "\n".join(zeilen)

        if not veraendernd:
            return text

        geglueckt = [c for c in veraendernd if c.ergebnis.ok]
        gescheitert = [c for c in veraendernd if not c.ergebnis.ok]
        zeilen: List[str] = []

        if gescheitert and _behauptet_erfolg(text):
            zeilen.append(
                f"> **Achtung, {self.cfg.user_title}: Der Text oben stimmt nicht vollständig.** "
                f"{len(gescheitert)} von {len(veraendernd)} Änderungen sind fehlgeschlagen."
            )
            zeilen.append("")

        zeilen.append("**Tatsächlich ausgeführt:**")
        if geglueckt:
            for aufruf in geglueckt:
                pfad = aufruf.ergebnis.daten.get("pfad") or aufruf.ergebnis.daten.get("nachher", "")
                zeilen.append(f"- ✔ {aufruf.werkzeug}: {aufruf.ergebnis.meldung}"
                              + (f"  `{pfad}`" if pfad else ""))
        else:
            zeilen.append("- (nichts)")

        if gescheitert:
            zeilen.append("")
            zeilen.append("**Fehlgeschlagen:**")
            for aufruf in gescheitert:
                ziel = aufruf.parameter.get("name") or aufruf.parameter.get("titel") or ""
                zeilen.append(f"- ✖ {aufruf.werkzeug}"
                              + (f" ({ziel})" if ziel else "")
                              + f": {aufruf.ergebnis.meldung}")
            zeilen.append("")
            zeilen.append('Mit "nochmal versuchen" nehme ich die offenen Punkte erneut in Angriff.')

        return (text or "").rstrip() + "\n\n---\n" + "\n".join(zeilen)

    def _zeichnungen_anhaengen(self, text: str, calls: Sequence[ToolCallRecord]) -> str:
        """Setzt gerechnete Bilder in die Antwort - in der Form, die ankommt.

        Zwei Formen aus demselben Layout, weil zwei Oberflächen zuhören:
        Das SVG zeichnet die grafische Oberfläche (sie kennt den ```svg-Zaun
        und säubert ihn); im Terminal fällt es auf die Unicode-Kästen zurück.
        Beides gleichzeitig anzuhängen wäre doppelt - deshalb entscheidet
        LIVE_VIEW, welche Form gesetzt wird.
        """
        bilder = [c for c in calls
                  if c.werkzeug == "struktur_zeichnen" and c.ergebnis.ok]
        if not bilder:
            return text
        teile = [(text or "").rstrip()]
        grafisch = bool(getattr(self.cfg, "live_view", False))
        for aufruf in bilder:
            svg = aufruf.ergebnis.daten.get("svg") or ""
            zeichnung = aufruf.ergebnis.daten.get("zeichnung") or ""
            if grafisch and svg:
                teile.append("```svg\n" + svg + "\n```")
            elif zeichnung:
                teile.append("```\n" + zeichnung + "\n```")
        return "\n\n".join(t for t in teile if t.strip())

    def _leere_antwort_erklaeren(self, calls: Sequence["ToolCallRecord"]) -> str:
        """Sagt, warum keine Antwort kam - statt sie als '…' zu zeigen.

        Vier unterscheidbare Fälle, und jeder braucht eine andere Reaktion des
        Nutzers. Sie alle als drei Punkte darzustellen war die schlechteste
        Auskunft von allen.
        """
        gedanken = (getattr(self.llm, "last_gedanken", "") or "").strip()
        num = getattr(self.cfg, "num_predict", 0)
        zeilen = ["**Das Modell hat nichts geantwortet.**", ""]

        if gedanken:
            zeilen.append(
                f"Es hat {len(gedanken)} Zeichen lang nachgedacht und dabei das "
                f"Token-Budget aufgebraucht (NUM_PREDICT={num}). "
                f"Abhilfe: NUM_PREDICT erhöhen oder in der `.env` `DENKEN=aus` setzen.")
            zeilen.append("")
            auszug = gedanken.strip().replace("\n", " ")[:400]
            zeilen.append(f"Anfang der Überlegung: *{auszug}…*")
        elif calls:
            geglueckt = [c for c in calls if c.ergebnis.ok]
            zeilen.append(
                f"{len(calls)} Werkzeug(e) liefen ({len(geglueckt)} erfolgreich), "
                f"danach kam kein Text mehr. Das Ergebnis der Werkzeuge steht oben; "
                f"frag mit `/weiter` nach einer Zusammenfassung.")
        else:
            zeilen.append(
                "Kein Text, keine Werkzeuge, kein Denkprotokoll. Prüfe mit `/server`, "
                "ob Host und Modell erreichbar sind, und mit `/tempo`, ob das Modell "
                "überhaupt antwortet.")
        return "\n".join(zeilen)

    def _verbindungshinweis(self, exc: Exception) -> str:
        text = str(exc)
        hinweise = [f"**Verbindungsproblem:** {text}"]
        if "innerhalb von" in text or "timeout" in text.lower():
            hinweise += [
                "",
                "Mögliche Ursachen, in der Reihenfolge ihrer Häufigkeit:",
                f"1. `{self.cfg.llm_model}` ist noch nicht im Speicher – der erste Aufruf nach dem "
                f"Start dauert bei großen Modellen Minuten. `KEEP_ALIVE={self.cfg.keep_alive}` hält "
                "es danach geladen.",
                f"2. Das Modell ist für die Hardware zu groß und rechnet auf der CPU. "
                f"`/tempo` misst die tatsächliche Geschwindigkeit.",
                f"3. Der Kontext ist zu groß (`NUM_CTX={self.cfg.num_ctx}`). Weniger Kontext "
                "beschleunigt spürbar.",
                "",
                f"Stellschrauben: `IDLE_TIMEOUT` (aktuell {self.cfg.idle_timeout}s) erhöhen, "
                f"`NUM_PREDICT` (aktuell {self.cfg.num_predict}) senken, kleineres Modell testen.",
            ]
        return "\n".join(hinweise)
