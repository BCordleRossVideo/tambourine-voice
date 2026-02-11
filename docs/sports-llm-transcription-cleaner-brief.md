# Sports Broadcast LLM Transcription Cleaner — Technical Brief

## Goal

Build a lightweight, sub-2-second pipeline that takes live speech-to-text output (partial or final transcriptions), runs it through an LLM formatting/correction step seeded with a player roster and sport-specific dictionary, and delivers the cleaned transcription to a downstream TCP receiver.

---

## Architecture Overview

```
 Microphone / Audio Feed
        │
        ▼
 ┌──────────────┐
 │  STT Engine   │   (Whisper, Deepgram, Azure, etc.)
 │  partials +   │
 │  finals        │
 └──────┬───────┘
        │  raw transcription frames
        ▼
 ┌──────────────┐
 │  Accumulator  │   Buffer partials; trigger on finals or silence timeout
 └──────┬───────┘
        │  complete utterance
        ▼
 ┌──────────────┐
 │  LLM Cleaner  │   Single-shot prompt w/ roster + dictionary context
 │  (Cerebras /   │
 │   Groq / local)│
 └──────┬───────┘
        │  cleaned text
        ▼
 ┌──────────────┐
 │  TCP Sender   │   Writes to downstream receiver (scoreboard, caption
 └──────────────┘    system, graphics engine, etc.)
```

---

## Latency Budget (Target: < 2 seconds end-to-end)

| Stage | Target | Notes |
|---|---|---|
| STT final delivery | ~300-500 ms | Most real-time STT engines finalize within this window after speech stops |
| Accumulator drain | ~200-300 ms | Adaptive timeout waiting for late STT chunks (see below) |
| LLM TTFB + generation | ~100-400 ms | Provider-dependent; Cerebras/Groq are sub-200 ms TTFB for short prompts |
| TCP write | < 10 ms | Local or LAN socket write |
| **Total** | **~600 ms - 1.2 s** | Well within the 2 s budget |

The tightest knob is **LLM provider choice**. Cerebras and Groq are purpose-built for fast inference on short-context tasks — exactly this use case. A local model via Ollama is also viable if the hardware supports it, eliminating network round-trips entirely.

---

## Key Components

### 1. Accumulator (Buffer + Trigger)

STT engines emit two kinds of frames:

- **Partials**: in-progress, unstable hypotheses that change as the speaker continues
- **Finals**: committed, stable transcriptions for a completed phrase/segment

The accumulator's job is to collect text and decide *when* to fire the LLM call:

```
Strategy A — Finals-only (simpler, recommended to start)
  Collect final transcription segments.
  On silence timeout (configurable, default 300 ms after last final),
  concatenate all finals into one utterance and send to LLM.

Strategy B — Partials-aware (lower perceived latency)
  Display partials to a preview buffer in real time (no LLM).
  When a final arrives, replace the partial with the LLM-cleaned final.
  Gives instant feedback while the "real" cleaned text catches up.
```

The adaptive drain pattern from Tambourine works well here: set a short timeout (e.g., 300 ms), reset it every time a new transcription arrives, and fire the LLM call once the timeout expires with no new input.

### 2. LLM Cleaner — The Prompt

This is the core of the system. A single system prompt instructs the LLM, and the user message contains the raw transcription. One call per utterance, no conversation history.

#### System Prompt Structure

The prompt has three concatenated sections:

```
┌─────────────────────────────────────────────┐
│  Section 1: Core Formatting Rules (always)  │
│  Section 2: Sport-Specific Dictionary       │
│  Section 3: Active Roster                   │
└─────────────────────────────────────────────┘
```

#### Section 1 — Core Formatting Rules

```markdown
You are a live sports broadcast transcription corrector. You receive raw
speech-to-text output from a live announcer and clean it for display.

## Rules

- Fix capitalization and punctuation.
- Remove filler words (um, uh, you know, like, etc.).
- Correct obvious STT errors using the sport-specific dictionary
  and active roster provided below.
- Do NOT summarize, reword, or change the announcer's phrasing —
  preserve their voice and cadence.
- Do NOT add information that was not spoken.
- Output ONLY the cleaned text. No explanations, labels, or quotes.
- When a word sounds like a player name from the active roster,
  use the correct roster spelling.
- When a word sounds like a sport-specific term from the dictionary,
  use the correct dictionary spelling.
```

#### Section 2 — Sport-Specific Dictionary (Baseball Example)

```markdown
## Baseball Dictionary

Apply these corrections when the transcription contains words that
sound like these terms. Use the correct spelling shown here.

### General Terms
- RBI (runs batted in)
- ERA (earned run average)
- OPS (on-base plus slugging)
- WHIP (walks plus hits per inning pitched)
- K (strikeout)
- BB (base on balls / walk)
- double play
- ground ball, fly ball, line drive
- changeup, curveball, slider, cutter, sinker, splitter, four-seam, two-seam
- balk, infield fly, tag up, squeeze play, hit-and-run
- bullpen, closer, setup man, long reliever
- on-deck circle, batter's box, warning track

### Phonetic Corrections
- "ribby" or "ribbies" = RBI / RBIs
- "whip" (in pitching context) = WHIP
- "Kay" (in strikeout context) = K
- "punch out" = strikeout
- "can of corn" = routine fly ball
- "dinger" = home run
- "going yard" = hitting a home run
- "painting the corner" = precise pitch on the edge of the strike zone
```

#### Section 3 — Active Roster (Loaded Per-Game)

```markdown
## Active Roster

When the transcription contains a name that sounds like any player
below, use the exact spelling shown. Format: Name (#Number, Position).

### Home — New York Yankees
- Aaron Judge (#99, CF)
- Juan Soto (#22, RF)
- Giancarlo Stanton (#27, DH)
- Anthony Volpe (#11, SS)
- Gleyber Torres (#25, 2B)
- DJ LeMahieu (#26, 1B)
- Austin Wells (#28, C)
- Alex Verdugo (#24, LF)
- Gerrit Cole (#45, SP)

### Away — Boston Red Sox
- Rafael Devers (#11, 3B)
- Jarren Duran (#16, CF)
- Masataka Yoshida (#7, LF)
- Triston Casas (#36, 1B)
- Connor Wong (#12, C)
- Trevor Story (#10, SS)
- Brayan Bello (#66, SP)
```

#### What The LLM Receives

```
System: [Section 1 + Section 2 + Section 3, concatenated]
User:   "and judge steps up to the plate he's got a three eighty four oh
         PS this season uh soto on deck judge swings and drives one deep
         to left center going going gone a dinger for the big man"
```

#### What The LLM Returns

```
And Judge steps up to the plate. He's got a .384 OPS this season.
Soto on deck. Judge swings and drives one deep to left-center —
going, going, gone! A dinger for the big man.
```

Note: "oh PS" was corrected to "OPS" because it appears in the dictionary. "judge" and "soto" were capitalized because they match roster entries. Filler "uh" was removed. The announcer's cadence and wording were preserved.

### 3. Roster Loader

The roster is the most dynamic piece — it changes per game. Design it as a simple data source that can be swapped before each broadcast:

```
Option A: JSON file per game
  /rosters/2024-09-15-NYY-vs-BOS.json
  Loaded at pipeline startup or hot-reloaded via API.

Option B: API endpoint
  GET /api/roster/active → returns current roster
  Updated by production staff before the game.

Option C: CSV/spreadsheet import
  Drag-and-drop a lineup card export.
  Parsed into the prompt format at load time.
```

The roster gets templated into Section 3 of the prompt at the start of each session (or when the lineup changes mid-game, e.g., a substitution). The prompt is rebuilt and the LLM context is updated — no pipeline restart needed.

### 4. TCP Sender

After the LLM returns cleaned text, write it to a TCP socket for the downstream consumer:

```
Payload format (simple, parseable):

  <SOT>Cleaned transcription text here.<EOT>\n

  - SOT/EOT delimiters let the receiver frame messages
  - Newline-terminated for line-buffered readers
  - UTF-8 encoded
```

Alternatively, use a structured envelope if the receiver needs metadata:

```json
{"ts": 1694800000.123, "text": "Judge swings and drives one...", "final": true}
```

Keep the TCP connection persistent (open at pipeline start, reuse for all messages) to avoid per-message connection overhead.

---

## Provider Selection Guide

| Priority | Provider | Why |
|---|---|---|
| 1 | **Cerebras** | Fastest inference available (~100-200 ms TTFB). Ideal for this latency-critical, short-context task. |
| 2 | **Groq** | Comparable speed. Good fallback if Cerebras is unavailable. |
| 3 | **Local (Ollama + Llama 3)** | Zero network latency. Requires decent GPU. Best for air-gapped or on-premises broadcast environments. |
| 4 | **OpenAI / Anthropic** | Higher quality but 300-1000 ms TTFB. Use only if latency budget allows or for non-live post-processing. |

For a 1-2 second budget, Cerebras or Groq are the right choices. The prompt is short (~500-800 tokens for dictionary + roster + rules) and the input/output are both short (a sentence or two), so these speed-optimized providers excel.

---

## Operational Considerations

### Roster Updates Mid-Game

When a substitution happens, the production operator updates the roster (via API, file reload, or UI). The system rebuilds Section 3 of the prompt. The next utterance uses the updated roster — no restart, no downtime. This mirrors the Tambourine pattern of resetting context before each recording with the latest prompt state.

### Fallback: LLM Bypass

If the LLM provider is slow or down, bypass it entirely and send raw STT output straight to the TCP receiver. Uncleaned text is better than no text. This is a simple boolean gate in the pipeline — flip it and transcriptions flow directly to the TCP sender without touching the LLM.

### Dictionary Expansion

The sport dictionary is static per sport. Maintain a library:

```
/dictionaries/baseball.md
/dictionaries/football.md
/dictionaries/basketball.md
/dictionaries/hockey.md
```

Select the right one at pipeline startup based on the event type. These can be community-maintained and versioned.

### Prompt Size vs. Speed Tradeoff

Every token in the system prompt adds to TTFB. Keep the roster to the **active lineup** (18-26 players for baseball), not the full 40-man roster. Keep dictionary entries to terms the STT is likely to get wrong — don't include "home run" (STT gets this right) but do include "WHIP" and "OPS" (STT consistently mangles these).

Estimated prompt token counts:
- Core rules: ~200 tokens
- Sport dictionary: ~200-400 tokens
- Active roster (both teams): ~200-300 tokens
- **Total system prompt: ~600-900 tokens** — well within the fast-inference sweet spot

---

## Summary

This pipeline reuses the proven architecture from Tambourine's dictation cleaner:

1. **One LLM call per utterance** — not per word, not per partial
2. **Stateless** — no conversation history, context reset each utterance
3. **Three-section prompt** — rules + dictionary + roster, concatenated
4. **Speed-first provider** — Cerebras/Groq for sub-200 ms TTFB
5. **Adaptive drain** — wait just long enough for STT to finish, then fire
6. **Bypassable** — raw fallback if the LLM is slow or down

The result: an announcer says "judge hits a dinger to left," and within 1-2 seconds the downstream system receives "Judge hits a dinger to left." — correctly capitalized, roster-matched, and cleaned.
