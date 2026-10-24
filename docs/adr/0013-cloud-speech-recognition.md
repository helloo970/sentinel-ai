# 13. ElevenLabs Scribe for speech recognition

- Status: accepted
- Date: 2026-10-22

## Context

The owner found the local transcription (faster-whisper `small`, CPU, beam 1) too slow and
too often wrong. He already uses an ElevenLabs key for the voice and chose ElevenLabs
Scribe for recognition.

## Decision

- `ElevenLabsSTT` sends each recorded utterance as a 16 kHz WAV to
  `POST /v1/speech-to-text` (model `scribe_v2`, language `de`). The key only goes in the
  `xi-api-key` header.
- Up to 50 key terms (Jarvis, SENTINEL, names and terms the user entered in the brain) are
  sent to bias recognition; the same list is the `initial_prompt` for the local model.
- If the request fails, the local model transcribes. After a missing permission or no
  credit, Scribe is paused for 10 minutes, after a network error for one minute.
- `SENTINEL_STT_ENGINE=local` keeps everything on the PC.
- Emergency stop words are still detected locally on the transcribed text.

## Consequences

- Recordings leave the PC and go to ElevenLabs; key terms add about 20 % to the cost.
- The key needs the "Speech to Text" permission in addition to "Text to Speech".
