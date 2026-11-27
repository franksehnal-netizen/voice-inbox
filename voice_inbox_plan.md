# Voice Inbox – plán v0.3 (2026-11-27)

Cíl: iPhone Zkratka (1 ikona: nahraj / stop) → audio na Drive → PC lokálně přepíše (Whisper),
Claude shrne a zařadí → poznámka v Obsidianu.

**Rozhodnutí Franka (2026-11-27):** frontend = iOS Zkratka; domény osobní / Zlín City / AE;
přepis + shrnutí + zařazení do složky (varianta A – Claude needopisuje existující stránky wiki);
zpracování **lokálně na PC** (žádné cloudové API pro přepis); bez notifikací; audio se nemaže.

**Kritérium úspěchu:** 2min česká nahrávka → při zapnutém PC do ≤5 min poznámka ve vaultu ve správné
doménové složce; opakovaný běh nevytvoří duplikát; při chybě nic neztraceno (audio zůstává, stav `failed` v logu).

**Změna 2026-11-27 večer:** frontend = vlastní web appka (`app/`, PWA, červené tlačítko uprostřed) místo Zkratky.
Upload endpoint `apps_script/` (Drive API v3, scope `drive.file`, token jen jako SHA-256) nasazen a otestován:
auth/validace/duplikát/15min soubor; appka v headless Chrome: normální nahrávka, offline fronta + retry, obnova po pádu stránky.
Hosting: Cloudflare Pages, projekt `voice-inbox` (`*.pages.dev`). Token se předá jednorázově odkazem `#t=…` → localStorage.

**Stav 2026-11-27 noc:** F1 + F2 hotové – `worker/voice_inbox_worker.py`, testy `worker/test_worker.py` 33/33 PASS
(duplikát, re-upload, pády po každém stavu, poškozené audio, ticho, webm, G: chybí, čerstvý soubor, mutex,
dosumarizování + přesun, ručně upravená poznámka se nemění, backoff shrnutí 10 min → 24 h / 6 pokusů,
3 reálná volání `claude -p` (sonnet, ~4 s) → správná doména a jen existující odkazy).
Volání: `claude.exe -p --tools "" --strict-mcp-config --setting-sources project --json-schema …` (`--bare` nejde – nečte OAuth).
Zbývá F3 (Task Scheduler, README, E2E z iPhonu) a deploy appky v2.

**Stav 2026-11-27 20:35:** appka v2 nasazena (`app/deploy.py`, sha256 10/10). Úloha „Voice Inbox Worker“ registrována
(`worker/install_task.ps1`): každých **5 min** (Frankovo rozhodnutí místo 2 min; prázdný běh ~0,3 s) + při přihlášení
(+2 min zpoždění na připojení G:). README hotové. Zbývá E2E z iPhonu (10 s, 2 min, 15 min) → read-back poznámek.

## Architektura

```
[iPhone Zkratka] Record Audio (Normal, AAC .m4a) → lokální kopie v „Na iPhonu"
   → Get Contents of URL: POST {recording_id, token, audio_base64} → [Apps Script doPost, execute as me]
   → Drive: My Drive/voice_inbox/incoming/<recording_id>.m4a (existující id nepřepíše) → HTTP 200 → Zkratka smaže lokální kopii
        │ Drive for desktop sync (G:)
        ▼
[PC worker, Task Scheduler při přihlášení + každé 2 min, pythonw, bez oken]
   1. named mutex (souběh) · logger jako první krok · stav lokálně %LOCALAPPDATA%\voice_inbox\state.json (atomic replace)
   2. nový <recording_id>.m4a → kopie do lokálního temp → ffprobe (retry ×3, pak stav failed)
   3. ffmpeg → 16k mono → faster-whisper large-v3 (cuda, cs, vad) → stav transcribed (přepis v state dir)
   4. claude -p BEZ nástrojů, 1 tah, JSON výstup: {domain, title, slug, summary, tasks[], tags[], related[]}
      – v promptu: přepis + seznam existujících stránek vaultu (Python, jen názvy) + popis 3 domén
      – related[] worker profiltruje na reálně existující stránky
   5. worker zapíše poznámku → stav noted
   6. přesun audia do voice_inbox/processed/YYYY-MM/ → stav archived
   Podprocesy: CREATE_NO_WINDOW + stdin=DEVNULL. claude -p selže → poznámka jen s přepisem, `status: needs-summary`,
   doména `nezarazeno`; další běh ji dosumarizuje a přesune.
```

Deterministický název poznámky = z `recording_id` → pád mezi kroky 5 a 6 nevytvoří duplikát (soubor už existuje → jen dokončit archivaci).

## Umístění ve vaultu

Poznámka = `type: source` (dle vault CLAUDE.md):
- `wiki/sources/voice/osobni/`, `wiki/sources/voice/zlin-city/`, `wiki/sources/voice/ae/`, `wiki/sources/voice/nezarazeno/`
- název `YYYY-MM-DD-HHMM-<slug>.md`; frontmatter `title, type: source, tags: [voice-note, <domena>, …], created, updated, recording_id, audio: <cesta v processed/>`
- sekce: Shrnutí · Úkoly · Přepis (plný) · `## Related` (hub domény `[[acoustic-empire]]` / `[[zlin-city-arch-moderna]]` + ověřené odkazy)
- `_hot`, `index`, `log` worker nemění. Zapracování do wiki (entity, rozhodnutí) dál ručně přes ae-capture.

## Fáze

| Fáze | Obsah | Ověření (koncový stav) |
|---|---|---|
| F0 spike | Apps Script `doPost` (clasp, nový projekt, token ve Script Properties) + Zkratka | nahrávka z telefonu leží v `G:\My Drive\voice_inbox\incoming\` – změřit zpoždění |
| F1 worker | přepis + stavový automat, zatím bez Clauda (poznámka s `needs-summary`) | testy: duplikát, pád uprostřed, poškozené audio, ticho, G: nepřipojený |
| F2 Claude | prompt + JSON schéma + validace, fallback | 3 testovací přepisy (osobní / Zlín / AE) → správná složka a jen existující odkazy |
| F3 nasazení | Task Scheduler (bez okna), README v této složce, memory pointer | E2E 3 reálné nahrávky (10 s, 2 min, 15 min) → read-back poznámek |

## Rizika
- PC vypnuté → zpracuje se po zapnutí (fronta na Drive). Mac zatím neřeším.
- Apps Script web app „anyone" + token: kdo zná URL a token, může nahrát soubor (nic nepřečte). Token jen ve Zkratce.
- iOS Zkratka: hovor/zamčení během nahrávání – ověřit v F0.
- `claude -p` čerpá z předplatného (1 krátký tah na nahrávku).
- Citlivý obsah: audio a přepisy na Drive + ve vaultu; přepis jde do Clauda (ne do dalších služeb).

## Historie review
v0.1 → v0.2: Gemini review 2026-11-27, 5 nálezů (3 CONFIRMED, 2 PLAUSIBLE) – claude bez nástrojů, upload přes
doPost s `recording_id`, lokální stav po krocích + mutex, kopie z G: do temp + ffprobe, CREATE_NO_WINDOW.
v0.2 → v0.3: Frankova rozhodnutí (výše); cloudová varianta (API přepis) zamítnuta – zpracování zůstává lokální.
