# mule branding

the look. mule should be recognizable in a terminal the way claude
code and gemini cli are: one mark, one color, one motion.

## the mark

```
   /\ /\
  / \ / \
  \ \__/ /
   \ ____ /
    \______/
```

a geometric mule head. it opens every interactive run, in brand
amber, above the version and model line. never in `--print`,
`--json`, or `--quiet` output: scripts get clean text.

## palette

one brand color, the rest are semantics:

- brand amber (bold ansi 33): the mark, the banner, panel frames,
  the status rule. the mule color.
- red (31): errors, dangerous-command warnings.
- green (32): added diff lines, ok states.
- yellow (33): confirmations, caution.
- dim (2): step metadata, token lines.
- cyan (36): tool panel names.

## motion

the spinner frames are clock faces: `◷ ◶ ◵ ◴`. it ticks while
waiting on a non-streamed model response, labeled `thinking`.
one line, carriage-return redraw, cleared when the reply lands.

## tool-call panels

tool calls render as bordered panels, not `$ name args` lines:

```
┌─ run_shell
│ command=echo hi
└─
```

panel name in cyan, frame in brand amber.

## status footer

every run ends with a footer, not a bare cost line:

```
────────────────────────────────────────
mule 0.9.0 | model gpt-4o-mini | 1,234 in / 567 out | $0.0012
```

model, token counts, cost. the rule above it is brand amber.

## the off switch

`--no-color` or the `NO_COLOR` env var disables everything: no
amber, no panels styling (plain text frames), no spinner. the
information stays, the decoration goes.
