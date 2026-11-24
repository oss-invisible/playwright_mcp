"""How this server says its own state as data, beside the sentence.

⛔ THE SENTENCE IS FOR A PERSON OR A MODEL, AND A PROGRAM THAT HAD TO KNOW
WHETHER A TYPING WAS STILL GOING ON, OR WHY A BROWSER DID NOT OPEN, MATCHED
ENGLISH. `browser_status` said "typing: still going on in the background, into
#x", `browser_open` said "the engine download failed: ...", and a client that
wanted to act on either had to look for those words - so a rewording here,
made for a model's sake, silently broke a program's. The wording is free to
improve only if nothing reads it as a value.

ONE MECHANISM, FOR EVERY FACT OF THIS KIND:

  an answer      `Answer(sentence, state)`: a `str` - so every caller that
                 wants the sentence has it, unchanged - that also carries its
                 `state`, a pydantic model.
  a refusal      `Refusal`: an exception whose message is the sentence and
                 which carries its `state` the same way.
  on the wire    `said(call, tool=...)`: the one place either becomes an MCP
                 result. The sentence is the text content, as before; the
                 state is `structuredContent`, checked against the tool's
                 `outputSchema`; a refusal is the same result with `isError`.

A tool declares `-> Annotated[CallToolResult, TheModel]`, which is how the SDK
derives the `outputSchema` from the model and checks the result against it,
success and error alike. That return form is the SDK's from mcp 1.19, which is
why that is the floor (pyproject.toml): before it a tool can return only what
it wants as text, or let the SDK dump the structure as its text.

⛔ WHAT AN EXCEPTION CANNOT CARRY. The SDK turns an exception raised by a tool
into an error result with only its message, and offers no way to attach data to
it. That is why a refusal that has a state is returned, not raised, once it
reaches the tool: `work.py` raises `Refusal` so its callers keep `except
RuntimeError` and `except ValueError`, and `said` is what turns it back into a
result that carries the data. An error that is not a `Refusal` stays what it
was: text only, no structured content.

The text of an error is the one the SDK used to make of the exception
(`Error executing tool <name>: <message>`), so a client reading text sees
exactly what it saw before.
"""
from __future__ import annotations

from typing import Awaitable, Literal, Optional

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field


# --- typing -------------------------------------------------------------------

TypingPhase = Literal["idle", "typing", "finished", "stopped"]

#: One line each. They are the documentation of the field in the tool's output
#: schema, so a client reads what a value means where it reads that it exists.
TYPING_PHASES = {
    "idle": "no typing to report on this browser",
    "typing": "a typing is going on in the background; actions on this browser are refused",
    "finished": "the typing ended and the field was read back (`kept`); said until the "
                "next action on this browser",
    "stopped": "the typing ended with an error, or the browser was closed under it",
}


class TypingState(BaseModel):
    """A typing, as the server knows it. Every field is always present;
    a value it does not know is null."""

    state: TypingPhase = Field(description="; ".join(
        "%s: %s" % (k, v) for k, v in TYPING_PHASES.items()))
    selector: Optional[str] = Field(
        default=None, description="the field being, or last, typed into; null when idle")
    done: Optional[int] = Field(
        default=None, description="characters typed so far, read from the field; the whole "
                                  "text once finished; null when not known")
    total: Optional[int] = Field(
        default=None, description="characters in the text being typed; null when idle")
    kept: Optional[int] = Field(
        default=None, description="characters the field held when typing ended; null until "
                                  "then, or when the field cannot be read back")


class TypingAnswer(BaseModel):
    """What `browser_status` and `browser_type` carry."""

    typing: TypingState


# --- opening ------------------------------------------------------------------

OpenCause = Literal["profile_locked", "no_display", "missing_libraries",
                    "sandbox_blocked", "launch_failed", "downloading", "download_failed"]

#: One line per cause: why a `browser_open` did not end with an open browser.
#: The first five are errors (`isError`); the last two are answers, because the
#: engine is not on this machine yet and asking again is the whole remedy.
OPEN_CAUSES = {
    "profile_locked": "another Firefox holds the profile directory; close it or use another directory",
    "no_display": "there is no display to draw on (no X server, no DISPLAY, no desktop)",
    "missing_libraries": "a system library the engine needs is not installed",
    "sandbox_blocked": "the operating system or a host sandbox refused to start the engine",
    "launch_failed": "the engine started and failed, or never answered; the sentence has its output",
    "downloading": "the engine is being downloaded or verified; call browser_open again shortly",
    "download_failed": "the engine download failed; call browser_open again to retry it",
}


class OpenAnswer(BaseModel):
    """What `browser_open` carries."""

    opened: bool = Field(description="whether the browser is open now")
    cause: Optional[OpenCause] = Field(
        default=None, description="why it is not, or null when it is. " + "; ".join(
            "%s: %s" % (k, v) for k, v in OPEN_CAUSES.items()))



# --- the mechanism ------------------------------------------------------------

class Answer(str):
    """A sentence that also knows its state. It is the sentence for every
    caller that only wants words; `said` is what sends the state as data."""

    state: BaseModel

    def __new__(cls, sentence: str, state: BaseModel):
        self = super().__new__(cls, sentence)
        self.state = state
        return self


class Refusal(Exception):
    """A failure that carries its state. Combined with the built-in class a
    caller already catches (`class X(Refusal, RuntimeError)`), so nothing that
    caught the plain error stops catching it."""

    def __init__(self, sentence: str, state: BaseModel) -> None:
        super().__init__(sentence)
        self.state = state


async def said(call: Awaitable[Answer], *, tool: str) -> CallToolResult:
    """Await a call that answers or refuses with a state, and make the MCP
    result of it: the sentence as text, the state as structured content, and
    `isError` when it was a refusal. Anything else it raises is not handled
    here."""
    try:
        answer = await call
    except Refusal as refusal:
        return _result("Error executing tool %s: %s" % (tool, refusal), refusal.state, True)
    return _result(str(answer), answer.state, False)


def _result(text: str, state: BaseModel, is_error: bool) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structuredContent=state.model_dump(mode="json"),
                          isError=is_error)
