"""End-to-End-Test ohne Ollama: Struktur, Index, Suche, Ordner-Zuordnung."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
VAULT = TMP / "ObsidianVault"
STORE = TMP / "vector_store"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(STORE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.llm import OllamaClient  # noqa: E402
from jarvis.vault import scan_vault, similarity  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Deep Learning" / "Übungen").mkdir()
    (VAULT / "Regelungstechnik").mkdir()
    (VAULT / ".obsidian").mkdir()
    (VAULT / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")

    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        """---
tags: [dl, mathe]
aliases: ["DL-Skript"]
---

# Skript Deep Learning

## Grundlagen
Ein neuronales Netz ist ein Signalflussgraph mit einstellbaren Verstärkungen.

## Backpropagation
Das Training erfolgt durch Backpropagation, also die Kettenregel entlang des Graphen.
Der Gradient wird schichtweise zurückgerechnet.

```python
# wird beim Chunking als Code behandelt
w -= lr * grad
```
""",
        encoding="utf-8",
    )
    (VAULT / "Deep Learning" / "02_Agent_Architektur.md").write_text(
        """# Agent Architektur

Der Agent nutzt RAG mit FAISS und einem lokalen LLM über Ollama.
Siehe auch [[01_Skript_Deep_Learning]].

#agent #rag
""",
        encoding="utf-8",
    )
    (VAULT / "Deep Learning" / "Übungen" / "Uebung_01.md").write_text(
        "# Übung 1\n\nBerechne den Gradienten für ein zweischichtiges Netz.\n",
        encoding="utf-8",
    )
    (VAULT / "Regelungstechnik" / "PID_Regler.md").write_text(
        "---\ntags:\n  - regelung\n---\n\n# PID Regler\n\nDer PID-Regler besteht aus P-, I- und D-Anteil.\n",
        encoding="utf-8",
    )
    (VAULT / "Willkommen.md").write_text("# Willkommen\n\nStartseite der Vault.\n", encoding="utf-8")


ANTWORT = "Antwort aus den Notizen: Backpropagation nutzt die Kettenregel. [[01_Skript_Deep_Learning]]"


def fake_chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
    if format_json:
        return '{"ordner": "Deep Learning", "neu": false}'
    if stream_cb:
        for token in ANTWORT.split(" "):
            stream_cb(token + " ")
    return ANTWORT


def fake_chat_message(self, messages, tools=None, temperature=None, format_json=False, **kwargs):
    """Modell, das keine Werkzeuge braucht - antwortet direkt."""
    if format_json:
        return {"role": "assistant", "content": '{"ordner": "Deep Learning", "neu": false}'}
    return {"role": "assistant", "content": ANTWORT}


def main() -> int:
    build_vault()
    OllamaClient.chat = fake_chat  # type: ignore[assignment]
    OllamaClient.chat_message = fake_chat_message  # type: ignore[assignment]

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)

    # ---------------------------------------------------------------- Scannen
    notes = scan_vault(VAULT)
    check("Alle Notizen gefunden, .obsidian ignoriert", len(notes) == 5, f"{len(notes)} Notizen")

    skript = next(n for n in notes if n.stem == "01_Skript_Deep_Learning")
    check("Ordner erkannt", skript.folder == "Deep Learning", skript.folder)
    check("Frontmatter-Tags gelesen", set(skript.tags) >= {"dl", "mathe"}, str(skript.tags))
    check("Alias gelesen", skript.aliases == ["DL-Skript"], str(skript.aliases))
    check("Überschriften erfasst", "Backpropagation" in skript.headings, str(skript.headings))

    arch = next(n for n in notes if n.stem == "02_Agent_Architektur")
    check("Inline-Tags gelesen", set(arch.tags) >= {"agent", "rag"}, str(arch.tags))
    check("Wikilink erkannt", "01_Skript_Deep_Learning" in arch.links, str(arch.links))

    # -------------------------------------------------------------- Struktur
    report = agent.sync()
    print("   ", report.summary())
    structure = agent.structure
    check(
        "Ordnerbaum inkl. Unterordner",
        set(structure.folder_paths) == {"Deep Learning", "Deep Learning/Übungen", "Regelungstechnik"},
        str(structure.folder_paths),
    )
    check("Notizzähler pro Ordner", structure.folders["Deep Learning"].note_count == 2)
    check("Tag-Index gefüllt", "rag" in structure.tag_index)

    # ------------------------------------------------- Tippfehler-Toleranz
    check("'Depp Learning' ähnelt 'Deep Learning'", similarity("Depp Learning", "Deep Learning") > 0.85,
          f"{similarity('Depp Learning', 'Deep Learning'):.2f}")
    check("Kanonischer Name korrigiert Tippfehler",
          structure.canonical_name("Depp Learning") == "Deep Learning",
          structure.canonical_name("Depp Learning"))
    match = structure.match_folder("Depp Learning")
    check("Ordner-Match trotz Tippfehler", bool(match) and match[0][0] == "Deep Learning", str(match[:2]))

    # ------------------------------------------------------------- Retrieval
    hits = index.search("Wie funktioniert Backpropagation?")
    check("Treffer gefunden", bool(hits), f"{len(hits)} Treffer")
    check(
        "Richtige Quellnotiz ganz oben",
        any(h.metadata.get("stem") == "01_Skript_Deep_Learning" for h in hits[:3]),
        ", ".join(h.label for h in hits[:3]),
    )
    check(
        "Chunk kennt seinen Ordner",
        all(h.metadata.get("folder") is not None for h in hits),
    )
    struktur_hits = index.search("Welche Ordner gibt es in meiner Vault?")
    check(
        "Struktur-Chunks durchsuchbar",
        any(h.metadata.get("rel_path", "").startswith("__struktur__") for h in struktur_hits),
        ", ".join(h.label for h in struktur_hits[:3]),
    )
    gefiltert = index.search("Gradient", folder="Deep Learning")
    check(
        "Ordnerfilter greift",
        all(h.metadata.get("folder", "").startswith("Deep Learning") for h in gefiltert),
        ", ".join(h.label for h in gefiltert),
    )

    # -------------------------------------------------- Ordner-Zuordnung
    decision = agent.resolve_target_folder("Depp Learning", hits)
    check("Neue Notiz landet im Deep-Learning-Ordner", decision.folder == "Deep Learning",
          f"{decision.folder} ({decision.reason})")

    decision2 = agent.resolve_target_folder("PID Regler Tuning", index.search("PID Regler"))
    check("Themenwechsel landet in Regelungstechnik", decision2.folder == "Regelungstechnik",
          f"{decision2.folder} ({decision2.reason})")

    # ------------------------------------------------------ Notiz schreiben
    path, decision3 = agent.write_note(
        "Depp Learning", "Deep Learning ist ein Signalflussgraph …", hits
    )
    check("Notiz im richtigen Ordner abgelegt", path.parent.name == "Deep Learning", str(path))
    check("Dateiname mit korrigierter Schreibweise", path.name == "Deep_Learning.md", path.name)
    content = path.read_text(encoding="utf-8")
    check("Quellen als Wikilinks verlinkt", "[[01_Skript_Deep_Learning]]" in content)
    check("Begründung dokumentiert", "ordner_begruendung" in content)

    # --------------------------------------------------- Inkrementeller Sync
    before = sum(len(v["ids"]) for v in index.manifest["files"].values())
    report2 = index.sync()
    check("Neue Notiz ist erfasst", report2.total_notes == 6, str(report2.total_notes))
    check("Kein Vollaufbau nötig", not report2.rebuilt, report2.summary())

    (VAULT / "Regelungstechnik" / "PID_Regler.md").write_text(
        "# PID Regler\n\nErgänzung: Anti-Windup ist wichtig.\n", encoding="utf-8"
    )
    (VAULT / "Willkommen.md").unlink()
    report3 = index.sync()
    check("Änderung erkannt", report3.updated == 1, f"updated={report3.updated}")
    check("Löschung erkannt", report3.removed == 1, f"removed={report3.removed}")
    check("Gelöschte Notiz nicht mehr auffindbar",
          all("Willkommen" != h.metadata.get("stem") for h in index.search("Willkommen Startseite")),
          "")
    after = sum(len(v["ids"]) for v in index.manifest["files"].values())
    check("Manifest bleibt konsistent", after > 0 and before > 0)

    # ------------------------------------------------------------- Antworten
    answer = agent.ask("@Deep Learning Wie funktioniert Backpropagation?")
    check("Antwort mit Ordnerfilter erzeugt", bool(answer.text) and answer.folder_filter == "Deep Learning",
          str(answer.folder_filter))
    check("Quellenliste ohne Struktur-Pseudodateien",
          all(not s.startswith("__struktur__") for s in answer.sources), str(answer.sources))

    # --------------------------------------------------- Persistenz/Neuladen
    index2 = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    report4 = index2.sync()
    check("Index wird von Platte geladen, nicht neu gebaut", not report4.rebuilt, report4.summary())

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
