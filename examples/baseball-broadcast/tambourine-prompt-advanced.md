<!-- tambourine-prompt: advanced -->
enabled: true
mode: manual
---
## Backtrack Corrections

Handle mid-broadcast announcer corrections by outputting only the corrected version:

- "actually" signals a correction: output only the revised version.
- "scratch that" removes the preceding phrase; use the replacement.
- "wait" or "I mean" also signal corrections; replace the prior phrase.
- For restatements (e.g., "two to one... make that three to one"), output only the final version.

**Examples:**
- "Strike two actually strike three, he's out!" → "Strike three, he's out!"
- "Safe at second scratch that he's out at second" → "He's out at second."
- "Caught by the shortstop I mean the second baseman" → "Caught by the second baseman."
- "That makes it two to one wait three to one Yankees" → "That makes it 3-1 Yankees."

## Additional Announcement Templates

These supplement the templates in the main prompt:
- "leading off [position] number [N] [name]" → "Leading Off: [Position] #[N] [Name]"
- "batting [Nth] [position] number [N] [name]" → "Batting [Nth]: [Position] #[N] [Name]"
- "pinch running for [player] number [N] [name]" → "Pinch Running for [Player]: #[N] [Name]"
- "defensive substitution [position] number [N] [name]" → "Defensive Substitution: [Position] #[N] [Name]"

## Additional Formatting

Non-obvious patterns the LLM should handle beyond the main prompt:
- "tied at two" → "Tied 2-2" (expand to show both scores)
- "yankees five red sox three" → "Yankees 5, Red Sox 3" (team-name score format)
- "the score is two nothing" → "The score is 2-0"
- "full count" → "Full count (3-2)"
- "seventh inning stretch" → "7th Inning Stretch"
- "extra innings" → "Extra Innings"
- "six for his last ten" → "6-for-his-last-10"
- "two for four with a homer and three RBIs" → "2-for-4 with a homer and 3 RBIs"
