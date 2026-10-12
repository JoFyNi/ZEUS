"""Online nachschlagen - das Werkzeug, das bis zum 21.09. fehlte.

Der Anlass steht im Protokoll vom 21.09., 12:58 Uhr. Josh bat um eine
Recherche zu einer Hardwarefrage. Zeus rief dreimal `notizen_suchen` auf,
meldete "Erledigt: 2 Treffer für 'Allplan Systemvoraussetzungen'" und tat
damit so, als habe er recherchiert. Erst nach dem dritten Nachhaken - in
Großbuchstaben - gab er zu:

    "Ich habe kein Werkzeug zum Online-Surfen. Meine Werkzeuge greifen auf
     deine Obsidian-Vault zu - aber nicht ins Internet. Die letzten Runden
     war das mein Fehler."

Zwei Fehler auf einmal, und der zweite ist der schwerere:

1. Die Fähigkeit fehlte.
2. Statt das zu sagen, griff der Agent zum nächstähnlichen Werkzeug und
   meldete Erfolg. Das ist der stille Fehlschlag aus ENTWICKLUNG.md Muster
   2.1 - nur diesmal nicht im Code, sondern im Verhalten.

Punkt 1 behebt diese Datei. Punkt 2 behebt sie mit: Wenn das Netz nicht
erreichbar oder abgeschaltet ist, sagt das Werkzeug das **als Fehler**. Ein
Werkzeug, das leere Ergebnisse als Erfolg zurückgibt, erzeugt genau wieder
die Behauptung, die hier abgestellt werden soll.

Zur Auswahl der Quellen, und das ist eine Einschränkung, die man kennen muss:

Der erste Versuch ging über die HTML-Oberfläche von DuckDuckGo. Die antwortet
mit einer Bot-Erkennung ("anomaly"), und die wird hier NICHT umgangen - das
wäre ein Missbrauch fremder Infrastruktur und würde beim nächsten Umbau der
Seite ohnehin brechen.

Benutzt werden stattdessen Schnittstellen, die für Programme gedacht sind und
ohne Konto auskommen:

    api.duckduckgo.com      Kurzantworten zu Begriffen (Instant Answer)
    de.wikipedia.org/w/api  Volltextsuche in der Wikipedia

Damit bleibt die Leitlinie "lokal, ohne Konto" gewahrt. Der Preis ist ehrlich
zu nennen: Das ist **Nachschlagen, keine allgemeine Websuche.** Für eine Frage
wie "welche Systemvoraussetzungen hat Allplan 2026" liefert es den Artikel
über Allplan, nicht die Herstellertabelle. Dafür gibt es `web_lesen` - sobald
die Adresse bekannt ist, wird jede Seite gelesen.

Wer echte Websuche will, braucht einen Anbieter mit Schlüssel (Brave, Tavily).
Das ist bewusst nicht eingebaut: Es bräche "ohne Konto", und die Entscheidung
darüber gehört dem Nutzer, nicht dem Agenten.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import List

from .ergebnis import ToolResult

# Wie lange auf eine Antwort gewartet wird. Kurz gehalten: Eine Recherche,
# die eine Minute braucht, blockiert die Werkzeugschleife und laeuft in das
# Zeitlimit des Modellaufrufs.
FRIST = 20

# Ohne Kennung antworten viele Seiten mit 403. Ein ehrlicher, sprechender
# Name ist die hoeflichere Variante gegenueber einem getarnten Browser.
KENNUNG = "Mozilla/5.0 (compatible; Zeus-Wissensagent/1.0)"

# Obergrenze je gelesener Seite. Eine ganze Seite Fliesstext sprengt sonst
# das Kontextfenster - genau der Fehler, der am 18.09. behoben wurde.
MAX_SEITE = 6000

_TREFFER = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<titel>.*?)</a>',
    re.DOTALL | re.IGNORECASE)
_AUSZUG = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(?P<text>.*?)</a>',
    re.DOTALL | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_SKRIPT = re.compile(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>",
                     re.DOTALL | re.IGNORECASE)


def _text(roh: str) -> str:
    """HTML zu lesbarem Text - ohne Fremdpaket."""
    ohne = _TAGS.sub(" ", roh or "")
    return re.sub(r"\s+", " ", html.unescape(ohne)).strip()


def _holen(url: str) -> str:
    anfrage = urllib.request.Request(url, headers={
        "User-Agent": KENNUNG,
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(anfrage, timeout=FRIST) as antwort:
        roh = antwort.read(1_500_000)
    for kodierung in ("utf-8", "latin-1"):
        try:
            return roh.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return roh.decode("utf-8", errors="replace")


class NetzWerkzeuge:
    """Nachschlagen im Internet. Liest nur - es wird nie etwas gesendet."""

    # --------------------------------------------------------------- Suchen
    def web_suchen(self, frage: str, anzahl: int = 5) -> ToolResult:
        """Schlaegt einen Begriff im Internet nach (Kurzantwort + Wikipedia)."""
        if not getattr(self.cfg, "web_enabled", True):
            return ToolResult(
                False,
                "Der Internetzugriff ist abgeschaltet (WEB_ENABLED=false in der .env). "
                "Sag dem Nutzer, dass du nicht online nachsehen kannst - such NICHT "
                "ersatzweise in der Vault, das beantwortet seine Frage nicht.")
        frage = str(frage or "").strip()
        if not frage:
            return ToolResult(False, "Kein Suchbegriff übergeben. Beispiel: "
                                     'web_suchen(frage="Allplan Systemvoraussetzungen").')
        anzahl = max(1, min(int(anzahl or 5), 10))

        # Wiederholtes Suchen mit leicht anderer Formulierung bringt bei
        # diesen Quellen nichts - sie kennen den Begriff oder nicht. Am 21.09.
        # stellte das Modell acht Varianten derselben Frage und lief dabei in
        # die Sperre der Wikipedia (429). Nach zwei Fehlschlaegen im selben
        # Auftrag wird deshalb abgebrochen.
        self._netz_fehlschlaege = getattr(self, "_netz_fehlschlaege", 0)
        if self._netz_fehlschlaege >= 2:
            return ToolResult(
                False,
                "Schon zweimal nichts gefunden. Weitere Umformulierungen helfen "
                "hier nicht. SAG DEM NUTZER, dass die Recherche nicht geglückt "
                "ist, und frag ihn nach der Adresse der richtigen Seite - die "
                "liest du dann mit web_lesen.")

        treffer: List[dict] = []
        fehler: List[str] = []

        # --- 0. Echte Suchmaschine, falls ein Schluessel hinterlegt ist -----
        # Ohne Konto gibt es keine allgemeine Websuche: DuckDuckGo-HTML,
        # Mojeek, Startpage und Brave weisen Programme ab (Bot-Erkennung,
        # JavaScript-Zwang, 429). Wer Herstellerangaben und Tagesaktuelles
        # braucht, kommt um einen Anbieter mit Schluessel nicht herum. Die
        # Entscheidung darueber gehoert dem Nutzer - deshalb nur der Anschluss.
        schluessel = str(getattr(self.cfg, "web_such_schluessel", "") or "").strip()
        if schluessel:
            try:
                anfrage = urllib.request.Request(
                    "https://api.search.brave.com/res/v1/web/search?count="
                    f"{anzahl}&q=" + urllib.parse.quote_plus(frage),
                    headers={"Accept": "application/json",
                             "X-Subscription-Token": schluessel})
                with urllib.request.urlopen(anfrage, timeout=FRIST) as antwort:
                    daten = json.loads(antwort.read().decode("utf-8"))
                for eintrag in (daten.get("web", {}).get("results") or [])[:anzahl]:
                    treffer.append({
                        "titel": _text(eintrag.get("title", ""))[:160],
                        "adresse": eintrag.get("url", ""),
                        "auszug": _text(eintrag.get("description", ""))[:320],
                        "quelle": "Websuche",
                    })
            except Exception as exc:
                fehler.append(f"Websuche: {type(exc).__name__}: {exc}")

        # --- 1. Kurzantwort (DuckDuckGo Instant Answer, offizielle API) -----
        try:
            if len(treffer) >= anzahl:
                raise StopIteration
            roh = _holen("https://api.duckduckgo.com/?format=json&no_html=1&q="
                         + urllib.parse.quote_plus(frage))
            daten = json.loads(roh)
            text = (daten.get("AbstractText") or "").strip()
            if text:
                treffer.append({
                    "titel": daten.get("Heading") or frage,
                    "adresse": daten.get("AbstractURL") or "",
                    "auszug": text[:600],
                    "quelle": daten.get("AbstractSource") or "DuckDuckGo",
                })
            for eintrag in (daten.get("RelatedTopics") or []):
                if len(treffer) >= anzahl:
                    break
                if not isinstance(eintrag, dict) or not eintrag.get("Text"):
                    continue
                treffer.append({
                    "titel": eintrag.get("Text", "")[:90],
                    "adresse": (eintrag.get("FirstURL") or ""),
                    "auszug": eintrag.get("Text", "")[:300],
                    "quelle": "DuckDuckGo",
                })
        except StopIteration:
            pass
        except Exception as exc:
            fehler.append(f"Kurzantwort: {type(exc).__name__}: {exc}")

        # --- 2. Wikipedia-Volltextsuche (offizielle API) --------------------
        if len(treffer) < anzahl:
            for sprache in ("de", "en"):
                if len(treffer) >= anzahl:
                    break
                try:
                    roh = _holen(
                        f"https://{sprache}.wikipedia.org/w/api.php?action=query"
                        f"&list=search&format=json&srlimit={anzahl}&srsearch="
                        + urllib.parse.quote_plus(frage))
                    for eintrag in json.loads(roh)["query"]["search"]:
                        if len(treffer) >= anzahl:
                            break
                        titel = eintrag.get("title", "")
                        treffer.append({
                            "titel": titel,
                            "adresse": f"https://{sprache}.wikipedia.org/wiki/"
                                       + urllib.parse.quote(titel.replace(" ", "_")),
                            "auszug": _text(eintrag.get("snippet", ""))[:300],
                            "quelle": f"Wikipedia ({sprache})",
                        })
                except Exception as exc:
                    fehler.append(f"Wikipedia {sprache}: {type(exc).__name__}: {exc}")

        if not treffer:
            # Ausdruecklich ein FEHLER, kein leeres Ergebnis. Ein Werkzeug, das
            # "nichts gefunden" meldet, wo in Wahrheit die Leitung fehlt, laedt
            # das Modell dazu ein, sich etwas auszudenken - genau der Fall vom
            # 21.09.
            self._netz_fehlschlaege += 1
            grund = "; ".join(fehler)[:160] if fehler else "keine passenden Einträge"
            return ToolResult(
                False,
                f"Nichts gefunden zu '{frage}' ({grund}). Behaupte nichts über das "
                f"Ergebnis. Kennst du eine konkrete Adresse, nimm web_lesen.",
                {"fehler": fehler})

        self._netz_fehlschlaege = 0
        zeilen = []
        for i, t in enumerate(treffer, 1):
            zeilen.append(f"{i}. {t['titel']}  [{t['quelle']}]\n"
                          f"   {t['adresse']}\n"
                          f"   {t['auszug']}")
        return ToolResult(
            True,
            f"{len(treffer)} Fundstellen zu '{frage}'.",
            {"treffer": treffer, "uebersicht": "\n".join(zeilen),
             "hinweis": "Das ist NACHSCHLAGEN, keine vollständige Websuche: Die "
                        "Quellen sind Kurzantworten und Wikipedia. Für Hersteller-"
                        "angaben, Preise oder tagesaktuelle Daten reicht das nicht - "
                        "sag das dem Nutzer, statt es zu überspielen. Kennst du die "
                        "Adresse der richtigen Seite, lies sie mit web_lesen.",
             "fehler": fehler},
        )

    # ---------------------------------------------------------------- Lesen
    def web_lesen(self, url: str, max_zeichen: int = MAX_SEITE) -> ToolResult:
        """Holt eine Internetseite und gibt ihren Text zurück."""
        if not getattr(self.cfg, "web_enabled", True):
            return ToolResult(False, "Die Internetsuche ist abgeschaltet "
                                     "(WEB_ENABLED=false in der .env).")
        url = str(url or "").strip()
        if not url:
            return ToolResult(False, "Keine Adresse übergeben.")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        grenze = max(500, min(int(max_zeichen or MAX_SEITE), 20000))

        try:
            seite = _holen(url)
        except Exception as exc:
            return ToolResult(
                False,
                f"Seite nicht abrufbar: {type(exc).__name__}: {exc}. "
                f"Sag dem Nutzer, dass du sie NICHT gelesen hast.",
                {"adresse": url})

        titel = ""
        treffer = re.search(r"<title[^>]*>(.*?)</title>", seite,
                            re.DOTALL | re.IGNORECASE)
        if treffer:
            titel = _text(treffer.group(1))[:200]

        text = _text(_SKRIPT.sub(" ", seite))
        gekuerzt = text[:grenze]
        return ToolResult(
            True,
            f"Gelesen: {titel or url} ({len(text)} Zeichen, "
            f"{len(gekuerzt)} übergeben).",
            {"adresse": url, "titel": titel, "inhalt": gekuerzt,
             "vollstaendig": len(text) <= grenze},
        )
