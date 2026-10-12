"""VaultTools: der Zusammenbau aus Unterbau und Werkzeuggruppen.

Warum ein Zusammenbau und keine einzelne Klasse mehr: `tools.py` war mit
3 200 Zeilen und 72 Methoden die groesste Datei des Projekts, und beim Suchen
nach "wo wird eigentlich verlinkt" scrollte man an einem Dutzend
Sachgebieten vorbei. Die Aufteilung aendert nichts am Verhalten - dieselben
Methoden, dieselbe Klasse, dieselben Namen -, sie macht nur sichtbar, welche
Werkzeuge zusammengehoeren.

Zur Reihenfolge der Basisklassen: `VaultBasis` steht ZULETZT. Python sucht
Methoden von links nach rechts; alles, was eine Gruppe ueberschreiben wollte,
gewaenne damit gegen den Unterbau. Die Gruppen ueberschreiben zwar nichts -
ihre Namen sind ueberschneidungsfrei, und `teile_tools.py` prueft das -, aber
die Reihenfolge soll auch dann noch stimmen, wenn jemand das vergisst.

Was hier NICHT stehen darf: Fachlogik. Jede Methode gehoert in ihre Gruppe.
Eine Ausnahme "nur diese eine" macht aus dem Zusammenbau in einem halben Jahr
wieder eine dritte Sammelstelle.
"""
from __future__ import annotations

from .abschnitte import AbschnittsWerkzeuge
from .basis import VaultBasis
from .code import CodeWerkzeuge
from .einlesen import ImportWerkzeuge
from .lesen import LeseWerkzeuge
from .netz import NetzWerkzeuge
from .listen import ListenWerkzeuge
from .notizen import NotizWerkzeuge
from .verknuepfen import VerknuepfungsWerkzeuge
from .wartung import WartungsWerkzeuge
from .zeichnen import ZeichenWerkzeuge


class VaultTools(
    LeseWerkzeuge,
    NetzWerkzeuge,
    NotizWerkzeuge,
    ListenWerkzeuge,
    AbschnittsWerkzeuge,
    VerknuepfungsWerkzeuge,
    CodeWerkzeuge,
    ImportWerkzeuge,
    WartungsWerkzeuge,
    ZeichenWerkzeuge,
    VaultBasis,
):
    """Vollzugriff auf die Vault - lesen, schreiben, umbauen, verlinken.

    Die Klasse ist unveraendert die, die sie war: `VaultTools(index, cfg)`
    baut sie, `ausfuehren(name, parameter)` ruft ein Werkzeug auf, und jede
    Aenderung laeuft durch Sicherung, Journal und Rueckleseprobe.
    """
