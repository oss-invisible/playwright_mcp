"""Why a browser did not start, named from what the launch said.

⛔ CLASSIFIED WHERE THE EXCEPTION IS CAUGHT (`Work.open`), BECAUSE THAT IS THE
ONLY PLACE THE FAILURE IS WHOLE. The engine reports a failed launch as a plain
`RuntimeError`, `OSError`, `ProtocolError` or timeout - there is no class per
cause - and puts what Firefox printed in the message: "it printed:" after an
exit during startup, "its last output:" after a pipe that closed. That text is
the one account of why, so it is what is read here, along with the exception's
type for the one cause that has a type of its own (`PermissionError`). Further
up, a client would have the sentence and nothing else to go on, which is the
matching of English this module exists to end.

ORDER MATTERS AND IS THE ORDER OF THE TABLE: a profile another Firefox holds
is named before anything else because its output also mentions a sandbox; a
missing library before a refused permission because "cannot open shared object
file" is followed by "No such file or directory", not by a permission. What
matches nothing is `launch_failed`, which is an honest answer and not a
fallback for a bug: the engine started and failed for a reason this does not
know, and the sentence beside it carries the engine's own words.

Each pattern is a line Firefox, the core or the operating system really writes:

  profile_locked     parent.lock, "Firefox is already running"
  no_display         "no DISPLAY environment variable", "cannot open display",
                     "Missing X server", the core's "requires Xvfb" and its
                     hidden-desktop refusal on a machine with no logged-on
                     window station
  missing_libraries  the loader's "error while loading shared libraries ...
                     cannot open shared object file", and on Windows the exit
                     code 0xC0000135 (3221225781, STATUS_DLL_NOT_FOUND)
  sandbox_blocked    EPERM / EACCES ("Operation not permitted", "Permission
                     denied"), Windows "Access is denied" and a policy that
                     blocks the file, a seccomp or user-namespace refusal, and
                     error 14001 - the side-by-side failure of an engine that
                     was downloaded into a packaged app's redirected AppData
"""
from __future__ import annotations

import re

#: In the order they are tried. The first that matches names the cause.
PATTERNS = (
    ("profile_locked", re.compile(
        r"parent\.lock|already running|profile[^\n]{0,60}(in use|is locked)", re.I)),
    ("no_display", re.compile(
        r"no DISPLAY environment variable|(cannot|can't|unable to|failed to) open display"
        r"|Missing X server|Unable to init server|requires Xvfb|no free X display"
        r"|Xvfb [^\n]*(failed|exited|neither)|no interactive window station"
        r"|Authorization required, but no authorization protocol", re.I)),
    ("missing_libraries", re.compile(
        r"error while loading shared libraries|cannot open shared object file"
        r"|STATUS_DLL_NOT_FOUND|0xC0000135|\b3221225781\b"
        r"|specified module could not be found|code execution cannot proceed", re.I)),
    ("sandbox_blocked", re.compile(
        r"Operation not permitted|Permission denied|Access is denied|blocked by group policy"
        r"|application control policy|seccomp|user namespace|No usable sandbox|AppArmor"
        r"|Landlock|side-by-side configuration|\b14001\b", re.I)),
)


def cause_of(failure: BaseException) -> str:
    """The cause of a launch that raised `failure`, one of `state.OPEN_CAUSES`
    (the first five): read from the messages along the exception's chain,
    which is where the engine put what Firefox printed."""
    seen, texts, link = set(), [], failure
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        texts.append(str(link))
        link = link.__cause__ or link.__context__
    text = "\n".join(texts)
    for cause, pattern in PATTERNS:
        if pattern.search(text):
            return cause
    return "sandbox_blocked" if isinstance(failure, PermissionError) else "launch_failed"
