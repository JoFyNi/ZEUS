"""Tests für die erweiterte Bearbeitung: Abschnitte, Zusammenführen, Aufteilen,
Massenverlinkung, Übersichten – und für den Planer, der jetzt ALLE Operationsarten
beherrscht (bis v5 wurde jede Absicht zwangsläufig zu 'neue Notiz')."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_edit_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import PLANBARE_AKTIONEN  # noqa: E402
from jarvis.tools import TOOL_NAMES  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def dateien() -> list:
    return sorted(p.relative_to(VAULT).as_posix() for p in VAULT.rglob("*.md")
                  if ".jarvis" not in p.parts)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Alt").mkdir()
    (VAULT / "Deep Learning" / "Skript.md").write_text(
        "---\ntags: [dl]\n---\n\n# Skript\n\n"
        "## Neuron\nGewichtete Summe und Aktivierung.\n\n"
        "## Backpropagation\nKettenregel rückwärts.\nAlter Stand, muss ersetzt werden.\n\n"
        "### Details\nUnterabschnitt gehört dazu.\n\n"
        "## Ausblick\nWas noch kommt.\n",
        encoding="utf-8",
    )
    # Zwei Dubletten, wie sie bei dir entstanden sind
    for i in (4, 5):
        (VAULT / "Alt" / f"Agenten_{i}.md").write_text(
            f"---\ntags: [agent]\n---\n\n# Agenten {i}\n\nTeil {i} über lernende Agenten.\n"
            f"Beobachtung und Rückmeldung sind zentral.\n",
            encoding="utf-8",
        )
    (VAULT / "Alt" / "Verweis.md").write_text(
        "# Verweis\n\nSiehe [[Agenten_4]] und [[Agenten_5]].\n", encoding="utf-8")


def fake_chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
    if format_json:
        return '{"ordner": "Deep Learning", "neu": false}'
    return ("## Kurzfassung\nNeuer Text. " + "Inhalt. " * 60 +
            "\n\n## Funktionsweise / Vorgehen\nSo geht es. " + "Details. " * 60)


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = fake_chat.__get__(agent.llm)
    agent.sync()
    tools = agent.tools

    # ------------------------------------------ 1. Nichts ist verlorengegangen
    alt_bestand = {
        "notizen_suchen", "notiz_lesen", "struktur_anzeigen", "ordner_anlegen",
        "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten", "notiz_ueberschreiben",
        "notiz_verschieben", "notiz_loeschen", "notizen_verknuepfen", "verwandte_finden",
        "links_pruefen", "tags_setzen", "vault_wartung",
    }
    check("Alle bisherigen Werkzeuge sind erhalten", alt_bestand <= TOOL_NAMES,
          str(sorted(alt_bestand - TOOL_NAMES)))
    neu = {"abschnitt_ersetzen", "abschnitt_einfuegen", "abschnitt_loeschen",
           "notizen_zusammenfuehren", "notiz_aufteilen", "auto_verlinken",
           "uebersichtsnotiz", "frontmatter_setzen", "notiz_wiederherstellen",
           "ordner_umbenennen"}
    check("Zehn neue Werkzeuge sind dazugekommen", neu <= TOOL_NAMES,
          f"{len(TOOL_NAMES)} Werkzeuge gesamt")

    # ------------------------------------------------- 2. Abschnitt ersetzen
    ergebnis = tools.abschnitt_ersetzen("Skript", "Backpropagation",
                                        "Neuer Stand: Gradient per Kettenregel.")
    inhalt = (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8")
    check("Abschnitt ersetzt", ergebnis.ok and "Neuer Stand" in inhalt, ergebnis.meldung)
    check("Alter Text ist weg", "Alter Stand, muss ersetzt werden" not in inhalt)
    check("Nachbarabschnitte unberührt", "## Neuron" in inhalt and "## Ausblick" in inhalt)
    check("Unterabschnitt wurde mit ersetzt", "Unterabschnitt gehört dazu" not in inhalt)

    fehl = tools.abschnitt_ersetzen("Skript", "Gibt es nicht", "x")
    check("Unbekannter Abschnitt wird abgelehnt", not fehl.ok, fehl.meldung)

    # ------------------------------------------------- 3. Abschnitt einfügen
    tools.abschnitt_einfuegen("Skript", "Aktivierungen", "ReLU und Tanh.",
                              nach_abschnitt="Neuron")
    inhalt = (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8")
    check("Abschnitt eingefügt", "## Aktivierungen" in inhalt)
    check("An der richtigen Stelle",
          inhalt.index("## Neuron") < inhalt.index("## Aktivierungen") < inhalt.index("## Backpropagation"))

    # ------------------------------------------------- 4. Abschnitt löschen
    tools.abschnitt_loeschen("Skript", "Ausblick")
    inhalt = (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8")
    check("Abschnitt gelöscht", "## Ausblick" not in inhalt and "## Neuron" in inhalt)
    tools.rueckgaengig()
    check("Löschen ist rückgängig zu machen",
          "## Ausblick" in (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8"))

    # ---------------------------------------------- 5. Dubletten zusammenführen
    vorher = len(dateien())
    zusammen = tools.notizen_zusammenfuehren(
        quellen=["Agenten_4", "Agenten_5"], ziel_titel="Lernende Agenten", ordner="Alt")
    check("Zusammenführen gelingt", zusammen.ok, zusammen.meldung)
    ziel = VAULT / zusammen.daten["pfad"]
    text = ziel.read_text(encoding="utf-8")
    check("Beide Inhalte enthalten", "Teil 4" in text and "Teil 5" in text)
    check("Quellen im Papierkorb", not (VAULT / "Alt" / "Agenten_4.md").exists()
          and any((VAULT / "_Papierkorb").glob("Agenten_4_*.md")))
    verweis = (VAULT / "Alt" / "Verweis.md").read_text(encoding="utf-8")
    check("Verweise wurden umgebogen",
          "[[Lernende_Agenten]]" in verweis and "[[Agenten_4]]" not in verweis, verweis.strip())

    # ------------------------------------------------------ 6. Notiz aufteilen
    (VAULT / "Deep Learning" / "Gross.md").write_text(
        "# Gross\n\n## Teil A\n" + "Inhalt A. " * 30 +
        "\n\n## Teil B\n" + "Inhalt B. " * 30 +
        "\n\n## Teil C\n" + "Inhalt C. " * 30, encoding="utf-8")
    index.sync()
    geteilt = tools.notiz_aufteilen("Gross")
    check("Aufteilen erzeugt Kapitelnotizen", geteilt.ok and len(geteilt.daten["kapitel"]) == 3,
          str(geteilt.daten.get("kapitel")))
    check("Kapitel behalten ihre eigenen Namen",
          set(geteilt.daten.get("kapitel", [])) == {"Teil_A", "Teil_B", "Teil_C"},
          str(geteilt.daten.get("kapitel")))
    uebrig = (VAULT / "Deep Learning" / "Gross.md").read_text(encoding="utf-8")
    check("Original wird zur Übersicht mit Links",
          "## Kapitel" in uebrig and uebrig.count("[[") >= 3, uebrig[:80])

    # ------------------------------------------------- 7. Massenverlinkung
    vorher_links = sum(len(n.links) for n in index.notes)
    verlinkt = tools.auto_verlinken(ordner="Deep Learning", pro_notiz=2, mindestnaehe=0.0)
    index.sync()
    nachher_links = sum(len(n.links) for n in index.notes)
    check("Automatische Verlinkung setzt neue Links",
          verlinkt.ok and nachher_links > vorher_links,
          f"{vorher_links} → {nachher_links}, {len(verlinkt.daten['verknuepfungen'])} gesetzt")

    # -------------------------------------------------- 8. Übersichtsnotiz
    uebersicht = tools.uebersichtsnotiz("Deep Learning")
    check("Übersichtsnotiz entsteht", uebersicht.ok, uebersicht.meldung)
    if uebersicht.ok:
        moc = (VAULT / uebersicht.daten["pfad"]).read_text(encoding="utf-8")
        check("Übersicht verlinkt die Notizen des Ordners", moc.count("[[") >= 2)
        nochmal = tools.uebersichtsnotiz("Deep Learning")
        check("Erneuter Aufruf aktualisiert statt zu duplizieren", nochmal.ok and
              len([d for d in dateien() if "bersicht" in d]) == 1,
              str([d for d in dateien() if "bersicht" in d]))

    # ------------------------------------------------- 9. Frontmatter setzen
    fm = tools.frontmatter_setzen("Skript", "status", "geprüft")
    inhalt = (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8")
    check("Frontmatter-Feld gesetzt", fm.ok and "status: geprüft" in inhalt, fm.meldung)
    tools.frontmatter_setzen("Skript", "status", "überarbeitet")
    inhalt = (VAULT / "Deep Learning" / "Skript.md").read_text(encoding="utf-8")
    check("Feld wird überschrieben, nicht verdoppelt",
          inhalt.count("status:") == 1 and "überarbeitet" in inhalt)
    check("Bestehende Tags bleiben erhalten", "dl" in inhalt)

    # ----------------------------------------------- 10. Wiederherstellen
    tools.notiz_loeschen("Verweis", grund="Test")
    zurueck = tools.notiz_wiederherstellen("Verweis", ordner="Alt")
    check("Notiz aus Papierkorb zurückgeholt",
          zurueck.ok and (VAULT / "Alt" / "Verweis.md").exists(), zurueck.meldung)

    # ------------------------------------------------ 11. Ordner umbenennen
    umbenannt = tools.ordner_umbenennen("Alt", "Agenten")
    check("Ordner umbenannt", umbenannt.ok and (VAULT / "Agenten").exists()
          and not (VAULT / "Alt").exists(), umbenannt.meldung)
    check("Notizen sind mitgewandert", any(d.startswith("Agenten/") for d in dateien()),
          str([d for d in dateien() if d.startswith("Agenten/")]))

    # ============================================ 12. Planer: alle Aktionsarten
    check("Planer kennt alle geplanten Aktionen", set(PLANBARE_AKTIONEN) <= TOOL_NAMES,
          str(sorted(set(PLANBARE_AKTIONEN) - TOOL_NAMES)))

    def plan_llm(plan_json):
        def chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
            if format_json:
                return json.dumps(plan_json, ensure_ascii=False)
            return ("## Kurzfassung\nText. " + "Inhalt. " * 60)
        return chat

    # -- Verschieben statt Anlegen (genau der Fehler bis v5) --
    agent.llm.chat = plan_llm({
        "absicht": "Notiz verschieben",
        "operationen": [{"aktion": "notiz_verschieben",
                         "parameter": {"name": "Skript", "ziel_ordner": "Agenten"}}],
    }).__get__(agent.llm)
    vorher = len(dateien())
    antwort = agent.ask("verschiebe das Skript nach Agenten")
    check("Verschiebe-Auftrag erzeugt KEINE neue Notiz", len(dateien()) == vorher,
          f"{vorher} → {len(dateien())}")
    check("Notiz liegt jetzt im Zielordner", (VAULT / "Agenten" / "Skript.md").exists(),
          antwort.text[:80])

    # -- Ergänzen statt Anlegen --
    agent.llm.chat = plan_llm({
        "absicht": "Abschnitt ergänzen",
        "operationen": [{"aktion": "notiz_ergaenzen",
                         "parameter": {"name": "Skript", "abschnitt": "Ausblick"},
                         "auftrag": "Was als Nächstes kommt"}],
    }).__get__(agent.llm)
    vorher = len(dateien())
    agent.ask("ergänze im Skript den Ausblick")
    check("Ergänzen erzeugt keine neue Datei", len(dateien()) == vorher)
    check("Ergänzung steht in der Notiz",
          "Kurzfassung" in (VAULT / "Agenten" / "Skript.md").read_text(encoding="utf-8"))

    # -- Verknüpfen --
    agent.llm.chat = plan_llm({
        "absicht": "Verknüpfen",
        "operationen": [{"aktion": "notizen_verknuepfen",
                         "parameter": {"von": "Skript", "nach": "Lernende Agenten"}}],
    }).__get__(agent.llm)
    agent.ask("verknüpfe das Skript mit den lernenden Agenten")
    check("Verknüpfungs-Auftrag setzt echte Links",
          "[[Lernende_Agenten]]" in (VAULT / "Agenten" / "Skript.md").read_text(encoding="utf-8"))

    # -- Erfundene Aktion wird verworfen --
    verworfen = agent.planner._plan_parsen(json.dumps({
        "absicht": "Unsinn",
        "operationen": [
            {"aktion": "alles_loeschen", "parameter": {}},
            {"aktion": "notiz_erstellen", "parameter": {"titel": "Gültig"}},
        ]}))
    check("Erfundene Aktion wird verworfen, gültige bleibt",
          verworfen is not None and len(verworfen.operationen) == 1
          and verworfen.operationen[0].aktion == "notiz_erstellen",
          str([o.aktion for o in (verworfen.operationen if verworfen else [])]))

    unvollstaendig = agent.planner._plan_parsen(json.dumps({
        "operationen": [{"aktion": "notiz_verschieben", "parameter": {}}]}))
    check("Operation ohne Pflichtparameter wird verworfen", unvollstaendig is None)

    flach = agent.planner._plan_parsen(json.dumps({
        "operationen": [{"aktion": "tags_setzen", "name": "Skript", "tags": ["neu"]}]}))
    check("Flach notierte Parameter werden erkannt",
          flach is not None and flach.operationen[0].parameter.get("name") == "Skript",
          str(flach.operationen[0].parameter if flach else None))

    # ------------------------------------------------ 13. Alte Planform
    alt = agent.planner._plan_parsen(json.dumps({
        "absicht": "Alt", "ordner": "Agenten",
        "notizen": [{"titel": "Thema A", "auftrag": "Erkläre A"}]}))
    check("Ältere Planform bleibt lesbar",
          alt is not None and alt.operationen[0].aktion == "notiz_erstellen")

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
