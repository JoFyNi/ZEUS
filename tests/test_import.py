"""Tests für den Import der eigenen Codebasis.

Anlass ist Joshs Protokoll vom 24.08.:

  * `codebasis_importieren` lief über eine doppelt vorhandene Codebasis und
    legte jede Datei zweimal ab.
  * Ausgerechnet `tools.py` und `agent.py` wurden mit "Zu große Datei
    (>60 000 Zeichen)" ÜBERSPRUNGEN - also genau die Dateien, um die es bei
    einer Frage nach dem eigenen Code fast immer geht. Der Index enthielt
    danach alles außer dem Wichtigen.
  * Nach 25 Notizen war das Schreibbudget erschöpft, gemeldet wurde trotzdem
    Erfolg. Ein zweiter Aufruf fing wieder bei Datei 1 an und kam nie weiter.

Alle drei sind Fälle derselben Art: Der Import meldet Erfolg, obwohl das
Ergebnis unvollständig ist. Genau das prüfen die Tests hier.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_import_"))
VAULT = TMP / "Zeus"
CODE = TMP / "code"
KOPIE = TMP / "code" / "kopie"
CODE.mkdir(parents=True)
KOPIE.mkdir(parents=True)
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(CODE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["MAX_NOTE_CHARS"] = "4000"
os.environ["MAX_FILES_PER_RUN"] = "6"

from jarvis.config import Settings  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import VaultTools  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


KLEIN = '"""Ein kleines Modul."""\n\n\ndef f(x):\n    return x + 1\n'
# Deutlich über MAX_NOTE_CHARS=4000 - vorher wäre diese Datei ausgelassen worden.
GROSS = ('"""Ein großes Modul."""\n'
         + "\n".join(f"ZEILE_{i} = 'x' * 20  # Kommentar {i}" for i in range(600))
         + "\n")


def werkzeuge(cfg: Settings) -> VaultTools:
    index = KnowledgeIndex(cfg, embeddings=stubs.StubEmbedding())
    index.sync()
    return VaultTools(index, cfg)


def notizen(cfg: Settings):
    ordner = cfg.vault_path / cfg.code_folder
    return sorted(p.name for p in ordner.rglob("*.md")) if ordner.is_dir() else []


def main() -> int:
    (CODE / "klein_a.py").write_text(KLEIN, encoding="utf-8")
    (CODE / "gross.py").write_text(GROSS, encoding="utf-8")
    # Inhaltsgleiche Kopie - dieselbe Konstellation wie bei Josh auf der Platte
    (KOPIE / "klein_a.py").write_text(KLEIN, encoding="utf-8")

    cfg = Settings()
    tools = werkzeuge(cfg)

    # ============================================= 1. Dubletten und Aufteilung
    ergebnis = tools.codebasis_importieren()
    daten = ergebnis.daten
    check("Import meldet Erfolg", ergebnis.ok, ergebnis.meldung)
    check("inhaltsgleiche Kopie wird übersprungen",
          len(daten.get("doppelte") or []) == 1, str(daten.get("doppelte")))

    alle = notizen(cfg)
    check("die große Datei wird NICHT ausgelassen",
          any(n.startswith("gross") for n in alle), str(alle))
    teile = [n for n in alle if n.startswith("gross")]
    check("die große Datei wird in nummerierte Teile zerlegt",
          len(teile) > 1 and "von" in teile[0], str(teile))

    # Kein Teil darf über der Grenze liegen - sonst hätte das Aufteilen
    # nichts gebracht und notiz_erstellen würde es doch ablehnen.
    ordner = cfg.vault_path / cfg.code_folder
    groessen = [len(p.read_text(encoding="utf-8")) for p in ordner.rglob("gross*.md")]
    check("kein Teilstück überschreitet MAX_NOTE_CHARS",
          bool(groessen) and max(groessen) <= cfg.max_note_chars,
          f"größtes {max(groessen) if groessen else 0} von {cfg.max_note_chars}")

    # Der Zuschnitt folgt Zeilen: keine abgeschnittene Zeile im Inhalt
    erster = sorted(ordner.rglob("gross*.md"))[0].read_text(encoding="utf-8")
    check("Teilstücke nennen den Zeilenbereich", "Zeilen 1" in erster, erster[:120])
    check("Teilstücke sind gültige Codeblöcke",
          erster.count("```") >= 2 or erster.count("````") >= 2)

    # ================================================== 2. Fortsetzbarkeit
    # Der eigentliche Anspruch: Wiederholtes Aufrufen muss den Import
    # FERTIGSTELLEN. Vorher fing jeder Aufruf wieder bei Datei 1 an.
    check("erster Lauf meldet offene Dateien",
          ergebnis.daten.get("fortsetzen") is True,
          str(ergebnis.daten.get("offen"))[:100])

    laeufe = 1
    letzter = ergebnis
    while letzter.daten.get("fortsetzen") and laeufe < 20:
        tools._neue_dateien = 0            # neuer Auftrag, neues Budget
        letzter = tools.codebasis_importieren()
        laeufe += 1
    check("nach mehreren Läufen ist nichts mehr offen",
          not letzter.daten.get("fortsetzen"), f"{laeufe} Läufe")

    alle = notizen(cfg)
    check("am Ende sind alle acht Teile der großen Datei da",
          len([n for n in alle if n.startswith("gross")]) == 8,
          str([n for n in alle if n.startswith("gross")]))
    check("und die kleine Datei ebenfalls",
          any(n.startswith("klein_a") for n in alle), str(alle))

    vorher = len(notizen(cfg))
    tools._neue_dateien = 0
    nochmal = tools.codebasis_importieren()
    check("ein Lauf auf fertigem Bestand legt nichts an",
          len(notizen(cfg)) == vorher, f"{vorher} → {len(notizen(cfg))}")
    check("und meldet, dass alles schon da war",
          (nochmal.daten.get("schon_vorhanden") or 0) >= 2, nochmal.meldung)
    check("er meldet trotzdem Erfolg, nicht Fehlschlag", nochmal.ok, nochmal.meldung)

    # Ein abgebrochener Teil wird beim nächsten Lauf nachgeholt, ohne dass
    # die anderen sieben neu geschrieben werden.
    ordner = cfg.vault_path / cfg.code_folder
    vierter = next(p for p in ordner.rglob("gross*4_von_8*.md"))
    vierter.unlink()
    tools.index.sync()
    tools._neue_dateien = 0
    nachgeholt = tools.codebasis_importieren()
    check("ein fehlendes Teilstück wird gezielt nachgeholt",
          len(nachgeholt.daten.get("importiert") or []) == 1,
          str(nachgeholt.daten.get("importiert")))
    check("die übrigen Teile bleiben unberührt",
          len([n for n in notizen(cfg) if n.startswith("gross")]) == 8)

    # ============================================ 3. Budget ehrlich melden
    zweite_vault = TMP / "Zeus2"
    zweite_vault.mkdir()
    for i in range(12):
        (CODE / f"modul_{i:02}.py").write_text(
            f'"""Modul {i}."""\n\n\ndef f{i}(x):\n    return x + {i}\n', encoding="utf-8")

    os.environ["OBSIDIAN_VAULT"] = str(zweite_vault)
    knapp = Settings()
    knappe_tools = werkzeuge(knapp)
    eng = knappe_tools.codebasis_importieren()
    check("bei erschöpftem Budget bleibt der Rest namentlich offen",
          bool(eng.daten.get("offen")), str(eng.daten.get("offen"))[:120])
    check("jede offene Datei steht nur einmal in der Liste",
          len(eng.daten["offen"]) == len(set(eng.daten["offen"])),
          str(eng.daten["offen"]))
    check("und die Meldung sagt, wie es weitergeht",
          "wiederholen" in eng.meldung and "MAX_FILES_PER_RUN" in eng.meldung,
          eng.meldung)
    check("nicht mehr als das Budget wurde geschrieben",
          len(notizen(knapp)) <= knapp.max_files_per_run,
          f"{len(notizen(knapp))} bei Budget {knapp.max_files_per_run}")

    vorher = len(notizen(knapp))
    knappe_tools._neue_dateien = 0
    knappe_tools.codebasis_importieren()
    check("der Folgeaufruf legt neue Notizen an, statt am Anfang zu verharren",
          len(notizen(knapp)) > vorher, f"{vorher} → {len(notizen(knapp))}")

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
