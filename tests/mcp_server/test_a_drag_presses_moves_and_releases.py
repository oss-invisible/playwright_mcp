"""browser_drag draws a pointer path: press, move through every waypoint, release.

No browser starts here. The page is a recorder of what the mouse was told, so
what is under test is the decisions: that a button pressed is always released,
that every stroke lifts before the next, that the pointer never jumps, and that
a path that cannot be drawn is refused before anything moves.
"""
from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from invisible_playwright_mcp.mcp import actions, server


class _Mouse:
    def __init__(self, log, fail_on=None):
        self.log = log
        self.fail_on = fail_on

    async def move(self, x, y, steps=1):
        if self.fail_on == (x, y):
            raise RuntimeError("the page went away mid-stroke")
        self.log.append(("move", x, y, steps))

    async def down(self):
        self.log.append(("down",))

    async def up(self):
        self.log.append(("up",))


class _Page:
    def __init__(self, fail_on=None):
        self.log: list = []
        self.mouse = _Mouse(self.log, fail_on)

    async def wait_for_timeout(self, ms):
        pass

    async def screenshot(self):
        return b"png"


class _Session:
    def __init__(self, page):
        self._page = page

    def page(self):
        return self._page


async def test_each_stroke_presses_at_its_start_moves_through_the_rest_and_lifts():
    page = _Page()
    png = await actions.drag_path(_Session(page), [
        [[10, 20], [30, 40], [50, 60]],
        [[70, 80], [90, 100]],
    ])
    assert png == b"png"
    assert [e[0] for e in page.log] == [
        "move", "down", "move", "move", "up",
        "move", "down", "move", "up"]
    assert [e[1:3] for e in page.log if e[0] == "move"] == [
        (10, 20), (30, 40), (50, 60), (70, 80), (90, 100)]


async def test_the_pointer_never_jumps_between_waypoints():
    page = _Page()
    await actions.drag_path(_Session(page), [[[0, 0], [100, 100]]])
    assert all(e[3] > 1 for e in page.log if e[0] == "move"), page.log


async def test_a_button_pressed_is_released_when_a_move_fails():
    """Known-bad: take the `try`/`finally` away and the button stays down."""
    page = _Page(fail_on=(50, 60))
    with pytest.raises(RuntimeError):
        await actions.drag_path(_Session(page), [[[10, 20], [50, 60]]])
    assert [e[0] for e in page.log] == ["move", "down", "up"]


@pytest.mark.parametrize("strokes", [
    [],
    [[]],
    [[[1, 2]]],
    [[[1, 2], [3]]],
    [[[1, 2], [3, 4, 5]]],
    [[[1, 2], "ab"]],
    ["ab"],
])
async def test_a_path_that_cannot_be_drawn_is_refused_before_anything_moves(strokes):
    with pytest.raises(ToolError):
        await server.browser_drag(strokes)
