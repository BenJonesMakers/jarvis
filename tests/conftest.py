import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

# Before anything imports `server` (which loads `.env` at module scope): point
# its loader at a path that does not exist, so a developer's real `.env` — a
# Fish key, JARVIS_TTS, a brain model — cannot leak into the test process and
# make results depend on the machine. The autouse fixture below still gives
# each test its own writable JARVIS_ENV_FILE; this only guards the first import.
os.environ.setdefault(
    "JARVIS_ENV_FILE",
    os.path.join(tempfile.gettempdir(), "jarvis-tests-nonexistent.env"),
)


# --- Windows: read/write files as UTF-8, like the Linux CI already does -------
# The suite reads its own source and fixtures through `Path.read_text()` /
# `Path.open()` in dozens of places with no `encoding=`. On Windows that
# defaults to the locale codepage (cp1252) and chokes on the em-dashes and
# smart quotes throughout this repo — five test files fail to even import.
# The application code passes `encoding=` explicitly (it has to: it runs
# outside pytest); this only lifts the *test tree* to the same UTF-8 default a
# UTF-8 locale gives it for free.
if sys.platform == "win32":
    import pathlib as _pathlib

    _orig_read_text = _pathlib.Path.read_text
    _orig_write_text = _pathlib.Path.write_text
    _orig_open = _pathlib.Path.open

    def _read_text(self, encoding=None, errors=None, newline=None):
        return _orig_read_text(self, encoding=encoding or "utf-8", errors=errors)

    def _write_text(self, data, encoding=None, errors=None, newline=None):
        # newline="\n", not the platform default: several tests compare a
        # write_text() against a read_bytes(), and CRLF translation on Windows
        # would break every one. The Linux CI writes LF.
        return _orig_write_text(self, data, encoding=encoding or "utf-8",
                                errors=errors, newline=newline or "\n")

    def _open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding is None:
            encoding = "utf-8"
        if "b" not in mode and newline is None and ("w" in mode or "a" in mode or "x" in mode):
            newline = "\n"
        return _orig_open(self, mode, buffering, encoding, errors, newline)

    _pathlib.Path.read_text = _read_text
    _pathlib.Path.write_text = _write_text
    _pathlib.Path.open = _open


@pytest.fixture(autouse=True)
def _never_spawn_a_real_brain(monkeypatch):
    """server.lifespan builds the brain but must not start `claude` under test."""
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")


@pytest.fixture(autouse=True)
def _never_post_a_real_notification(monkeypatch, request):
    """No test may spam the developer's Notification Centre.

    Patched on the `notifier` module object itself rather than on `server`, so
    the `importlib.reload(server_module)` that several test fixtures do cannot
    hand the real implementation back. test_notifier.py is exempt: it tests
    notify() itself and mocks the subprocess boundary directly.
    """
    if request.module.__name__.endswith("test_notifier"):
        return
    import notifier

    async def _blocked(*args, **kwargs):
        raise AssertionError("a test tried to post a real macOS notification; "
                             "mock notifier.notify")

    monkeypatch.setattr(notifier, "notify", _blocked)


@pytest.fixture(autouse=True)
def _never_touch_the_real_projects_folder(monkeypatch, tmp_path):
    """No test may create a directory in the user's real ~/Projects.

    `create_project` writes into JARVIS_PROJECTS_DIR (default ~/Projects) and
    will create that root if it is missing, so the default is redirected into
    a tmp_path for every test — the same reasoning as JARVIS_DATA_DIR. A test
    that wants its own root still sets the variable itself; this only fills in
    a safe default.
    """
    monkeypatch.setenv("JARVIS_PROJECTS_DIR", str(tmp_path / "projects-root"))


@pytest.fixture(autouse=True)
def _never_write_to_the_live_dotenv(monkeypatch, tmp_path):
    """No test may write into the developer's live `.env`.

    Found the hard way: the settings endpoints write straight into the
    repository's own .env, so a test that posted a preference silently
    rewrote the developer's real configuration — and a test written to
    prove `.env` line injection injected the line for real. Same reasoning
    as JARVIS_DATA_DIR; a test that wants its own file still sets the
    variable itself.
    """
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / "dotenv" / ".env"))


@pytest.fixture(autouse=True)
def _never_write_to_the_live_data_dir(monkeypatch, tmp_path):
    """No test may write into the user's real `data/`.

    Most tests already set JARVIS_DATA_DIR (and still do — this only fills in
    a safe default), but a test that merely drives the brain writes there too
    now that a rate-limit event is persisted: without this, running the suite
    overwrote the live usage reading with a fixture's fake one.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data-dir"))


@pytest_asyncio.fixture(autouse=True)
async def _no_run_left_mid_flight():
    """No test may end with a run's driver still starting its child.

    The CI hang, twice, on the macOS runner's Python 3.12 and never on 3.13:
    a test spawned a run, asserted on the row, and returned in the same
    millisecond — while `RunExecutor._drive` was still inside
    `asyncio.create_subprocess_exec`, before the child existed. The loop's
    teardown then cancelled that task mid-spawn, and 3.12's subprocess
    transport never completes a cancellation delivered there: the suite sat
    in `_cancel_all_tasks` until GitHub killed the job 25 minutes later.
    3.13 completes it, which is why no local run ever showed it.

    So every driver alive at the end of a test is waited for here, inside
    the test's own loop, before the runner closes it. A test's fake `claude`
    exits in milliseconds, so the wait is normally nothing; a driver that
    is queued or reading forever is cancelled only after it has had time to
    get past the spawn, which is the one place cancellation must not land.
    """
    yield
    me = asyncio.current_task()

    def _alive(qualname: str) -> list:
        return [t for t in asyncio.all_tasks()
                if t is not me and not t.done()
                and getattr(t.get_coro(), "__qualname__", "") == qualname]

    # First, the exact place: asyncio's own pipe-connection task, which
    # exists only between fork and "the child is up". Whoever spawned it
    # (a run driver, the brain, a fake `osascript`) is parked on it. Let it
    # finish — milliseconds — and yield once so the spawner moves on.
    connecting = _alive("BaseSubprocessTransport._connect_pipes")
    if connecting:
        await asyncio.wait(connecting, timeout=5)
        await asyncio.sleep(0)
    # Then a run driver still going: give it time to end on its own (a
    # test's fake claude exits at once) before it is cancelled somewhere
    # safe to cancel.
    drivers = _alive("RunExecutor._drive")
    if not drivers:
        return
    _done, pending = await asyncio.wait(drivers, timeout=10)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5)
