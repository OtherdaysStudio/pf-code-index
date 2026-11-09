---
name: pf
description: Use before grep or reading files whenever you need to locate code — "where is X", "which line declares Y", "what does this render", "who calls this", "what is in this file". pf is a self-refreshing code index that answers in one call for about 60 tokens instead of a file dump. Also use it to confirm a line number, to read one declaration, or to take a project's census.
---

# Find code with pf, not with grep

`pf` is a local code index. It builds itself on the first call (0.1 s to 20 s), refreshes itself
when a file changes, and resolves a project by name, so you never need `cd`.

**Pick the cheapest mode for the question's shape, and make one call.**

- `pf -p <project> -b <words>` — facts only: the best file and its matching symbols with line
  numbers. About 50 tokens. Use it for "where is X", "which line declares Y".
- `pf -p <project> -0 <words>` — one line, `path:line name`. The pinpoint lookup.
- `pf -p <project> <words>` — the full line: symbols, `used` (who calls them), `uses` (what the
  symbol's body renders), the file's purpose, plus a `~` digest of further files. About 120 tokens.
  Use it for "what does this render", "who calls this".
- `pf -p <project> <words> // <words> // -c` — several questions in ONE call. `-c` prints one line
  of counts: languages, views, routes, entry point.
- `pf -p <project> -f <file> [words]` — that file in full, or its API.
- `pf -p <project> -r <name>` — who uses this name.
- `pf -p <project> -s path:line` — the declaration at that line, and nothing around it.

## How to query

Put the question's own words in the query. pf strips filler and matches word stems, so plain English
works: `pf -p myapp where is the streak freeze applied` behaves the same as `pf -p myapp streak
freeze apply`.

pf prefers the project's own source over `site/`, `tests/`, `tools/`, `docs/` and `scripts/`, so a
plain question lands in the app. Name that side of the tree only when you want it: add `web`,
`test` or `tool` to force it.

When your query names a symbol that renders something, pf answers in one line:

```
path/to/File.swift beam:1085 QuestionScreen:970 > SpeakerBeam:1096 LiveBeam:1103
```

Read it as: the file, the symbol you asked about, the thing that holds it, then what it renders.
That is usually the whole answer.

## Rules

- **Trust the line numbers.** They are live, taken from the current file. Never grep or open a file
  to confirm what pf printed.
- **Read code only when the answer needs a body**, and then with `pf -s path:line`, not with a whole
  file read.
- **One call, not three.** Chain questions with `//` rather than calling pf repeatedly.
- **Use grep only when pf prints nothing relevant.**

## First use in a project

The first call indexes the project and registers its folder name, which is what makes `-p` work from
anywhere afterwards. Add `.pf/` to the project's `.gitignore`.
