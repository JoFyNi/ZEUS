# ZEUS – strukturbewusster Second Brain für Obsidian

Weiterentwicklung deines RAG-Skripts. Zwei Probleme waren zu lösen:

1. **Der Agent kannte die Struktur deiner Vault nicht.** Er hat Dateien als flachen
   Textstrom gelesen – Ordner, Frontmatter, Tags und Links landeten nie in den
   Metadaten. Deshalb konnte er den Ordner `Deep Learning` nicht sehen und hat die
   neue Notiz in die Wurzel geschrieben (und den Tippfehler „Depp“ mitgenommen).
2. **Es fehlte ein Gesprächs-Interface.** Jetzt gibt es einen Terminal-Chat mit
   Streaming, Sprachausgabe und Spracheingabe.

**Version 8** macht drei Dinge stabiler und öffnet den Code für den Agenten selbst.

### Neu: Überschreiben braucht den exakten Namen

Der Prüfbericht `Code-Prüfung 2026-08-24` landete in
`Deep Learning/Lauffähiger_Code_für_Agentenarchitekturen.md` – und **überschrieb
diese Notiz**. Ursache: Die Prüfung „gibt es schon eine Notiz mit diesem Titel?"
lief über `resolve_note`, und das sucht notfalls **semantisch** weiter. Es fand
eine thematisch verwandte Notiz, und der Bericht ersetzte deren Inhalt.

Zwei Änderungen, beide nötig:

1. **`notiz_exakt(name)`** – nur Pfad, Dateiname, Titel oder Alias, keine
   Ähnlichkeit, keine Semantik. Alle Stellen, die „existiert das schon?" fragen,
   benutzen jetzt diese Funktion.
2. **`notiz_ueberschreiben` verweigert ungefähre Treffer.** `resolve_note` merkt
   sich, über welche Stufe es getroffen hat. Kam der Treffer aus Wortüberlappung,
   Tippfehlerkorrektur oder semantischer Suche, wird das Ganz-Ersetzen abgelehnt:
   *„'X' trifft keine Notiz genau – am ehesten passt 'Y'. Überschreiben ersetzt
   den gesamten Inhalt, deshalb braucht es hier den exakten Namen oder Pfad."*
   Das schützt auch vor dem Modell, nicht nur vor meinem eigenen Code.
   **Ergänzen und Abschnitt-Ersetzen bleiben tolerant** – nur das Ganz-Ersetzen
   ist streng, weil nur dort alles verloren geht.

### Neu: Doppelte Codebasis wird erkannt

Ein `--codereview --tief` meldete 36 Dateien und 23 471 Zeilen – jede Datei
doppelt, weil der Code sowohl im Projektordner als auch in einem Unterordner lag.
Zwei der sechs Modellabschnitte gingen an dieselbe Datei. Jetzt werden Dateien
über eine Prüfsumme ihres Inhalts entdoppelt; die Doppel stehen unter „Nicht
geprüft" mit dem Hinweis, dass zwei Kopien bedeuten: *Änderungen an der einen
wirken nicht.*

Dazu ein weiterer Filter für Modellbefunde: Eine Fehlerbehauptung („verschluckt",
„ungeprüft", „ohne Prüfung") auf einer **Zuweisungszeile** wird verworfen. Genau
die zwei Befunde, die beim ersten `--tief`-Lauf durchkamen, waren das:
`self.status = "deaktiviert"` und `gesehen.add(datei)`.

---


### Neu: Mehrzeilige Eingabe

Ein eingefügter Anweisungsprompt wurde **zeilenweise** gelesen – der Agent
beantwortete Zeile 1, ohne die Regeln aus Zeile 5 zu kennen. `input()` liefert
nun einmal genau eine Zeile.

Drei Wege, alle drin:

1. **Automatisch.** Ein Hintergrund-Thread liest stdin in eine Warteschlange.
   Nach der ersten Zeile wird 120 ms gewartet: Kommt in dieser Zeit mehr, gehört
   es zum selben Einfügevorgang. Getippt wird nie so schnell. Der Agent meldet
   „*8 Zeilen als EINE Eingabe übernommen*", damit man sieht, dass es griff.
2. **Von Hand.** Eine Zeile mit drei Anführungszeichen öffnet einen Block, eine
   zweite schließt ihn – für den Fall, dass die Erkennung danebenliegt.
3. **Aus Datei.** `/prompt <pfad>` schickt eine ganze Textdatei als eine
   einzige Eingabe. Der sicherste Weg für lange Anweisungsblätter; im Ordner
   `prompts/` liegt `selbstpruefung.txt` als fertiges Beispiel.

*Warum ein Thread und nicht `select()`?* Python puffert stdin intern. Nach einem
`readline()` können die restlichen eingefügten Zeilen in **Pythons** Puffer
liegen statt im Betriebssystem – `select()` meldet dann „nichts da", obwohl der
Text längst angekommen ist. Der Thread liest stur weiter und umgeht das.

Die leere Eingabe bleibt leer und öffnet weiterhin das Mikrofon.

---


### Neu: Stufe 2 – der Messstand

Dein eigener Stufenplan sagt: *„Stufe 2 — Evaluation. **Nicht überspringen.**"*
Genau die fehlte. Gebaut waren Schleife (1), Gedächtnis (3), Reflexion (4) und
Fertigkeiten (5) – die Messung dazwischen nicht. Damit war unbekannt, ob
Lektionen die Trefferquote heben **oder senken**. Deine Notiz nennt auch, warum
das kein Randproblem ist: *„Oft ist sie gesunken, weil sich der Kontext mit
mittelmäßigen Lektionen füllt und das Modell ablenkt."*

```bash
python jarvis_run.py --evaluieren              # A/B: mit und ohne Gedächtnis
python jarvis_run.py --evaluieren --nur 5      # erst mal fünf Aufgaben
python jarvis_run.py --evaluieren --variante mit
/messen        /messen mit 5       /messen verlauf
```

Beim ersten Aufruf entsteht `_Lernen/Pruefaufgaben.md` als Vorlage. Dort gehören
20–30 **echte** Aufgaben aus deinem Alltag hinein, jede mit prüfbaren
Bedingungen:

```markdown
## A3 Notiz anlegen
frage: Lege eine Notiz "Prüfnotiz Messstand" mit einer kurzen Zusammenfassung an.
pruefung: notiz_existiert: Prüfnotiz Messstand
pruefung: ordner_ist: Prüfnotiz Messstand -> _System
pruefung: kein_fehlschlag:
```

Elf Bedingungen stehen bereit: `notiz_existiert`, `notiz_fehlt`, `ordner_ist`,
`enthaelt`, `link_vorhanden`, `antwort_enthaelt`, `antwort_enthaelt_nicht`,
`werkzeug_benutzt`, `kein_fehlschlag`, `keine_toten_links`, `quelle_genannt`.

**Drei Entwurfsentscheidungen, jede aus einem Fehler heraus:**

1. **Erfolgsbedingungen sind Daten, kein Modellurteil.** Ein Modell, das seine
   eigene Leistung bewertet, misst nichts. Geprüft wird im Code.
2. **Gemessen wird auf einer Kopie.** Ein Durchlauf legt Notizen an und
   verschiebt sie. Auf der echten Vault wäre die Messung ein Eingriff – und der
   zweite Durchlauf fände andere Ausgangsbedingungen vor. A/B wäre wertlos,
   weil A die Welt für B verändert hätte.
3. **Das Urteil beschönigt nicht.** Sinkt die Quote mit Gedächtnis, steht das so
   im Bericht – samt der richtigen Gegenmaßnahme („erst die Abrufqualität
   verbessern, nicht mehr Lektionen hinzufügen"). Bei unter zehn Aufgaben steht
   ein Vorbehalt dabei, weil ein einzelner Umschlag die Quote dann zweistellig
   verschiebt.

Der Verlauf landet in `.jarvis/messungen.jsonl`, der Bericht als Notiz unter
`_System/Code/Messungen/`.

### Neu: Lektionen müssen sich bewähren

Bisher war eine Lektion eine Textzeile – einmal geschrieben, für immer im
Kontext. Ob sie je geholfen hat, war nicht feststellbar. Jetzt trägt jede ihre
Bilanz:

```markdown
- Vor dem Verschieben immer den Zielordner prüfen.  <!-- status: bestaetigt · verwendet: 7 · bewaehrt: 6 -->
```

| Status | Bedeutung | Übergang |
|---|---|---|
| `vorlaeufig` | Hypothese, wird benutzt | Startzustand |
| `bestaetigt` | Hat sich bewährt, kommt zuerst in den Prompt | ab 3 Einsätzen und ≥ 60 % Erfolg |
| `verworfen` | Bleibt in der Datei, aber draußen aus dem Kontext | ab 5 Einsätzen und < 30 % Erfolg |

Das ist der Tiefpass aus deiner Notiz (§4.2): *„Wenn eine Korrektur sofort eine
harte Regel erzeugt, hast du η zu hoch gewählt."* Ein einzelner Erfolg macht
deshalb noch keine Regel. Alte Zeilen ohne Zähler bleiben lesbar und starten als
vorläufig. `/lektionen` zeigt die Bilanz.

---


### Neu: Stimme folgt der Sprache

Deutsche Ausgabe, englische Stimme – die las den deutschen Text mit englischer
Aussprache vor. Die Ursache war eine **fest verdrahtete englische Vorgabe**:
`PIPER_VOICE` stand auf `en_GB-alan-medium`, egal was `LANGUAGE` sagte. Beim
Sprachwechsel bleibt so zwangsläufig die falsche Stimme stehen.

Drei Änderungen:

1. **`PIPER_VOICE` ist standardmäßig leer** und bedeutet „nimm die beste
   installierte Stimme zur eingestellten `LANGUAGE`". Bei mehreren gewinnt die
   empfohlene, sonst die höhere Qualitätsstufe (`high` > `medium` > `low`).
2. **Ein Konflikt wird erkannt und gemeldet, nicht verschwiegen.** Steht eine
   Stimme fremder Sprache in der `.env` und liegt eine passende vor, gewinnt die
   passende – und der Status sagt: *„'en_GB-alan-medium' passt nicht zu
   LANGUAGE=Deutsch – nutze stattdessen 'de_DE-thorsten-high'. Dauerhaft:
   PIPER_VOICE=de_DE-thorsten-high"*.
3. **`/sprache Deutsch` zieht die Stimme mit** – vorher wurde nur die
   Antwortsprache umgestellt. Ist keine passende Stimme da, nennt er den
   Befehl zum Nachladen statt kommentarlos falsch weiterzusprechen.

**Deutsche männliche piper-Stimmen:**

| Kennung | Klang |
|---|---|
| `de_DE-thorsten-high` | Ruhig, klar, höchste Qualitätsstufe – **die Empfehlung** |
| `de_DE-thorsten-medium` | Wie oben, schneller und sparsamer |
| `de_DE-karlsson-low` | Dunkler und getragener, nur Stufe `low` |
| `de_DE-pavoque-low` | Sachlich-trocken, nur Stufe `low` |
| `de_DE-thorsten_emotional-medium` | Mit Stimmungen, braucht `PIPER_SPEAKER` (4 = neutral, 7 = whisper) |

Feinschliff für den Butler-Ton: `PIPER_LENGTH_SCALE=1.06` (etwas gesetzter),
`PIPER_NOISE_SCALE=0.6` und `PIPER_NOISE_W=0.7` (gleichmäßiger, weniger
Schwankung). Fertige Vorlage: **`env_deutsch_maennlich.txt`**.

---


### Neu: Kein Befehl reißt mehr die Sitzung mit

`/tempo` flog mit einem Traceback aus dem Chat – und damit war das ganze
Gespräch weg. Ursache: `wmic` ist in **Windows 11 entfernt** worden.
`subprocess.run` wirft dann `FileNotFoundError`, und das ist ein **`OSError`,
kein `SubprocessError`** – es rutschte durch jedes `except
subprocess.SubprocessError` hindurch.

| Änderung | Wirkung |
|---|---|
| RAM-Ermittlung über `GlobalMemoryStatusEx` (ctypes) | Kein Unterprozess mehr, kann nicht fehlen. `wmic` bleibt nur noch als Rückfallebene für alte Systeme |
| Alle externen Aufrufe in `hardware.py` gehen durch **eine** Funktion | Fehlt das Programm, kommt leerer Text zurück statt einer Ausnahme |
| Schutzschicht um jeden Slash-Befehl und jede Antwort | Ein Fehler zeigt sich als Meldung, landet in `.jarvis/fehler.log` und als offener Punkt – die Sitzung läuft weiter |

**Und die eigentliche Lehre:** Der Code-Prüfer hätte das finden müssen und hat
es übersehen. Er prüfte `subprocess` nur auf ein fehlendes `timeout`. Neu ist
die Prüfung **„externer Aufruf ohne Absicherung gegen ein fehlendes Programm"**
– sie kennt den Unterschied zwischen `except subprocess.SubprocessError` (fängt
es *nicht*) und `except OSError` (fängt es). Der erste Lauf mit der neuen Regel
fand **sechs weitere** Stellen derselben Art: fünf in `voice.py` (fehlt `aplay`,
`espeak` oder `say`, stirbt der Sprech-Thread) und eine in `jarvis_run.py`
(fehlt `pip`, bricht der Start ab). Alle behoben.

### Neu: Ruhe beim ersten Diktieren

Beim ersten `/hoeren` erschienen drei Absätze über `HF_TOKEN`, Symlinks und den
Windows-Entwicklermodus. Nichts davon ist ein Problem – der Download läuft ohne
Konto, ohne Symlinks braucht der Zwischenspeicher nur etwas mehr Platz. Es sieht
nur aus, als sei etwas kaputt. Die Warnungen sind jetzt abgestellt.

---


### Neu: Wiederholungssperre und Aufgabenliste

Ein Lauf vom 22.08. zeigte vier Fehler in einem Bild. Das Modell wollte tote
Links entfernen, griff dafür zu `notizen_verknuepfen(nach="")` – und probierte
es **fünfmal identisch**.

| Fehler | Behebung |
|---|---|
| Es gab **kein Werkzeug**, tote Links zu entfernen – nur `links_pruefen(reparieren)`, das sie auf eine *ähnliche* Notiz umbiegt | Neu: `links_bereinigen(name, modus)` mit `entschaerfen` (wird Klartext), `entfernen`, `markieren` |
| `nach=""` meldete nur „Notiz '' nicht gefunden" | Die Meldung nennt jetzt das **gemeinte Werkzeug**: „Zum Entfernen toter Verweise: links_bereinigen" |
| `von` war eine **Liste**, daraus wurde per `str()` die Zeichenfolge `"['a', 'b']"` | Liste an einer Einzelwert-Stelle wird zentral erkannt und benannt |
| Nichts stoppte den identischen Aufruf | **Wiederholungssperre**: der zweite identische Aufruf wird nicht mehr ausgeführt, sondern beantwortet – samt Hinweis auf das richtige Werkzeug |

Die Sperre gilt **pro Auftrag**: im selben Auftrag nicht zweimal dasselbe, im
nächsten darf es wieder versucht werden.

### Neu: Offene Punkte werden geführt und abgehakt

```
/aufgaben              offene Punkte zeigen
/aufgaben abarbeiten   jeden Punkt nachprüfen und abhaken, was erledigt ist
/aufgaben leeren       erledigte Punkte entfernen
```

Drei Regeln, damit die Liste ehrlich bleibt:

1. **Eintragen ist automatisch.** Jeder fehlgeschlagene Werkzeugaufruf wird
   festgehalten – niemand muss daran denken, auch das Modell nicht.
2. **Abgehakt wird nur mit Beleg.** Nicht, weil das Modell „erledigt" sagt,
   sondern weil *derselbe* Vorgang später wirklich geklappt hat. Der Beleg ist
   die echte Werkzeugmeldung. `/aufgaben abarbeiten` prüft ohne Modell nach:
   Existiert die Notiz? Steht der Link wirklich in der Datei? Sind die toten
   Links weg?
3. **Ab drei Fehlversuchen: festgefahren.** Der Punkt wird nicht weiter blind
   wiederholt, sondern dir vorgelegt.

Die offenen Punkte stehen im Systemprompt („nicht erneut identisch versuchen")
und als Notiz unter `_Lernen/Offene_Punkte.md` – Zeus liest sie wie jede andere.
Maßgeblich ist das Journal unter `.jarvis/aufgaben.jsonl`; die Notiz ist die
lesbare Fassung und darf von Hand bearbeitet werden, ohne die Buchführung zu
beschädigen.

---


### Neu: Code-Prüfung, die nicht am Modell hängt

Der Versuch, Zeus per Prompt durch eine mehrstufige Selbstprüfung zu führen
(„erst `code_dateien`, dann `quellcode_lesen`, dann `code_suchen`"), endete
reproduzierbar nach dem **ersten** Werkzeug: Zeus bekam die Dateiliste, formulierte
sie in Prosa aus und hielt das für die Antwort. Ein 14B-Modell hält eine
Schrittfolge über mehrere Werkzeugrunden nicht durch – dieselbe Erfahrung wie bei
Schreibaufträgen, und dieselbe Lösung: **Die Schrittfolge gehört in Python.**

```bash
python jarvis_run.py --codereview            # statisch, Sekunden
python jarvis_run.py --codereview --tief     # zusätzlich das Modell, langsam
/codereview          /codereview tief 8      # dasselbe im Chat
```

**Ebene 1 – statisch, ohne Modell.** Der Python-Parser sucht Muster, die im Betrieb
nachweislich wehtun: verschluckte Schreibfehler, `open()` ohne `encoding` (auf
deutschem Windows cp1252 – ein Umlaut genügt), `subprocess` ohne `timeout`,
veränderliche Vorgabewerte, nacktes `except`. Diese Befunde sind keine Vermutung.
Die Einstufung richtet sich nach dem, was im `try` steht: ein verschlucktes
`write_text` ist **hoch**, ein verschluckter optionaler Import **gering** – sonst
melden vierzig gleich aussehende Treffer, und der eine, auf den es ankommt, geht
darin unter.

**Ebene 2 – mit Modell, überprüft.** Ausgewählte Abschnitte gehen **einzeln** ans
Modell, mit je einer kleinen Frage und JSON-Antwort. Jeder Befund muss die
beanstandete Zeile **wörtlich zitieren**; stimmt das Zitat nicht mit dem echten
Quelltext überein, wird der Befund **verworfen** und im Bericht unter „Verworfen"
gezählt. Damit kann nichts Erfundenes als Tatsache im Bericht landen.

Der Bericht landet als Notiz unter `_System/Code/Pruefungen/` – Zeus kann ihn also
selbst lesen und darauf aufbauen. Ein zweiter Lauf am selben Tag **ersetzt** den
Bericht, statt `…_2.md` danebenzulegen.

### Ordnerzuordnung: exakt vor ungefähr

Ein ausdrücklich genannter Ordner wurde bisher komplett durch die
Tippfehler-Erkennung geschickt (`match_folder`, Schwelle 0,6). Bei einem
einzelnen Namen ist das richtig – „Deep learning" soll „Deep Learning" treffen.
Bei einem **mehrstufigen Pfad** war es falsch: `_System/Code/Pruefungen` landete
in einem völlig fremden Ordner, weil irgendein vorhandener über der Schwelle lag,
und der angeforderte Unterordner entstand nie.

Jetzt wird **Stufe für Stufe** aufgelöst: exakt vorhanden gewinnt (auch bei
abweichender Groß-/Kleinschreibung), sonst ein Tippfehler-Treffer **unter
demselben Elternordner**, sonst wird die Stufe wörtlich genommen und angelegt.
„Depp Learning" trifft weiterhin „Deep Learning"; „Pruefberichte" wird nicht mehr
auf einen unbeteiligten Ordner gebogen.

**Was der Prüfer über sich selbst gelernt hat.** Erster Lauf gegen den eigenen
Code: acht echte Befunde (darunter `pip install` ohne Timeout und ein
stillschweigend scheiterndes `piper.stdin.write`) – und drei Fehlalarme des
Prüfers, die wichtiger waren als die Treffer:

| Fehlalarm | Konsequenz |
|---|---|
| `str.replace(",", ".")` als Schreibvorgang gemeldet | Nur unzweideutige Datei-Aufrufe zählen; `write`, `flush`, `dump`, `replace` sind raus |
| `open(pfad, "rb")` ohne `encoding` bemängelt | Binärmodus wird erkannt – dort wäre `encoding` ein `TypeError` |
| Rat „wenn beabsichtigt: kurzer Kommentar" gegeben, den Kommentar aber ignoriert | Ein erklärtes `except … : pass` gilt jetzt als erledigt |

Ein Bericht mit Fehlalarmen wird nach dem zweiten Mal nicht mehr gelesen. Nach
den Korrekturen: **null** offene Befunde über „gering" im eigenen Code.

**Und was das Modell beisteuert.** Beim ersten `--tief`-Lauf auf echter Hardware
standen zwei Modellbefunde ganz oben auf „hoch" – beide falsch („es ist nicht
klar, ob `TOOL_NAMES` eine Menge ist", über einer Zeile mit Mengennotation).
Drei Konsequenzen: Modellbefunde sind auf **mittel** gedeckelt und stehen nie
über einem vom Parser belegten; Vermutungsformeln („es ist nicht klar",
„könnte", „stelle sicher") werden verworfen; ein Befund auf einer `def`-, Import-
oder Kommentarzeile ebenso. Alle vier Fehlbefunde aus dem echten Lauf fallen
damit heraus. Außerdem gingen alle sechs Abschnitte in dieselbe Datei – jetzt
sind es höchstens zwei je Datei, damit Breite vor Dateigröße geht.

Ehrlich bleibt: **Der Wert liegt in Ebene 1.** Bei 14B liefert die Modellprüfung
wenig, was der Parser nicht sicherer findet. Sie lohnt sich für gezielte
Nachfragen zu einzelnen Abschnitten, nicht als Rundumprüfung.

### Neu: Notizinhalte sind an die Vault gebunden

Eine erzeugte Notiz über „Vault" handelte von HashiCorp Vault – Secret Engines,
TLS-Zertifikate, Redis-Cache. Kein Wort davon stand in den Notizen. Ursache:
`_inhalt_erzeugen` schickte **gar keinen Kontext** mit, der Text kam vollständig
aus der Modellerinnerung. Jetzt wird zuerst der eigene Bestand zum Thema gesucht
und in den Prompt gestellt, mit dem ausdrücklichen Vorrang vor dem Weltwissen des
Modells. Zusätzlich sind die Hausbegriffe im Systemprompt festgenagelt: „Vault"
ist immer die Obsidian-Notizsammlung, nie ein Tresor.

---


### „Kein verwertbarer Plan" – drei Ebenen dagegen

| Ebene | Was passiert |
|---|---|
| Synonyme | `move_note`, `create`, `append`, `target_folder`, `note`, `content` … werden auf die echten Namen abgebildet. Ein brauchbarer Plan fällt nicht mehr durch, nur weil das Modell englisch benennt |
| Zweiter Versuch | Scheitert der erste, folgt ein **knapper Prompt mit Beispiel** auf dem großen Modell |
| Ersatzplan ohne Modell | „verschiebe X nach Y", „lösche X", „benenne X in Y um", „verknüpfe X mit Y" werden direkt aus dem Satz gelesen |

Und wenn doch nichts geht, steht jetzt **warum** dort: „unbekannte Aktion 'zaubern'",
„Pflichtangabe fehlt (name)", „Antwort war kein JSON: …" statt eines pauschalen Satzes.

### Falsche Erfolgsmeldungen

> „Die Notiz '_Lernen/gleichstrom' wurde erfolgreich verschoben." – war sie nicht.

Die Prosa des Modells war bisher der einzige Bericht. Jetzt hängt der Code hinter
jede Antwort mit Schreibzugriff einen **Tatsachenbericht aus den echten
Rückgabewerten**. Behauptet der Text Erfolg, während ein Werkzeug fehlschlug,
steht darüber eine ausdrückliche Warnung mit dem tatsächlichen Fehlergrund.

### Ein Fehler, der lange unbemerkt blieb

`safe_filename` schnitt **führende Unterstriche** ab: Aus `_Lernen` wurde `Lernen`,
aus `_System/Code` wurde `System/Code`. Der Agent legte Dateien in einem *neuen*
Ordner ab, während der eigentliche unberührt daneben lag – und meldete Erfolg.
Das erklärt einen Teil der „er hat es doch nicht verschoben"-Fälle.

### Neu: Code und externe Dateien

| Werkzeug | Wofür |
|---|---|
| `codeblock_hinzufuegen` | Code sauber eingezäunt einfügen – Rauten werden nicht zu Überschriften |
| `code_auslesen` | Codeblöcke einer Notiz zurückgeben |
| `datei_importieren` | Externe Datei (Python, JSON, CSV, Log, Markdown …) als Notiz ablegen |

### Neu: Zeus liest seinen eigenen Code

```env
CODE_ROOTS=C:\Users\...\Zeus
CODE_FOLDER=_System/Code
```

| Werkzeug | Wofür |
|---|---|
| `code_dateien` | Überblick: welche Quelldateien gibt es, wie groß |
| `code_suchen` | Fundstellen mit Datei und Zeilennummer |
| `quellcode_lesen` | Datei oder Zeilenbereich, **mit Zeilennummern** |
| `codebasis_importieren` | Code als Notizen ablegen, damit die semantische Suche ihn findet |

Im Chat: `/code` listet auf, `/codebasis` übernimmt alles in die Vault.

**Bewusste Grenze:** Nur lesend. Zeus kann seinen Code ansehen, verstehen und
Verbesserungen mit Datei und Zeilennummer vorschlagen – ändern kann er ihn nicht.
Ein Agent, der sich selbst umschreibt, während er läuft, ist keine Funktion,
sondern eine Fehlerquelle ohne Rückweg. Pfade außerhalb von `CODE_ROOTS` werden
abgewiesen, auch wenn sie existieren.

---

**Version 7** behebt den häufigsten Grund für fehlgeschlagene Bearbeitungen:
Das Modell **erfindet Notiznamen**. Es schreibt „Erfolgreiche Optimierung von
nicht-linearen Modellen", während die Datei `Optimierung_nichtlinearer_Modelle.md`
heißt. Bis v6 scheiterte die Operation erst bei der Ausführung.

| Änderung | Wirkung |
|---|---|
| Namen werden **vor** der Ausführung aufgelöst | Der erfundene Titel wird auf die echte Datei abgebildet |
| `resolve_note` in sieben Stufen | Pfad → Name → Titel → gefaltet → Wortüberlappung → Tippfehler → semantische Suche |
| Semantik nur mit Beleg | Die Vektorsuche findet sonst für *jede* Anfrage etwas – jetzt braucht der Treffer ein gemeinsames Wort |
| Fehlermeldung nennt Alternativen | „Notiz X gibt es nicht. Vorhanden ist z. B.: [[A]], [[B]]" |
| Teilerfolg statt Totalausfall | Unauflösbare Operationen fallen raus, der Rest läuft |

**Und:** Deine Rückfrage „Kannst du mir sagen wieso du es nicht schaffst … zu
löschen?" wurde als Schreibauftrag behandelt – sie enthält „löschen",
„verschieben", „bearbeiten". Fragen über das eigene Tun werden jetzt erkannt und
beantwortet, mit den echten Fehlschlägen aus dem Protokoll statt mit Vermutungen.
Gleichzeitig sind **trennbare Verben** dazugekommen: „führe … zusammen",
„lege … an", „benenne … um" waren vorher überhaupt keine erkannten Aufträge.

---

**Version 6** gab dem Agenten die volle Kontrolle über bestehende Notizen –
und behebt einen Fehler aus v5: Der Planer fing jede Schreibabsicht ab, konnte
aber nur *anlegen*. „Verschiebe die Agenten-Notizen" hätte neue Dateien erzeugt.
Jetzt plant er beliebige Operationen aus dem vollen Werkzeugkasten. Dazu zehn
neue Werkzeuge (kein einziges wurde entfernt) und eine echte Hardware-Diagnose.

### Neu: Bestehendes verändern

| Werkzeug | Wofür |
|---|---|
| `abschnitt_ersetzen` | Ein Kapitel austauschen, Rest bleibt unberührt |
| `abschnitt_einfuegen` | Neues Kapitel an definierter Stelle |
| `abschnitt_loeschen` | Kapitel samt Unterabschnitten entfernen |
| `notizen_zusammenfuehren` | Dubletten vereinen, Verweise umbiegen, Quellen in den Papierkorb |
| `notiz_aufteilen` | Überlange Notiz in Kapitel zerlegen, Original wird Übersicht |
| `auto_verlinken` | Verwandte Notizen im Bestand automatisch verknüpfen |
| `uebersichtsnotiz` | Einstiegsnotiz je Ordner, nach Tags gruppiert |
| `frontmatter_setzen` | Beliebiges Metadatenfeld (status, prioritaet …) |
| `notiz_wiederherstellen` | Aus dem Papierkorb zurückholen |
| `ordner_umbenennen` | Ordner umbenennen, Notizen ziehen mit |

Damit sind es **25 Werkzeuge**. Der Planer wählt die passende Aktion selbst:
„verschiebe" wird `notiz_verschieben`, „ergänze" wird `notiz_ergaenzen`,
„führe zusammen" wird `notizen_zusammenfuehren`. Erfundene Aktionen und
Operationen mit fehlenden Pflichtparametern werden verworfen, gültige laufen weiter.

### Neu: Tempo-Diagnose statt Raten

`/hardware` bzw. `--hardware` liest über `/api/ps` aus, wie viel des Modells
**wirklich** im VRAM liegt. Die entscheidende Zeile sieht so aus:

```
Modell 'qwen2.5:14b': 9.2 GB geladen, davon 3.1 GB im VRAM → 34 % auf der GPU
```

Details und die passenden Gegenmaßnahmen: [GPU_OPTIMIERUNG.md](GPU_OPTIMIERUNG.md).
Kurzfassung: `FAST_MODEL=qwen2.5:3b` für Routinearbeit, `NOTE_TARGET_WORDS` senken,
`NUM_CTX` senken – und Ollama nutzt weder TensorFlow noch PyTorch, das ändert nichts.

---

**Version 5** behob die Timeouts und macht den Lernkreis auf: Der Agent beobachtet
seine eigenen Aktionen, macht aus Korrekturen dauerhafte Regeln und kann sich neue
Fertigkeiten aus vorhandenen Werkzeugen zusammenbauen.

---

## Warum es Timeouts gab

`Read timed out (read timeout=1500)` hatte eine mechanische Ursache: Werkzeug- und
Planer-Aufrufe liefen **ohne Streaming**. Die Verbindung wartete auf die fertige
Antwort. Gerät ein Modell in eine Wiederholungsschleife – bei langem Kontext
typisch – erzeugt es Token bis zum Kontextende, ohne dass ein einziges Byte ankommt.

Vier Änderungen, jede für einen anderen Teil des Problems:

| Änderung | Wogegen |
|---|---|
| **Alles streamt**, auch Werkzeugaufrufe | Das Lesezeitlimit gilt jetzt *zwischen* zwei Token. Eine langsame, aber laufende Generierung läuft nie mehr in einen Timeout. |
| `IDLE_TIMEOUT` statt Gesamtlimit | Ein echter Hänger wird nach 90 s erkannt statt nach 25 Minuten – und die Teilantwort bleibt erhalten. |
| `NUM_PREDICT` + `REPEAT_PENALTY` | Kappt Wiederholungsschleifen, bevor sie den Kontext vollschreiben. |
| `KEEP_ALIVE` + Warmlauf beim Start | Das Modell bleibt geladen. Ohne das beginnt jeder Aufruf mit dem Laden von 20 GB. |

Dazu wird der Prompt hart auf ~65 % des Kontextfensters begrenzt. Vorher konnte er
`NUM_CTX` überschreiten – Ollama schneidet dann stillschweigend ab, und zwar am
Anfang, wo die Anweisungen stehen. Genau daher kamen auch die englischen Antworten.

**`/tempo`** misst, wie schnell dein Modell tatsächlich ist, und rechnet aus, wie
lange eine Notiz dauert. Bei einem großen Modell lohnt der Blick: Läuft es auf der CPU,
sind einstellige Token/s normal – dann ist nicht die Software langsam, sondern das
Modell zu groß für die Maschine.

---

## Der Lernkreis

Deine Unterscheidung – Gradientenlernen, In-Context, Fine-Tuning, **Systemlernen** –
trifft den Punkt: Für einen Agenten ist das System um das Modell herum die Stellschraube.
Die Gewichte bleiben, was sich ändert, ist der Kontext, in dem das Modell arbeitet.

**Beobachtung.** Jeder Zug wird gemessen: Wurden Quellen gefunden? Hat der
Schreibvorgang die Rücklesekontrolle bestanden? Wurde abgebrochen? Das landet in
`_Lernen/.beobachtungen.jsonl`. Ohne Beobachtung kein Lernen – und die Qualität der
Rückmeldung begrenzt alles Weitere.

**Korrektur → Regel.** Sagst du „das ist falsch", erkennt der Agent das als
Rückmeldung, formuliert daraus **eine allgemeine Regel** und legt sie in
`_Lernen/Lektionen.md` ab. Bei jeder folgenden Frage werden die passendsten Regeln
in den Systemprompt gelegt – auch in der nächsten Sitzung, auch nächste Woche.
Das ist der Teil, der über den Kontext hinaus wirkt.

**Wissenslücken.** Fragen, zu denen die Vault nichts hergab, sammeln sich in
`_Lernen/Wissensluecken.md`. Daraus wird die Themenliste, an der er als Nächstes
arbeiten kann – gesteuertes Selbstlernen statt zufälliges.

**Reflexion.** `/reflektieren` schaut auf die Beobachtungen zurück und schlägt
Konkretes vor. Als Bericht, nicht als stille Änderung.

| Befehl | Zweck |
|---|---|
| `/lernen` | Kennzahlen, Regeln, offene Lücken |
| `/lektionen [konsolidieren]` | Regeln zeigen bzw. inhaltsgleiche zusammenfassen |
| `/reflektieren` | Rückblick mit Verbesserungsvorschlägen |
| `/fertigkeiten` | Was der Agent sich selbst beigebracht hat – geprüft, mit Grund für Gesperrtes |
| `/fertigkeit <name> [thema]` | Eine Fertigkeit ausführen und das Protokoll sehen |

---

## Selbsterweiterung – und warum nicht mit Code

Dein Punkt „der Agent soll neue Werkzeuge schreiben und ausführen" ist richtig, aber
die naheliegende Umsetzung – Python erzeugen und ausführen – ist bei Schreibzugriff
auf deine Wissensbasis fahrlässig. Ein halluziniertes `shutil.rmtree` genügt.

Deshalb: Eine **Fertigkeit** ist ein Ablaufplan aus vorhandenen Werkzeugen,
deklarativ als Notiz in `_Fertigkeiten/` gespeichert. Der Agent schreibt sie selbst;
geprüft wird im Code (nur bekannte Werkzeuge, gültige Parameter, aufgelöste
Platzhalter, begrenzte Schrittzahl). Danach ist sie wie ein eingebautes Werkzeug
aufrufbar.

```yaml
---
fertigkeit: thema_aufarbeiten
beschreibung: Recherchiert ein Thema und legt eine Übersicht an
parameter: [thema]
---
1. notizen_suchen(frage="{thema}", anzahl=8)
2. notiz_erstellen(titel="Übersicht {thema}", inhalt="{ergebnis1}")
3. verwandte_finden(name="Übersicht {thema}")
```

Neue Fähigkeit, keine neue Angriffsfläche. `{ergebnis1}` reicht das Ergebnis von
Schritt 1 weiter – damit lassen sich mehrstufige Abläufe bauen, ohne dass je
fremder Code läuft.

### Drei Regeln, die im Code geprüft werden

Der erste echte Selbstbau-Versuch hat gezeigt, dass eine reine Namensprüfung
nicht reicht. Das Modell baute Abläufe, die formal durchgingen und im Betrieb
zwangsläufig scheitern. Deshalb gilt jetzt:

1. **Jeder Platzhalter ist auflösbar.** Entweder ein deklarierter Parameter oder
   `{ergebnisN}` mit `N` = Nummer eines *früheren* Schritts. `{ergebnis}` ohne
   Zahl wird auf den Vorschritt repariert, wenn es einen gibt – im ersten Schritt
   abgelehnt. Verweise nach vorn werden abgelehnt. Vorher rutschte alles durch,
   was mit „ergebnis" anfing, und landete wörtlich in der Notiz.
2. **`{ergebnisN}` ist Text, kein Name.** Es darf in `inhalt`/`text` stehen,
   niemals in `name`/`titel`/`ziel`. `notiz_ergaenzen(name="{ergebnis1}")` nach
   einer Suche sucht eine Notiz namens *„4 Treffer für …"* – die gibt es nie.
   Beim Auflösen bekommt ein Bezeichner-Parameter den **Notiznamen**, ein
   Inhalts-Parameter den **Text**; Trefferlisten werden als `[[Wikilinks]]`
   eingesetzt, nicht als roher `dict`-Abdruck.
3. **Kein Eintrag ohne Probelauf.** Vor der Aufnahme läuft die Fertigkeit einmal
   durch: lesende Schritte **wirklich** (so zeigt sich, ob eine Suche überhaupt
   etwas findet), schreibende nur simuliert. Das Protokoll steht anschließend im
   Abschnitt `## Probelauf` der Fertigkeitsnotiz. Scheitert der Probelauf, wird
   nichts gespeichert.

**Angelegt ≠ ausgeführt.** `fertigkeit_definieren` meldet ausdrücklich
`ANGELEGT (noch nicht ausgeführt)`. Behauptet das Modell trotzdem, es habe
Änderungen durchgeführt – oder die Fertigkeit laufe „nun regelmäßig", was mangels
Zeitplaner nie stimmt –, hängt der Agent einen Faktenbericht an, der dem
widerspricht. Ausgeführt wird mit `/fertigkeit <name>` oder im Gespräch.

**Alte Fertigkeiten werden beim Laden mitgeprüft.** Was vor dieser Verschärfung
entstanden ist und die Regeln verletzt, wird **gesperrt**: es verschwindet aus der
Werkzeugliste, und ein Aufruf nennt den konkreten Grund statt „unbekannt".
`/fertigkeiten` zeigt beides getrennt – einsatzbereit und gesperrt mit Begründung.
Datei in `_Fertigkeiten/` korrigieren oder löschen, dann greift sie wieder.

---

**Version 4** löste das hartnäckigste Problem: Schreibaufträge im Gespräch liefen
ins Leere. Ursache war eine falsche Annahme in v3 – Ollama meldet **keinen Fehler**,
wenn man einem Modell ohne Werkzeug-Schnittstelle trotzdem `tools` schickt; es
ignoriert das Feld stillschweigend. Der Rückfall auf das JSON-Protokoll wurde also
nie ausgelöst, und `llama3` hat brav beschrieben, was es tun würde. Jetzt werden die
Fähigkeiten des Modells aktiv abgefragt – und Schreibaufträge laufen über einen
deterministischen Planer, der nicht auf die Werkzeug-Disziplin des Modells angewiesen ist.

**Version 3** löste das dritte Problem: Der Agent konnte gar nichts schreiben, wenn man
ihn im Gespräch darum bat – er hatte schlicht keine Möglichkeit dazu und antwortete
trotzdem „erledigt". Jetzt hat er echte Werkzeuge und Vollzugriff auf die Vault.

---

## Der Agent kann die Vault selbst umbauen

Die Vault ist das Lern- und Arbeitsmedium des Agenten, nicht nur sein Nachschlagewerk.
Er entscheidet im Gespräch selbst, welches Werkzeug er braucht:

| Werkzeug | Was es tut |
|---|---|
| `notizen_suchen` | semantisch + per Stichwort suchen |
| `notiz_lesen` | Notiz oder einzelnes Kapitel vollständig lesen |
| `struktur_anzeigen` | Ordnerbaum und Ordnerinhalte |
| `ordner_anlegen` | neuen Ordner erzeugen |
| `notiz_erstellen` | neue Notiz, Ordner automatisch passend gewählt |
| `notiz_ergaenzen` | Inhalt anhängen – auch gezielt unter eine Überschrift |
| `notiz_bearbeiten` | Textstelle ersetzen |
| `notiz_ueberschreiben` | Notiz neu schreiben, Frontmatter bleibt |
| `notiz_verschieben` | verschieben/umbenennen **inkl. Backlink-Aktualisierung** |
| `notiz_loeschen` | in den Papierkorb, nie endgültig |
| `notizen_verknuepfen` | `[[Wikilinks]]` setzen, standardmäßig beidseitig |
| `verwandte_finden` | semantisch verwandte Notizen als Link-Kandidaten |
| `links_pruefen` | tote Links finden und reparieren |
| `tags_setzen` | Tags im Frontmatter pflegen |

## So wird aus „ich habe angelegt" ein Nachweis

Ein Schreibauftrag durchläuft jetzt fünf Stufen, von denen jede scheitern darf –
aber keine stillschweigend:

1. **Absicht erkennen.** „erstelle …", „lege an", „ergänze", „verschiebe" → Schreibpfad.
   Reine Fragen („Wie erstelle ich ein Netz?") laufen weiter als normale Antwort.
2. **Plan erzwingen.** Das Modell liefert *nur* Titel, Ordner und Auftrag je Notiz –
   als JSON, im Code validiert. Kein Inhalt, keine Prosa, nichts zu halluzinieren.
3. **Inhalt einzeln erzeugen.** Pro Notiz ein eigener Aufruf mit fester Gliederung
   (Kurzfassung, Einordnung, Funktionsweise, Probleme, Lösungen, offene Fragen).
   Unter 80 Zeichen Ergebnis gilt als Fehlschlag und wird nicht gespeichert.
4. **Atomar schreiben.** Temp-Datei → `fsync` → `os.replace`. Eine halb geschriebene
   Notiz kann es nicht geben, auch nicht bei Absturz.
5. **Zurücklesen und vergleichen.** SHA1 der Datei gegen SHA1 des Solls, danach
   Parse-Test. Weicht etwas ab: bis zu `WRITE_RETRIES` Wiederholungen, dann Abbruch
   mit Klartext-Meldung.

Der Bericht am Ende zählt, was wirklich auf der Platte liegt:

```
**8 Notizen angelegt:**
- `Deep Learning/Data_Augmentation.md` (2193 Zeichen)
…
**2 fehlgeschlagen – bitte ansehen, Sir:**
- Transfer Learning: Modell lieferte zu wenig Inhalt.
Sagen Sie "nochmal versuchen", dann nehme ich die fehlenden erneut in Angriff.
```

„Nochmal versuchen" startet gezielt nur die fehlgeschlagenen neu.

**Transaktionen.** Ein Stapel ist eine Einheit: Bricht er mittendrin ab, wird der
gesamte Stapel zurückgerollt – keine Vault voller Halbfertigem.

**Sicherheitsnetz.** Jede Änderung sichert die betroffenen Dateien vorher nach
`<Vault>/.jarvis/backups/` und wird in `.jarvis/journal.jsonl` protokolliert.
`/rueckgaengig` nimmt die letzte Aktion zurück – auch das Anlegen einer Datei.
`/verlauf` zeigt, was der Agent zuletzt an der Vault gemacht hat. Gelöscht wird nie
wirklich: Notizen wandern nach `_Papierkorb/`. Pfade sind hart auf die Vault begrenzt.
Der Ordner `.jarvis` beginnt mit einem Punkt und taucht deshalb weder in Obsidian
noch im Index auf.

**Modelle.** Die Fähigkeiten werden beim Start über `/api/show` abgefragt, nicht
geraten. Mit nativer Werkzeug-Unterstützung (`qwen2.5`, `llama3.1+`, `mistral-nemo`)
darf das Modell frei Werkzeuge wählen. Ohne – **`llama3` gehört dazu** – bleibt der
Schreibbetrieb über den Planer trotzdem zuverlässig, freies Werkzeug-Denken ist aber
eingeschränkt. `/doktor` zeigt die erkannten Fähigkeiten, `/werkzeuge` den Modus.
Empfehlung für den Schreibbetrieb: `ollama pull qwen2.5:14b`.

**Betriebsschalter.** `DRY_RUN=true` bzw. `/trocken an` zeigt alles an, schreibt aber
nichts. `PROTECTED_FOLDERS=Archiv,Privat` sperrt Ordner komplett. `MAX_FILES_PER_RUN`
begrenzt neue Notizen pro Auftrag (Folgeänderungen wie Verlinkung zählen nicht mit).
Jede Aktion landet als JSONL-Zeile unter `<Vault>/.jarvis/logs/`.

**Wartung und Autonomie.** `/wartung` bzw. `python jarvis_run.py --wartung` gleicht den
Index ab, repariert tote Links und meldet verwaiste Notizen sowie Dubletten – ohne
Modellaufruf, also deterministisch und billig. Für die Windows-Aufgabenplanung oder
cron gedacht. Weitere Ausbaustufen stehen in [ROADMAP.md](ROADMAP.md).

---

## Was jetzt anders ist

| Vorher | Jetzt |
|---|---|
| Nur Dateiname als Metadatum | Ordner, Titel, Frontmatter, Tags, Aliase, Wikilinks, Überschriften-Pfad |
| Chunks ohne Herkunft | Jeder Chunk trägt `Ordner › Notiz › Abschnitt` im Text **und** in den Metadaten |
| Kein Strukturwissen | Ordnerbaum + Meta-Chunks pro Ordner sind selbst durchsuchbar |
| Notiz landet in der Vault-Wurzel | 4-Signal-Zuordnung wählt den passenden Ordner und begründet die Wahl |
| „Depp Learning“ bleibt „Depp Learning“ | Fuzzy-Abgleich gegen echte Ordner-/Notiznamen korrigiert auf „Deep Learning“ |
| Jeder Start = komplette Neuindexierung | Inkrementell über Prüfsummen-Manifest; nur Geändertes wird neu eingebettet |
| Gelöschte Notizen bleiben im Index | Werden erkannt und entfernt |
| Nur Vektorsuche | Hybrid: Vektor + Stichwort (Dateinamen, Tags, exakte Begriffe) + Dedup über Notizen |
| `answer[:200] + "..."` beim Speichern | Vollständiger Inhalt, Quellen als echte `[[Wikilinks]]` |
| Blockierende Anfragen | Streaming-Antworten, parallele Batch-Embeddings |
| Kontext = 900-Zeichen-Fragmente, mitten im Satz abgeschnitten | Ganze Kapitel; kleine Notizen komplett; Schnitte nur an Zeilen-/Satzgrenzen |
| Schreiben nur über `/notiz` und `/merke` | 14 Werkzeuge, die der Agent im Gespräch selbst aufruft |
| — | Backups, Änderungsjournal, `/rueckgaengig`, Papierkorb |
| — | Gesprächsverlauf, Ordnerfilter `@Ordner`, Sprachein-/-ausgabe |

---

## Auf einem neuen Rechner

**Windows:** Doppelklick auf `START_WINDOWS.bat`. Das sucht Python, installiert
beim ersten Lauf die Pakete, legt eine `.env` an und startet den Chat.

Von Hand geht es genauso:

```bat
cd "C:\Pfad\zu\Zeus"
py -m pip install -r requirements.txt
py jarvis_run.py --doktor
```

`ModuleNotFoundError: No module named 'requests'` heißt immer dasselbe: Auf
**diesem** Rechner wurden die Pakete nie installiert. Seit v7 fängt der Start das
ab und nennt den Befehl statt eines Tracebacks; `py jarvis_run.py --installieren`
erledigt es in einem Schritt.

Zwei Stolpersteine dabei:

- **Mehrere Python-Installationen.** `pip` muss zu dem Python gehören, mit dem du
  startest. Deshalb immer `py -m pip install …` statt nur `pip install …` – die
  Fehlermeldung zeigt zur Kontrolle an, welches Python gerade läuft.
- **Die `.env` zeigt auf den alten Rechner.** Der Vault-Pfad ist maschinenabhängig.
  `py jarvis_run.py --vault "C:\Pfad\zur\Vault"` schreibt ihn neu, `--doktor`
  zeigt, welche `.env` überhaupt gelesen wurde.

## Installation

```bash
pip install -r requirements.txt

# Ollama-Modelle
ollama pull nomic-embed-text
ollama pull qwen2.5:32b        # oder llama3, mistral, …

cp .env.example .env           # Windows: copy .env.example .env
# .env öffnen und OBSIDIAN_VAULT auf deine Vault setzen
```

Minimal nötig sind `requests`, `python-dotenv`, `PyYAML`, die drei `langchain-*`-Pakete
und `faiss-cpu`. `rich` (Oberfläche), `pyttsx3`/`piper-tts` (Sprachausgabe) sowie
`faster-whisper` + `sounddevice` (Spracheingabe) sind optional – fehlt eines,
läuft der Rest weiter und sagt dir beim Start, was fehlt.

## Start

```bash
python jarvis_run.py                 # Chat im Terminal
python jarvis_run.py --ui            # Oberflaeche im eigenen Fenster
python jarvis_run.py --browser       # dieselbe Oberflaeche im Browser
python jarvis_run.py --sync          # nur nachindexieren
python jarvis_run.py --reindex       # kompletter Neuaufbau
python jarvis_run.py --baum          # Ordnerstruktur anzeigen
python jarvis_run.py --frage "Was steht zu Backpropagation?"
python jarvis_run.py --notiz "Deep Learning"
python jarvis_run.py --stimmen       # empfohlene piper-Stimmen zeigen
python jarvis_run.py --stimme-einrichten en_GB-alan-medium
```

## Die Oberfläche

`python jarvis_run.py --ui` öffnet ein eigenes Fenster. Das Terminal bleibt
vollständig erhalten und läuft unverändert weiter; die Oberfläche kommt dazu.

**Ein Board, keine Reiter.** Links das Gespräch wie ein Terminal, rechts das
Gehirn – gleichzeitig, nicht abwechselnd. Reiter verstecken genau das, was man
beim Entwickeln nebeneinander sehen will. Die Trennlinie lässt sich ziehen.

**Links: das Gespräch.** Monospace, Zeitstempel, Werkzeugaufrufe live mit
`✔`/`✖` und der echten Rückmeldung des Werkzeugs – nicht mit dem, was das
Modell darüber behauptet. Über allem der Name und daneben das Gehirn: matt im
Ruhezustand, leuchtend und pulsierend, solange er arbeitet.

**Rechts: das Gehirn.** Der Link-Graph aus den echten `[[Wikilinks]]`.
Punktgröße = Anzahl der Verbindungen, Farbe = oberster Ordner. Ein Klick fährt
die Notiz von rechts ein und hebt ihre Nachbarn hervor; `verwaiste` zeigt, was
noch nicht verknüpft ist.

**Oben: die Lade.** Ein halbdurchsichtiger Griff am oberen Rand, der beim
Anfahren sichtbar wird. Ein Klick fährt alle Werkzeuge und Fertigkeiten als
eine waagerechte Reihe herunter – mit Nutzungsbalken, Fehlerquote, Testring
und Filtern (`liest` / `schreibt` / `eigene` / `nie benutzt`). Das Mausrad
scrollt dort seitwärts. Ein Klick auf eine Karte setzt `[name]` ins Eingabefeld.

### Die Magic-Eingabe

Zwei Auslöser, dieselbe Mechanik. Pfeiltasten oder Maus wählen, Enter oder Tab
übernimmt, Escape bricht ab.

| Tippen | Was erscheint |
|---|---|
| `[` | Alle Werkzeuge **und** Fertigkeiten, mit Art, Beschreibung, Parametern und Aufrufzahl. Die Auswahl schreibt die schließende `]` gleich mit |
| `/` am Zeilenanfang | Die komplette Befehlsliste mit Gruppe und Argumenthilfe |
| `/befehl ` | Die Argumente *dieses* Befehls – Fertigkeiten bei `/fertigkeit`, Notizen bei `/notiz`, Ordner, Quelldateien oder eine feste Auswahl wie `an|aus` |

Alle Listen kommen aus dem laufenden System. Eine Vorschlagsliste, die etwas
anbietet, das es nicht gibt, wäre schlimmer als gar keine.

### Warum `/help` in der ersten Fassung nicht ging

Der Befehlssatz steckte in `ui.py._command()`, also im Terminalteil. Die
Oberfläche kannte ihn nicht und hat `/help` als Frage ans Modell geschickt –
das Modell hat sich daraufhin eine Antwort ausgedacht. Zwei Änderungen:

1. **`jarvis/befehle.py`** ist der Katalog: Name, Kurznamen, Gruppe, Argumentart
   und Argumenthilfe für jeden Befehl. Die Hilfetabelle des Terminals wird
   daraus **erzeugt**, und die Oberfläche holt sie für die Vorschlagsliste.
   Eine Liste, drei Verwender. Ein Test vergleicht den Katalog mit dem, was
   `_command()` tatsächlich umsetzt – auseinanderlaufen können sie nicht mehr.
2. **`jarvis/web/bruecke.py`** führt Befehle aus, indem es `_command()`
   *unverändert* laufen lässt – nur mit einer Rich-Konsole, die in einen Puffer
   statt ins Terminal schreibt. Damit gibt es jeden Befehl im Fenster, auch
   jeden, der später dazukommt, ohne dass dort etwas geändert werden muss.

Befehle, die eine echte Konsole brauchen (`/stimme`, `/hoeren`, `/cls`),
sagen das und tun nicht so, als hätten sie gewirkt.

### Das Protokoll

Das Zahnrad hat einen Nachbarn bekommen: **Strg+L** öffnet das Protokoll rechts.
Ausklappbar oder anheftbar — angeheftet schiebt es den Graphen zusammen, statt
ihn zu verdecken. Sieben Ebenen, jede mit eigener Farbe und eigenem Zähler:

| Ebene | Was drinsteht |
|---|---|
| `✔ Erfolg` | ein Werkzeug hat etwas geschafft – mit Parametern |
| `✖ Fehler` | ein Werkzeug ist gescheitert – mit Grund |
| `⚠ Warnung` | auffällig, aber nicht gescheitert (z. B. Ausweichserver aktiv) |
| `✎ Änderung` | eine Datei wurde angelegt, geändert, verschoben oder gelöscht |
| `→ Netz` | eine Anfrage hat den Rechner verlassen – Ziel, Modell, Bytes |
| `← Netz` | die Antwort – Status, Bytes, Dauer |

Die letzten beiden sind der eigentliche Grund für das Protokoll. Zeus ist
lokal, aber **„lokal" ist eine Behauptung, solange niemand nachsehen kann.**
Jede HTTP-Anfrage läuft über eine protokollierende Session; eine Testreihe
durchsucht den gesamten Quelltext danach, ob irgendein Modul daran vorbei
`requests.get(` aufruft. Findet sie eine Stelle, schlägt sie fehl — ein
Protokoll mit blindem Fleck wäre schlimmer als keins, weil es Vollzähligkeit
behauptet.

Alles landet zusätzlich in `.jarvis/protokoll.jsonl`. „Leeren" räumt nur die
Anzeige; die Datei bleibt, sie ist der Nachweis.

### Einstellungen

Das Zahnrad oben rechts fährt sie ein. Sie schreiben direkt in die `.env`, mit
einer Erklärung je Feld und dem Hinweis, welche Werte erst nach einem Neustart
greifen. Ein fehlerhafter Wert verhindert **alle** Schreibvorgänge – halb
gespeicherte Einstellungen sind schlimmer als gar keine. `CHUNK_SIZE`,
`CHUNK_OVERLAP` und der Speicherpfad lassen sich hier bewusst **nicht** ändern:
Sie machen den vorhandenen Index unbrauchbar, ohne dass es auffällt.

Der Server hört nur auf `127.0.0.1` und verlangt bei jeder Anfrage einen beim
Start erzeugten Schlüssel. Nicht aus Paranoia – er darf über die Werkzeuge in
die Vault schreiben.

## Befehle im Chat

| Befehl | Wirkung |
|---|---|
| *(einfach tippen)* | Frage an die Vault |
| `@Deep Learning wie …` | Suche auf diesen Ordner beschränken (auch mit Tippfehler) |
| *(Enter ohne Text)* | Mikrofon an – sprich einfach, die Pause beendet die Aufnahme |
| `/sync` / `/reindex` | Index aktualisieren / neu bauen |
| `/baum` / `/struktur` | Ordnerbaum bzw. kompakte Übersicht |
| `/notiz <Thema>` | Wissen zum Thema bündeln und als Notiz ablegen |
| `/merke [Thema]` | letzte Antwort als Notiz speichern |
| `/rueckgaengig` | letzte Änderung an der Vault zurücknehmen |
| `/verlauf` | Änderungsjournal |
| `/werkzeuge` | Werkzeugliste und aktiver Modus |
| `/links [reparieren]` | tote `[[Links]]` finden und reparieren |
| `/quellen` | Quellen der letzten Antwort |
| `/sprich an\|aus` | Sprachausgabe schalten |
| `/hoeren` | Spracheingabe erzwingen |
| `/status`, `/leeren`, `/ende` | Diagnose, Verlauf löschen, beenden |

### Aufträge, an denen er selbständig arbeitet

Der Unterschied zu einer Frage: Ein **Ziel** überlebt die Antwort. Es wird in
Schritte zerlegt, die Schritte liegen in einer Datenbank, und was davon
erledigt ist, bleibt erledigt – auch über einen Absturz, einen Neustart oder
ein Wochenende hinweg.

| Befehl | Wirkung |
|---|---|
| `/ziel <was erreicht werden soll>` | Ziel aufgeben; wird zerlegt und abgearbeitet |
| `/ziele [alle\|<kennung>]` | woran gerade gearbeitet wird, mit Schritten und Ergebnissen |
| `/arbeiten [runde]` | Warteschlange jetzt im Vordergrund abarbeiten |
| `/freigeben [<nr> ja\|nein <Grund>]` | wartende Entscheidungen ansehen und beantworten |
| `/termine [neu <abstand> <auftrag>\|aus <kennung>]` | zeitgesteuerte Aufträge |
| `/ablaeufe [start <name>\|zeigen <name>]` | gespeicherte Abläufe |

Dasselbe ohne Terminalschleife:

```
python jarvis_run.py --ziel "Erstelle eine Übersicht zum Thema Sandkisten"
python jarvis_run.py --ziele          # Stand ansehen
python jarvis_run.py --arbeiten       # nach einem Absturz fortsetzen
python jarvis_run.py --freigaben      # was auf dich wartet
python jarvis_run.py --freigeben 1:ja
```

**Was dabei sicher ist – und was nicht.** Drei Schranken stehen wirklich im
Weg, nicht nur im Prompt:

* **Rechtestufe.** `RECHTE_STUFE=0` heißt lesen und ändert nichts – das gilt
  jetzt auch für das Werkzeug selbst, nicht nur für die Anzeige. Ohne Stufe 2
  arbeitet nichts im Hintergrund; Ziele lassen sich trotzdem anlegen und mit
  `/arbeiten` im Vordergrund ausführen.
* **Freigabe.** Unbeaufsichtigt hält eine zerstörende Handlung (löschen,
  überschreiben, zusammenführen) an und wartet auf eine Entscheidung. Die
  Aufgabe schläft dabei in der Datenbank – kein Prozess muss offen bleiben.
* **Budget.** Modellaufrufe, Werkzeugaufrufe, Laufzeit, Versuche und
  Neuplanungen sind je Aufgabe begrenzt. Ist das Budget alle, wird die
  Aufgabe **angehalten**, nicht abgebrochen: Der Zwischenstand bleibt, und
  `/arbeiten` setzt nach einer Budgeterhöhung fort.

Einstellbar in der `.env`: `AUFGABEN_AKTIV`, `AUFGABEN_ARBEITER`,
`FREIGABE_AB_RISIKO`, `BUDGET_*`, `KONTEXT_PLATZ`.

---

## Wie die Ordner-Zuordnung entscheidet

`JarvisAgent.resolve_target_folder()` sammelt vier Signale und gewichtet sie:

1. **Herkunft der Quellen** (stärkstes Signal): Kamen die Treffer aus
   `Deep Learning/`, gehört die neue Notiz mit hoher Wahrscheinlichkeit dorthin.
   Gewichtung nach Rang und Trefferscore.
2. **Namensähnlichkeit** Thema ↔ Ordnername, umlaut- und tippfehlertolerant
   (`Depp Learning` ↔ `Deep Learning` = 0.92).
3. **Ähnliche Notiznamen** – deren Ordner bekommt Punkte.
4. **Tag-Profil** des Ordners.

Ist das Feld eng oder schwach, entscheidet das LLM per JSON-Antwort aus der
**vorhandenen** Ordnerliste; erfindet es einen Ordner, obwohl Kandidaten da waren,
wird die Erfindung verworfen. Bleibt alles unklar, geht die Notiz nach
`00_Inbox` (konfigurierbar über `INBOX_FOLDER`).

Die Begründung landet als `ordner_begruendung` im Frontmatter – du kannst also
nachvollziehen (und korrigieren), warum eine Notiz wo liegt.

## Wie das Strukturwissen in den Index kommt

Jeder Chunk beginnt mit einer Kopfzeile, die mit eingebettet wird:

```
[Vault] Ordner: Deep Learning | Notiz: Skript Deep Learning (01_Skript_Deep_Learning)
        | Abschnitt: Grundlagen > Backpropagation | Tags: #dl, #mathe
```

Zusätzlich schreibt der Indexer **Meta-Chunks**: einen pro Ordner (mit Notiztiteln,
Tags, Unterordnern) und eine Gesamtübersicht. Fragen wie „Was liegt eigentlich in
Deep Learning?“ oder „Welche Ordner habe ich?“ treffen damit echte Dokumente statt
ins Leere. Bei Struktur-Fragen bekommt das Modell den kompletten Ordnerbaum zusätzlich
in den Kontext.

## Inkrementelle Indexierung

`vector_store/manifest.json` speichert pro Datei SHA1 und die Chunk-IDs im FAISS-Index.
Beim Sync werden nur geänderte Dateien neu eingebettet, gelöschte werden aus dem Index
entfernt. Wechselst du das Embedding-Modell oder die Chunk-Größe, erkennt der Agent das
und baut automatisch neu auf.

---

## Struktur des Projekts

```
jarvis/
  config.py   Einstellungen aus .env
  llm.py      Ollama: Chat mit Streaming, Batch-Embeddings, Modell-Check
  vault.py    Vault-Verständnis: Parsing, Struktur, Fuzzy-Matching, Chunking
  index.py    Inkrementeller FAISS-Index + hybride Suche
  tools.py    Schreibzugriff: Werkzeuge, atomare Writes, Verifikation, Transaktionen
  planner.py  Deterministischer Schreibpfad: Absicht → Plan → Inhalt → Kontrolle
  hardware.py GPU-Erkennung, Ladezustand, Tempo-Empfehlungen
  learning.py Systemlernen: Beobachtungen, Lektionen, Wissenslücken, Reflexion
  skills.py   Selbsterweiterung: eigene Fertigkeiten aus geprüften Bausteinen
  aufgaben.py Offene Punkte: automatisch eintragen, nachprüfen, abhaken
  evaluation.py  Messstand: Prüfaufgaben, A/B gegen das Gedächtnis
  eingabe.py  Mehrzeilige Eingabe: Einfügen wird als eine Eingabe erkannt
  codereview.py  Selbstprüfung des Quellcodes – statisch und modellgestützt
  agent.py    Persona, Werkzeug-Schleife, RAG, Ordner-Zuordnung
  voice.py    TTS (piper/pyttsx3/espeak) und STT (faster-whisper)
  piper_stimmen.py  piper-Stimmen finden, auflisten, nachladen
  ui.py       Terminal-Chat
jarvis_run.py Einstiegspunkt
tests/        Selbsttest ohne Ollama (stubs.py ersetzt FAISS/LLM)
```

Selbsttest laufen lassen (braucht kein Ollama, kein FAISS):

```bash
cd tests
python test_smoke.py     # Struktur, Index, Suche, Ordner-Zuordnung
python test_tools.py     # Schreibzugriff, Backlinks, Undo, Werkzeug-Schleife
python test_writing.py   # Härtetest: llama3 ohne Tools, 10 Dateien, Schreibfehler, Rollback
python test_learning.py  # Timeouts gegen echten HTTP-Server, Lernschleife, Fertigkeiten
python test_fertigkeiten.py  # Selbstbau: Platzhalter, Probelauf, "angelegt != ausgefuehrt"
python test_stimme.py    # piper-Stimmen finden, wechseln, synthetisieren
python test_codereview.py  # statische Muster, Einstufung, Zitatpruefung
python test_schleife.py    # Wiederholungssperre, links_bereinigen, Aufgabenliste
python test_messstand.py   # Pruefbedingungen, A/B-Messung, Bewaehrung von Lektionen
python test_eingabe.py     # Einfuegen vs. Tippen, Blockmarke, /prompt
python test_editing.py   # Abschnitte, Zusammenführen, Aufteilen, Planer mit allen Aktionsarten
python test_namen.py     # Erfundene Notiznamen, Titel statt Dateiname, Meta-Fragen
python test_stabilitaet.py  # Plan-Diagnose, Ersatzplan, falsche Erfolgsmeldungen, Code-Werkzeuge
```

`test_learning.py` startet einen Ollama-Attrappenserver und prüft drei Fälle
getrennt: langsam aber stetig (darf **nicht** abbrechen), mitten im Stream
verstummt (Abbruch nach `IDLE_TIMEOUT`, Teilantwort bleibt), tot (schneller Fehler
mit verständlicher Meldung).

`test_writing.py` stellt genau den gemeldeten Fehlerfall nach: ein Modell, das
`tools` stillschweigend ignoriert, und den Auftrag „erstelle eine Struktur mit
Dateien für alle Punkte". Er prüft, dass die Dateien danach wirklich existieren,
dass ein simulierter Plattenfehler als Fehler gemeldet wird statt als Erfolg, und
dass ein abgebrochener Stapel vollständig zurückgerollt wird.

---

## Sprache und Stimme umstellen

Drei Werte in der `.env` – oder im laufenden Chat, dann werden sie gleich gesichert:

```env
LANGUAGE=English          # Antwortsprache des Agenten
TTS_GENDER=maennlich      # maennlich | weiblich | egal
TTS_VOICE=                # leer = automatisch passend zu Sprache und Geschlecht
SPEAK_GREETING=true       # Begrüßung beim Start vorlesen
STT_LANGUAGE=en           # Sprache fürs Diktieren
```

Im Chat:

| Befehl | Wirkung |
|---|---|
| `/stimmen` | Alle installierten Stimmen mit Sprache und Geschlecht |
| `/stimme David` bzw. `/stimme 3` | Stimme wechseln, sofort hören, in die `.env` schreiben |
| `/stimme installieren [Kennung]` | Natürlich klingende piper-Stimme nachladen |
| `/stimme liste` | Empfohlene piper-Stimmen mit Klangbeschreibung |
| `/sprache English` | Antwortsprache umstellen, Stimme folgt automatisch |

`TTS_VOICE` leer zu lassen ist der bequemste Weg: Der Agent sucht dann selbst
eine Stimme, die zu `LANGUAGE` **und** `TTS_GENDER` passt. Findet er nichts
Passendes, bleibt die Systemstimme – `/stimmen` zeigt, was überhaupt da ist.

**Auf deutschem Windows fehlen englische Stimmen oft.** Standardmäßig ist nur
eine deutsche installiert. Nachrüsten: *Einstellungen → Zeit und Sprache →
Sprache und Region → Sprache hinzufügen → English (United States) → bei den
Sprachfunktionen „Sprachausgabe" anhaken.* Danach stehen **David** (männlich, US)
und **Mark** zur Verfügung, oft auch **George** (männlich, UK).

### Wirklich gute Stimmen: piper

Die SAPI-Stimmen von Windows klingen roboterhaft – das lässt sich nicht
wegkonfigurieren. **piper** ist ein lokales neuronales TTS: kein Konto, keine
Cloud, nach dem Download kein Internet mehr nötig. Einmal einrichten:

```bat
python jarvis_run.py --stimme-einrichten en_GB-alan-medium
```

Das installiert `piper-tts`, lädt die Stimme nach `stimmen\` neben
`jarvis_run.py` und schreibt `TTS_ENGINE=piper`, `PIPER_VOICE` und
`PIPER_DATA_DIR` in deine `.env`. Danach normal starten.

Im laufenden Chat geht es genauso: `/stimme installieren en_GB-alan-medium`.
Danach ist die neue Stimme **sofort** aktiv, ohne Neustart.

| Kennung | Klang |
|---|---|
| `en_GB-alan-medium` | Britisch, ruhig, sachlich – **die Butler-Stimme**. Empfehlung. |
| `en_GB-northern_english_male-medium` | Britisch, wärmer, etwas rauer |
| `en_US-ryan-high` | Amerikanisch, tief und gelassen, höchste Qualitätsstufe |
| `en_US-joe-medium` | Amerikanisch, neutral, nachrichtensprecherhaft |
| `de_DE-thorsten-medium` | Deutsch, männlich, klar – der Standard für Deutsch |
| `de_DE-karlsson-low` | Deutsch, männlich, ruhiger und dunkler |

Die vollständige Liste zeigt `python jarvis_run.py --stimmen` bzw. `/stimme liste`;
ein `✓` markiert die schon installierten. Anhören lassen sich alle unter
[rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/).

Feinschliff über die `.env`:

```env
PIPER_VOICE=en_GB-alan-medium
PIPER_DATA_DIR=            # leer = Ordner "stimmen" neben jarvis_run.py
PIPER_LENGTH_SCALE=1.08    # 1.0 = Originaltempo, mehr = gesetzter
PIPER_NOISE_SCALE=0.667    # Variation in der Betonung
PIPER_NOISE_W=0.8          # Variation in der Sprechdauer
```

`/tempo` und `TTS_RATE` wirken auch auf piper: Der Wert wird in die Längenskala
umgerechnet (175 = Normaltempo).

Der Ton läuft unter Windows über das eingebaute `winsound` – kein `ffplay`,
kein zusätzliches Audioprogramm. Unter Linux/macOS wird `aplay`, `afplay`,
`ffplay` oder `paplay` genommen, was vorhanden ist.

**Nichts wurde ersetzt:** `pyttsx3` (Systemstimmen), `espeak` und `say` bleiben
als Rückfallebene erhalten. `TTS_ENGINE=auto` probiert der Reihe nach
piper → pyttsx3 → espeak/say. Wer die alte, separat installierte `piper.exe`
nutzt, wird über `PIPER_BINARY`/`PIPER_MODEL` weiterhin bedient.

## Alte Notizen zur Sprachausrichtung

Unter Windows funktioniert `pyttsx3` sofort mit den SAPI5-Stimmen (`TTS_VOICE=Hedda`).

**Eingabe** – `faster-whisper`, komplett lokal. `STT_MODEL=base` ist ein guter
Kompromiss; `small` ist bei Fachbegriffen deutlich besser, `STT_DEVICE=cuda`
beschleunigt spürbar. Die Aufnahme stoppt nach `STT_SILENCE_SECONDS` Stille.

## Wenn etwas klemmt

**Erste Anlaufstelle:** `python jarvis_run.py --doktor` bzw. `/doktor` im Chat. Das zeigt,
welche `.env` gelesen wurde, ob die Vault existiert, ob Ollama läuft und ob die Modelle da sind.

| Symptom | Ursache / Lösung |
|---|---|
| Banner zeigt `Vault: obsidian` und `Modell: llama3` | Das sind die Defaults – die `.env` wurde nicht gefunden. Meist heißt sie unter Windows in Wahrheit `.env.txt` (Endungen sind ausgeblendet). `dir /a` zeigt den echten Namen. Sofortlösung: `python jarvis_run.py --vault "C:\Users\...\Zeus"` |
| `FileNotFoundError: Vault-Ordner nicht gefunden` | Pfad in `OBSIDIAN_VAULT` stimmt nicht. Im Chat `/vault <Pfad>` setzen – wird direkt in die `.env` geschrieben |
| `Read timed out` | Sollte behoben sein. Falls doch: `/tempo` messen. Unter 5 Token/s ist das Modell zu groß für die Hardware – `qwen2.5:14b` testen oder `NUM_CTX` senken. `IDLE_TIMEOUT` erhöhen hilft nur beim Symptom |
| Erste Frage nach dem Start hängt ewig | Modell wird geladen. `WARMUP=true` erledigt das beim Start, `KEEP_ALIVE=30m` hält es im Speicher |
| Antwort bricht mit Hinweis ab | Das Modell hat `IDLE_TIMEOUT` lang nichts geliefert – die Teilantwort bleibt erhalten. Meist eine Wiederholungsschleife; `REPEAT_PENALTY` erhöhen |
| Ausgabe schiebt sich über den alten Bildschirminhalt | Altes `cmd.exe`. Seit v7.1 wird ANSI aktiv eingeschaltet und der Bildschirm hart geleert. Bleibt es zerrissen: `/ansicht aus` bzw. `LIVE_VIEW=aus` – dann wird nur mitgeschrieben statt neu gezeichnet. `/cls` leert zwischendurch |
| Umlaute als Kästchen | Die Ausgabe wird auf UTF-8 gestellt; im alten cmd.exe zusätzlich `chcp 65001` vor dem Start. Windows Terminal statt `cmd.exe` löst beides dauerhaft |
| `PortAudio library not found` | Nur die Spracheingabe betroffen, der Rest läuft weiter. Unter Windows: `pip install sounddevice`; unter Linux `sudo apt install libportaudio2` |
| „Modell nicht gefunden“ beim Start | `ollama pull <modell>`; `/status` zeigt, was konfiguriert ist |
| Erste Indexierung dauert lang | normal – danach nur noch Änderungen; `EMBED_WORKERS` erhöhen |
| Antworten ignorieren neue Notizen | `/sync`, bei Verdacht auf Inkonsistenz `/reindex` |
| Agent sagt „erledigt", aber nichts passiert | Sollte nicht mehr vorkommen – Schreibaufträge laufen über den Planer und melden jede Datei einzeln. Falls doch: `/doktor` zeigt, ob der Planer aktiv ist; `PLANNER_ENABLED=true` setzen |
| Antwort kommt auf Englisch | `LANGUAGE=Deutsch` in der `.env`; kleine Modelle ignorieren das gelegentlich trotzdem – `qwen2.5` hält sich daran |
| Bericht meldet fehlgeschlagene Notizen | „nochmal versuchen" sagen – nur die fehlenden werden neu angegangen. Grund steht im Bericht und in `.jarvis/logs/` |
| Antwort gibt Inhalte unvollständig wieder | Sollte behoben sein (ganze Kapitel statt Fragmente). Falls doch: `FULL_NOTE_CHARS` und `SECTION_MAX_CHARS` erhöhen und `NUM_CTX` mitziehen |
| Agent hat etwas Falsches geschrieben | `/rueckgaengig`; ältere Stände liegen in `<Vault>/.jarvis/backups/` |
| Notiz landet im falschen Ordner | `/merke` mit klarerem Thema, oder Ordnernamen präziser benennen; die Begründung steht im Frontmatter |
| Keine Sprachausgabe | `python jarvis_run.py --stimme-einrichten` – das erledigt Paket und Stimme in einem Zug |
| Stimme klingt blechern | Es läuft noch pyttsx3. `/stimmen` zeigt es; `/stimme installieren` wechselt auf piper |
| Mikrofon reagiert nicht | `pip install faster-whisper sounddevice`, Eingabegerät prüfen |
| Umlaute im Terminal kaputt | Windows: `chcp 65001` vor dem Start |


## License

ZEUS is licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**. You may use, modify, and redistribute ZEUS under the terms of this license.

See the [`LICENSE`](LICENSE) file for the full license text.

### Third-party dependencies

ZEUS uses third-party Python packages and software components, each of which is subject to its own license terms and copyright notices.

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for an overview of the project's direct dependencies and their licenses.

### AI models and voice files

AI models, model weights, embedding models, speech-recognition models, and text-to-speech voice files are **not included in this repository by default** and may need to be downloaded separately.

These components are subject to their own license terms, which may differ from the license of ZEUS. Users are responsible for reviewing and complying with the applicable terms, particularly regarding commercial use and redistribution.

See [`docs/MODEL_LICENSES.md`](docs/MODEL_LICENSES.md) for further information about the models and voice files used by ZEUS.

### Disclaimer

ZEUS is an independent project and is not affiliated with or endorsed by the authors or maintainers of third-party software, AI models, or voice files used with it, unless explicitly stated otherwise.
