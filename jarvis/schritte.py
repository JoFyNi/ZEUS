"""Was ein Werkzeug INNEN tut - Schritt für Schritt, und live beim Arbeiten.

Bis hierher war ein Werkzeugaufruf eine Blackbox: Man sah `notiz_erstellen`,
dann eine Weile nichts, dann `✔ angelegt`. Was dazwischen passiert - Ordner
wählen, Namen prüfen, sichern, schreiben, zurücklesen, indexieren - stand
nirgends. Beim Suchen eines Fehlers ist genau das die einzige Frage: **Wo
genau ist es abgebogen?**

Zwei Teile:

* **Der Katalog** unten sagt, welche Schritte ein Werkzeug hat. Er ist aus dem
  Quelltext abgelesen, nicht erfunden - jeder Eintrag entspricht einer Stelle,
  die es wirklich gibt. Damit kann die Oberfläche die Schritte schon zeigen,
  bevor etwas läuft.
* **Die Meldung** zur Laufzeit: `VaultTools.schritt(name)` sagt, welcher
  Schritt gerade dran ist. Die Oberfläche lässt den Stern des Werkzeugs
  entsprechend weit aufleuchten.

Ehrlichkeitsregel, wie überall hier: Gemeldet wird nur, was wirklich läuft.
Ein Schritt, der im Katalog steht, aber nie gemeldet wird, bleibt in der
Anzeige matt - er behauptet nicht, stattgefunden zu haben.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# Die vier Phasen, die JEDES schreibende Werkzeug durchläuft. Sie stecken
# nicht in den Werkzeugen selbst, sondern in VaultTools.ausfuehren() und
# transaktion() - deshalb stehen sie hier einmal und nicht dreißigmal.
GEMEINSAM_SCHREIBEND: List[Tuple[str, str]] = [
    ("pruefen", "Parameter und Notiznamen prüfen (kein geratener Name beim Schreiben)"),
    ("sichern", "Sicherung anlegen und Transaktion öffnen"),
    ("schreiben", "Datei atomar schreiben und zurücklesen"),
    ("verbuchen", "Journal, Index und Strichliste nachziehen"),
]

GEMEINSAM_LESEND: List[Tuple[str, str]] = [
    ("aufloesen", "Namen auf eine echte Notiz auflösen"),
    ("lesen", "Inhalt holen"),
    ("aufbereiten", "Ergebnis für das Modell zuschneiden"),
]

# Werkzeugeigene Schritte. Nur dort eingetragen, wo das Werkzeug wirklich mehr
# tut als das gemeinsame Muster - sonst wäre die Liste Dekoration.
EIGEN: Dict[str, List[Tuple[str, str]]] = {
    "struktur_zeichnen": [
        ("lesen", "Kantenschreibweise einlesen und Knoten anlegen"),
        ("ebenen", "Ebenen über den längsten Weg, Rückwärtskanten aussortieren"),
        ("ordnen", "Reihenfolge je Ebene gegen Kreuzungen (Baryzentrum)"),
        ("aufbereiten", "Führung rechnen und in drei Formen ausgeben"),
    ],
    "notizen_suchen": [
        ("stichwort", "Stichwortsuche über alle Notizen"),
        ("semantisch", "Vektorsuche im Index"),
        ("mischen", "Treffer zusammenführen und nach Güte sortieren"),
    ],
    "notiz_erstellen": [
        ("ordner", "Ordner über vier gewichtete Signale wählen"),
        ("dublette", "Prüfen, ob es die Notiz schon gibt"),
        ("frontmatter", "Frontmatter mit Ordnerbegründung setzen"),
    ],
    "notiz_ueberschreiben": [
        ("exakt", "Namen EXAKT auflösen (kein ungefährer Treffer)"),
        ("frontmatter", "Bestehendes Frontmatter retten"),
    ],
    "notiz_verschieben": [
        ("ziel", "Zielordner auflösen oder anlegen"),
        ("verschieben", "Datei umziehen"),
        ("verweise", "Verweise in anderen Notizen mitziehen"),
    ],
    "notizen_verknuepfen": [
        ("beide", "Beide Notizen exakt auflösen"),
        ("abschnitt", "Abschnitt für verwandte Notizen finden oder anlegen"),
        ("hin", "Link in der Quelle setzen"),
        ("rueck", "Rücklink im Ziel setzen"),
    ],
    "auto_verlinken": [
        ("sammeln", "Notizen des Ordners einsammeln"),
        ("aehnlichkeit", "Paarweise Nähe berechnen"),
        ("auswaehlen", "Beste Paare je Notiz wählen (Obergrenze beachten)"),
        ("setzen", "Verknüpfungen setzen – ein Schreibvorgang je Notiz"),
    ],
    "links_pruefen": [
        ("sammeln", "Alle [[Links]] einsammeln – ohne die aus Codeblöcken"),
        ("pruefen", "Jedes Ziel gegen den Bestand halten"),
        ("reparieren", "Tote Links auf den nächsten passenden Namen umbiegen"),
    ],
    "links_bereinigen": [
        ("sammeln", "Echte Links einsammeln (Code bleibt außen vor)"),
        ("entscheiden", "Modus anwenden: entschärfen, entfernen oder markieren"),
    ],
    "vault_wartung": [
        ("index", "Index abgleichen"),
        ("links", "Tote Links reparieren"),
        ("verwaist", "Verwaiste Notizen suchen"),
        ("dubletten", "Dublettenverdacht sammeln"),
    ],
    "notizen_zusammenfuehren": [
        ("quellen", "Quellnotizen auflösen (mindestens zwei)"),
        ("zusammen", "Inhalte in eine Notiz gießen"),
        ("verweise", "Verweise auf die Quellen umbiegen"),
        ("papierkorb", "Quellen in den Papierkorb, nicht löschen"),
    ],
    "notiz_aufteilen": [
        ("kapitel", "Kapitel an den Überschriften trennen"),
        ("anlegen", "Je Kapitel eine Notiz anlegen"),
        ("uebersicht", "Original zur Übersichtsnotiz umbauen"),
    ],
    "uebersichtsnotiz": [
        ("sammeln", "Notizen des Ordners einsammeln"),
        ("gruppieren", "Nach Tags gruppieren"),
        ("exakt", "Eigenen Titel EXAKT prüfen – sonst neu anlegen"),
    ],
    "codebasis_importieren": [
        ("dateien", "Quelldateien nach Muster suchen"),
        ("hash", "Inhalt hashen – Unverändertes überspringen"),
        ("teilen", "Große Dateien in Teile schneiden"),
        ("ablegen", "Als Notizen in die Vault legen"),
    ],
    "notiz_loeschen": [
        ("papierkorb", "In den Papierkorb verschieben, nicht löschen"),
    ],
    "rueckgaengig": [
        ("journal", "Letzten Eintrag im Journal lesen"),
        ("zuruecklegen", "Vorige Fassung zurückspielen"),
    ],
    "block_ersetzen": [
        ("bloecke", "Notiz in Blöcke zerlegen"),
        ("vorschau", "Unterschied zeigen und bestätigen lassen"),
        ("ersetzen", "Nur diesen einen Block austauschen"),
    ],
    "verwandte_finden": [
        ("bezug", "Ausgangsnotiz auflösen"),
        ("vergleichen", "Gegen den Bestand vergleichen"),
        ("ranken", "Nach Nähe sortieren"),
    ],
    "code_suchen": [
        ("dateien", "Quelldateien durchgehen"),
        ("suchen", "Zeichenkette suchen"),
        ("stellen", "Datei und Zeile nennen"),
    ],
}


def schritte(werkzeug: str, schreibend: bool) -> List[dict]:
    """Alle Schritte eines Werkzeugs in der Reihenfolge, in der sie laufen.

    Erst das Eigene, dann das Gemeinsame - so herum, weil die Vorarbeit
    (Ordner wählen, Ähnlichkeit rechnen) vor dem Schreiben passiert.
    """
    reihe = list(EIGEN.get(werkzeug, []))
    reihe += GEMEINSAM_SCHREIBEND if schreibend else GEMEINSAM_LESEND
    gesehen, ausgabe = set(), []
    for schluessel, text in reihe:
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        ausgabe.append({"schluessel": schluessel, "text": text})
    return ausgabe


def alle(werkzeugnamen, schreibende) -> Dict[str, List[dict]]:
    """Katalog für die Oberfläche: Werkzeugname → Schrittliste."""
    return {name: schritte(name, name in schreibende) for name in werkzeugnamen}
