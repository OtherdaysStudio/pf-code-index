# pf — a code index built for agents, not for people

An AI agent that searches a codebase with grep pays for every match it reads. The bill is
unpredictable: on one measured task, five fresh agents spent 392, 624, 2,060, 2,415 and 2,706
tokens answering the same question. `pf` answers it in 49 to 68 tokens, in one call, in 0.03
seconds.

It is one Python file, no dependencies.

## The problem

Ask an agent "where is the border beam drawn on the question screen". Without a tool, it runs grep,
reads the matches, opens a file, reads more, tries another word, opens another file. Every line
enters its context window and every line costs money. The project decides how much output a grep
produces, so nothing bounds the cost.

A file list is not the answer either. Knowing which file holds a name does not say what the code
does with it, so the agent still opens the file.

## What it does

One pass over the project records four things:

- every symbol and its line number
- one sentence saying what each file is for
- who calls each symbol (incoming)
- what each symbol's body renders or calls (outgoing)

That last pair carries the win. Because the index knows both directions, a question about a drawn
component answers in a single line:

```
$ pf -p myapp border beam question screen
App/Sources/SessionFlow.swift beam:1085 QuestionScreen:970 > SpeakerBeam:1096 LiveBeam:1103
```

The file, the property that draws the beam, the screen that holds it, and the two components it
draws. No file read, no second query. The agent has everything it needs to make the edit.

For a question with no such edge, `pf` prints the best file with its matching symbols, its purpose,
its callers, and then a one-line digest of the next few files:

```
$ pf -p myapp streak freeze apply
services/streak-service.ts  applyStreakFreeze:870 freezeCount:844 StreakFreeze:12
  purpose: streak counters, freezes and the daily rollover
  used  routes/streak.ts:44 jobs/rollover.ts:120
~ routes/streak.ts:post:44,guard:12  models/streak.ts:Streak:8  jobs/rollover.ts:run:102
```

## Measured results

The benchmark task: find the view property that draws a border beam on one screen of a 1,900-file
iOS repo, and name the components it renders. The control arm used grep and file reads. The
treatment arm used `pf`. Both arms were fresh agents with no prior context, so each run is a cold
start. Cost counts marginal search traffic only: tool inputs plus tool results, excluding each
agent's final answer, measured from the session transcripts with tiktoken.

| arm | tokens | tool calls | complete answer |
|---|---|---|---|
| grep and file reads, 5 runs | 392, 624, 2,060, 2,415, 2,706 | 3 to 9 | 5/5 |
| pf, 5 runs | 49, 63, 63, 68, 68 | 1 | 5/5 |
| pf, reworded, 3 runs | 66, 67, 66 | 1 | 3/3 |

Against the luckiest grep run, 392, that is a 83% cut. Against the median, 97%. Six other tasks
across four projects, each compared against its own cheapest control run, cut 85% to 97%.

Speed, on a 1,900-file repo: a cold index build takes 0.13 s, a query takes 0.03 to 0.09 s.

The full record, including the nine ranking attempts that failed and went back, is in
[MEASUREMENTS.md](MEASUREMENTS.md).

## Install

```sh
git clone https://github.com/OtherdaysStudio/pf-code-index.git
mkdir -p ~/.local/bin
printf '#!/bin/sh\nexec python3 "%s/pf.py" "$@"\n' "$PWD/pf-code-index" > ~/.local/bin/pf
chmod +x ~/.local/bin/pf
```

Python 3.9 or newer. No packages to install.

## Use

Run it inside a project, or name a project from anywhere with `-p`.

| command | what it prints |
|---|---|
| `pf <words>` | the best file, its symbols, callers, what they render, purpose, then a digest |
| `pf -b <words>` | facts only: the best file and its matching symbols |
| `pf -0 <words>` | one line, `path:line name` |
| `pf -p <name> <words>` | the same, in the project whose folder name matches `<name>` |
| `pf <words> // <words> // -c` | several questions in one call |
| `pf -f <file> [words]` | that file in full, or its API |
| `pf -s path:line` | the smallest meaningful slice: the declaration at that line |
| `pf -r <name>` | who references this name |
| `pf -c` | one line of counts: languages, views, routes, entry point |
| `pf -i` | rebuild the index |

Put the question's own words in the query. The tool strips filler words and matches word stems, so
plain English works.

The first call in a project builds the index and registers the project's name, which is what makes
`-p` work later. The index sits at `<root>/.pf/idx.tsv`. Add `.pf/` to your `.gitignore`.

## How it works

**Four tables, one file.** The index is tab-separated text: `F` rows for files, `S` rows for
symbols, `U` rows for incoming usage, `O` rows for outgoing usage. Loading it is one read and one
split.

**It refreshes itself.** Before any query, `pf` counts the files, takes the newest mtime and sums
the sizes. If any of the three differs from the header, it rebuilds. The check costs milliseconds,
so an agent never works from a stale index and never has to remember to rebuild.

**Ranking by rarity.** Each query word is weighted by how rare it is in the project, so a word that
appears in 400 files steers nothing and a word that appears in two decides the answer. An exact
name match beats a partial one; a shorter name beats a longer name containing it.

**The project's own source wins.** A path under `site/`, `tests/`, `tools/`, `docs/` or `scripts/`
is scored at a third, unless the question's own words name that side of the tree. So "border beam"
reaches the app and "border beam web" reaches the web folder. This single rule removed the need for
the agent to name the language, and it took nine attempts to find.

**Symlinks stay out.** A project often holds a link to a shared config or an `.env` that lives
outside it. pf never reads through a link, and never resolves an indexed path outside the project
root, so nothing from outside reaches the index or an agent's context.

**The output is shaped by the question.** A question whose subject renders something gets the
one-line edge answer and nothing else. Size, purpose and the neighbour digest are printed only when
they could matter. A grep has no limit on its output because the project decides how many matches
exist. An index has a limit because the tool decides what to print. That is the whole saving.

## As a Claude Code skill

Copy the folder into your skills directory and the agent will reach for it on its own:

```sh
mkdir -p ~/.claude/skills/pf && cp SKILL.md pf.py ~/.claude/skills/pf/
```

[SKILL.md](SKILL.md) holds the instructions the agent reads: which mode fits which question shape,
how to phrase a query, and the rule that matters most, which is to trust the line numbers rather
than open a file to confirm them.

For a global rule instead of a skill, paste the contents of SKILL.md into your `CLAUDE.md`.

## The gate

`selfcheck.sh` builds a small throwaway repo, indexes it, and asserts eleven behaviours: the build
runs, a query finds the right symbol and line, the purpose line appears, a usage edge resolves in
both directions, an edit triggers a rebuild, a slice returns the enclosing declaration, and the
overview prints. Run it after any change.

```sh
./selfcheck.sh
```

A second gate, not shipped here because it points at private repositories, scores 18 real queries
against the file and symbol each one must surface, and prints the token cost of every answer. The
method is worth copying: feed it the exact sentences your agents typed, not sentences you invented.
Stemming and the compound-name bonus were both found that way.

## Licence

MIT. See [LICENSE](LICENSE).
