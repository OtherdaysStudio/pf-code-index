# How this was measured, and what failed

Everything here was measured, not estimated. The projects are private, so they appear by shape
rather than by name.

## Method

Each run was a fresh agent (Claude sonnet, read-only tools) with no prior context, given one task
and one arm:

- **control** — grep, glob and file reads
- **treatment** — `pf` only, with its usage note in the prompt

Cost is **marginal search traffic**: the tokens of every tool input plus every tool result,
excluding the agent's final answer, counted from the session transcript with tiktoken
(`cl100k_base`). The agent's final answer is excluded because both arms pay for it, so including it
flatters the cheaper arm.

Totals: about 275 fresh agents, 33 rounds, 7 tasks, 4 projects.

## Three measurement errors worth knowing about

1. **Counting the deliverable as search cost.** The first figures included each agent's final
   answer. Both arms pay that, so the comparison was inflated. Excluded afterwards.
2. **Anchoring on one control run.** The first control run cost 2,706 tokens and every reduction was
   quoted against it. Two more cold controls then cost 392 and 624. The honest baseline is the
   *cheapest* control measured, not the first one.
3. **Scoring a running agent.** One arm looked free (34 tokens, no answer) because its transcript
   was read before it finished. It had actually spent 725 tokens and answered correctly.

If you benchmark a search tool, expect the control arm to vary by a factor of seven. A single
control run proves nothing.

## The benchmark

Task: in a 1,900-file iOS repo (48% Swift, 40% Python, with a web folder), find the view property
that draws a border beam on one screen, and name the components it renders.

| arm | tokens | tool calls | complete |
|---|---|---|---|
| the author, by hand, 7 calls | 5,694 | 7 | yes |
| cold grep agents, 5 runs | 392, 624, 2,060, 2,415, 2,706 | 3 to 9 | 5/5 |
| pf, with a language word, 5 runs | 49, 63, 63, 68, 68 | 1 | 5/5 |
| pf, no language word, 3 runs | 66, 67, 66 | 1 | 3/3 |

Against the best-aimed grep run, 392, pf is 83% cheaper. Against the median, 97%.

Why grep varies so much: when the searched words sit literally in the code, the first grep lands
and the task costs 392 tokens. When they do not, the agent reformulates, reads two or three files,
and the task costs 2,000 or more. The project decides which case you are in, so grep's cost has no
ceiling. An index has one.

## Other tasks

Each compared against its own cheapest control run, which is the hard comparison rather than the
flattering one.

| task | project shape | cut |
|---|---|---|
| where a streak freeze is applied | TypeScript backend | 97% |
| where a swipe event is built | Swift desktop app | 97% |
| stripe webhook signature check | TypeScript backend | 92% |
| the paywall screen | SwiftUI app | 89% |
| screen orientation handling | SwiftUI app | 86% |
| what advances to the next question | SwiftUI app | 85% |

## A floor worth knowing

A fresh agent answering one fact with `pf` spends about 121 tokens all in: the index's answer plus
the prompt cost of the call itself. The grep equivalent is about 186. On a task where the control
arm is already cheap, no tool can show a large percentage, because the per-call overhead dominates.
Percentages only mean something next to the absolute numbers.

## What carried the win

- **Usage edges in both directions.** Knowing who calls a symbol, and what a symbol's body renders,
  turns a file list into an answer. This was the single largest gain.
- **Rarity weighting.** Each query word is weighted by how few files contain it.
- **A precision bonus.** An exact name beats a longer name that contains it.
- **Conditional output.** The answer's shape follows the question's shape.
- **Self-refresh.** A cheap state check (file count, newest mtime, total size) means an agent never
  works from a stale index.
- **Real query sentences in the gate.** The ranking gate uses the exact sentences agents typed.
  Stemming and the compound-name bonus were both found that way, and neither was predictable.

## What failed and went back

**Early:**

- A plain symbol list. Grep wins when the searched words sit literally in the code.
- A file-grouped output that hid the best symbol inside a group.
- Query relaxation to the rarest words: it dropped the key word.
- A synonym map.
- Printing the source line on every query.
- A word-stop list in place of rarity weighting.
- Shortening the digest to basenames. An agent said full paths were unknown, then named the wrong
  file.
- Making the usage edges conditional on the agent's verb. It saved 80 tokens and caused agents to
  guess a plausible component name instead of reading the real one. Edges are now unconditional.

**Nine attempts at one problem.** The one-line answer first needed the query to name the language,
because a file called `border-beam.js` in the web folder is a fair hit for "border beam". Without
that word the task cost 118 to 310 tokens and two of three answers were incomplete.

- scan the top three files for the one-line answer — gate fell to 12/16
- the same with a symbol-score floor — 15/16
- a dominant-language prior — never fires, since no language held 60% of the repo
- a cross-language second line — dead code, the web file has no render edge
- an edge-first default that dropped size, purpose and digest — 13/16
- a view-name boost — sent a previously working query to the wrong screen
- a path prior applied to one term's score — the gate held, the ranking did not change
- the same prior tested against the cleaned query — `web` and `site` are consumed as language hints,
  so the exemption never fired and an explicit web query returned the wrong file

The ninth worked: multiply the **total** file score by 0.35 for a path under `site/`, `tests/`,
`tools/`, `docs/` or `scripts/`, and exempt the file when the agent's **raw** words name that side
of the tree. Both wordings then answered completely at 66 to 68 tokens, an explicit web query still
returned the web file, and the ranking gate improved from 16/16 to 18/18 with the mean query cost
falling from 82 to 71 tokens.

A ranking change that feels better and fails one gate case goes back. That rule saved this tool
twice: a view-name boost and an edge-first default both felt like improvements and both broke a
query that already worked.

## Two gates, run after every change

- `selfcheck.sh` — seven behaviour assertions in a throwaway repo. Ships with this repository.
- `rankcheck.py` — 18 real queries, each asserting the file and the symbol it must surface, with the
  token cost of every answer printed. Not shipped, because it points at private repositories.
