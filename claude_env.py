"""The environment every Claude Code child of JARVIS is given.

There is exactly one rule and it is a billing rule: JARVIS's Claude Code
children run on the user's **subscription**, never on an API key. The CLI
prefers an inherited `ANTHROPIC_API_KEY` over the login without saying so —
`claude auth status` still reports `loggedIn: true` while `apiKeySource`
quietly flips to the env key and the account's email and organisation go
blank — so a key in the environment silently moves every spawned run onto
paid API billing.

`server.py` loads `.env` into `os.environ` at import, and a developer's
`.env` legitimately holds `ANTHROPIC_API_KEY` for the older lookup paths.
That is how the key reaches a child that never wanted it.

This module exists so the scrub is written once. It was fixed for the brain
during milestone 1 and NOT for the run pipeline, which is precisely the
failure mode a second copy of the logic produces: the copy that was not
updated is the one nobody notices.
"""

from __future__ import annotations

import os
import shlex

# Every ANTHROPIC_* variable, not just the key: the base URL and the model
# override redirect a child just as effectively as credentials do.
SCRUBBED_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_")
SCRUBBED_ENV_KEYS = {"CLAUDECODE"}

# The one CLAUDE_CODE_-prefixed variable that must survive the scrub above.
# It is a *subscription* credential -- the long-lived token `claude
# setup-token` prints, documented as the way to authenticate a headless `-p`
# child when no browser login is available (docs.claude.com/en/docs/
# claude-code/authentication#generate-a-long-lived-token) -- not an API key,
# so keeping it does not reintroduce the billing hazard the rest of the
# prefix exists to block. Dropping it silently is worse than keeping it: a
# server deployment sets exactly this variable and nothing else, and a
# scrubbed child would either fail to authenticate at all or (if
# ANTHROPIC_API_KEY happened to be set too) fall back to paid API billing
# with no error pointing at why.
_KEPT_DESPITE_PREFIX = {"CLAUDE_CODE_OAUTH_TOKEN"}

# asyncio.create_subprocess_exec gives a child's stdout/stderr StreamReader a
# 64 KiB *line* buffer by default (asyncio.streams._DEFAULT_LIMIT). `claude -p
# --output-format stream-json` emits one JSON object per line, and a single
# line carrying a large tool result (a big file read, a long assistant
# message, a large diff) routinely exceeds that. When it does, `readline()`
# raises `ValueError("Separator is not found, and chunk exceed the limit")` —
# which, uncaught, killed an otherwise-healthy run (run_executor.py) or the
# brain process (brain.py) outright. A run that had been working for 28
# minutes was recorded as `failed` in 0 seconds because of exactly this.
#
# This is a buffer *ceiling*, not a pre-allocation: asyncio grows the
# underlying bytearray as data arrives, so a generous limit costs nothing
# while idle. 64 MiB is comfortably larger than any single stream-json line
# JARVIS has observed in practice (the worst offenders are full-file Read
# results and large diffs, which top out in the low single-digit MiB) while
# staying small enough that even a runaway line cannot balloon memory
# unboundedly — pass this to every `create_subprocess_exec(..., limit=...)`
# that reads a Claude Code child's stdout/stderr.
STREAM_LINE_LIMIT = 64 * 1024 * 1024  # 64 MiB per line


def split_command(spec: str) -> list[str]:
    """Split a configured `claude` invocation into argv.

    `spec` is usually a bare path but may carry arguments (the tests pass
    `"<python> <script>"`). Two traps:

      * A bare path can contain spaces — `C:\\Users\\me\\Author Software\\claude.exe`
        — and must NOT be split on them. So if the whole string names an
        existing file, it is taken verbatim.
      * Otherwise it is a command line and is split. POSIX `shlex.split` treats
        `\\` as an escape and eats every backslash in a Windows path, so on
        Windows the split is done with `posix=False`, which keeps separators.
    """
    spec = spec.strip()
    if spec and os.path.isfile(spec):
        return [spec]
    return shlex.split(spec, posix=(os.name != "nt"))


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with everything that would redirect or
    re-bill a Claude Code child removed. Everything else — PATH, HOME,
    CLAUDE_CONFIG_DIR, the user's own variables — passes through untouched."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items()
            if (not k.startswith(SCRUBBED_ENV_PREFIXES) or k in _KEPT_DESPITE_PREFIX)
            and k not in SCRUBBED_ENV_KEYS}
