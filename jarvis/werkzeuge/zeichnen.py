"""Ablaeufe zeichnen.

Das Werkzeug faellt aus der Reihe: Es fasst die Vault nicht an
und rechnet nur. Es steht trotzdem hier, damit es ueber
denselben Weg aufgerufen, geprueft, gezaehlt und protokolliert
wird wie jedes andere.
"""
from __future__ import annotations


from .ergebnis import ToolResult


class ZeichenWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ------------------------------------------------------------- Zeichnen
    def struktur_zeichnen(self, beschreibung: str, titel: str = "",
                          richtung: str = "") -> ToolResult:
        """Zeichnet einen Ablauf. Das LAYOUT rechnet Python, nicht das Modell.

        Ein Werkzeug, das die Vault nicht anfasst - es baut nur ein Bild aus
        einer Strukturbeschreibung. Es steht trotzdem hier und nicht im Agenten,
        damit es über denselben Weg aufgerufen, protokolliert und gezählt wird
        wie alles andere. Ein Bild, das das Modell selbst malt, ist beim zweiten
        Aufruf ein anderes; dieses hier ist bei gleicher Eingabe gleich.
        """
        from ..darstellung import DarstellungsFehler, zeichnen

        self.schritt("lesen")
        try:
            teile = zeichnen(str(beschreibung or ""), str(titel or ""), str(richtung or ""))
        except DarstellungsFehler as exc:
            # Der Grund gehört in die Meldung, nicht ins Protokoll: Das Modell
            # muss aus dem Fehlschlag die richtige Schreibweise lernen können.
            return ToolResult(
                False, f"Nicht zeichenbar: {exc}",
                {"schreibweise": 'Eine Verbindung je Zeile: "A -> B", Form über '
                                 'Klammern: (rund) Anfang/Ende, [eckig] Schritt, '
                                 '{geschweift} Entscheidung, <spitz> Ein-/Ausgabe. '
                                 'Beschriftung nach dem Doppelpunkt: "A -> B: ok".'})
        self.schritt("aufbereiten")

        zeichnung = teile.get("zeichen", "")
        meldung = (f"Ablauf gezeichnet: {teile.get('knoten')} Knoten, "
                   f"{teile.get('kanten')} Verbindungen.\n\n```\n{zeichnung}\n```")
        return ToolResult(
            True, meldung,
            {"zeichnung": zeichnung,
             "mermaid": teile.get("mermaid", ""),
             "svg": teile.get("svg", ""),
             "knoten": teile.get("knoten"),
             "kanten": teile.get("kanten"),
             # Ausdrücklich, damit das Modell nicht "ich habe es gespeichert"
             # daraus macht: gezeichnet ist nicht abgelegt.
             "gespeichert": False,
             "hinweis": "Das Bild steht in der Antwort, NICHT in der Vault. Für eine "
                        "Notiz den Mermaid-Quelltext mit notiz_erstellen ablegen - "
                        "Obsidian zeichnet ihn selbst."})
