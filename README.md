# Voice Inbox

Hlasová poznámka z mobilu → přepis na PC (lokálně, Whisper) → shrnutí a zařazení (Claude) → poznámka v Obsidianu.
Plán, rozhodnutí a historie: `voice_inbox_plan.md`.

```
iPhone PWA (app/, https://voice-inbox-5l6.pages.dev)
  → Apps Script endpoint (apps_script/) → My Drive/voice_inbox/incoming/<yyyyMMdd-HHmmss>.<ext>
  → PC: naplánovaná úloha „Voice Inbox Worker“ (každých 5 min + při přihlášení, pythonw, bez okna)
      worker/voice_inbox_worker.py: whisper large-v3 (GPU) → claude -p (sonnet, bez nástrojů)
  → Obsidian wiki/sources/voice/{ae,zlin-city,osobni,nezarazeno}/YYYY-MM-DD-HHMM-<slug>.md
  → audio do My Drive/voice_inbox/processed/YYYY-MM/ (nikdy se nemaže)
```

## Provoz

| Co | Kde |
|---|---|
| Log | `%LOCALAPPDATA%\voice_inbox\worker.log` (bez práce se nic nezapisuje) |
| Stav nahrávek | `%LOCALAPPDATA%\voice_inbox\state.json` – `new → transcribed → noted → archived`, chyba `failed` |
| Přepisy | `%LOCALAPPDATA%\voice_inbox\transcripts\<id>.txt` |
| Úloha | Plánovač úloh → „Voice Inbox Worker“; instalace/změna `worker/install_task.ps1` |

- Poznámka `status: needs-summary` (v `nezarazeno`) = shrnutí selhalo; worker to zkouší znovu (6× s odstupem 10 min → 24 h),
  pak ji přesune do domény. Ručně upravenou poznámku (bez textu „Čeká na shrnutí“) už nemění.
- `status: no-speech` = v nahrávce nebyla řeč.
- Nahrávka `failed` (3× nečitelné audio) zůstává v `incoming`. Znovu zkusit: `python worker/voice_inbox_worker.py --retry-failed`.
- Index, `_hot` a log vaultu worker nemění; zapracování do wiki ručně (ae-capture).

## Příkazy

```powershell
python worker\voice_inbox_worker.py                 # jeden běh ručně
python worker\test_worker.py                        # testy (~1 min, GPU + 3 krátká volání Clauda)
powershell -File worker\install_task.ps1            # (pře)registrace úlohy
Unregister-ScheduledTask -TaskName 'Voice Inbox Worker' -Confirm:$false   # vypnout
python app\deploy.py                                # nasadit PWA (ověří sha256)
```

Závislosti: Python 3.13 + faster-whisper + nvidia-cublas/cudnn (viz memory `reference_local_transcription_whisper`),
ffmpeg/ffprobe v PATH, přihlášený `claude` CLI (npm). Token appky: memory `reference_voice_inbox`.
