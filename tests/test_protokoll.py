"""Tests gegen die Befunde aus Joshs echten Protokollen (20.–24.08.).

Aus `.jarvis/logs/*.jsonl` und dem Messbericht:

  * 350 Schreibvorgänge, davon 315 Überschreibungen. '01_Skript_Deep_Learning.md'
    (48 000 Zeichen) wurde FÜNFMAL komplett neu geschrieben, um jeweils rund
    50 Zeichen anzuhängen - einmal je automatisch gesetztem Verweis.
  * 142 Verknüpfungen auf ~35 Notizen. Eine beliebte Notiz sammelte neun
    Zeilen "thematisch verwandt" ein, weil 'pro_notiz' nur die AUSGEHENDEN
    Links begrenzte.
  * Der Messbericht bekam ZWEI Frontmatter-Blöcke: einen von notiz_erstellen,
    einen aus dem Bericht selbst. Der innere stand als Fließtext in der Notiz.
  * "Keine Notiz namens '*' gefunden" - das Modell versuchte einen Platzhalter.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_protokoll_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.vault import strip_frontmatter  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    # Zwölf thematisch nahe Notizen - genau die Konstellation, in der die
    # automatische Verlinkung aus dem Ruder lief.
    themen = ["Neuron", "Backprop", "Gradient", "Optimierer", "Momentum",
              "Adam", "Lernrate", "Initialisierung", "Aktivierung", "Verlust",
              "Softmax", "Regularisierung"]
    for thema in themen:
        (VAULT / "Deep Learning" / f"{thema}.md").write_text(
            f"# {thema}\n\n## Kern\nGradient, Gewichte, Verlust, Lernrate, "
            f"Optimierer und {thema} im Training neuronaler Netze.\n",
            encoding="utf-8")


def main() -> int:
    build_vault()
    agent = JarvisAgent(index=KnowledgeIndex(embeddings=stubs.StubEmbedding()))
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    agent.sync()
    tools = agent.tools

    # ------------------------------------- 1. Ein Schreibvorgang je Notiz
    schreibvorgaenge = []
    echtes_write = tools._write

    def mitzaehlen(pfad, inhalt, neue_datei=False):
        schreibvorgaenge.append(str(pfad))
        return echtes_write(pfad, inhalt, neue_datei=neue_datei)

    tools._write = mitzaehlen
    ergebnis = tools.auto_verlinken(ordner="Deep Learning", pro_notiz=3,
                                    mindestnaehe=0.0)
    tools._write = echtes_write

    check("Automatisches Verlinken läuft durch", ergebnis.ok, ergebnis.meldung[:110])
    doppelt = len(schreibvorgaenge) - len(set(schreibvorgaenge))
    check("Keine Notiz wird zweimal geschrieben", doppelt == 0,
          f"{len(schreibvorgaenge)} Schreibvorgänge, {doppelt} doppelt")
    check("Deutlich weniger Schreibvorgänge als Verknüpfungen",
          len(schreibvorgaenge) <= len(ergebnis.daten.get("verknuepfungen", [])) + 12,
          f"{len(schreibvorgaenge)} Schreibvorgänge für "
          f"{len(ergebnis.daten.get('verknuepfungen', []))} Paare")

    # ------------------------------------- 2. Obergrenze gilt in BEIDE Richtungen
    obergrenze = agent.cfg.max_links_pro_notiz
    schlimmste = 0
    for pfad in (VAULT / "Deep Learning").glob("*.md"):
        text = pfad.read_text(encoding="utf-8")
        anzahl = text.count("[[")
        schlimmste = max(schlimmste, anzahl)
    check("Keine Notiz übersteigt die Obergrenze",
          schlimmste <= obergrenze,
          f"höchstens {schlimmste} Verweise, erlaubt {obergrenze}")

    # Wiederholte Läufe müssen KONVERGIEREN. Bis zur Obergrenze darf ein
    # zweiter Lauf noch ergänzen - danach nicht mehr. Vorher wuchsen die
    # Notizen bei jedem Lauf weiter, weil nichts die Summe begrenzte.
    for runde in range(2, 5):
        agent.sync()
        tools.auto_verlinken(ordner="Deep Learning", pro_notiz=3, mindestnaehe=0.0)
        hoechste = max(p.read_text(encoding="utf-8").count("[[")
                       for p in (VAULT / "Deep Learning").glob("*.md"))
        assert hoechste <= obergrenze, f"Runde {runde}: {hoechste} Verweise"

    vorher = {p.name: p.read_text(encoding="utf-8") for p
              in (VAULT / "Deep Learning").glob("*.md")}
    agent.sync()
    tools.auto_verlinken(ordner="Deep Learning", pro_notiz=3, mindestnaehe=0.0)
    nachher = {p.name: p.read_text(encoding="utf-8") for p
               in (VAULT / "Deep Learning").glob("*.md")}
    check("Wiederholte Läufe konvergieren – irgendwann wächst nichts mehr",
          all(nachher[n] == vorher[n] for n in vorher),
          ", ".join(f"{n}:{len(vorher[n])}→{len(nachher[n])}"
                    for n in vorher if nachher[n] != vorher[n])[:120])
    check("Auch nach vier Läufen hält die Obergrenze",
          max(t.count("[[") for t in nachher.values()) <= obergrenze,
          str(max(t.count("[[") for t in nachher.values())))

    # --------------------------------------------- 3. Nur EIN Frontmatter-Block
    bericht = ('---\ntitel: "Messung 2026-08-24"\ntags:\n  - system\n'
               '  - evaluation\n---\n\n# Messung 2026-08-24\n\nInhalt des Berichts.\n')
    ergebnis = tools.notiz_erstellen(titel="Messung 2026-08-24", inhalt=bericht,
                                     ordner="_System/Messungen")
    check("Bericht mit eigenem Kopf wird angelegt", ergebnis.ok, ergebnis.meldung)
    pfad = VAULT / str(ergebnis.daten.get("pfad", ""))
    text = pfad.read_text(encoding="utf-8")
    check("Genau ein Frontmatter-Block", text.count("\n---") == 1,
          f"{text.count(chr(10) + '---')} Blöcke")
    kopf, rumpf = strip_frontmatter(text)
    check("Der innere Kopf steht nicht im Fließtext",
          "titel:" not in rumpf and "tags:" not in rumpf, rumpf[:80].replace("\n", " "))
    check("Die Tags aus dem Bericht sind übernommen",
          "evaluation" in str(kopf.get("tags", "")), str(kopf.get("tags")))
    check("Die Überschrift steht nur einmal",
          rumpf.count("# Messung 2026-08-24") == 1,
          str(rumpf.count("# Messung 2026-08-24")))
    check("Der Inhalt ist vollständig da", "Inhalt des Berichts." in rumpf)

    # ---------------------------------------------- 4. Platzhalter als Notizname
    # Über ausfuehren() - das ist der Weg, den das Modell nimmt.
    ergebnis = tools.ausfuehren("notiz_ergaenzen", {"name": "*", "inhalt": "Etwas."})
    check("'*' wird nicht als Notizname behandelt",
          not ergebnis.ok, ergebnis.meldung[:90])
    check("Die Meldung erklärt, dass Platzhalter nicht gehen",
          "Platzhalter" in ergebnis.meldung, ergebnis.meldung[:90])
    check("Und nennt die Werkzeuge, die über die ganze Vault gehen",
          "vault_wartung" in ergebnis.meldung or "notizen_suchen" in ergebnis.meldung,
          ergebnis.meldung[-100:])
    check("resolve_note merkt sich den Platzhalterfall",
          tools.resolve_note("*") is None and tools.letzte_aufloesung == "platzhalter",
          tools.letzte_aufloesung)

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
