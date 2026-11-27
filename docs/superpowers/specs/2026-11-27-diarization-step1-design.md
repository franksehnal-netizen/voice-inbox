# Rozlišení mluvčích – krok 1: diarizace po replikách

Schváleno 2026-11-27. Krátký design (malá změna).

## Proč
Přepis nahrávky s více lidmi je dnes jeden blok textu – není poznat, kdo co řekl. Krok 1 ze tří:
1. diarizace po replikách (tento dokument),
2. poznat autora podle hlasu (enrollment) → jméno místo „Mluvčí N“,
3. Claude odhadne jména ostatních z obsahu (označeno jako odhad).

## Cíle / ne-cíle / anti-cíle
- Cíl: přepis rozdělený na repliky `**Mluvčí N** (m:ss): text`, shrnutí od Clauda dostane text s mluvčími.
- Ne-cíl: identita mluvčích (kroky 2–3), cloudové služby.
- Anti-cíl: diarizace nesmí nikdy zablokovat ani zdržet vznik poznámky – při chybě platí dnešní chování.

## Přístupy
- **A – pyannote.audio 4 + `pyannote/speaker-diarization-community-1` lokálně (zvoleno).** CC-BY-4.0, gated model (HF účet, licence, read token `HF_TOKEN`). Ověřeno 2026-11-27: běží na RTX 4070 SUPER, 26 s audia za 0,7 s.
- B – NVIDIA Sortformer (NeMo): na Windows těžká instalace.
- C – DiariZen: méně ověřený.
- D – cloud (ElevenLabs Scribe): v rozporu s „zpracování zůstává lokální“.
- E – jen Claude z textu: bez audia nespolehlivé.

## Návrh
- **Izolace:** pyannote + torch CUDA v samostatném venv `%LOCALAPPDATA%\voice_inbox\diar-venv` (worker torch nemá a nechceme ho tam). Pevné verze v `worker/diar_requirements.txt` (torch/torchaudio 2.11.0+cu128 – novější torch z PyPI je CPU a kolidoval s torchaudio).
- **`worker/diarize.py`** – spouští se v diar-venv jako podproces: vstup audio soubor, výstup JSON `[{"start", "end", "speaker"}]` z `exclusive_speaker_diarization` (žádné překryvy). Audio dekóduje ffmpeg → waveform v paměti (obchází torchcodec).
- **Worker:** `transcribe` zachová segmenty s časy. U nahrávek ≥ 60 s zavolá diarizaci; každému segmentu whisperu přiřadí mluvčího s největším časovým překryvem, sousední segmenty téhož mluvčího sloučí do repliky. Mluvčí se přečíslují `Mluvčí 1, 2…` podle prvního výskytu.
- **Formát:** `**Mluvčí 1** (0:42): text` – odstavec na repliku. Jediný mluvčí → přepis jako dnes.
- **Fallback:** chybí `HF_TOKEN`/venv, nenulový návratový kód, timeout → varování do logu, přepis bez mluvčích.
- **Úložiště:** `transcripts/{rid}.txt` drží už text s mluvčími (dosumarizace ho tak použije). Frontmatter `speakers: N` (jen když N ≥ 2).
- **Testy:** čistá funkce přiřazení segment → mluvčí v `worker/test_worker.py` na syntetických datech; falešná diarizace přes proměnnou prostředí jako u stávajících fake režimů. Reálný test na nahrávce se 2 mluvčími z iPhonu.

## Nechává se na implementaci
Timeout podprocesu, přesné jméno fake proměnné, předání `HF_TOKEN` z user env do úlohy Plánovače.
