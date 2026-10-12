"""Bildabnahme der Oberfläche - kein Test, sondern eine Sichtprüfung.

Startet den Server gegen eine erfundene Vault, fährt jede Ansicht an und legt
je ein PNG ab. Der Grund: Ein Test kann sagen, dass ein Endpunkt antwortet, aber
nicht, ob die Sterne übereinander liegen oder der Graph aus dem Bild läuft.

    python tests/bild_oberflaeche.py [Zielordner]
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_bild_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["ASSISTANT_NAME"] = "ZEUS"

ZIEL = Path(sys.argv[1] if len(sys.argv) > 1 else TMP / "bilder")

THEMEN = [
    ("Wissen", "Neuronale_Netze", ["Backpropagation", "Aktivierungsfunktionen",
                                   "Gradientenabstieg", "Verlustfunktion"]),
    ("Wissen", "Backpropagation", ["Neuronale Netze", "Kettenregel"]),
    ("Wissen", "Aktivierungsfunktionen", ["Neuronale Netze", "ReLU"]),
    ("Wissen", "Gradientenabstieg", ["Verlustfunktion", "Lernrate"]),
    ("Wissen", "Verlustfunktion", ["Gradientenabstieg"]),
    ("Wissen", "Kettenregel", []),
    ("Wissen", "ReLU", ["Aktivierungsfunktionen"]),
    ("Wissen", "Lernrate", ["Gradientenabstieg"]),
    ("Agenten", "Werkzeugschleife", ["Planer", "Werkzeuge"]),
    ("Agenten", "Planer", ["Werkzeugschleife", "Namensaufloesung"]),
    ("Agenten", "Werkzeuge", ["Planer"]),
    ("Agenten", "Namensaufloesung", ["Planer"]),
    ("Agenten", "Reflexion", ["Lektionen"]),
    ("Agenten", "Lektionen", ["Reflexion"]),
    ("Projekte", "Zweites_Gehirn", ["Neuronale Netze", "Werkzeugschleife"]),
    ("Projekte", "Messstand", []),
    ("Projekte", "Oberflaeche", ["Zweites_Gehirn"]),
    ("Notizen", "Einkaufsliste", []),
    ("Notizen", "Ideen", []),
    ("Notizen", "Tagebuch_2026", []),
]


def vault_bauen() -> None:
    for ordner, name, links in THEMEN:
        (VAULT / ordner).mkdir(parents=True, exist_ok=True)
        verweise = "".join(f"- [[{l}]]\n" for l in links)
        (VAULT / ordner / f"{name}.md").write_text(
            f"---\ntags: [{ordner.lower()}]\n---\n\n# {name.replace('_',' ')}\n\n"
            f"Beispieltext für die Sichtprüfung.\n\n"
            + (f"## Verwandte Notizen\n{verweise}" if verweise else ""),
            encoding="utf-8")


def nutzung_erfinden() -> None:
    """Etwas Strichliste, damit die Sterne nicht alle gleich matt sind."""
    from jarvis import nutzung
    muster = {
        "notizen_suchen": (140, 3), "notiz_lesen": (95, 1), "notiz_erstellen": (34, 4),
        "struktur_anzeigen": (22, 0), "notiz_ergaenzen": (17, 1),
        "notizen_verknuepfen": (12, 5), "notiz_verschieben": (9, 0),
        "uebersichtsnotiz": (6, 2), "auto_verlinken": (4, 3),
        "block_ersetzen": (3, 0), "notiz_loeschen": (2, 1),
        "quellcode_lesen": (11, 0), "code_suchen": (7, 0), "code_dateien": (5, 0),
        "frontmatter_setzen": (2, 0), "tags_setzen": (1, 0),
        "notiz_ueberschreiben": (3, 2), "rueckgaengig": (1, 0),
    }
    for name, (aufrufe, fehler) in muster.items():
        for i in range(aufrufe):
            nutzung.vermerken(name, i >= fehler)


def main() -> int:
    from playwright.sync_api import sync_playwright

    vault_bauen()
    nutzung_erfinden()
    ZIEL.mkdir(parents=True, exist_ok=True)

    from jarvis.agent import JarvisAgent
    from jarvis.index import KnowledgeIndex
    from jarvis.web.server import starten

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    antwort = ("## Kurzfassung\n\nDie **Werkzeugschleife** ruft Werkzeuge auf, bis die "
               "Frage beantwortet ist. Die Reihenfolge steht in `planner.py`, nicht im "
               "Prompt – siehe [[Werkzeugschleife]].\n\n"
               "- Planer löst Namen **vor** der Ausführung auf\n"
               "- Jede Änderung bekommt vorher eine Sicherung\n"
               "- Der Tatsachenbericht steht über der Modellprosa\n")
    agent.llm.chat = (lambda self, m, **k: antwort).__get__(agent.llm)
    agent.llm.chat_message = (lambda self, m, tools=None, **k: {
        "role": "assistant", "content": antwort}).__get__(agent.llm)
    agent.sync()

    # Ein wenig echter Protokollverkehr, damit die Ansicht etwas zeigt.
    from jarvis.protokoll import protokoll
    log = protokoll()
    agent.tools.ausfuehren("notiz_erstellen",
                           {"titel": "Protokollprobe", "inhalt": "Text.",
                            "ordner": "Notizen"})
    agent.tools.ausfuehren("notiz_lesen", {"name": "GibtsNichtWirklich"})
    log.melden("netz_raus", "POST http://192.168.173.176:11434/api/chat", "ollama",
               bytes=4210, modell="qwen3:32b", strom=True)
    log.melden("netz_rein", "200 von http://192.168.173.176:11434/api/chat",
               "ollama", bytes=-1, dauer=3.4, strom=True)
    log.warnung("Ausweichserver aktiv – Hauptserver antwortet nicht", "ollama")
    agent.tools.ausfuehren("notiz_verschieben",
                           {"name": "Protokollprobe", "ziel_ordner": "Projekte"})

    server, adresse, _ = starten(agent=agent, port=0, schluessel="bild")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            seite = browser.new_page(viewport={"width": 1440, "height": 900},
                                     device_scale_factor=2)
            seite.goto(adresse, wait_until="networkidle")
            seite.wait_for_timeout(900)

            # 1 Board mit einer echten Antwort
            seite.fill("#feld", "Wie funktioniert die Werkzeugschleife?")
            seite.press("#feld", "Enter")
            seite.wait_for_timeout(2600)
            seite.screenshot(path=str(ZIEL / "1_board.png"))

            # 2 Die Lade mit allen Werkzeugen
            seite.click("#lade-griff")
            seite.wait_for_timeout(1400)
            seite.screenshot(path=str(ZIEL / "2_lade.png"))
            seite.click("#lade-griff")
            seite.wait_for_timeout(500)

            # 2b Lade mit angeleuchteten Schritten (wie beim Arbeiten)
            seite.click("#lade-griff")
            seite.wait_for_timeout(900)
            seite.evaluate("""() => {
                const s = lade.sterne.find(x => x.name === 'auto_verlinken')
                       || lade.sterne[0];
                if(!s) return;
                lade.x = -Math.max(0, s.x - 340); lade.laeuft = s.name;
                (s.trabanten||[]).slice(0,4).forEach(t => t.an = true);
                lade.bewegt(s.x + lade.x, s.y); lade.zeichnen();
            }""")
            seite.wait_for_timeout(700)
            seite.screenshot(path=str(ZIEL / "2b_schritte.png"))
            seite.click("#lade-griff")
            seite.wait_for_timeout(400)

            # 3 Magic-Eingabe: eckige Klammer
            seite.click("#feld")
            seite.type("#feld", "Fass zusammen mit [not", delay=25)
            seite.wait_for_timeout(900)
            seite.screenshot(path=str(ZIEL / "3_magie_werkzeug.png"))

            # 4 Magic-Eingabe: Schrägstrich
            seite.fill("#feld", "")
            seite.type("#feld", "/", delay=25)
            seite.wait_for_timeout(900)
            seite.screenshot(path=str(ZIEL / "4_magie_befehl.png"))

            # 5 Ein echter Befehl im Chat
            seite.fill("#feld", "")
            seite.type("#feld", "/werkzeuge", delay=12)
            seite.keyboard.press("Escape")
            seite.keyboard.press("Enter")
            seite.wait_for_timeout(2400)
            seite.screenshot(path=str(ZIEL / "5_befehl.png"))

            # 6 Notiz per Klick im Hirn
            box = seite.locator("#hirn-leinwand").bounding_box()
            seite.evaluate("""() => {
                const k = hirn.knoten.find(x => x.grad >= 2) || hirn.knoten[0];
                if(k) hirn.geklickt(k.x*hirn.zoom+hirn.x, k.y*hirn.zoom+hirn.y);
            }""")
            seite.wait_for_timeout(1300)
            seite.screenshot(path=str(ZIEL / "6_notiz.png"))

            # 6b Das Protokoll, angeheftet
            seite.click('[data-zu="notiz-blende"]')      # Notiz-Blende zu
            seite.wait_for_timeout(400)
            seite.click("#knopf-log")
            seite.wait_for_timeout(500)
            seite.click("#log-fest")
            seite.wait_for_timeout(1100)
            seite.screenshot(path=str(ZIEL / "6b_protokoll.png"))
            seite.click("#log-zu")
            seite.wait_for_timeout(400)

            # 7 Einstellungen als Blende
            seite.click("#knopf-einst")
            seite.wait_for_timeout(1300)
            seite.screenshot(path=str(ZIEL / "7_einstellungen.png"))

            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    for datei in sorted(ZIEL.glob("*.png")):
        print(f"  {datei}  ({datei.stat().st_size//1024} kB)")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
