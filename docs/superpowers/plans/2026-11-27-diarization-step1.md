# Rozlišení mluvčích – krok 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Přepis nahrávky ≥ 60 s s více mluvčími se v poznámce zobrazí po replikách `**Mluvčí N** (m:ss): text`.

**Architecture:** pyannote běží v samostatném venv jako podproces (`worker/diarize.py` → JSON úseků). Worker si od whisperu ponechá segmenty s časy, každému přiřadí mluvčího podle největšího překryvu a sousední segmenty sloučí do replik. Jakákoli chyba diarizace = dnešní přepis bez mluvčích.

**Tech Stack:** Python 3.13, faster-whisper (worker), pyannote.audio 4.1.1 + torch 2.11.0+cu128 (diar-venv), ffmpeg.

**Spec:** `docs/superpowers/specs/2026-11-27-diarization-step1-design.md`

## Global Constraints

- Repo je veřejné: žádné osobní údaje, e-maily ani absolutní cesty s uživatelským jménem v kódu/dokumentech (`%LOCALAPPDATA%`, `Path.home()`).
- Diarizace nikdy nezablokuje vznik poznámky – každá chyba → log `warning` + přepis bez mluvčích.
- Práh: `DIAR_MIN_S = 60` (s). Jediný rozpoznaný mluvčí → přepis jako dnes (řádky textu bez značek).
- Formát repliky: `**Mluvčí N** (m:ss): text` (čas přes existující `fmt_dur`), repliky oddělené prázdným řádkem, číslování podle prvního výskytu.
- Venv: `%LOCALAPPDATA%\voice_inbox\diar-venv`, model `pyannote/speaker-diarization-community-1`, token `HF_TOKEN`.
- Testy jsou skript: `python worker\test_worker.py` (ne pytest), výstup `SOUHRN: n/n PASS`.

## Review Focus

1. Úloha Plánovače nemá `HF_TOKEN` v prostředí procesu (proměnná přibyla po startu session) → token se dočte z `HKCU\Environment`; test v Task 2.
2. Segment whisperu v mezeře mezi úseky diarizace (nulový překryv) → dostane mluvčího nejbližšího úseku, nezahodí se; test v Task 1.
3. Segment přes změnu mluvčího → mluvčí s větším překryvem, text se nedělí; test v Task 1.
4. Dlouhá nahrávka (1 h) → timeout podprocesu `max(300, 0.5 × délka)` s, pak fallback; test fallbacku přes `FAIL` v Task 2.
5. Dosumarizace (`resummarize`) po výpadku Clauda → bere uložený `transcripts/{rid}.txt` už s mluvčími, `speakers:` ve frontmatteru zůstane; test v Task 2.

---

### Task 1: Přiřazení segmentů mluvčím (čistá funkce)

**Files:**
- Modify: `worker/voice_inbox_worker.py` (nová funkce za `fmt_dur`, ~ř. 214)
- Test: `worker/test_worker.py` (nová sekce `# 0)` hned za `check`, před přípravou souborů)

**Interfaces:**
- Produces: `assign_speakers(segments: list[tuple[float, float, str]], turns: list[dict]) -> str` – `turns` = `[{"start": float, "end": float, "speaker": str}]`. Vrací hotový text přepisu: při ≥ 2 mluvčích repliky, jinak `"\n".join(texty)` (totéž co dnes). Prázdné `turns` → jako jeden mluvčí.
- Produces: `count_speakers(transcript: str) -> int` – počet různých `**Mluvčí N**` na začátku řádku (0 = bez diarizace).

- [ ] **Step 1: Napiš failing testy** – v `test_worker.py` `sys.path.insert(0, str(Path(WORKER).parent)); import voice_inbox_worker as w`, pak:

```python
T = [{"start": 0.0, "end": 5.0, "speaker": "SPEAKER_01"}, {"start": 5.5, "end": 9.0, "speaker": "SPEAKER_00"},
     {"start": 9.5, "end": 70.0, "speaker": "SPEAKER_01"}]
out = w.assign_speakers([(0.2, 2.0, "Ahoj."), (2.1, 4.8, "Jak se máš?"), (5.6, 8.9, "Dobře."), (61.0, 65.0, "Tak čau.")], T)
check("0 diar: repliky + přečíslování", out == "**Mluvčí 1** (0:00): Ahoj. Jak se máš?\n\n**Mluvčí 2** (0:06): Dobře.\n\n**Mluvčí 1** (1:01): Tak čau.", out)
check("0 diar: mezera → nejbližší mluvčí", w.assign_speakers([(0.0, 1.0, "A"), (5.2, 5.5, "B"), (6.0, 8.0, "C")], T)
      .startswith("**Mluvčí 1** (0:00): A\n\n**Mluvčí 2** (0:05): B C"))
check("0 diar: segment přes změnu → větší překryv", "**Mluvčí 2** (0:04): X" in w.assign_speakers([(4.0, 8.0, "X"), (0.0, 1.0, "A")], T))
check("0 diar: 1 mluvčí → prostý text", w.assign_speakers([(0.0, 1.0, "A"), (2.0, 3.0, "B")], T[:1]) == "A\nB")
check("0 diar: bez úseků → prostý text", w.assign_speakers([(0.0, 1.0, "A")], []) == "A")
check("0 diar: count_speakers", w.count_speakers(out) == 2 and w.count_speakers("A\nB") == 0)
```

Pozn.: segmenty se před zpracováním seřadí podle `start` (třetí check je předává obráceně).

- [ ] **Step 2: Spusť** `python worker\test_worker.py` → FAIL/AttributeError na `assign_speakers`.
- [ ] **Step 3: Implementuj** obě funkce. Mluvčí segmentu = argmax překryvu `min(end) - max(start)`; při nulovém překryvu s všemi úseky úsek s nejmenší vzdáleností středu segmentu od intervalu úseku. Prázdné texty segmentů přeskoč (`.strip()`).
- [ ] **Step 4: Spusť testy** → sekce 0 PASS, ostatní beze změny.
- [ ] **Step 5: Commit** `feat(worker): přiřazení segmentů přepisu mluvčím`

### Task 2: diarize.py, venv a napojení do workeru

**Files:**
- Create: `worker/diarize.py`, `worker/diar_requirements.txt`
- Modify: `worker/voice_inbox_worker.py` – `transcribe` (~ř. 185), nová `diarize`, `process` (~ř. 431), `render_note` (~ř. 303), `SYSTEM_PROMPT` (~ř. 77)
- Modify: `README.md` (sekce instalace: diar-venv + HF token, gated licence)
- Test: `worker/test_worker.py` (nová sekce `# 15)` před `SOUHRN`)

**Interfaces:**
- Consumes: `assign_speakers`, `count_speakers` z Task 1.
- Produces: `diarize.py <audio>` → stdout JSON `[{"start","end","speaker"}]` (z `exclusive_speaker_diarization`), exit 0; chyba → exit ≠ 0 + stderr. Audio dekóduje ffmpeg na 16 kHz mono do paměti (`{"waveform", "sample_rate"}`), pipeline `.to(cuda)`. Ověřený vzor: sonda z 2026-11-27 (26 s audia / 0,7 s).
- Produces: `transcribe(path) -> list[tuple[float, float, str]]`. `VOICE_INBOX_FAKE_TRANSCRIPT=text` → `[(0.0, 0.0, text)]`; nové `VOICE_INBOX_FAKE_SEGMENTS` = JSON `[[start, end, text], …]`.
- Produces: `diarize(path: Path, duration: float) -> list[dict] | None` – `None` při `duration < DIAR_MIN_S`, chybějícím venv/tokenu, rc ≠ 0, timeoutu, nevalidním JSON (vždy `log.warning`, kromě krátké nahrávky). Python venv `LOCAL / "diar-venv" / "Scripts" / "python.exe"` (přepis `VOICE_INBOX_DIAR_PY`), token z `os.environ` nebo `winreg` `HKCU\Environment\HF_TOKEN`, předává se v `env` podprocesu, `creationflags=NO_WINDOW`, timeout `max(300, 0.5 * duration)`. Fake `VOICE_INBOX_FAKE_DIAR` = JSON úseků nebo `FAIL` (→ `None` s warningem), práh 60 s platí i pro fake.
- `process`: text = `assign_speakers(segs, diarize(local, dur) or [])`; do state přibude `speakers=count_speakers(text)`, `diarize_s`; log `"%s: diarizace %d mluvčí, %.1f s"`.
- `render_note`: frontmatter řádek `speakers: N` za `duration`, jen když `count_speakers(transcript) >= 2`.
- `SYSTEM_PROMPT`: věta „Přepis může být rozdělený na repliky `**Mluvčí N** (čas)` – ve shrnutí uváděj, kdo co řekl, jen když na tom záleží; mluvčí nepojmenovávej.“

- [ ] **Step 1: Failing test 15** – vyrob 61s soubor `ff("-f","lavfi","-i","anullsrc=r=48000:cl=mono","-t","61","-c:a","aac", long)`, pak:

```python
segs = json.dumps([[0.5, 3, "Kdy dorazí panely?"], [4, 7, "Ve čtvrtek."]])
turns = json.dumps([{"start": 0, "end": 3.5, "speaker": "SPEAKER_00"}, {"start": 3.8, "end": 8, "speaker": "SPEAKER_01"}])
put(long, "20261127-150000.m4a"); worker(env={"VOICE_INBOX_FAKE_SEGMENTS": segs, "VOICE_INBOX_FAKE_DIAR": turns})
t = note_of("20261127-150000").read_text("utf-8")  # existující helper, vrací Path poznámky
check("15 diar: repliky v poznámce", "**Mluvčí 1** (0:00): Kdy dorazí panely?\n\n**Mluvčí 2** (0:04): Ve čtvrtek." in t, t)
check("15 diar: speakers ve frontmatteru", "\nspeakers: 2\n" in t and state()["20261127-150000"]["speakers"] == 2)
put(long, "20261127-150100.m4a"); worker(env={"VOICE_INBOX_FAKE_SEGMENTS": segs, "VOICE_INBOX_FAKE_DIAR": "FAIL"})
t = note_of("20261127-150100").read_text("utf-8")
check("15 diar FAIL: prostý přepis, poznámka vznikla", "Kdy dorazí panely?\nVe čtvrtek." in t and "speakers:" not in t, t)
put(speech, "20261127-150200.m4a"); worker(env={"VOICE_INBOX_FAKE_SEGMENTS": segs, "VOICE_INBOX_FAKE_DIAR": turns})
check("15 diar: krátká nahrávka bez diarizace", "Mluvčí" not in note_of("20261127-150200").read_text("utf-8"))
make_due(); worker(env={"VOICE_INBOX_FAKE_SUMMARY": fake_sm})  # proměnná fake_sm z testu 11
t = note_of("20261127-150000").read_text("utf-8")
check("15 diar: dosumarizace zachová repliky a speakers", state()["20261127-150000"]["note_status"] == "summarized"
      and "**Mluvčí 2** (0:04): Ve čtvrtek." in t and "\nspeakers: 2\n" in t, t)
check("15 diar: token z registru", w.hf_token() not in (None, ""))  # viz níže
```

Pro poslední check vystav `hf_token() -> str | None` (env → `winreg`) a v testu ho volej s dočasně odebraným `os.environ["HF_TOKEN"]`.

- [ ] **Step 2: Spusť** → sekce 15 FAIL.
- [ ] **Step 3: Implementuj** `diarize.py`, `diar_requirements.txt` (`torch==2.11.0+cu128`, `torchaudio==2.11.0+cu128`, `pyannote.audio==4.1.1`, `pytorch-metric-learning==2.9.0`; v hlavičce instalační příkaz s `--index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple`) a změny workeru podle Interfaces.
- [ ] **Step 4: Spusť testy** → `SOUHRN: n/n PASS` (vč. testů 1–14).
- [ ] **Step 5: Smoke reálné diarizace:** `& "$env:LOCALAPPDATA\voice_inbox\diar-venv\Scripts\python.exe" worker\diarize.py "G:\My Drive\voice_inbox\processed\2026-11\20261127-205602.m4a"` → JSON s jediným `SPEAKER_00`, exit 0.
- [ ] **Step 6: README** – instalace diar-venv, HF účet + licence modelu + token přes sync-env.
- [ ] **Step 7: Commit** `feat(worker): diarizace mluvčích přes pyannote (krok 1)`

### Task 3: Reálné ověření na nahrávce dvou mluvčích (čeká na Frankovu nahrávku)

- [ ] **Step 1:** Frank nahraje 1–2 min rozhovoru dvou lidí přes PWA; úloha Plánovače ji zpracuje.
- [ ] **Step 2:** Ověř v `worker.log` řádek `diarizace 2 mluvčí` a v poznámce repliky, které odpovídají skutečnosti (Frank potvrdí poslechem/přečtením). Chybné přiřazení krátkých replik zapiš jako nález, ne opravu.
- [ ] **Step 3:** Ověř, že úloha Plánovače (pythonw, bez okna) diarizovala – token z registru, žádné okno konzole.
- [ ] **Step 4:** `git push`; aktualizuj memory `reference_voice_inbox` (diar-venv, HF_TOKEN, práh 60 s).
