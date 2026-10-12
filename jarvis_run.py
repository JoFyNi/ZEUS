#!/usr/bin/env python3
"""Startpunkt für JARVIS.

    python jarvis_run.py            # Chat im Terminal starten
    python jarvis_run.py --ui       # grafische Oberfläche im eigenen Fenster
    python jarvis_run.py --browser  # dieselbe Oberfläche im Browser
    python jarvis_run.py --installieren  # fehlende Pakete nachinstallieren
    python jarvis_run.py --doktor   # Konfiguration prüfen (.env, Vault, Ollama)
    python jarvis_run.py --sync     # nur Index aktualisieren
    python jarvis_run.py --reindex  # Index komplett neu bauen
    python jarvis_run.py --baum     # Ordnerstruktur zeigen
    python jarvis_run.py --frage "Was steht zu Backpropagation?"
    python jarvis_run.py --notiz "Deep Learning"
    python jarvis_run.py --vault "C:\\Users\\...\\Zeus"   # Pfad in die .env schreiben
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

# (Importname, Paketname für pip) - der Importname weicht oft ab.
KERNPAKETE = [
    ("requests", "requests"),
    ("dotenv", "python-dotenv"),
    ("yaml", "PyYAML"),
    ("langchain_core", "langchain-core"),
    ("langchain_community", "langchain-community"),
    ("langchain_text_splitters", "langchain-text-splitters"),
    ("faiss", "faiss-cpu"),
]
KOMFORTPAKETE = [
    ("rich", "rich"),
    # Nur für --ui: ein eigenes Fenster statt eines Browser-Tabs. Fehlt es,
    # läuft die Oberfläche im Browser weiter - deshalb Komfort, nicht Kern.
    ("webview", "pywebview"),
    ("pyttsx3", "pyttsx3"),
    ("faster_whisper", "faster-whisper"),
    ("sounddevice", "sounddevice"),
    ("numpy", "numpy"),
]


def _fehlt(importname: str) -> bool:
    try:
        return importlib.util.find_spec(importname) is None
    except (ImportError, ValueError):
        return True


def abhaengigkeiten_pruefen(installieren: bool = False) -> int:
    """Prüft VOR dem ersten jarvis-Import, ob die Pakete da sind.

    Ohne das begrüßt ein frisch aufgesetzter Rechner mit einem nackten
    'ModuleNotFoundError: No module named requests' - technisch korrekt,
    aber es sagt niemandem, was zu tun ist.
    """
    fehlend_kern = [pip for imp, pip in KERNPAKETE if _fehlt(imp)]
    fehlend_komfort = [pip for imp, pip in KOMFORTPAKETE if _fehlt(imp)]

    if installieren and (fehlend_kern or fehlend_komfort):
        anforderungen = Path(__file__).resolve().parent / "requirements.txt"
        befehl = [sys.executable, "-m", "pip", "install"]
        befehl += ["-r", str(anforderungen)] if anforderungen.is_file() else \
            fehlend_kern + fehlend_komfort
        print("Installiere fehlende Pakete …\n  " + " ".join(befehl) + "\n")
        try:
            # Ohne Zeitlimit hängt der Start still mit, wenn pip auf ein
            # Netzlaufwerk oder einen langsamen Spiegel wartet.
            ergebnis = subprocess.run(befehl, timeout=1800)
        except subprocess.TimeoutExpired:
            print("\npip hat nach 30 Minuten nicht geantwortet. Abgebrochen.")
            print("Von Hand:  py -m pip install -r requirements.txt")
            return 1
        except OSError as exc:
            # FileNotFoundError ist ein OSError, kein SubprocessError.
            print(f"\npip konnte nicht gestartet werden: {exc}")
            print(f"Verwendetes Python: {sys.executable}")
            print("Von Hand:  py -m pip install -r requirements.txt")
            return 1
        if ergebnis.returncode != 0:
            print("\nInstallation fehlgeschlagen. Läuft Python mit Schreibrechten? "
                  "Notfalls: py -m pip install --user -r requirements.txt")
            return 1
        fehlend_kern = [pip for imp, pip in KERNPAKETE if _fehlt(imp)]

    if fehlend_kern:
        print("\n  Es fehlen Pakete, ohne die JARVIS nicht starten kann:\n")
        for paket in fehlend_kern:
            print(f"    - {paket}")
        print("\n  So beheben Sie das:\n")
        print(f'    cd "{Path(__file__).resolve().parent}"')
        print("    py -m pip install -r requirements.txt")
        print("\n  (Wenn 'py' nicht gefunden wird: python -m pip install -r requirements.txt)")
        print("  Oder in einem Rutsch:  py jarvis_run.py --installieren\n")
        print(f"  Verwendetes Python: {sys.executable}")
        print("  Achtung bei mehreren Python-Installationen: pip muss zu DIESEM Python gehören.\n")
        return 1

    if fehlend_komfort:
        print("Hinweis: optionale Pakete fehlen (" + ", ".join(fehlend_komfort) + ").")
        print("Der Agent läuft, aber ohne farbige Oberfläche bzw. ohne Sprachein-/-ausgabe.")
        print("Nachrüsten mit: py -m pip install " + " ".join(fehlend_komfort) + "\n")
    return 0


def doktor() -> int:
    from jarvis.config import settings
    from jarvis.llm import OllamaClient

    rows = list(settings.diagnose())
    client = OllamaClient(settings)
    alive = client.is_alive()
    rows.append(("Ollama erreichbar", alive, settings.host))

    # Der Ausweichserver gehört mit in die Diagnose. Ist der Hauptserver aus,
    # ist die einzige interessante Frage, ob der Ersatz trägt - und ob dort
    # die Modelle liegen, die dann gebraucht werden.
    if settings.host_alternative:
        ersatz_da = client.host_erreichbar(settings.host_alternative)
        rows.append(("Ausweichserver erreichbar", ersatz_da, settings.host_alternative))
        if ersatz_da:
            dort = client.modelle_auf(settings.host_alternative)
            gewuenscht = settings.llm_model_alternative or settings.llm_model
            rows.append((
                "Ausweichmodell vorhanden",
                client._modell_vorhanden(gewuenscht, dort),
                f"{gewuenscht} auf {settings.host_alternative}"))
            rows.append((
                "Einbettungsmodell auf dem Ausweichserver",
                client._modell_vorhanden(settings.embedding_model, dort),
                f"{settings.embedding_model} – ohne das findet die Suche dort nichts"))
    elif not alive:
        rows.append(("Ausweichserver", False,
                     "nicht eingetragen – OLLAMA_HOST_ALTERNATIVE in die .env"))

    if alive:
        models = client.available_models()
        bare = {m.split(":")[0] for m in models}
        for label, name in (("LLM-Modell", settings.llm_model),
                            ("Embedding-Modell", settings.embedding_model)):
            ok = name in models or name.split(":")[0] in bare
            rows.append((label, ok, name if ok else f"'{name}' fehlt → ollama pull {name}"))

    print("\nDiagnose")
    print("=" * 60)
    for title, ok, note in rows:
        print(f"{'[ok]  ' if ok else '[FEHL]'} {title}: {note}")

    # Sprachausgabe separat: ein Fehlen ist kein Fehler, nur ein Hinweis.
    try:
        from jarvis.voice import Speaker

        sprecher = Speaker(settings)
        print(f"[info] Sprachausgabe: {sprecher.status}")
        if sprecher.engine_name != "piper":
            print("       Natürlichere Stimme: python jarvis_run.py --stimme-einrichten")
    except Exception as exc:
        print(f"[info] Sprachausgabe nicht prüfbar: {exc}")
    print("=" * 60)
    return 0 if all(ok for _, ok, _ in rows) else 1


def _bericht_schreiben(agent, titel: str, bericht, zielordner: str):
    """Bericht ablegen - beim zweiten Lauf am selben Tag den alten ERSETZEN.

    Sonst sammeln sich Code-Prüfung_2026-08-22.md, …_2.md, …_3.md an, und
    niemand weiß mehr, welcher der aktuelle ist.
    """
    inhalt = bericht.als_markdown(titel)
    vorhanden = agent.tools.notiz_exakt(titel)
    if vorhanden is not None:
        ergebnis = agent.tools.notiz_ueberschreiben(name=titel, inhalt=inhalt)
        if ergebnis.ok:
            ergebnis.meldung = f"Bericht '{titel}' aktualisiert."
        return ergebnis
    return agent.tools.notiz_erstellen(titel=titel, inhalt=inhalt, ordner=zielordner)


def _text_ablegen(agent, text: str, titel: str, unterordner: str) -> str:
    """Fertigen Markdown-Text als Notiz ablegen bzw. den heutigen ersetzen."""
    ordner = (f"{agent.cfg.code_folder}".strip("/") or "_System/Code")
    try:
        agent.index.refresh_structure()
        if agent.tools.notiz_exakt(titel) is not None:
            ergebnis = agent.tools.notiz_ueberschreiben(name=titel, inhalt=text)
        else:
            ergebnis = agent.tools.notiz_erstellen(
                titel=titel, inhalt=text, ordner=f"{ordner}/{unterordner}")
    except Exception as exc:
        print(f"  Bericht konnte nicht abgelegt werden: {exc}")
        return ""
    if not ergebnis.ok:
        print(f"  Bericht konnte nicht abgelegt werden: {ergebnis.meldung}")
        return ""
    return str(ergebnis.daten.get("pfad") or titel)


def _bericht_ablegen(agent, bericht) -> str:
    """Prüfbericht als Notiz in die Vault - dort kann Zeus ihn selbst lesen."""
    import datetime as dt

    titel = f"Code-Prüfung {dt.date.today().isoformat()}"
    ordner = f"{agent.cfg.code_folder}".strip("/") or "_System/Code"
    try:
        # --codereview läuft ohne sync(); ohne Struktur stürzte das Ablegen ab.
        agent.index.refresh_structure()
        ergebnis = _bericht_schreiben(agent, titel, bericht, f"{ordner}/Pruefungen")
    except Exception as exc:
        print(f"  Bericht konnte nicht abgelegt werden: {exc}")
        print("  Der Befund oben steht trotzdem – die Prüfung selbst war erfolgreich.")
        return ""
    if not ergebnis.ok:
        print(f"  Bericht konnte nicht abgelegt werden: {ergebnis.meldung}")
        return ""
    return str(ergebnis.daten.get("pfad") or titel)


def _stimmen_kommando(args) -> int:
    """--stimmen / --stimme-einrichten: läuft ohne den Index-Unterbau."""
    from jarvis.config import reload_settings, settings
    from jarvis.piper_stimmen import STANDARD_STIMME, einrichten, uebersicht

    if args.stimmen and args.stimme_einrichten is None:
        print(uebersicht(settings))
        return 0

    kennung = (args.stimme_einrichten or settings.piper_voice or STANDARD_STIMME).strip()
    print(f"Richte piper mit '{kennung}' ein. Beim ersten Mal dauert das ein paar Minuten.\n")
    ok, meldung = einrichten(kennung, settings, protokoll=lambda t: print(f"  {t}"))
    print()
    print(meldung)
    if ok:
        settings.persist("TTS_ENGINE", "piper")
        settings.persist("PIPER_VOICE", kennung)
        settings.persist("PIPER_DATA_DIR", str(settings.piper_data_path))
        reload_settings()
        print("\nIn der .env gespeichert. Start:  python jarvis_run.py")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS – Second Brain für Obsidian")
    parser.add_argument("--doktor", action="store_true", help="Konfiguration prüfen")
    parser.add_argument("--installieren", action="store_true",
                        help="fehlende Python-Pakete nachinstallieren")
    parser.add_argument("--vault", metavar="PFAD", help="Vault-Pfad setzen und in die .env schreiben")
    parser.add_argument("--sync", action="store_true", help="geänderte Notizen nachindexieren")
    parser.add_argument("--reindex", action="store_true", help="Index komplett neu aufbauen")
    parser.add_argument("--baum", action="store_true", help="Ordnerbaum ausgeben")
    parser.add_argument("--vault-git-einrichten", action="store_true",
                        dest="vault_git_einrichten",
                        help="Git-Historie in der Vault anlegen")
    parser.add_argument("--vault-verlauf", action="store_true",
                        dest="vault_verlauf",
                        help="die juengsten Aenderungen an der Vault zeigen")
    parser.add_argument("--aufgaben", action="store_true",
                        help="offene Aufgaben und Freigaben zeigen")
    parser.add_argument("--aufgaben-aufnehmen", action="store_true",
                        dest="aufgaben_aufnehmen",
                        help="nach einem Absturz: offene Aufgaben wieder aufgreifen")
    parser.add_argument("--frage", metavar="TEXT", help="eine einzelne Frage stellen")
    parser.add_argument("--notiz", metavar="THEMA", help="Notiz zu einem Thema erzeugen und ablegen")
    parser.add_argument("--wartung", action="store_true",
                        help="Wartungslauf: Index, tote Links, Verwaiste, Dubletten (für Cron/Aufgabenplanung)")
    parser.add_argument("--reflektieren", action="store_true",
                        help="Selbstreflexion über die letzten Beobachtungen, Bericht in die Vault")
    parser.add_argument("--tempo", action="store_true",
                        help="Misst die Ausgabegeschwindigkeit des Modells")
    parser.add_argument("--hardware", action="store_true",
                        help="GPU-Diagnose: warum ist es langsam und was hilft")
    parser.add_argument("--stimme-einrichten", metavar="KENNUNG", nargs="?", const="",
                        dest="stimme_einrichten",
                        help="natürliche piper-Stimme laden, z. B. en_GB-alan-medium")
    parser.add_argument("--stimmen", action="store_true",
                        help="empfohlene piper-Stimmen auflisten")
    parser.add_argument("--evaluieren", action="store_true",
                        help="Pruefaufgaben durchlaufen und die Trefferquote messen")
    parser.add_argument("--variante", default="beide",
                        choices=["mit", "ohne", "blind", "ohne_sitzung", "beide", "alle"],
                        help="zu --evaluieren: mit/ohne gelernte Regeln, 'blind' ohne "
                             "jeden Abruf (Kontrollgruppe), 'ohne_sitzung' trennt das "
                             "Sitzungsgedaechtnis von den Regeln, 'alle' fuer alle vier")
    parser.add_argument("--nur", type=int, default=0, metavar="N",
                        help="zu --evaluieren: nur die ersten N Aufgaben")
    parser.add_argument("--runden", type=int, default=1, metavar="N",
                        help="zu --evaluieren: jede Variante N-mal laufen lassen. "
                             "Erst damit wird sichtbar, wie stark das Ergebnis "
                             "von allein schwankt.")
    parser.add_argument("--codereview", action="store_true",
                        help="eigenen Quellcode prüfen und Bericht in die Vault schreiben")
    parser.add_argument("--tief", action="store_true",
                        help="zu --codereview: zusätzlich das Modell befragen (langsam)")
    parser.add_argument("--abschnitte", type=int, default=6,
                        help="zu --codereview --tief: wie viele Abschnitte ans Modell gehen")
    parser.add_argument("--architektur", action="store_true",
                        help="nur den Aufbau vermessen: Größe, Verzweigungen, Kopplung, "
                             "Testabdeckung und fehlende Fähigkeiten (Sekunden)")
    parser.add_argument("--ui", action="store_true",
                        help="grafische Oberfläche in einem eigenen Fenster starten "
                             "(Graph, Fertigkeitsbaum, Einstellungen)")
    parser.add_argument("--browser", action="store_true",
                        help="zu --ui: statt eines Fensters den Browser öffnen")
    parser.add_argument("--port", type=int, default=0, metavar="N",
                        help="zu --ui: fester Port (0 = freien Port suchen)")
    args = parser.parse_args()

    # Stimmen einrichten braucht weder FAISS noch langchain - deshalb VOR der
    # Paketprüfung. Wer nur die Stimme reparieren will, soll nicht erst den
    # ganzen Index-Unterbau installieren müssen.
    if args.stimmen or args.stimme_einrichten is not None:
        return _stimmen_kommando(args)

    # Vor allem anderen: sind die Pakete überhaupt da?
    fehlercode = abhaengigkeiten_pruefen(installieren=args.installieren)
    if fehlercode:
        return fehlercode
    if args.installieren:
        print("Pakete sind vollständig.\n")

    if args.vault:
        from jarvis.config import reload_settings, settings

        path = Path(args.vault.strip().strip('"').strip("'")).expanduser()
        if not path.is_dir():
            print(f"Kein Ordner: {path}")
            return 1
        target = settings.persist("OBSIDIAN_VAULT", str(path))
        reload_settings()
        print(f"Vault gesetzt: {path}" + (f"  (gespeichert in {target})" if target else ""))
        if not any([args.doktor, args.sync, args.reindex, args.baum, args.frage, args.notiz]):
            print("Weiter mit:  python jarvis_run.py --reindex")
            return 0

    if args.doktor:
        return doktor()

    if args.aufgaben or args.aufgaben_aufnehmen:
        from jarvis.config import settings as cfg
        from jarvis.kern.aufgabenspeicher import Aufgabenspeicher, WARTET_AUF_MENSCH
        speicher = Aufgabenspeicher(cfg.work_dir / "aufgaben.sqlite3")
        if args.aufgaben_aufnehmen:
            wieder = speicher.wiederaufnehmen()
            if wieder:
                print(f"{len(wieder)} Aufgabe(n) zur Neuplanung vorgemerkt - beim "
                      f"Absturz liefen sie, der Stand ist unbekannt:")
                for a in wieder:
                    print(f"   {a.titel[:70]}")
            else:
                print("Keine unterbrochenen Aufgaben gefunden.")
            print()

        zahlen = speicher.kennzahlen()
        print(f"Aufgaben gesamt {zahlen['gesamt']}, davon offen {zahlen['offen']}, "
              f"auf dich wartend {zahlen['wartet_auf_mensch']}")
        offene = speicher.alle(nur_offene=True, grenze=40)
        if offene:
            print()
            for a in offene:
                print(f"  {a.stichpunkt()}")
        freigaben = speicher.offene_freigaben()
        if freigaben:
            print()
            print("Wartet auf deine Entscheidung:")
            for f in freigaben:
                print(f"  [{f['risiko']}] {f['titel'][:50]}")
                print(f"        {f['frage'][:100]}")
        speicher.schliessen()
        return 0

    if args.vault_git_einrichten or args.vault_verlauf:
        from jarvis.config import settings as cfg
        from jarvis.vaultgit import Vaultgit
        git = Vaultgit(cfg.vault_path, melden=print)
        if args.vault_git_einrichten:
            ok, meldung = git.einrichten()
            print(meldung)
            if not ok:
                return 1
        eintraege = git.letzte(10)
        if eintraege:
            print()
            print("Die juengsten Aenderungen an der Vault:")
            for e in eintraege:
                print(f"  {e['kennung']}  {e['zeit']}  {e['text']}")
        elif args.vault_verlauf:
            print("Noch keine Aenderungen in der Historie.")
        return 0

    # Die Oberfläche kommt VOR der Terminalschleife, damit --ui nicht erst den
    # Textmodus hochfährt. Das Terminal bleibt unverändert: ohne Schalter
    # startet weiterhin der Chat, wie eh und je.
    if args.ui or args.browser:
        from jarvis.web.fenster import oberflaeche_starten

        return oberflaeche_starten(port=args.port, browser=args.browser)

    if not any([args.sync, args.reindex, args.baum, args.frage, args.notiz, args.wartung,
                args.reflektieren, args.tempo, args.hardware, args.codereview,
                args.architektur, args.evaluieren, args.vault_git_einrichten,
                args.vault_verlauf, args.aufgaben, args.aufgaben_aufnehmen]):
        from jarvis.ui import main as ui_main

        ui_main()
        return 0

    from jarvis.agent import JarvisAgent

    agent = JarvisAgent()

    try:
        if args.reindex or args.sync:
            print(agent.sync(force=args.reindex, progress=lambda m: print(f"  {m}")).summary())

        if args.wartung:
            ergebnis = agent.tools.vault_wartung(reparieren=True)
            d = ergebnis.daten
            print(d.get("index", ""))
            print(f"Reparierte Links: {len(d.get('links_repariert', []))}")
            print(f"Offene Links:     {len(d.get('links_offen', []))}")
            print(f"Verwaiste Notizen:{len(d.get('verwaiste_notizen', []))}")
            print(f"Dubletten-Verdacht:{len(d.get('moegliche_dubletten', []))}")

        if args.tempo or args.hardware:
            from jarvis.hardware import analysieren

            tps = agent.llm.benchmark() if args.tempo else None
            if args.tempo:
                print(f"Tempo: {tps} Token/s" if tps else "Tempo konnte nicht gemessen werden.")
                print()
            print(analysieren(agent.cfg, tempo=tps).bericht())

        if args.architektur and not args.codereview:
            # Ohne Defektsuche: nur vermessen. Das dauert Sekunden und ist der
            # Lauf, den man vor jeder Umbauentscheidung machen will.
            from jarvis.architektur import architektur_pruefen, terminalfassung
            from jarvis.codereview import CodePruefer, ist_testdatei

            pruefer = CodePruefer(agent, agent.cfg)
            alle = pruefer.quelldateien()
            quellen = [(p, a) for p, a in alle if not ist_testdatei(a)]
            tests = [(p, a) for p, a in alle if ist_testdatei(a)]
            print(f"{len(quellen)} Module, {len(tests)} Testdateien – vermesse …\n")
            bericht = architektur_pruefen(quellen, tests, agent.cfg)
            for zeile in terminalfassung(bericht):
                print(zeile)

        if args.codereview:
            from jarvis.codereview import CodePruefer

            pruefer = CodePruefer(agent, agent.cfg)
            bericht = pruefer.pruefen(mit_modell=args.tief,
                                      abschnitte=max(1, args.abschnitte),
                                      fortschritt=lambda m: print(f"  {m}"))
            print()
            print(bericht.kurzfassung())
            print()
            for zeile in bericht.terminalfassung():
                print(zeile)
            if bericht.verworfen:
                print(f"\n{len(bericht.verworfen)} Modellbefunde verworfen, z. B.:")
                for eintrag in bericht.verworfen[:3]:
                    print(f"  - {eintrag[:150]}")
            pfad = _bericht_ablegen(agent, bericht)
            if pfad:
                print(f"\nBericht abgelegt: {pfad}")

        if args.evaluieren:
            from jarvis.evaluation import Messstand, aufgaben_lesen, vorlage_schreiben

            stand = Messstand(agent, agent.cfg)
            if not aufgaben_lesen(stand.aufgabendatei):
                if vorlage_schreiben(stand.aufgabendatei):
                    print(f"Keine Pruefaufgaben gefunden – Vorlage angelegt:")
                    print(f"  {stand.aufgabendatei}")
                    print("\nTrage dort 20-30 echte Aufgaben ein, dann erneut starten.")
                    print("Ohne echte Aufgaben misst der Messstand nichts Verwertbares.")
                else:
                    print(f"Vorlage nicht schreibbar: {stand.aufgabendatei}")
                return 1

            if args.variante == "beide":
                varianten = ["mit", "ohne"]
            elif args.variante == "alle":
                varianten = ["mit", "ohne", "blind", "ohne_sitzung"]
            else:
                varianten = [args.variante]
            anzahl = len(aufgaben_lesen(stand.aufgabendatei))
            if args.nur:
                anzahl = min(anzahl, args.nur)
            print(f"{anzahl} Aufgaben x {len(varianten)} Varianten. "
                  f"Das dauert - jede Aufgabe ist ein vollstaendiger Agentenlauf.\n")
            laeufe = stand.messen(varianten=varianten, grenze=args.nur,
                                  runden=max(1, args.runden),
                                  fortschritt=lambda m: print(f"  {m}"))
            print()
            for durchlauf in laeufe.values():
                print(durchlauf.zusammenfassung())
            if "mit" in laeufe and "ohne" in laeufe:
                print()
                print(stand._urteil(laeufe["mit"], laeufe["ohne"]).replace("## Urteil\n\n", ""))
            stand.journal_schreiben(laeufe)
            import datetime as _dt

            pfad = _text_ablegen(agent, stand.bericht(laeufe),
                                 f"Messung {_dt.date.today().isoformat()}", "Messungen")
            if pfad:
                print(f"\nBericht abgelegt: {pfad}")

        if args.reflektieren:
            agent.index.refresh_structure()
            print(agent.lernwerk.lektionen_konsolidieren())
            print()
            print(agent.lernwerk.reflektieren())

        if args.baum:
            agent.index.refresh_structure()
            print(agent.structure.tree())

        if args.frage:
            agent.index.refresh_structure()
            if agent.index.store is None:
                agent.sync()
            answer = agent.ask(args.frage, stream_cb=lambda t: sys.stdout.write(t))
            print("\n\nQuellen: " + (", ".join(answer.sources) or "–"))

        if args.notiz:
            if agent.index.store is None:
                agent.sync()
            content, hits = agent.summarize_for_note(args.notiz)
            path, decision = agent.write_note(args.notiz, content, hits)
            print(f"\nGespeichert: {path}  (Ordner: {decision.folder or 'Wurzel'} – {decision.reason})")
    except FileNotFoundError as exc:
        print(f"\n{exc}\n")
        print("Tipp: python jarvis_run.py --doktor  zeigt, welche .env gelesen wurde.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
