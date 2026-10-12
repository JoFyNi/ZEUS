"""Tests zu den Fehlern aus dem Chatverlauf vom 2026-08-30.

Vier Beschwerden, vier Ursachen:

1. Eine FRAGE ("ist es richtig, dass du die Fertigkeit im Code Ordner hast?")
   galt als Schreibauftrag. Ursache: das Muster "ordne\\w*" fing das
   SUBSTANTIV "Ordner" mit. Vier Operationen liefen los, eine davon hat eine
   fremde Notiz überschrieben.

2. uebersichtsnotiz() suchte den eigenen Titel mit resolve_note() - also
   großzügig. "Fertigkeiten Baum" landete auf "Fertigkeiten_Definitionen",
   und die Notiz wurde komplett überschrieben.

3. "Erstelle eine Fertigkeit …" endete als angezeigter Quelltext statt als
   Aufruf. Ursache: _aufruf_aus_text() verwarf jede Antwort mit einem
   Zeilenumbruch oder über 400 Zeichen - und kannte den Namen
   'fertigkeit_definieren' gar nicht, weil er erst zur Laufzeit entsteht.

4. Auf drei Fragen kam dreimal "…". Ursache: leerer content, während das
   Modell die Token beim Nachdenken verbraucht hatte. Der Grund stand
   nirgends.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_verlauf_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import (  # noqa: E402
    _argumente_lesen,
    _aufruf_aus_text,
    JarvisAgent,
)
from jarvis.eingabe import Echosperre, Eingabeleser  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import looks_like_write_request  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Wissen").mkdir(parents=True)
    (VAULT / "Wissen" / "Fertigkeiten_Definitionen.md").write_text(
        "---\ntags: [fertigkeit]\n---\n\n# Fertigkeiten Definitionen\n\n"
        "Diese Notiz beschreibt, was eine Fertigkeit ist. Sie ist WERTVOLL "
        "und darf unter keinen Umständen von einer Übersichtsnotiz "
        "überschrieben werden.\n",
        encoding="utf-8")
    (VAULT / "Wissen" / "Neuronale_Netze.md").write_text(
        "# Neuronale Netze\n\nGewichte und Schichten.\n", encoding="utf-8")


# ============================================== 1. "Ordner" ist kein Verb
def test_ordner_frage() -> None:
    faelle = [
        ("ist es richtig das du die Fertigkeit im Code Ordner hast? "
         "macht es nicht sinn, dass du einen eigenen FertigkeitenBaum besitzt?",
         False, "Frage über einen Ordner"),
        ("Welche Ordner gibt es in meinem Vault?", False, "Frage nach Ordnern"),
        ("Wo liegt der Ordner mit den Fertigkeiten?", False, "Ortsfrage"),
        ("Ordne die Notizen nach Thema", True, "echter Auftrag (Verb)"),
        ("Ordnest du die Notizen bitte nach Datum?", True, "Verb in Frageform"),
        ("Lege einen Ordner Wissen an", True, "anlegen ist der Auslöser"),
    ]
    for text, erwartet, was in faelle:
        ist = looks_like_write_request(text)
        check(f"Schreibauftrag={erwartet}: {was}", ist == erwartet,
              f"ergab {ist}")


# ================================= 2. Übersichtsnotiz überschreibt nichts Fremdes
def test_uebersichtsnotiz(tools) -> None:
    vorher = (VAULT / "Wissen" / "Fertigkeiten_Definitionen.md").read_text(encoding="utf-8")

    ergebnis = tools.uebersichtsnotiz(ordner="Wissen", titel="Fertigkeiten Baum")
    check("Übersichtsnotiz wird angelegt", ergebnis.ok, ergebnis.meldung)

    nachher = (VAULT / "Wissen" / "Fertigkeiten_Definitionen.md").read_text(encoding="utf-8")
    check("Fremde Notiz bleibt unangetastet", vorher == nachher,
          "Fertigkeiten_Definitionen.md wurde überschrieben!" if vorher != nachher else "")
    check("WERTVOLL steht noch drin", "WERTVOLL" in nachher)

    neue = list((VAULT / "Wissen").glob("Fertigkeiten?Baum.md")) + \
        list((VAULT / "Wissen").glob("Fertigkeiten_Baum.md"))
    check("Eine eigene Datei ist entstanden", bool(neue),
          f"gefunden: {[p.name for p in (VAULT / 'Wissen').iterdir()]}")

    # Zweiter Lauf trifft den EIGENEN Titel exakt und darf überschreiben.
    zweiter = tools.uebersichtsnotiz(ordner="Wissen", titel="Fertigkeiten Baum")
    check("Zweiter Lauf aktualisiert die eigene Übersicht", zweiter.ok, zweiter.meldung)
    dateien = [p.name for p in (VAULT / "Wissen").glob("Fertigkeiten*Baum*.md")]
    check("Keine Zweitschrift angelegt", len(dateien) == 1, f"{dateien}")


# ================================ 3. Prosa-Aufruf über mehrere Zeilen
def test_prosa_aufruf() -> None:
    Z = {"fertigkeit_definieren"}

    einzeilig = _aufruf_aus_text(
        'notiz_lesen(name="_System/Code/Pruefungen/Code-Prüfung 2026-08-25")')
    check("Einzeiliger Aufruf wird weiterhin erkannt",
          einzeilig is not None and einzeilig[0] == "notiz_lesen")

    mehrzeilig = _aufruf_aus_text(
        'notiz_erstellen(\n'
        '    titel="Übersicht SBIOS",\n'
        '    inhalt="Zeile eins, mit Komma\\nZeile zwei (mit Klammer)",\n'
        '    ordner="Wissen"\n'
        ')')
    check("Mehrzeiliger Aufruf wird erkannt", mehrzeilig is not None)
    if mehrzeilig:
        args = mehrzeilig[1]
        check("Komma im Wert bricht das Zerlegen nicht ab",
              args.get("ordner") == "Wissen" and "Komma" in args.get("inhalt", ""),
              str(args))

    lang = 'fertigkeit_definieren(\n  name="x",\n  code="""\n' + \
        "\n".join(f"    zeile_{i} = {i}, {i + 1}" for i in range(200)) + '\n"""\n)'
    fertig = _aufruf_aus_text(lang, Z)
    check("Langer Aufruf (über 400 Zeichen) wird nicht mehr verworfen",
          fertig is not None and len(lang) > 400, f"{len(lang)} Zeichen")
    if fertig:
        check("Dreifach-Anführungszeichen liefern den ganzen Quelltext",
              "zeile_199" in fertig[1].get("code", ""))

    check("Ohne Laufzeitnamen bleibt fertigkeit_definieren unbekannt",
          _aufruf_aus_text('fertigkeit_definieren(name="x", code="1")') is None)
    check("Mit Laufzeitnamen wird er erkannt",
          _aufruf_aus_text('fertigkeit_definieren(name="x", code="1")', Z) is not None)

    check("Aufruf im Fließtext wird NICHT ausgeführt",
          _aufruf_aus_text('Dafür nimmst du notiz_loeschen(name="X"), wenn du magst.') is None)
    check("Codeblock nach einer Ankündigung wird ausgeführt",
          _aufruf_aus_text('Ich lege sie an:\n\n```python\n'
                           'fertigkeit_definieren(name="x", code="1")\n```', Z) is not None)
    check("Codeblock als Beispiel wird NICHT ausgeführt",
          _aufruf_aus_text('Die Syntax lautet zum Beispiel:\n\n```python\n'
                           'notiz_loeschen(name="X")\n```') is None)
    check("Unbekanntes Werkzeug bleibt Text",
          _aufruf_aus_text('kaputt_werkzeug(name="x")') is None)

    listen = _argumente_lesen('name="a", parameter=["thema"], '
                              'schritte=["notizen_suchen(frage=\\"{thema}\\")"], anzahl=8')
    check("Listenargumente werden zu echten Listen",
          isinstance(listen.get("parameter"), list)
          and isinstance(listen.get("schritte"), list)
          and listen.get("anzahl") == 8,
          str(listen))


# ================================= 4. Leere Antwort nennt ihren Grund
def test_leere_antwort(agent) -> None:
    agent.llm.last_gedanken = ""
    agent.llm.last_warning = ""
    agent.llm.chat = (lambda self, m, **k: "").__get__(agent.llm)
    agent.llm.chat_message = (lambda self, m, tools=None, **k: {
        "role": "assistant", "content": ""}).__get__(agent.llm)

    antwort = agent.ask("Was steht in Neuronale Netze?")
    check("Leere Antwort ist nicht leer", bool(antwort.text.strip()))
    check("Leere Antwort erwähnt keine drei Punkte", antwort.text.strip() != "…")
    check("Leere Antwort verweist auf /server", "/server" in antwort.text,
          antwort.text[:120])

    agent.llm.last_gedanken = "Ich überlege, welche Notiz gemeint sein könnte. " * 20
    antwort2 = agent.ask("Und jetzt?")
    check("Denkfall nennt NUM_PREDICT", "NUM_PREDICT" in antwort2.text, antwort2.text[:160])
    check("Denkfall nennt DENKEN=aus", "DENKEN=aus" in antwort2.text)
    check("Denkfall zeigt den Anfang der Überlegung", "Ich überlege" in antwort2.text)
    agent.llm.last_gedanken = ""


# ================================= 5. Echosperre und Warteschlangenstand
def test_eingabe() -> None:
    import io

    leser = Eingabeleser(io.StringIO("eine Zeile\nzweite Zeile\n"))
    import time
    time.sleep(0.3)
    check("wartet() zählt die Warteschlange", leser.wartet() >= 1, str(leser.wartet()))

    # Ohne Terminal darf die Sperre nichts tun - und vor allem nicht werfen.
    with Echosperre(io.StringIO("")) as sperre:
        check("Echosperre ohne Terminal ist folgenlos", sperre._wieder is None)
    check("Echosperre lässt sich schließen", True)


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "## Kurzfassung\n" + "Inhalt. " * 60
                      ).__get__(agent.llm)
    agent.sync()

    test_ordner_frage()
    test_uebersichtsnotiz(agent.tools)
    test_prosa_aufruf()
    test_eingabe()
    test_leere_antwort(agent)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
