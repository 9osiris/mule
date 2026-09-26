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

the spinner ticks while waiting on the model: first frame after a
short delay so fast replies never flicker, one line, labeled
`thinking`, carriage-return redraw, cleared when the reply lands.
the frames are clock faces (`◷ ◶ ◵ ◴`) on terminals that render
unicode, plain `| / - \` on legacy consoles.

## tool-call transcript

tool calls render as compact transcript lines, not bordered
panels: one amber line per call, results nested underneath.

```
⏺ run_shell command=echo hi
⎿ hi
```

errors print red so failures jump out. long output is trimmed to
the first line plus a `+N lines` count.

## glyph fallback

mule detects what the terminal can render. modern terminals get
unicode glyphs (`⏺ ⎿ ❯ ─ ◷`); legacy windows consoles, where
those print as boxes, get plain ascii instead (`* | > -`, the
classic `|/-\` spinner). colors still work in both. `--no-color`
(or the `NO_COLOR` env var) turns all styling off.

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
