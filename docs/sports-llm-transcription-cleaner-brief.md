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

---

## Testing Learnings (Feb 2026)

Initial integration testing against Tambourine's pipeline with custom baseball broadcast prompts surfaced several issues. These findings should inform the next iteration of the design.

### 1. Turn Detection Is the Biggest Bottleneck

**Problem:** The user had to manually press "stop recording" to end each turn. After stop-recording, the system enters a WaitingForSTT state and falls back to a **1.5-second timeout every single time** because the VAD speech-stopped signal never arrives in time. This adds 1.5s of dead time to every turn before the LLM even starts.

**Evidence:**
```
16:40:26.841 | Stop-recording received, waiting for STT to finalize
16:40:28.341 | Timeout waiting for speech stopped after 1.5s  ← always hits
16:40:28.466 | LLM processing starts
```

**Root cause:** The current turn detection relies on the user pressing stop, then waits for a VAD signal that never comes (likely because audio stops immediately when recording stops). The timeout is the only exit path.

**Impact on latency budget:**
| Stage | Brief Target | Actual Measured |
|---|---|---|
| Manual stop + STT timeout | 200-300 ms | **1,500 ms** (always hits ceiling) |
| LLM inference | 100-400 ms | **700-1,300 ms** |
| **Total (stop → cleaned text)** | **600-1,200 ms** | **2,200-2,900 ms** |

**Possible mitigations:**
- For live broadcast, automatic turn detection via VAD silence threshold (e.g., 500ms of silence = end of utterance) would eliminate the manual stop requirement entirely.
- Reduce the STT timeout from 1.5s to something shorter (e.g., 300-500ms) since the VAD signal never arrives anyway — the timeout is effectively the turn-end delay.
- Investigate why VADUserStoppedSpeakingFrame doesn't fire in WaitingForSTTState — may be a timing issue with audio stream cutoff.

### 2. LLM Inference Is Slower Than Projected

**Problem:** The brief projected 100-200ms TTFB for Cerebras, but measured inference (TTFB + full generation) on `gpt-oss-120b` was **700ms-1,300ms** per turn.

**Likely causes:**
- The three-section prompt (main + dictionary + advanced) is significantly larger than the brief's ~600-900 token estimate. The baseball prompts include formatting examples, disambiguation tables, backtrack correction rules, and announcement templates.
- `gpt-oss-120b` is a 120B parameter model — fast by cloud standards, but not as fast as the smaller `llama3.1-8b` would be for this use case.
- The brief's 100-200ms figure was TTFB only; total generation time for a cleaned sentence adds more.

**Action items:**
- Benchmark `llama3.1-8b` on Cerebras — smaller model, faster inference, may be sufficient for transcription cleaning.
- Measure actual prompt token count and trim aggressively. The advanced prompt (backtrack corrections, extra templates) may not justify its token cost.
- Profile TTFB vs. generation time separately to understand where the time goes.

### 3. Name Disambiguation Is Unreliable (~50% Accuracy)

**Problem:** Two test roster players with similar-sounding names — **John Smith (#22, SS)** and **Jon Smyth (#24, RF)** — were disambiguated correctly only about half the time, despite an explicit disambiguation table in the dictionary prompt.

**Test results:**
| Raw STT Input | Expected Output | Actual Output | Correct? |
|---|---|---|---|
| "number 22 John Smith" | #22 John Smith | #22 John Smith | Yes |
| "number 24 John Smith" | #24 Jon Smyth | #24 John Smith | **No** |
| "number 24 Smith" | #24 Jon Smyth | #24 Jon Smyth | Yes |

**Analysis:** When the STT outputs a name that exactly matches one roster player ("John Smith") but the jersey number matches a different player (#24 → Jon Smyth), the LLM faces a conflict. The prompt says to use jersey number to disambiguate, but the LLM sometimes anchors on the literal name match instead. This only fails when the STT confidently outputs the wrong player's exact name — when the STT outputs just a last name ("Smith"), disambiguation works.

**Possible mitigations:**
- Strengthen the disambiguation instruction with more explicit priority ordering: "Jersey number ALWAYS takes priority over name spelling when they conflict."
- Add a negative example to the prompt showing this exact failure case.
- Consider a two-pass approach: first resolve jersey number to a player, then use that player's name regardless of what STT transcribed.

### 4. Avoid "Thinking" Models (Qwen 3)

**Problem:** `qwen-3-32b` on Cerebras is a chain-of-thought model that outputs `<think>` reasoning blocks before the actual response. These thinking tokens are included in the cleaned text output, making it unusable.

**Evidence:**
```
Cleaned text: '<think>
Okay, let's see. The user provided a raw transcription and wants it cleaned...
[~200 tokens of reasoning]
</think>

Testing. One. Two three. Mic check. 123. Oh'
```

**Impact:** The thinking adds ~200+ tokens of latency and pollutes the output. Even if we stripped the `<think>` tags post-hoc, the extra generation time defeats the purpose of a fast inference provider.

**Rule:** Only use non-thinking (non-CoT) models for this pipeline. Stick to `gpt-oss-120b` or `llama3.1-8b` on Cerebras. If using other providers, verify the model doesn't default to chain-of-thought output.

### 5. Garbled Late STT Transcriptions

**Problem:** After stop-recording, a late transcription arrived with garbled content: `"Third rash under breast. I mean"`. This appears to be the STT engine misrecognizing ambient noise or mic artifacts as the audio stream closes.

**Impact:** The garbled text was concatenated into the LLM input. The "I mean" backtrack correction rule in the advanced prompt did work (the LLM dropped the correction phrase), but the garbled prefix was a wasted STT/LLM cycle.

**Possible mitigations:**
- Shorter draining timeout to reduce the window for garbled late arrivals.
- Minimum transcription confidence threshold to reject low-quality late frames.

### Updated Latency Budget (Realistic)

| Stage | Original Target | Measured Reality | Notes |
|---|---|---|---|
| STT final delivery | 300-500 ms | ~4-5s from speech start | Includes user speaking time; final arrives quickly after speech ends |
| Turn detection overhead | 200-300 ms | **1,500 ms** | Timeout always fires; VAD signal never arrives |
| LLM TTFB + generation | 100-400 ms | **700-1,300 ms** | gpt-oss-120b with full 3-section prompt |
| TCP write | < 10 ms | N/A | Not yet tested |
| **Total (stop → cleaned)** | **600-1,200 ms** | **2,200-2,900 ms** | ~2x over budget |

### Priority Actions

1. **Reduce turn detection dead time** — lower STT timeout or fix VAD signal propagation.
2. **Benchmark smaller models** — `llama3.1-8b` may hit the latency target where `gpt-oss-120b` doesn't.
3. **Trim prompt tokens** — measure actual token count, cut what doesn't earn its latency cost.
4. **Strengthen name disambiguation** — add explicit priority rules and negative examples.
5. **Avoid thinking models** — `qwen-3-32b` and similar CoT models are incompatible with this pipeline.
