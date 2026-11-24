"""The server says its own state as data, beside the sentence, so a client never
has to match its wording.

⛔ A CLIENT HAD TO LOOK FOR "still going on in the background" TO KNOW A TYPING
WAS NOT OVER, AND FOR "the engine download failed" TO KNOW WHY A BROWSER DID NOT
OPEN. A sentence written for a model is free to improve only while nothing reads
it as a value, and a program did. Three tools now carry their state in the
result's `structuredContent`, checked against an `outputSchema`, with the
sentence unchanged as the text:

  browser_status  `typing`          idle | typing | finished | stopped
  browser_type    `typing`          in its answer, and in the refusal while typing
  browser_open    `opened`, `cause` profile_locked | no_display | missing_libraries |
                                    sandbox_blocked | launch_failed | downloading |
                                    download_failed

No browser starts here. Sessions are stand-ins that launch nothing, and the
launch failures are the messages the engine and Firefox really write.
"""
from __future__ import annotations

import asyncio
import threading
import time
import typing

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from invisible_playwright_mcp.engine import Engine
from invisible_playwright_mcp.link import answer_of, text_of
from invisible_playwright_mcp.mcp import actions, open_cause, server, state
from invisible_playwright_mcp.mcp import work as work_module
from invisible_playwright_mcp.mcp.work import Work


class _Field:
    """What a locator answers for the field being typed into."""

    def __init__(self, held):
        self.held = held

    @property
    def first(self):
        return self

    async def input_value(self, timeout=None):
        return self.held


class _Page:
    def __init__(self, held):
        self.field = _Field(held)

    def locator(self, selector):
        return self.field


class _Typist:
    """A session that launches nothing. `held` is what its field holds."""

    def __init__(self, held="", **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self._page = _Page(held)

    async def start(self):
        pass

    async def close(self):
        self.closed = True

    def is_usable(self):
        return not self.closed

    def page(self):
        return self._page

    async def describe_pages(self):
        return [{"url": "https://example.com/", "active": True}]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("STEALTHFOX_SEED", "STEALTHFOX_PROXY", "STEALTHFOX_PROFILE_DIR",
                 "STEALTHFOX_HEADLESS", "STEALTHFOX_BINARY", "STEALTHFOX_NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server.store, "load", lambda session_id: None)
    monkeypatch.setattr(server.store, "save", lambda *a, **k: None)


@pytest.fixture
def work(monkeypatch):
    """A piece of work with `main` open on a stand-in, installed in the server."""
    w = Work("default", factory=_Typist)
    w._open["main"] = _Typist()
    w._launched["main"] = {"seed": 1}
    monkeypatch.setattr(server, "work", w)
    return w


def _idle():
    return {"typing": {"state": "idle", "selector": None, "done": None,
                       "total": None, "kept": None}}


def _typing(**fields):
    shape = {"state": "idle", "selector": None, "done": None, "total": None, "kept": None}
    shape.update(fields)
    return {"typing": shape}


@pytest.fixture
def slow_typing(work, monkeypatch):
    """A typing that outlasts its answer, ended by `gate`, and the field it
    types into, which holds 12 characters while it goes on."""
    monkeypatch.setattr(work_module, "ANSWER_WITHIN_S", 0.1)
    work._open["main"] = _Typist(held="x" * 12)
    gate = asyncio.Event()
    outcome = {"raise": None, "kept": 40}

    async def type_text(session, selector, text):
        await gate.wait()
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return actions.Typed("typed into %s" % selector, outcome["kept"])

    monkeypatch.setattr(actions, "type_text", type_text)
    gate.outcome = outcome
    return gate


async def _ends(work, gate):
    gate.set()
    await asyncio.sleep(0.05)
    assert all(rec.task.done() for rec in work._typing.values())


# --- typing -------------------------------------------------------------------

async def test_status_with_nothing_typing_says_idle(work):
    result = await server.browser_status()
    assert result.structuredContent == _idle()
    assert not result.isError
    assert "seed 1" in text_of(result), "the sentence is still the text"


async def test_a_typing_that_finishes_inside_its_answer_is_finished_with_what_the_field_kept(
        work, monkeypatch):
    async def type_text(session, selector, text):
        return actions.Typed("typed into %s" % selector, len(text) - 3)

    monkeypatch.setattr(actions, "type_text", type_text)
    result = await server.browser_type("#name", "Ada Lovelace")

    assert text_of(result) == "typed into #name"
    assert result.structuredContent == _typing(
        state="finished", selector="#name", done=12, total=12, kept=9)
    assert work._typing == {}, "a typing that ended inside its call leaves no record"


async def test_a_typing_whose_field_cannot_be_read_back_keeps_nothing_known(work, monkeypatch):
    """`kept` is null for a sentence the action did not count, as for a field
    whose text cannot be read back. Known-bad: 0, which says the field is
    empty."""
    async def type_text(session, selector, text):
        return "typed into %s; it is not a field whose text can be read back" % selector

    monkeypatch.setattr(actions, "type_text", type_text)
    result = await server.browser_type("#editor", "hi")
    assert result.structuredContent["typing"]["kept"] is None


async def test_a_typing_that_goes_on_says_so_as_data_with_how_far_it_has_got(work, slow_typing):
    result = await server.browser_type("#msg", "y" * 2000)

    assert text_of(result).startswith("still typing into #msg: 2000 characters")
    assert not result.isError
    assert result.structuredContent == _typing(
        state="typing", selector="#msg", done=12, total=2000)
    slow_typing.set()


async def test_how_far_is_null_when_the_field_cannot_be_read(work, slow_typing):
    work._open["main"].page = lambda: (_ for _ in ()).throw(RuntimeError("no page"))
    result = await server.browser_type("#msg", "y" * 2000)
    assert result.structuredContent["typing"]["state"] == "typing"
    assert result.structuredContent["typing"]["done"] is None
    slow_typing.set()


async def test_status_says_typing_while_it_goes_on_and_finished_after(work, slow_typing):
    await server.browser_type("#msg", "y" * 2000)

    during = await server.browser_status()
    assert during.structuredContent == _typing(
        state="typing", selector="#msg", done=12, total=2000)
    assert "still going on in the background, into #msg" in text_of(during)

    await _ends(work, slow_typing)
    after = await server.browser_status()
    assert after.structuredContent == _typing(
        state="finished", selector="#msg", done=2000, total=2000, kept=40)


async def test_a_finished_typing_is_still_there_for_a_client_that_asks_again(work, slow_typing):
    """⛔ THE OUTCOME IS NOT USED UP BY BEING LOOKED AT. `browser_status` is
    declared read-only, and a client polling it must see how a typing ended for
    as long as it has not moved on - a read that deleted the record made the
    outcome visible to exactly one poll, and to none if that answer was lost.

    Known-bad: retire the record in `typing_report`, as `_typing_news` does."""
    await server.browser_type("#msg", "y" * 2000)
    await _ends(work, slow_typing)

    first = await server.browser_status()
    again = await server.browser_status()
    assert first.structuredContent == again.structuredContent
    assert again.structuredContent["typing"]["state"] == "finished"
    assert "has finished" in text_of(again)


async def test_the_next_action_says_the_outcome_once_and_retires_it(work, slow_typing, monkeypatch):
    await server.browser_type("#msg", "y" * 2000)
    await _ends(work, slow_typing)

    async def press_key(session, key):
        return "pressed"

    monkeypatch.setattr(actions, "press_key", press_key)
    said = await server.browser_press_key("Enter")
    assert said.startswith("(the typing that went on in the background has finished")
    assert said.endswith("\npressed")
    assert (await server.browser_status()).structuredContent == _idle()
    assert await server.browser_press_key("Enter") == "pressed"


async def test_a_typing_that_failed_is_stopped(work, slow_typing):
    slow_typing.outcome["raise"] = RuntimeError("the page went away")
    await server.browser_type("#msg", "y" * 2000)
    await _ends(work, slow_typing)

    result = await server.browser_status()
    assert result.structuredContent == _typing(state="stopped", selector="#msg", total=2000)
    assert "stopped: the page went away" in text_of(result)


async def test_closing_the_browser_ends_a_typing_in_the_record_too(work, slow_typing):
    await server.browser_type("#msg", "y" * 2000)
    await server.browser_close()
    work._open["main"] = _Typist()
    work._launched["main"] = {"seed": 1}
    assert (await server.browser_status()).structuredContent == _idle()


async def test_a_refusal_while_typing_carries_the_typing_as_an_error_result(work, slow_typing):
    await server.browser_type("#msg", "y" * 2000)

    refused = await server.browser_type("#other", "z")

    assert refused.isError
    assert text_of(refused).startswith(
        "Error executing tool browser_type: the main browser is still typing into #msg: "
        "12 of 2000 characters so far")
    assert refused.structuredContent == _typing(
        state="typing", selector="#msg", done=12, total=2000)
    slow_typing.set()


async def test_other_tools_refuse_in_words_and_browser_status_is_where_to_ask(work, slow_typing):
    """The limit, stated: a refusal from any tool but `browser_type` stays an
    ordinary error, text only (those tools declare no output of their own).
    `browser_status` is the authoritative source for the typing state."""
    await server.browser_type("#msg", "y" * 2000)
    with pytest.raises(RuntimeError, match="still typing into #msg"):
        await server.browser_click("#send")
    assert (await server.browser_status()).structuredContent["typing"]["state"] == "typing"
    slow_typing.set()


# --- opening -------------------------------------------------------------------

class _FailsToStart(_Typist):
    failure: BaseException = RuntimeError("it did not start")

    async def start(self):
        raise self.failure


def _failing(failure):
    class Failing(_FailsToStart):
        pass

    Failing.failure = failure
    return Failing


async def test_an_opened_browser_says_opened_and_no_cause(work):
    result = await server.browser_open(seed=4242)
    assert result.structuredContent == {"opened": True, "cause": None}
    assert not result.isError
    assert text_of(result).startswith("the main browser is open.")


#: What the engine puts in the message of a failed launch, taken from what it
#: composes (`connection.launch`, the core's headless display) and from what
#: Firefox and the loader print. The cause is read from these, not from a type.
LAUNCHES_THAT_FAIL = [
    ("profile_locked", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : 0\n  it printed:\n    Firefox is already running, but is not "
        "responding.")),
    ("profile_locked", RuntimeError(
        "the pipe is closed\n  its last output:\n    Couldn't create parent.lock: busy")),
    ("no_display", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : 1\n  it printed:\n    Error: no DISPLAY environment variable "
        "specified")),
    ("no_display", RuntimeError(
        "  it printed:\n    Unable to init server: Could not connect: Connection refused\n"
        "    Error: cannot open display: :0")),
    ("no_display", RuntimeError(
        "invisible_playwright headless=True requires Xvfb. Install it: sudo apt install xvfb")),
    ("no_display", RuntimeError(
        "invisible_playwright headless=True could not create a hidden desktop "
        "(CreateDesktopW failed, WinError 5). A session with no interactive window "
        "station - a service - cannot host a headed browser")),
    ("missing_libraries", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : 127\n  it printed:\n    /opt/firefox/firefox: error while loading "
        "shared libraries: libgtk-3.so.0: cannot open shared object file: No such file "
        "or directory")),
    ("missing_libraries", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : 3221225781\n  it printed:\n    (nothing)")),
    ("sandbox_blocked", OSError(
        5, "CreateProcessW failed for C:\\cache\\firefox.exe: Access is denied.")),
    ("sandbox_blocked", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : -31\n  it printed:\n    Sandbox: seccomp sandbox violation: pid 40, "
        "tid 40, syscall 56")),
    ("sandbox_blocked", RuntimeError(
        "the browser exited during startup, before the protocol could be used.\n"
        "  exit code : 1\n  it printed:\n    clone() failed: Operation not permitted")),
    ("sandbox_blocked", OSError(
        14001, "CreateProcessW failed for C:\\Packages\\Claude\\firefox.exe: The application "
               "has failed to start because its side-by-side configuration is incorrect.")),
    ("sandbox_blocked", PermissionError("firefox")),
    ("launch_failed", RuntimeError("the proxy refused the connection")),
    ("launch_failed", TimeoutError("no answer from the browser in 60 s")),
    ("launch_failed", FileNotFoundError("firefox")),
]


@pytest.mark.parametrize("cause,failure", LAUNCHES_THAT_FAIL,
                         ids=["%s-%d" % (c, i) for i, (c, _) in enumerate(LAUNCHES_THAT_FAIL)])
async def test_a_launch_that_fails_names_its_cause(work, cause, failure):
    work._factory = _failing(failure)

    result = await server.browser_open(seed=4242)

    sentence, failed = answer_of(result)
    assert failed and "did NOT start" in sentence, sentence
    assert result.structuredContent == {"opened": False, "cause": cause}
    assert work.roles() == [], "a failed start left a browser behind"


async def test_a_cause_is_read_down_the_chain_of_the_exception():
    """The engine wraps what Firefox said (`raise ... from exc`), and the
    outer message may say nothing. Known-bad: reading only `str(exc)`."""
    try:
        try:
            raise RuntimeError("error while loading shared libraries: libasound.so.2")
        except RuntimeError as inner:
            raise RuntimeError("the engine did not start") from inner
    except RuntimeError as outer:
        assert open_cause.cause_of(outer) == "missing_libraries"


async def test_a_profile_held_by_another_firefox_is_refused_before_the_launch_with_its_cause(
        work, monkeypatch, tmp_path):
    monkeypatch.setattr(work_module, "profile_holder", lambda directory: "another Firefox")
    result = await server.browser_open(browser="support", profile=str(tmp_path))

    assert result.isError
    assert result.structuredContent == {"opened": False, "cause": "profile_locked"}
    assert "already has the profile" in text_of(result)


async def test_a_held_profile_is_still_a_value_error_and_a_failed_start_a_runtime_error(
        work, monkeypatch, tmp_path):
    """What caught the plain error before still catches it: the refusal is the
    same exception with data on it."""
    monkeypatch.setattr(work_module, "profile_holder", lambda directory: "another Firefox")
    with pytest.raises(ValueError, match="already has the profile") as held:
        await work.open("support", profile=str(tmp_path))
    assert held.value.state.cause == "profile_locked"

    work._factory = _failing(RuntimeError("it did not start"))
    with pytest.raises(RuntimeError, match="did NOT start") as failed:
        await work.open("main", seed=1)
    assert failed.value.state.cause == "launch_failed"


async def test_a_refusal_of_the_arguments_stays_text_only(work):
    """A plan the planner refuses (here an echoed `none`) is the caller's
    mistake and has no cause: it is an ordinary error, with no structured
    content to read."""
    with pytest.raises(ValueError, match="refused"):
        await server.browser_open(profile="none")


class _Download:
    def __init__(self, fail=None):
        self.fail = fail
        self.release = threading.Event()

    def __call__(self, progress, status):
        status("downloading")
        progress(30 << 20, 100 << 20)
        self.release.wait(5)
        if self.fail is not None:
            raise self.fail
        return "C:/cache/firefox.exe"


async def test_an_engine_that_is_downloading_is_an_answer_with_that_cause(monkeypatch):
    fetch = _Download()
    work = Work("default", factory=_Typist, engine=Engine(fetch=fetch))
    monkeypatch.setattr(server, "work", work)

    result = await server.browser_open()

    assert not result.isError, "a download in flight is an answer, not a failure"
    assert result.structuredContent == {"opened": False, "cause": "downloading"}
    assert "30% of 100 MB" in text_of(result)
    assert work.roles() == []
    fetch.release.set()


async def test_a_failed_download_is_download_failed(monkeypatch):
    fetch = _Download(fail=RuntimeError("no route to github.com"))
    engine = Engine(fetch=fetch)
    engine.start()
    fetch.release.set()
    deadline = time.monotonic() + 5
    while engine.state != "failed":
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)
    # `open` retries a failed download, and this one fails again at once.
    work = Work("default", factory=_Typist, engine=engine)
    monkeypatch.setattr(server, "work", work)

    result = await server.browser_open()

    assert result.structuredContent == {"opened": False, "cause": "download_failed"}
    assert "no route to github.com" in text_of(result)


@pytest.mark.parametrize("phase,cause", [
    ("pending", "downloading"), ("starting", "downloading"), ("downloading", "downloading"),
    ("verifying", "downloading"), ("extracting", "downloading"), ("failed", "download_failed"),
])
def test_every_state_a_download_is_in_has_a_cause(phase, cause):
    engine = Engine(fetch=lambda progress, status: "x")
    engine.state = phase
    assert engine.cause() == cause


# --- the contract --------------------------------------------------------------

def test_every_cause_and_phase_is_declared_once_and_described_in_one_line():
    assert set(state.OPEN_CAUSES) == set(typing.get_args(state.OpenCause))
    assert set(state.TYPING_PHASES) == set(typing.get_args(state.TypingPhase))
    assert all(v and "\n" not in v for v in state.OPEN_CAUSES.values())
    assert all(v and "\n" not in v for v in state.TYPING_PHASES.values())
    assert [c for c, _ in open_cause.PATTERNS] + ["launch_failed"] == [
        "profile_locked", "no_display", "missing_libraries", "sandbox_blocked", "launch_failed"]


async def test_the_three_tools_declare_their_output_and_the_others_do_not_change(work):
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert set(tools["browser_open"].outputSchema["properties"]) == {"opened", "cause"}
    for name in ("browser_status", "browser_type"):
        assert set(tools[name].outputSchema["properties"]) == {"typing"}
    causes = tools["browser_open"].outputSchema["properties"]["cause"]["anyOf"][0]["enum"]
    assert causes == list(state.OPEN_CAUSES)
    assert "idle" in str(tools["browser_status"].outputSchema)
    for name in ("browser_click", "browser_navigate", "browser_snapshot"):
        assert "typing" not in (tools[name].outputSchema or {}).get("properties", {}), name


async def test_the_three_docstrings_say_the_fields_exist_inside_the_cut(work):
    tools = {t.name: t.description for t in await server.mcp.list_tools()}
    assert "`opened`" in tools["browser_open"] and "`cause`" in tools["browser_open"]
    for name in ("browser_status", "browser_type"):
        assert "`typing`" in tools[name], name
    assert "kept" in tools["browser_status"] and "idle|typing|finished|stopped" in tools["browser_status"]
    assert all(len(d) <= 1024 for d in tools.values())


async def test_a_client_reads_the_state_from_structured_content_over_the_protocol(work, slow_typing):
    """What a client does, with the SDK's own client: the call is validated
    against the declared output schema on the way in, and the state is
    `result.structuredContent`. Success and error alike."""
    async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
        status = await client.call_tool("browser_status", {})
        assert status.structuredContent == _idle()
        assert status.content[0].text.startswith("identity: seed 1")

        started = await client.call_tool("browser_type", {"selector": "#msg", "text": "y" * 2000})
        assert started.structuredContent["typing"]["state"] == "typing"
        assert started.content[0].text.startswith("still typing into #msg")

        refused = await client.call_tool("browser_type", {"selector": "#o", "text": "z"})
        assert refused.isError
        assert refused.structuredContent["typing"]["state"] == "typing"
        assert refused.content[0].text.startswith("Error executing tool browser_type:")

        work._factory = _failing(RuntimeError("error while loading shared libraries: libgtk-3.so.0"))
        failed = await client.call_tool("browser_open", {"seed": 5})
        assert failed.isError
        assert failed.structuredContent == {"opened": False, "cause": "missing_libraries"}
    slow_typing.set()
