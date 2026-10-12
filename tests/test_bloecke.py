"""Tests für die blockweise Bearbeitung und die Diff-Rückfrage.

Zur Aufgabenstellung: Sie ging davon aus, `vault.py` könne nur anhängen und es
fehle ein dreistufiger Pfad aus Schnappschuss, Blockzerlegung und Diff-Abfrage.
Zwei der drei Stufen gab es bereits, nur woanders:

* Schnappschuss: `VaultTools._track()` sichert jede betroffene Datei nach
  `.jarvis/backups/`, `transaktion()` klammert mehrere Änderungen, und
  `rueckgaengig()` spielt sie zurück.
* Bearbeitung: notiz_bearbeiten, abschnitt_ersetzen, abschnitt_einfuegen,
  abschnitt_loeschen, notiz_ueberschreiben.

Wirklich gefehlt haben die BLOCK-ADRESSIERUNG (bisher ging nur „Abschnitt mit
dieser Überschrift") und die DIFF-RÜCKFRAGE: `CONFIRM_WRITES` stand in der
Konfiguration und wurde nirgends ausgewertet. Beides wird hier geprüft - samt
der Frage, ob die vorhandenen Sicherungen dabei auch wirklich greifen.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_bloecke_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.bloecke import (block_ersetzen_text, parse_blocks,  # noqa: E402
                            serialize, unterschied, unterschied_knapp)
from jarvis.config import Settings  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import VaultTools  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


NOTIZ = """# Agentenarchitektur

Ein Agent besteht aus Wahrnehmung, Entscheidung und Handlung.
Das gilt unabhängig davon, ob ein Modell im Spiel ist.

## Werkzeuge

- notizen_suchen
- notiz_lesen
- notiz_erstellen

Ein Werkzeug ohne Test ist ein Versprechen ohne Deckung.

## Beispiel

```python
def schritt(zustand):
    # Eine leere Zeile im Codeblock

    return zustand + 1
```

Danach folgt der nächste Absatz.
"""


def main() -> int:
    cfg = Settings()

    # ================================================= 1. Zerlegen
    bloecke = parse_blocks(NOTIZ)
    arten = [b.art for b in bloecke]
    check("Überschriften werden eigene Blöcke",
          arten.count("ueberschrift") == 3, str(arten))
    check("Absätze, Listen und Code werden unterschieden",
          "absatz" in arten and "liste" in arten and "code" in arten, str(arten))
    check("Die Nummerierung ist lückenlos und 1-basiert",
          [b.nummer for b in bloecke] == list(range(1, len(bloecke) + 1)))

    code = next(b for b in bloecke if b.art == "code")
    check("Ein Codeblock bleibt trotz Leerzeile am Stück",
          code.text.count("```") == 2 and "return zustand + 1" in code.text,
          code.text[:60])

    liste = next(b for b in bloecke if b.art == "liste")
    check("Eine Aufzählung bleibt ein Block",
          liste.text.count("\n") == 2, repr(liste.text))
    check("Blöcke kennen ihre Überschrift",
          liste.ueberschrift == "Werkzeuge", liste.ueberschrift)
    check("Und ihre Quellzeilen", liste.von < liste.bis, f"{liste.von}–{liste.bis}")
    check("Die Adresse ist lesbar",
          "unter „Werkzeuge“" in liste.adresse, liste.adresse)

    # ================================================= 2. Zusammensetzen
    check("Unverändert zerlegt und zusammengesetzt bleibt der Inhalt gleich",
          serialize(bloecke, NOTIZ).split() == NOTIZ.split(),
          serialize(bloecke, NOTIZ)[:80])

    # ================================================= 3. Ersetzen (nur Text)
    neu, getroffen, fehler = block_ersetzen_text(
        NOTIZ, liste.nummer, "- notizen_suchen\n- notiz_lesen\n- block_ersetzen")
    check("Ein Block lässt sich ersetzen", not fehler and getroffen is not None, fehler)
    check("Der neue Text steht drin", "block_ersetzen" in neu)
    check("Der Rest der Notiz bleibt unberührt",
          "Wahrnehmung, Entscheidung und Handlung" in neu
          and "def schritt(zustand)" in neu)
    check("Und der Codeblock bleibt heil", neu.count("```") == 2, str(neu.count("```")))

    _, _, fehler = block_ersetzen_text(NOTIZ, 999, "x")
    check("Eine falsche Blocknummer wird abgelehnt", bool(fehler), fehler)
    check("Und die Meldung nennt die vorhandenen Nummern",
          "Vorhanden sind" in fehler and "bloecke_anzeigen" in fehler, fehler[:120])
    check("'[3]' und '3.' werden auch verstanden",
          block_ersetzen_text(NOTIZ, "[3]", "Neu")[1] is not None
          and block_ersetzen_text(NOTIZ, "3.", "Neu")[1] is not None)

    # ================================================= 4. Diff
    diff = unterschied(NOTIZ, neu, "Agentenarchitektur")
    check("Der Diff nennt die entfernte Zeile",
          "-- notiz_erstellen" in diff or "-- notiz_erstellen" in diff.replace(" ", ""),
          [z for z in diff.splitlines() if z.startswith("-")][:3])
    check("Und die hinzugefügte", any(z.startswith("+- block_ersetzen")
                                      for z in diff.splitlines()), diff[:200])
    check("Ohne Änderung sagt der Diff das auch",
          unterschied(NOTIZ, NOTIZ) == "(keine Änderung)")
    check("Die Bilanz zählt beide Richtungen",
          unterschied_knapp(NOTIZ, neu) == "−1 Zeilen, +1 Zeilen",
          unterschied_knapp(NOTIZ, neu))

    # ================================================= 5. Am echten Werkzeug
    (VAULT / "Wissen").mkdir(parents=True, exist_ok=True)
    pfad = VAULT / "Wissen" / "Agentenarchitektur.md"
    pfad.write_text(NOTIZ, encoding="utf-8")
    index = KnowledgeIndex(cfg, embeddings=stubs.StubEmbedding())
    index.sync()
    tools = VaultTools(index, cfg)

    ergebnis = tools.bloecke_anzeigen("Agentenarchitektur")
    check("bloecke_anzeigen listet mit Nummern",
          ergebnis.ok and "1." in ergebnis.meldung, ergebnis.meldung[:80])
    check("Und liefert die Blöcke auch als Daten",
          len(ergebnis.daten.get("bloecke") or []) == len(bloecke),
          str(len(ergebnis.daten.get("bloecke") or [])))

    # -------- Vorschau schreibt NICHTS
    vorher = pfad.read_text(encoding="utf-8")
    schau = tools.block_ersetzen("Agentenarchitektur", liste.nummer,
                                 "- nur ein Eintrag", nur_vorschau=True)
    check("nur_vorschau meldet Erfolg", schau.ok, schau.meldung[:60])
    check("Und schreibt nichts", pfad.read_text(encoding="utf-8") == vorher)
    check("Der Bericht enthält den Diff", "```diff" in schau.meldung, schau.meldung[:80])

    # -------- Echtes Schreiben, mit Schnappschuss
    ergebnis = tools.block_ersetzen("Agentenarchitektur", liste.nummer,
                                    "- nur ein Eintrag")
    check("Ohne nur_vorschau wird geschrieben",
          ergebnis.ok and "nur ein Eintrag" in pfad.read_text(encoding="utf-8"),
          ergebnis.meldung[:80])
    check("Die Bilanz steht in der Meldung",
          "Zeilen" in ergebnis.meldung, ergebnis.meldung)
    sicherungen = list((cfg.vault_path / ".jarvis" / "backups").glob("*.md"))
    check("Vor der Änderung wurde eine Sicherung angelegt",
          bool(sicherungen), str(len(sicherungen)))
    check("Die Sicherung enthält den alten Stand",
          any("notiz_erstellen" in p.read_text(encoding="utf-8") for p in sicherungen))

    # -------- Rückgängig
    zurueck = tools.rueckgaengig()
    check("Die Änderung lässt sich zurücknehmen", zurueck.ok, zurueck.meldung[:80])
    check("Und der alte Stand ist wieder da",
          "notiz_erstellen" in pfad.read_text(encoding="utf-8"))

    # ================================================= 6. Rückfrage
    gefragt = []

    def ja(vorschau):
        gefragt.append(vorschau)
        return True

    def nein(vorschau):
        gefragt.append(vorschau)
        return False

    cfg.confirm_writes = False
    tools.bestaetigung = nein
    stand = pfad.read_text(encoding="utf-8")
    tools.block_ersetzen("Agentenarchitektur", liste.nummer, "- ohne Rückfrage")
    check("Ohne CONFIRM_WRITES wird nicht gefragt", not gefragt, str(len(gefragt)))
    tools.rueckgaengig()

    cfg.confirm_writes = True
    stand = pfad.read_text(encoding="utf-8")
    abgelehnt = tools.block_ersetzen("Agentenarchitektur", liste.nummer, "- abgelehnt")
    check("Mit CONFIRM_WRITES wird gefragt", len(gefragt) == 1, str(len(gefragt)))
    check("Ein Nein verhindert das Schreiben",
          not abgelehnt.ok and pfad.read_text(encoding="utf-8") == stand,
          abgelehnt.meldung[:80])
    check("Und wird als Ablehnung gemeldet, nicht als Fehler",
          abgelehnt.daten.get("abgelehnt") is True, str(abgelehnt.daten)[:80])
    check("Die Rückfrage bekommt den fertigen Diff",
          "```diff" in gefragt[0].bericht(), gefragt[0].bericht()[:60])

    tools.bestaetigung = ja
    angenommen = tools.block_ersetzen("Agentenarchitektur", liste.nummer, "- angenommen")
    check("Ein Ja lässt schreiben",
          angenommen.ok and "angenommen" in pfad.read_text(encoding="utf-8"),
          angenommen.meldung[:80])

    # ================================================= 7. Absicherungen
    cfg.confirm_writes = False
    leer = tools.block_ersetzen("Agentenarchitektur", 1, "   ")
    check("Leerer Inhalt wird abgelehnt", not leer.ok, leer.meldung[:80])
    check("Und verweist auf das richtige Werkzeug",
          "abschnitt_loeschen" in leer.meldung, leer.meldung[:120])

    fehlt = tools.block_ersetzen("Gibt es nicht", 1, "x")
    check("Eine unbekannte Notiz wird abgelehnt", not fehlt.ok, fehlt.meldung[:60])

    # Der zentrale Namensschutz muss auch für das neue Werkzeug gelten
    (VAULT / "Wissen" / "Architektur.md").write_text("# Architektur\n\nText.\n",
                                                     encoding="utf-8")
    index.sync()
    geraten = tools.ausfuehren("block_ersetzen",
                               {"name": "ARVIS Architektur Overview",
                                "block": 1, "inhalt": "x"})
    check("Ein geratener Notizname wird auch hier abgelehnt",
          not geraten.ok and "ungefähr" in geraten.meldung, geraten.meldung[:90])

    gleich = tools.block_ersetzen("Architektur", 1, "# Architektur")
    check("Eine Änderung ohne Unterschied wird als solche gemeldet",
          gleich.ok and "bereits" in gleich.meldung, gleich.meldung[:80])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
