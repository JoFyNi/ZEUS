"""Regressionspruefungen: Antwortpflicht, Triage und Schleifenschutz im Gespraech.

Anlass und Abnahmekriterien: claude/PROMPT_Antwortpflicht_und_Triage.md.
Der Kern jeder Pruefung hier ist derselbe Satz:

    Eine Runde, die keine Antwort enthaelt, ist gescheitert - egal wie viele
    Werkzeuge erfolgreich liefen.

Alles laeuft ohne Ollama: am echten JarvisAgent mit dem sturen Skriptmodell
aus `messung_gespraech.py`, das Werkzeuge ruft, solange es welche bekommt.
Damit ist diese Reihe NICHT umgebungsabhaengig.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import messung_gespraech as mg  # noqa: E402  (installiert die Stubs)

from jarvis.gespraech.antwort import ToolCallRecord  # noqa: E402
from jarvis.gespraech.antwortpflicht import ist_prosa_antwort  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# ================================================================ Stufe 1
def fall_was_als_antwort_zaehlt() -> None:
    namen = {"notiz_lesen", "notizen_suchen"}
    faelle = [
        ("", False),
        ("   ", False),
        ("Erledigt: Inhalt von 'Aufgabenliste_Autonomie_NN_Gehirn'; "
         "3 Treffer für 'offene Aufgaben To-do was steht an'", False),
        ("Inhalt von 'A'; 3 Treffer für 'b c'", False),
        ('notiz_lesen(name="Aufgabenliste")', False),
        ('{"name": "notiz_lesen", "arguments": {"name": "X"}}', False),
        ('{"werkzeug": "notizen_suchen", "parameter": {"frage": "x"}}', False),
        ("[[Aufgabenliste]]; [[Offene_Punkte]]", False),
        ("Hallo Josh.", True),
        ("Offen sind: Triage anschliessen und die Schlussrunde bauen.", True),
        ("Erledigt ist davon noch nichts - offen sind zwei Punkte.", True),
    ]
    falsch = [f"{t[:40]!r} -> {not soll}" for t, soll in faelle
              if ist_prosa_antwort(t, namen) != soll]
    check("ist_prosa_antwort trennt Antwort von Protokoll", not falsch,
          "; ".join(falsch))


def fall_testsatz_antwortpflicht(saetze) -> None:
    erledigt = [s.eingabe for s in saetze if s.antwort_anfang.startswith("Erledigt:")]
    check("Keine Runde im Testsatz endet mit 'Erledigt: '", not erledigt,
          ", ".join(erledigt))
    notbremse = sum(s.notbremse for s in saetze)
    check("Notbremse _schlussbericht wurde nie gezogen", notbremse == 0,
          f"{notbremse}x")
    leer = [s.eingabe for s in saetze if not s.antwort_anfang]
    check("Jede Eingabe bekommt Text", not leer, ", ".join(leer))

    budget = next(s for s in saetze if s.gruppe == "budget")
    check("Budgetfall: die Schlussrunde greift",
          budget.endete_mit == "schlussrunde", budget.endete_mit)
    check("Budgetfall: genau EINE Schlussrunde",
          budget.modell_phasen.get("schlussrunde") == 1, str(budget.modell_phasen))

    vault = next(s for s in saetze if s.eingabe == "wa steht an?")
    check("'wa steht an?' nennt die Aufgaben selbst",
          "Triage an den Gespraechspfad" in vault.antwort_anfang
          or "Sandkisten-Konzept" in vault.antwort_anfang, vault.antwort_anfang)


class _Skript:
    """Ein Modell fuer Einzelfaelle: Werkzeug immer, Schlussrunde nach Vorgabe."""

    def __init__(self, aufruf, schluss):
        # Ein Aufruf oder eine Liste, die der Reihe nach gerufen wird.
        self.aufrufe = aufruf if isinstance(aufruf, list) else [aufruf]
        self.schluss = schluss
        self.schlussrunden = 0
        self.schritt = 0

    def chat_message(self, messages, tools=None, **kw):
        if not tools:
            return {"role": "assistant", "content": self.schluss}
        name, args = self.aufrufe[self.schritt % len(self.aufrufe)]
        self.schritt += 1
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": name, "arguments": dict(args)}}]}

    def chat(self, messages, **kw):
        self.schlussrunden += 1
        return self.schluss


def _schleife(agent, skript, frage="Was steht in der Einkaufsnotiz?"):
    agent.llm.chat = skript.chat
    agent.llm.chat_message = skript.chat_message
    agent._aufrufe.clear()
    agent._letzte_meldung.clear()
    agent.letzte_runde = None
    agent._rundenfrage = frage
    return agent._tool_loop([{"role": "system", "content": "Systemprompt"},
                             {"role": "user", "content": frage}])


def fall_schlussrunde_fuehrt_nichts_aus(agent) -> None:
    geschrieben = 'notiz_loeschen(name="Einkauf")'
    # Lauter VERSCHIEDENE Aufrufe - gleiche wuerde der Schleifenschutz
    # (Stufe 4) abweisen, bevor das Budget erschoepft ist.
    skript = _Skript([("notiz_lesen", {"name": f"Notiz_{i}"}) for i in range(12)],
                     geschrieben)
    text, calls = _schleife(agent, skript)
    check("Ein Aufruf aus der Schlussrunde wird nicht ausgefuehrt",
          all(c.werkzeug != "notiz_loeschen" for c in calls)
          and (mg.VAULT / "00_Inbox" / "Einkauf.md").exists(),
          ", ".join(c.werkzeug for c in calls))
    check("Stattdessen der ehrliche Hinweis mit Grund",
          ("nicht zu einer Antwort gekommen" in text
           or "zu keiner Antwort gekommen" in text) and "Erledigt:" not in text,
          text[:120])
    check("Die Runde ist als 'hinweis' vermerkt",
          agent.letzte_runde.endete_mit == "hinweis", agent.letzte_runde.endete_mit)
    check("Die Schlussrunde zaehlt nicht gegen MAX_TOOL_STEPS",
          len(calls) == agent.cfg.max_tool_steps and skript.schlussrunden == 1,
          f"{len(calls)} Aufrufe, {skript.schlussrunden} Schlussrunde(n)")


def fall_tatsachen_stehen_ueber_der_schlussrunde(agent) -> None:
    """ok=False gilt, auch wenn die Schlussrunde Erfolg behauptet."""
    skript = _Skript(("notiz_verschieben", {"name": "Gibt_Es_Nicht", "ziel": "01_Wissen"}),
                     "Ich habe die Notiz erfolgreich verschoben.")
    text, calls = _schleife(agent, skript, "Verschiebe Gibt_Es_Nicht")
    text = agent._tatsachen_anhaengen(text, calls)
    check("Schlussrunde liefert Prosa", agent.letzte_runde.endete_mit == "schlussrunde",
          agent.letzte_runde.endete_mit)
    check("Der Tatsachenbericht widerspricht der Erfolgsbehauptung",
          "Fehlgeschlagen" in text and "stimmt nicht" in text, text[-200:])


def fall_fehlschlag_ohne_erfolg(agent) -> None:
    """Scheitert jeder Aufruf, nennt der Hinweis den Grund - nicht 'Erledigt'."""
    skript = _Skript(("notiz_lesen", {"name": "Gibt_Es_Nicht"}), "")
    text, _ = _schleife(agent, skript)
    check("Ohne Erfolg: Grund statt Liste",
          "Kein Werkzeug hat ein Ergebnis geliefert" in text
          and "Erledigt:" not in text, text[:160])


# ================================================================ Stufe 2
# Die deterministische Wegewahl - ohne Ollama, ohne Agent, nie umgebungsabhaengig.
WEGEWAHL = [
    ("Hallo", "direkt", False),
    ("danke", "direkt", False),
    ("Was kannst du eigentlich?", "direkt", False),
    ("reflektiere deine Aktionen", "direkt", False),
    ("Reflektier mal, was da schiefging", "direkt", False),
    ("denk bitte über dein Vorgehen nach", "direkt", False),
    ("warum hast du mir nicht geantwortet?", "direkt", False),
    ("Erledigt ist nicht die Antwort auf meine Frage", "direkt", False),
    ("Wo ist der Sinn eines Chats, wenn ich keine Antwort bekomme?", "direkt", False),
    ("wie sollte der Anfangsprompt aussehen?", "direkt", False),
    ("und?", "direkt", True),
    ("was heißt das?", "direkt", True),
    ("Hallo, was steht an?", "vault", False),
    ("Was kannst du mir über Transformer sagen?", "vault", False),
    ("reflektiere deine Aktionen und notiere das", "direkt+schreiben", False),
]


def fall_wegewahl_deterministisch() -> None:
    from jarvis.gespraech.triage import Triage, WEGTABELLE
    from jarvis.kern.bewertung import ABSICHTEN
    from jarvis.planner import looks_like_write_request

    check("Die Wegtabelle kennt jede Absicht des Bewerters",
          set(WEGTABELLE) == set(ABSICHTEN),
          str(set(ABSICHTEN) ^ set(WEGTABELLE)))
    triage = Triage()
    falsch = []
    for text, soll, verlauf in WEGEWAHL:
        wahl = triage.einordnen(text, verlauf=verlauf,
                                schreibauftrag=looks_like_write_request(text))
        if wahl.text != soll:
            falsch.append(f"{text!r}: {wahl.text} statt {soll}")
    check("Regelschicht waehlt die erwarteten Wege", not falsch, "; ".join(falsch))
    ohne = triage.einordnen("und?", verlauf=False)
    check("'und?' ohne Verlauf ist KEIN DIREKT (nichts, worauf es sich bezieht)",
          ohne.text == "vault", ohne.text)


def fall_meta_ohne_abruf(saetze) -> None:
    meta = [s for s in saetze if s.eingabe in mg.META_EINGABEN]
    check("Sechs Meta-Eingaben im Satz", len(meta) == 6, str(len(meta)))
    check("Meta-Eingaben: 0 Vektorabrufe",
          sum(s.vektorabrufe for s in meta) == 0,
          str([(s.eingabe, s.vektorabrufe) for s in meta if s.vektorabrufe]))
    check("Meta-Eingaben: 0 Werkzeugaufrufe",
          sum(s.werkzeugaufrufe + s.abgewiesen for s in meta) == 0)
    check("Meta-Eingaben laufen ueber DIREKT", all(s.weg == "direkt" for s in meta),
          str([(s.eingabe, s.weg) for s in meta if s.weg != "direkt"]))
    gruss = next(s for s in saetze if s.eingabe == "Hallo")
    check("Ein Gruss allein kostet keinen Modellaufruf", gruss.modellaufrufe == 0,
          str(gruss.modellaufrufe))
    rueck = next(s for s in saetze if s.gruppe == "rueckbezug")
    check("'und?' direkt nach einer Vault-Antwort ist DIREKT", rueck.weg == "direkt",
          rueck.weg)
    gemischt = next(s for s in saetze if s.gruppe == "gemischt")
    check("'Hallo, was steht an?' ist VAULT - der Gruss entscheidet nicht",
          gemischt.weg == "vault", gemischt.weg)


def fall_kombiniert(saetze) -> None:
    satz = next(s for s in saetze if s.gruppe == "kombiniert")
    text = satz.antwort_anfang
    check("Kombiniert: DIREKT, dann SCHREIBEN", satz.weg == "direkt+schreiben", satz.weg)
    check("Kombiniert: die Antwort steht VOR dem Schreibbericht",
          not text.startswith("**") and "Änderungen" not in text[:40], text)
    notiz = mg.VAULT / "01_Wissen" / "Reflexion Gespraech.md"
    check("Kombiniert: die Notiz liegt tatsaechlich in der Vault",
          notiz.exists() or any(p.stem.startswith("Reflexion")
                                for p in mg.VAULT.rglob("*.md")))


def fall_direkt_bekommt_laufbericht() -> None:
    """DIREKT sieht, was die letzte Runde wirklich tat - aus den Zaehlern."""
    agent = mg.JarvisAgent(index=mg.KnowledgeIndex(embeddings=mg.stubs.StubEmbedding()))
    agent._native_tools = True
    agent._capabilities = ["tools"]
    agent.sync()
    gesehen = []
    stur = mg.SturesModell(mg.Messung(agent))
    agent.llm.chat_message = stur.chat_message

    def chat(messages, **kw):
        gesehen.append(list(messages))
        return stur.chat(messages, **kw)
    agent.llm.chat = chat
    stur.neue_runde("wa steht an?")
    agent.ask("wa steht an?")
    gesehen.clear()
    stur.neue_runde("warum hast du mir nicht geantwortet?")
    antwort = agent.ask("warum hast du mir nicht geantwortet?")
    system = gesehen[0][0]["content"] if gesehen else ""
    check("DIREKT-Prompt enthaelt den Laufbericht der letzten Runde",
          "LAUFBERICHT" in system and "wa steht an?" in system
          and "notiz_lesen" in system and "Schlussrunde" in system, system[-400:])
    check("DIREKT-Prompt enthaelt das abgeleitete Selbstbild mit allen Wegen",
          all(w in system for w in ("DIREKT:", "VAULT:", "RECHERCHE:",
                                    "SCHREIBEN:", "WERKZEUG:")))
    check("DIREKT-Antwort ist Prosa", ist_prosa_antwort(antwort.text), antwort.text[:80])


# ================================================================ Stufe 3
def fall_wegeverteilung(saetze) -> None:
    falsch = [f"{s.eingabe}: {s.weg} statt {s.erwartet}" for s in saetze
              if s.weg != s.erwartet]
    check("Jede Eingabe des Testsatzes nimmt den erwarteten Weg", not falsch,
          "; ".join(falsch))


def fall_recherche(saetze) -> None:
    netz, ohne = [s for s in saetze if s.gruppe == "recherche"]
    check("Recherche: kein Vektorabruf", netz.vektorabrufe == 0, str(netz.vektorabrufe))
    check("Kein Netz: ehrliches 'kann ich nicht'",
          "Netzzugang" in ohne.antwort_anfang, ohne.antwort_anfang)
    check("Kein Netz: 0 Vektorabrufe, 0 Werkzeuge, 0 Modellaufrufe",
          (ohne.vektorabrufe, ohne.werkzeugaufrufe, ohne.modellaufrufe) == (0, 0, 0),
          f"{ohne.vektorabrufe}/{ohne.werkzeugaufrufe}/{ohne.modellaufrufe}")


def fall_werkzeug(saetze) -> None:
    satz = next(s for s in saetze if s.gruppe == "werkzeug")
    check("WERKZEUG: genau EIN Aufruf", satz.werkzeugaufrufe == 1,
          str(satz.werkzeugaufrufe))
    check("WERKZEUG: kein Vektorabruf", satz.vektorabrufe == 0, str(satz.vektorabrufe))


def _agent_frisch():
    agent = mg.JarvisAgent(index=mg.KnowledgeIndex(embeddings=mg.stubs.StubEmbedding()))
    agent._native_tools = True
    agent._capabilities = ["tools"]
    agent.sync()
    return agent


def fall_werkzeug_fehlschlag() -> None:
    """Scheitert der eine Aufruf, sagt die Antwort das - kein zweiter Versuch."""
    agent = _agent_frisch()
    angeboten = []

    def chat_message(messages, tools=None, **kw):
        namen = sorted(t["function"]["name"] for t in (tools or []))
        angeboten.append(namen)
        if not namen:
            return {"role": "assistant", "content": "Es sind 5 Notizen."}
        return {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "notiz_lesen", "arguments": {"name": "Gibt_Es_Nicht"}}}]}
    agent.llm.chat_message = chat_message
    agent.llm.chat = lambda messages, **kw: "Es sind 5 Notizen."
    antwort = agent.ask("Wie viele Notizen habe ich?")
    ausgefuehrt = [c.werkzeug for c in antwort.tool_calls]
    check("WERKZEUG: nur ein Aufruf, auch wenn er scheitert", len(ausgefuehrt) == 1,
          str(ausgefuehrt))
    check("WERKZEUG: die Antwort nennt das Scheitern",
          "gescheitert" in antwort.text, antwort.text[-160:])
    erste = set(angeboten[0]) if angeboten else set()
    check("WERKZEUG: keine schreibenden, keine Netz- und keine Suchwerkzeuge",
          not (erste & {"notiz_erstellen", "notiz_loeschen", "web_suchen",
                        "notizen_suchen"}) and "struktur_anzeigen" in erste,
          str(sorted(erste))[:160])


def fall_vault_nur_lesen() -> None:
    agent = _agent_frisch()
    angeboten = []

    def chat_message(messages, tools=None, **kw):
        angeboten.append({t["function"]["name"] for t in (tools or [])})
        # Ein Modell, das auf VAULT trotzdem loeschen will:
        return {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "notiz_loeschen", "arguments": {"name": "Einkauf"}}}]}
    agent.llm.chat_message = chat_message
    agent.llm.chat = lambda messages, **kw: "In deinen Notizen steht nichts dazu."
    antwort = agent.ask("wa steht an?")
    check("VAULT: nur lesende Werkzeuge ohne Netz im Angebot",
          angeboten and not (angeboten[0] & {"notiz_loeschen", "notiz_erstellen",
                                             "web_suchen", "web_lesen"})
          and "notiz_lesen" in angeboten[0])
    check("VAULT: ein trotzdem gerufenes Schreibwerkzeug wird abgewiesen, nicht ausgefuehrt",
          (mg.VAULT / "00_Inbox" / "Einkauf.md").exists()
          and not antwort.tool_calls and agent.letzte_runde.abgewiesen >= 1,
          f"{len(antwort.tool_calls)} ausgefuehrt, {agent.letzte_runde.abgewiesen} abgewiesen")
    check("VAULT: auch dann endet die Runde mit einer Antwort aus der Schlussrunde",
          agent.letzte_runde.endete_mit == "schlussrunde"
          and antwort.text.startswith("In deinen Notizen"), antwort.text[:80])


def fall_recherche_ohne_aufruf_wird_benannt() -> None:
    """Gemessen am echten Modell: 'im Internet gesucht' - ohne einen Aufruf."""
    agent = _agent_frisch()
    mg.netz_attrappe(agent)
    behauptung = "Ich habe im Internet gesucht: Allplan braucht 32 GB RAM."
    agent.llm.chat_message = lambda messages, tools=None, **kw: {
        "role": "assistant", "content": behauptung}
    agent.llm.chat = lambda messages, **kw: behauptung
    antwort = agent.ask("Was sind die Systemvoraussetzungen von Allplan 2026?")
    check("RECHERCHE ohne Netzaufruf: die Antwort sagt, dass nicht recherchiert wurde",
          "keine erfolgreiche Netzsuche" in antwort.text, antwort.text[-160:])


def fall_bestaetigung_bleibt_handlung() -> None:
    from jarvis.gespraech.triage import Triage
    wahl = Triage().einordnen("ok", verlauf=True, bestaetigung=True)
    check("'ok' nach eigenem Vorschlag ist SCHREIBEN (alter Pfad), kein Gruss",
          wahl.text == "schreiben", wahl.text)


# ================================================================ Stufe 4
def fall_aufrufschluessel() -> None:
    from jarvis.gespraech.schleifenschutz import aufrufschluessel
    gleich = [
        (("notiz_lesen", {"name": "Einkauf"}), ("notiz_lesen", {"name": " einkauf "})),
        (("notiz_lesen", {"name": "00_Inbox\\Einkauf.md"}),
         ("notiz_lesen", {"name": "00_inbox/einkauf"})),
        (("notizen_suchen", {"frage": "a  b", "anzahl": 5}),
         ("notizen_suchen", {"anzahl": 5, "frage": "A b"})),
    ]
    falsch = [f"{a} != {b}" for a, b in gleich
              if aufrufschluessel(*a) != aufrufschluessel(*b)]
    check("Kanonischer Schluessel: Schreibweise, Pfadtrenner, Reihenfolge egal",
          not falsch, "; ".join(falsch))
    check("Verschiedene Notizen bleiben verschieden",
          aufrufschluessel("notiz_lesen", {"name": "A"})
          != aufrufschluessel("notiz_lesen", {"name": "B"}))


def fall_testsatz_ohne_wiederholung(saetze) -> None:
    identisch = [(s.eingabe, s.identische_aufrufe) for s in saetze if s.identische_aufrufe]
    check("Kein identischer Werkzeugaufruf im Testsatz ausgefuehrt", not identisch,
          str(identisch))
    abgewiesen = sum(s.abgewiesen for s in saetze)
    check("Abweisungen werden getrennt gezaehlt (das sture Modell wiederholt sich)",
          abgewiesen > 0, str(abgewiesen))


def fall_abweisung_kostet_keinen_schritt() -> None:
    """Abweisungen sind kostenlos - aber gedeckelt."""
    agent = _agent_frisch()
    schritte = []

    def chat_message(messages, tools=None, **kw):
        if not tools:
            return {"role": "assistant", "content": "Einkauf: Milch."}
        schritte.append(1)
        return {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "notiz_lesen", "arguments": {"name": "Einkauf"}}}]}
    agent.llm.chat_message = chat_message
    agent.llm.chat = lambda messages, **kw: "Einkauf: Milch."
    antwort = agent.ask("Was steht in der Einkaufsnotiz?")
    deckel = agent.cfg.max_abweisungen
    check("Derselbe Aufruf laeuft genau einmal",
          [c.werkzeug for c in antwort.tool_calls] == ["notiz_lesen"],
          str([c.werkzeug for c in antwort.tool_calls]))
    check(f"Nach {deckel} Abweisungen ist Schluss (Deckel)",
          agent.letzte_runde.abgewiesen == deckel, str(agent.letzte_runde.abgewiesen))
    check("Deckel fuehrt in die Schlussrunde, nicht ins Leere",
          agent.letzte_runde.endete_mit == "schlussrunde" and "Milch" in antwort.text,
          f"{agent.letzte_runde.endete_mit}: {antwort.text[:60]}")
    check("Modellrunden = 1 Ausfuehrung + Abweisungen - kein Schritt verloren",
          len(schritte) == 1 + deckel, str(len(schritte)))


def fall_abweisung_nennt_ergebnis() -> None:
    from jarvis.gespraech.schleifenschutz import Schleifenschutz
    from jarvis.tools import ToolResult
    schutz = Schleifenschutz(schwelle=0.6, deckel=3)
    schutz.vermerken("notizen_suchen", {"frage": "offene Aufgaben To-do wa steht an"},
                     ToolResult(True, "3 Treffer für 'offene Aufgaben'",
                                {"treffer": ["Aufgabenliste"]}))
    grund = schutz.pruefen("notizen_suchen",
                           {"frage": "offene Aufgaben To-do wa steht an bitte"})
    check("Nahezu gleiche Suche wird vor der Ausfuehrung abgewiesen", bool(grund))
    check("Die Abweisung gibt das vorliegende Ergebnis zurueck",
          "Aufgabenliste" in grund, grund[:120])
    check("Eine enger gefasste Suche laeuft (Jaccard 0.4 < 0.6)",
          not schutz.pruefen("notizen_suchen", {"frage": "wa steht an"}))


def fall_kuerzen_behaelt_laufende_runde() -> None:
    agent = _agent_frisch()
    budget = agent.cfg.prompt_char_budget
    system = {"role": "system", "content": "SYSTEMPROMPT " * 200}
    verlauf = []
    for i in range(6):
        verlauf += [{"role": "user", "content": f"alte Frage {i} " + "x" * 900},
                    {"role": "assistant", "content": f"alte Antwort {i} " + "y" * 900}]
    frage = {"role": "user", "content": "Was steht in den Aufgabenlisten?"}
    agent._rundenfrage_msg = frage
    laufend = []
    for i in range(6):
        laufend += [{"role": "assistant", "content": "", "tool_calls": [{}]},
                    {"role": "tool", "name": "notiz_lesen",
                     "content": f'{{"ok": true, "meldung": "Inhalt von \'Notiz_{i}\'", '
                                f'"inhalt": "' + "z" * 2500 + '"}'}]
    gekuerzt = agent._schleife_kuerzen([system] + verlauf + [frage] + laufend)
    tools = [m for m in gekuerzt if m.get("role") == "tool"]
    check("Kuerzen: Systemprompt bleibt vollstaendig (keine Kuerzung am Anfang)",
          gekuerzt[0]["content"] == system["content"])
    check("Kuerzen: die Frage der Runde bleibt", frage in gekuerzt)
    check("Kuerzen: KEIN Werkzeugergebnis der laufenden Runde faellt weg",
          len(tools) == 6, str(len(tools)))
    check("Kuerzen: verdichtete Ergebnisse behalten den Notiznamen",
          all(f"Notiz_{i}" in t["content"] for i, t in enumerate(tools)))
    check("Kuerzen: fruehere Zuege weichen zuerst",
          not any("alte Frage 0" in (m.get("content") or "") for m in gekuerzt))
    groesse = sum(len(m.get("content") or "") for m in gekuerzt)
    check("Kuerzen: bleibt im Budget", groesse <= budget, f"{groesse}/{budget}")


def fall_geloeste_probleme_abgesichert() -> None:
    """§4: Fehlalarm im Modellregister hatte keine eigene Pruefung."""
    from jarvis.kern.modelle import Modell, Modellregister

    class Konf:
        host = "http://server:11434"
        host_alternative = ""
        llm_model = "gross:27b"
        fast_model = ""
        embedding_model = "bge-m3"
    reg = Modellregister(cfg=Konf())
    reg.hosts = {Konf.host: True}
    reg.modelle = {f"{Konf.host}|{n}": Modell(name=n, host=Konf.host, parameter=p,
                                              rolle=r)
                   for n, p, r in (("gross:27b", 27.3, "gross"),
                                   ("klein:14b", 14.0, "klein"),
                                   ("bge-m3:latest", 0.57, "einbettung"))}
    befunde = reg.pruefen()
    check("Modellregister: 'bge-m3' findet 'bge-m3:latest' - kein Fehlalarm",
          not any("EMBEDDING_MODEL" in b for b in befunde), "; ".join(befunde))


# ================================================================== Ablauf
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        fall_was_als_antwort_zaehlt()
        fall_wegewahl_deterministisch()
        saetze = mg.messen()
        fall_testsatz_antwortpflicht(saetze)
        fall_meta_ohne_abruf(saetze)
        fall_kombiniert(saetze)
        fall_direkt_bekommt_laufbericht()
        fall_wegeverteilung(saetze)
        fall_recherche(saetze)
        fall_werkzeug(saetze)
        fall_werkzeug_fehlschlag()
        fall_vault_nur_lesen()
        fall_recherche_ohne_aufruf_wird_benannt()
        fall_bestaetigung_bleibt_handlung()
        fall_aufrufschluessel()
        fall_testsatz_ohne_wiederholung(saetze)
        fall_abweisung_kostet_keinen_schritt()
        fall_abweisung_nennt_ergebnis()
        fall_kuerzen_behaelt_laufende_runde()
        fall_geloeste_probleme_abgesichert()

        agent = mg.JarvisAgent(index=mg.KnowledgeIndex(
            embeddings=mg.stubs.StubEmbedding()))
        agent._native_tools = True
        agent._capabilities = ["tools"]
        agent.sync()
        fall_schlussrunde_fuehrt_nichts_aus(agent)
        fall_tatsachen_stehen_ueber_der_schlussrunde(agent)
        fall_fehlschlag_ohne_erfolg(agent)
    finally:
        shutil.rmtree(mg.TMP, ignore_errors=True)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
