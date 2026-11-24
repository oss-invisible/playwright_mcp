---
title: "What an AI agent can and cannot do inside an iframe"
description: "The snapshot lists what is inside a frame with a ready-made frame chain, every selector tool takes it, read_text reads each frame, and a frame that cannot be read is named instead of hidden."
parent: "Using the Agent"
nav_order: 66
---

# What an AI agent can and cannot do inside an iframe

An iframe is a second document inside the first. Until 0.74 the tools of this
server stopped at the frame: the snapshot listed nothing from inside it,
`browser_read_text` skipped it, and a selector for a control in it timed out
after fifteen seconds and 275 attempts. That is no longer the case. The frame is
part of the page the tools walk, and a model does not need to be told "this is in
a frame" to get in.

Measured on 2026-11-24 against a local page whose application form is an iframe
(the shape of an iCIMS career site: the whole form in `#icims_content_iframe`),
with a second iframe nested inside it and one more with no id, name or title:

| what was tried | result |
|---|---|
| `browser_snapshot` | lists the form's input, select and file input, each with `"frame": ["#content"]` and the frame's origin; the nested frame's input has `"frame": ["#content", "#deep"]` |
| `browser_type`, `browser_select_option`, `browser_upload_files` with the element's `selector` and `frame` | **work**: the field read back `Ada Lovelace`, `fr`, `cv.pdf` |
| `browser_click` and `browser_read_html` with `frame` | **work** |
| `browser_read_text` | the page's text, then each frame's text under a line that names it |
| `browser_evaluate` with `frame` | runs in that frame's document |

## How a model reaches a control in a frame

It does not build anything. Each element of `browser_snapshot` that lives in a
frame carries `frame`, the selectors of each nested iframe from the outside in,
and `frame_origin`. The model passes that list, with the element's own
`selector`, to the tool:

```
browser_type  selector="#q"  text="Ada Lovelace"  frame=["#content"]
```

`frame` is accepted by `browser_click`, `browser_type`, `browser_select_option`,
`browser_upload_files`, `browser_read_text`, `browser_read_html` and
`browser_evaluate`. Left out, the tool acts on the top-level document exactly as
before. Each call resolves the chain afresh and requires every step to match one
iframe with a document, so a frame the page replaced is found again and a
selector that matches two frames is refused, not guessed.

Cross-origin frames work too: the engine reads each frame in its own document,
which a script in the parent cannot do. That is why the snapshot can list a
payment widget or a vendor's embedded form, and why it reports the frame's
**origin** and never its URL, which commonly carries a session token.

## What `browser_read_text` does with frames

The selector is looked for in the top-level document and in every painted iframe
under it. The page's text comes first, unmarked; each frame with a match follows
under a line that names it:

```
Career site

--- frame #content [http://127.0.0.1:53085] ---
Your details
Full name

--- frame #content > #deep [http://127.0.0.1:53085] ---
deep text
```

A frame in which the selector matches nothing is left out, so a narrow selector
costs nothing for the frames. `frame` reads inside that one frame alone.

## A frame that cannot be read is named, not hidden

`browser_snapshot` lists every frame it found under `frames`, and says what it
could not do with one:

- **`unreadable`**: the frame went away while the page was being read, or its
  document did not answer. It is listed, with no controls.
- **`unaddressable`**: no selector reaches it from its parent (no id, name, title,
  test id or label that is unique), or it sits inside a shadow root. Its controls
  are not listed because nothing could act on them. `browser_read_text` still
  reads its text, since reading needs no way in.

A frame that is not painted (an ad with `display: none`) is not listed at all.
Nesting is followed four levels deep; below that the page is one this server has
not met.

## What did not change

**The pointer still works across a frame.** A real click at a position inside the
frame's rectangle lands on whatever is there, and the snapshot gives `at` for
elements without a selector in the top-level viewport, so
`browser_click_at` takes it as it takes any other. The coordinate rung is still
the one for a control no selector describes, in a frame or out of it:
[clicking by selector or by coordinates](clicking-by-selector-or-by-coordinates.md).

**Keys go to the focused element.** If focus is in the outer document they do not
enter the frame; click or type into a field inside it first, with `frame`.

**A script does not act for you.** `browser_evaluate` with `frame` reads the
frame's document; acting from script is refused there as anywhere, for the reason
in
[keeping an AI browser agent out of destructive actions](keeping-an-ai-agent-out-of-destructive-actions.md).

**The wrong-frame symptom is gone, the long wait is not.** A control that is
neither in the snapshot nor in the frames list still burns the full fifteen
seconds when clicked by selector. The general shape of that is in
[how long the agent waits before it gives up](how-long-before-the-agent-gives-up.md).

## Say nothing special in the task

Consent walls, payment fields, embedded maps, video players, chat widgets and
application forms are frames more often than not. The model does not need the
word "iframe" in the task: the snapshot names the frame and hands over the chain.

## Short answers to the questions that lead here

**Can an AI agent click a button inside an iframe?** Yes, by selector with the
`frame` the snapshot gave, or by coordinates.

**Can it type into a field, choose an option or attach a file inside one?** Yes,
measured, including a file input through its chooser.

**Can it read text inside a frame?** Yes, same-origin or not: `browser_read_text`
includes it, marked per frame.

**What does it say about a frame it cannot read?** `unreadable` or
`unaddressable`, in the `frames` list of the snapshot, and a line naming it in
`browser_read_text`.

**How do I know something is in a frame?** Its element in `browser_snapshot`
carries `frame`.

**See also:**
[what a page snapshot costs, per control](what-a-page-snapshot-costs.md), and
[shadow DOM and an AI agent](shadow-dom-and-an-ai-agent.md), the mirror image of
this page: a boundary the reads cannot cross and the selector can.

## Sources

- Measured 2026-11-24 through this project's own tool functions against a local page served from `127.0.0.1`: an iframe holding a text input, a select and a file input, a nested iframe with a text input, and an iframe with no id, name or title. The snapshot listed the controls with `frame` `["#content"]` and `["#content", "#deep"]` and one frame as `unaddressable`; type, select and upload through those selectors left the fields reading `Ada Lovelace`, `fr` and `cv.pdf`, read back with `browser_evaluate` and `frame`. The page is in `tests/mcp_server/test_the_page_is_walked_once.py`.
- The earlier behaviour (an empty snapshot, `'#fb' not actionable in 15s after 275 attempts`) was measured on 2026-09-17 on a same-origin frame, before the tools walked frames.

---

*The pair to hold in your head: the frame is part of the page now, and the chain
in the snapshot is the way in.*
