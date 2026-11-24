"""One walk of the page's documents serves every tool that reads it.

iCIMS career sites put the whole application form in `#icims_content_iframe`.
The snapshot did not list the iframe, `browser_read_text` skipped it and
`browser_type "#q"` answered "matches nothing" for a field inside it, so an
application there could not be finished. The tools now share `actions.documents`:
the top-level document and every painted iframe under it, each with the `frame`
chain that reaches it, its origin, and whether it could be read. The snapshot,
`browser_read_text` and every selector tool's `frame` agree because there is one
list and one selector builder behind them.

The unit tests hold the shape of the walk and what is said of a frame that
cannot be read, with doubles. The e2e ones hold the outcome on a real engine:
what the snapshot lists inside a same-origin iframe is what type, select and
upload accept, and the text and the script reach it.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import socket
import threading

import pytest

from invisible_playwright_mcp.mcp import actions, server


# --- the walk, with doubles ---------------------------------------------------

class _Element:
    def __init__(self, info):
        self.info = info

    async def evaluate(self, expression):
        return self.info

    async def dispose(self):
        pass


class _Frame:
    """A frame the walk can ask about. `info` is what its <iframe> element
    answers (None: the element cannot be reached, as when it is detached);
    `origin` what its document answers (an Exception: it cannot be read)."""

    def __init__(self, info, origin="http://x.test", children=(), text=None, snapshot=None):
        self.info, self.origin, self.child_frames = info, origin, list(children)
        self.text, self.snap = text, snapshot

    async def frame_element(self):
        if self.info is None:
            raise RuntimeError("Frame was detached")
        return _Element(self.info)

    async def evaluate(self, expression):
        if "location.origin" in expression:
            if isinstance(self.origin, Exception):
                raise self.origin
            return self.origin
        if self.snap is None:
            raise RuntimeError("Cannot read this frame")
        return self.snap

    def locator(self, selector):
        frame = self

        class _Locator:
            @property
            def first(self):
                return self

            async def all_inner_texts(self):
                if isinstance(frame.text, Exception):
                    raise frame.text
                return [] if frame.text is None else [frame.text]
        return _Locator()


def _shown(selector, left=10, top=20):
    return {"shown": True, "left": left, "top": top, "selector": selector}


class _Top(_Frame):
    async def evaluate_read(self, expression):
        return {"title": "top", "url": "http://top.test/", "interactive_elements": []}


class _Page(_Top):
    def __init__(self, children):
        super().__init__(None, None, children, text="top text")
        self.main_frame = self


class _Session:
    def __init__(self, page):
        self._page = page

    def page(self):
        return self._page


def _field(selector):
    return {"tag": "input", "type": "text", "selector": selector, "at": [5, 6]}


def test_the_walk_lists_the_top_document_first_then_each_frame_with_its_chain():
    deep = _Frame(_shown("#deep", 3, 4), snapshot={"interactive_elements": [_field("#dq")]})
    inner = _Frame(_shown("#f", 10, 20), children=[deep],
                   snapshot={"interactive_elements": [_field("#q")]})
    docs = asyncio.run(actions.documents(_Session(_Page([inner]))))
    assert [d.chain for d in docs] == [[], ["#f"], ["#f", "#deep"]]
    assert docs[0].top and not docs[1].top
    assert docs[2].offset == (13, 24)           # the nesting adds up in the top viewport
    assert [d.entry() for d in docs[1:]] == [
        {"origin": "http://x.test", "frame": ["#f"]},
        {"origin": "http://x.test", "frame": ["#f", "#deep"]}]


def test_a_frame_that_is_not_painted_is_not_listed():
    hidden = _Frame({"shown": False, "left": 0, "top": 0, "selector": "#ad"})
    docs = asyncio.run(actions.documents(_Session(_Page([hidden]))))
    assert len(docs) == 1


def test_a_frame_the_walk_cannot_ask_about_is_listed_as_unreadable_not_dropped():
    detached = _Frame(None)
    docs = asyncio.run(actions.documents(_Session(_Page([detached]))))
    assert [d.state for d in docs] == ["readable", "unreadable"]
    assert docs[1].entry() == {"unreadable": True}


def test_a_frame_whose_document_does_not_answer_is_unreadable_and_keeps_its_chain():
    blocked = _Frame(_shown("#bank"), origin=RuntimeError("blocked"))
    docs = asyncio.run(actions.documents(_Session(_Page([blocked]))))
    assert docs[1].entry() == {"frame": ["#bank"], "unreadable": True}


def test_a_frame_no_selector_reaches_is_listed_as_unaddressable():
    bare = _Frame({"shown": True, "left": 0, "top": 0, "selector": None})
    docs = asyncio.run(actions.documents(_Session(_Page([bare]))))
    assert docs[1].entry() == {"origin": "http://x.test", "unaddressable": True}


def test_the_walk_stops_at_the_depth_limit():
    frame = None
    for _ in range(actions.FRAME_DEPTH + 2):
        frame = _Frame(_shown("#n"), children=[frame] if frame else [])
    docs = asyncio.run(actions.documents(_Session(_Page([frame]))))
    assert max(len(d.chain) for d in docs) == actions.FRAME_DEPTH


def test_the_snapshot_reports_an_unreadable_frame_and_still_lists_the_others():
    ok = _Frame(_shown("#ok", 10, 20), snapshot={"interactive_elements": [_field("#q")]})
    broken = _Frame(_shown("#broken"), snapshot=None)       # answers its origin, not its snapshot
    detached = _Frame(None)
    d = json.loads(asyncio.run(actions.snapshot(_Session(_Page([ok, broken, detached])))))
    assert d["frames"] == [{"origin": "http://x.test", "frame": ["#ok"]},
                           {"origin": "http://x.test", "frame": ["#broken"], "unreadable": True},
                           {"unreadable": True}]
    [inside] = d["interactive_elements"]
    assert inside["frame"] == ["#ok"] and inside["at"] == [15, 26] and inside["frame_at"] == [5, 6]


def test_read_text_names_each_frame_and_an_unreadable_one_is_named_too():
    ok = _Frame(_shown("#ok"), text="inside the frame")
    silent = _Frame(_shown("#empty"), text=None)            # no match there: left out
    blocked = _Frame(_shown("#blocked"), origin=RuntimeError("blocked"))
    failing = _Frame(_shown("#failing"), text=RuntimeError("went away"))
    out = asyncio.run(actions.read_text(_Session(_Page([ok, silent, blocked, failing])), "body"))
    assert out.startswith("top text\n\n--- frame #ok [http://x.test] ---\ninside the frame")
    assert "#empty" not in out
    assert "--- frame #blocked [origin unknown]: present but unreadable, not read ---" in out
    assert "--- frame #failing [http://x.test]: present but unreadable, not read ---" in out


def test_read_text_with_no_frames_is_the_text_alone():
    assert asyncio.run(actions.read_text(_Session(_Page([])), "body")) == "top text"


def test_read_text_in_one_frame_reads_that_frame_alone(monkeypatch):
    inner = _Frame(_shown("#f"), text="form text")

    async def target(session, frame=None):
        return inner
    monkeypatch.setattr(actions, "frame_target", target)
    assert asyncio.run(actions.read_text(_Session(_Page([inner])), "body", frame=["#f"])) == "form text"


@pytest.mark.parametrize("call", [
    lambda s: actions.snapshot(s),
    lambda s: actions.read_text(s, "body"),
])
def test_each_reader_walks_the_page_once(monkeypatch, call):
    """The snapshot and read_text take their frames from `documents`, not from a
    walk of their own: a second walk is a second opinion about the same page."""
    walks = []
    real = actions.documents

    async def counted(session):
        walks.append(1)
        return await real(session)
    monkeypatch.setattr(actions, "documents", counted)
    asyncio.run(call(_Session(_Page([_Frame(_shown("#f"), snapshot={"interactive_elements": []},
                                              text="t")]))))
    assert walks == [1]


def test_upload_files_declares_the_frame_like_every_selector_tool():
    import inspect
    for tool in (server.browser_upload_files, server.browser_read_text):
        params = inspect.signature(tool).parameters
        assert "frame" in params and params["frame"].default is None, tool.__name__


# --- a real engine ------------------------------------------------------------

INNER = b"""<!doctype html><html><head><title>apply form</title></head><body>
<h2>Your details</h2>
<label for="q">Full name</label><input id="q" name="q" type="text">
<select id="country"><option value="it">Italy</option><option value="fr">France</option></select>
<input id="cv" type="file">
<iframe id="deep" title="deep" src="/deep" style="width:200px;height:80px"></iframe>
</body></html>"""

DEEP = b"""<!doctype html><html><body><p>deep text</p><input id="dq" type="text"></body></html>"""

PAGE = b"""<!doctype html><html><head><title>Careers</title></head><body>
<h1>Career site</h1><input id="top-field" type="text">
<iframe id="content" src="/inner" style="width:500px;height:300px"></iframe>
<iframe src="/deep" style="width:200px;height:80px"></iframe>
</body></html>"""


def _serve():
    pages = {"/inner": INNER, "/deep": DEEP}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path, PAGE)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


@pytest.fixture(scope="module")
def run():
    """A same-origin page whose application form is an iframe, as on iCIMS."""
    from invisible_playwright_mcp.mcp.plan import plan_session
    from invisible_playwright_mcp.mcp.session import StealthSession

    srv, url = _serve()
    loop = asyncio.new_event_loop()
    session = StealthSession(**plan_session().kwargs)
    loop.run_until_complete(session.start())

    def go(fn):
        async def body():
            await actions.navigate(session, url, wait_until="load")
            return await fn(session)
        return loop.run_until_complete(body())

    try:
        yield go
    finally:
        loop.run_until_complete(session.close())
        loop.close()
        srv.shutdown()


@pytest.mark.e2e
def test_the_snapshot_lists_the_form_inside_the_iframe_with_selectors_that_work(run):
    d = json.loads(run(lambda s: actions.snapshot(s)))
    inside = {e["selector"]: e for e in d["interactive_elements"] if e.get("frame") == ["#content"]}
    assert {"#q", "#country", "#cv"} <= set(inside), sorted(inside)
    nested = [e for e in d["interactive_elements"] if e.get("frame") == ["#content", "#deep"]]
    assert [e["selector"] for e in nested] == ["#dq"]
    assert [e["selector"] for e in d["interactive_elements"] if not e.get("frame")] == ["#top-field"]
    # The iframe with no id, name or title has no selector to give: listed, not hidden.
    assert [f["frame"] for f in d["frames"] if "frame" in f] == [["#content"], ["#content", "#deep"]]
    assert [f for f in d["frames"] if f.get("unaddressable")] and len(d["frames"]) == 3


@pytest.mark.e2e
def test_type_select_and_upload_take_the_selectors_the_snapshot_gave(run, tmp_path, monkeypatch):
    root = tmp_path / "up"
    root.mkdir()
    (root / "cv.pdf").write_bytes(b"%PDF-1.4 cv")
    monkeypatch.setenv(actions.UPLOAD_DIRS_ENV, str(root))
    frame = ["#content"]

    async def body(s):
        snap = json.loads(await actions.snapshot(s))
        given = {e["selector"]: e["frame"] for e in snap["interactive_elements"] if e.get("frame")}
        typed = await actions.type_text(s, "#q", "Ada Lovelace", frame=given["#q"])
        chose = await actions.select_option(s, "#country", "France", frame=given["#country"])
        sent = await actions.upload_files(s, "#cv", [str(root / "cv.pdf")], frame=given["#cv"])
        read = {
            "q": await actions.evaluate(s, "document.querySelector('#q').value", frame=frame),
            "country": await actions.evaluate(s, "document.querySelector('#country').value", frame=frame),
            "cv": await actions.evaluate(s, "document.querySelector('#cv').files[0].name", frame=frame),
        }
        return typed, chose, sent, {k: json.loads(v) for k, v in read.items()}
    typed, chose, sent, read = run(body)
    assert typed.startswith("typed into #q") and chose.startswith("selected #country by label")
    assert sent.startswith("attached 1 file to #cv")
    assert read == {"q": "Ada Lovelace", "country": "fr", "cv": "cv.pdf"}


@pytest.mark.e2e
def test_read_text_includes_the_frame_text_under_a_line_naming_each_frame(run):
    out = run(lambda s: actions.read_text(s))
    top, _, rest = out.partition("\n\n--- frame ")
    assert "Career site" in top and "Your details" not in top
    assert "frame #content [http://127.0.0.1:" in out and "Your details" in out
    assert "frame #content > #deep [" in out and "deep text" in out
    assert "(no selector reaches it)" in out
    only = run(lambda s: actions.read_text(s, "h2", frame=["#content"]))
    assert only == "Your details"


@pytest.mark.e2e
def test_evaluate_runs_in_the_frame_and_in_the_top_document(run):
    async def body(s):
        return (json.loads(await actions.evaluate(s, "document.title", frame=["#content"])),
                json.loads(await actions.evaluate(s, "document.title")),
                json.loads(await actions.evaluate(s, "document.title", frame=["#content", "#deep"])))
    assert run(body) == ("apply form", "Careers", "")

