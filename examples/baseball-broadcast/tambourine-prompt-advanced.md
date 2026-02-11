<!-- tambourine-prompt: advanced -->
enabled: true
mode: manual
---
## Backtrack Corrections

Handle mid-broadcast announcer corrections by outputting only the corrected version:

- If the announcer uses "actually" to correct themselves (e.g., "strike two actually strike three"), output only the revised version ("strike three").
- If "scratch that" is spoken, remove the immediately preceding phrase and use the replacement (e.g., "safe at second scratch that he's out" becomes "he's out").
- The words "wait" or "I mean" also signal a correction; replace the prior phrase with the revised one (e.g., "caught by the shortstop I mean the second baseman" becomes "caught by the second baseman").
- For restatements (e.g., "two to one... make that three to one"), output only the final version ("3-1").

**Examples:**
- "Strike two actually strike three, he's out!" → "Strike three, he's out!"
- "Safe at second scratch that he's out at second" → "He's out at second."
- "Caught by the shortstop I mean the second baseman" → "Caught by the second baseman."
- "That makes it two to one wait three to one Yankees" → "That makes it 3-1 Yankees."

## Baseball Announcement Templates

Format standard baseball announcements into their canonical display forms. These are structured graphics/scoreboard phrases, not natural commentary — format them precisely.

### Batting / Pitching Introductions
- "now batting [position] number [N] [name]" → "Now Batting: [Position] #[N] [Name]"
- "now pitching number [N] [name]" → "Now Pitching: #[N] [Name]"
- "leading off [position] number [N] [name]" → "Leading Off: [Position] #[N] [Name]"
- "batting [Nth] [position] number [N] [name]" → "Batting [Nth]: [Position] #[N] [Name]"

### Substitutions
- "pinch hitting for [player] number [N] [name]" → "Pinch Hitting for [Player]: #[N] [Name]"
- "pinch running for [player] number [N] [name]" → "Pinch Running for [Player]: #[N] [Name]"
- "now pitching in relief number [N] [name]" → "Now Pitching in Relief: #[N] [Name]"
- "defensive substitution [position] number [N] [name]" → "Defensive Substitution: [Position] #[N] [Name]"

### Position Name Normalization
When positions are spoken as abbreviations or colloquial forms, normalize them:
- "short stop" / "short top" / "SS" → Shortstop
- "first base" / "first baseman" / "1B" → First Base
- "second base" / "second baseman" / "2B" → Second Base
- "third base" / "third baseman" / "3B" → Third Base
- "left field" / "left fielder" / "LF" → Left Field
- "center field" / "center fielder" / "CF" → Center Field
- "right field" / "right fielder" / "RF" → Right Field
- "catcher" / "C" → Catcher
- "pitcher" / "P" → Pitcher
- "designated hitter" / "DH" → Designated Hitter

**Examples:**
- "now batting short top number 22 john smith" → "Now Batting: Shortstop #22 John Smith"
- "batting third right fielder number 24 john smith" → "Batting 3rd: Right Field #24 Jon Smyth"
- "leading off center field number 99 aaron judge" → "Leading Off: Center Field #99 Aaron Judge"
- "pinch hitting for wells number 24 john smith" → "Pinch Hitting for Wells: #24 Jon Smyth"

## Score and Inning Formatting

Format score and inning references consistently:

### Scores
- "five to three" → "5-3"
- "tied at two" → "Tied 2-2"
- "yankees five red sox three" → "Yankees 5, Red Sox 3"
- "the score is two nothing" → "The score is 2-0"

### Innings
- "top of the first" → "Top of the 1st"
- "bottom of the seventh" → "Bottom of the 7th"
- "middle of the fifth" → "Middle of the 5th"
- "end of the third" → "End of the 3rd"
- "seventh inning stretch" → "7th Inning Stretch"
- "going to extras" / "extra innings" → "Extra Innings"

### Count (Balls-Strikes)
- "two and one count" → "2-1 count"
- "oh and two" → "0-2"
- "full count" → "Full count (3-2)"
- "three and oh" → "3-0"

## Stat Line Formatting

When the announcer reads off stats, format them cleanly:

- "batting three twenty five" → "batting .325"
- "ERA of two point seven five" → "ERA of 2.75"
- "oh PS of eight fifty" → "OPS of .850"
- "six for his last ten" → "6-for-his-last-10"
- "two for four with a homer and three RBIs" → "2-for-4 with a homer and 3 RBIs"
