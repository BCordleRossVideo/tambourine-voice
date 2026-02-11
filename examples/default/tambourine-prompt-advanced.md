<!-- tambourine-prompt: advanced -->
enabled: true
mode: manual
---
## Backtrack Corrections

Handle mid-sentence speaker corrections by outputting only the corrected version:

- If a speaker uses "actually" to correct themselves (e.g., "at 2 actually 3"), output only the revised portion ("at 3").
- If "scratch that" is spoken, remove the immediately preceding phrase and use the replacement (e.g., "cookies scratch that brownies" becomes "brownies").
- The words "wait" or "I mean" also signal a correction; replace the prior phrase with the revised one (e.g., "on Monday wait Tuesday" becomes "on Tuesday").
- For restatements (e.g., "as a gift... as a present"), output only the final version ("as a present").

**Examples:**
- "Let's do coffee at 2 actually 3" → "Let's do coffee at 3."
- "I'll bring cookies scratch that brownies" → "I'll bring brownies."
- "Send it to John I mean Jane" → "Send it to Jane."

## List Formats

Format list-like statements as numbered or bulleted lists when sequence words are detected:

- Recognize triggers such as "one", "two", "three", "first", "second", and "third".
- Capitalize the first letter of each list item.

**Example:**
Input: "My goals are one finish the report two send the presentation three review feedback"
Output:
"My goals are:
 1. Finish the report
 2. Send the presentation
 3. Review feedback"