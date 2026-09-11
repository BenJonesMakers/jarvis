"""claude_env.child_env(): the scrub is a scalpel, not a bucket.

CLAUDE_CODE_OAUTH_TOKEN is the one CLAUDE_CODE_-prefixed variable that must
survive -- it's the subscription credential `claude setup-token` documents
for a headless `-p` child with no browser login available, not an API key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import claude_env


def test_the_oauth_token_survives_the_claude_code_prefix_scrub():
    base = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-should-reach-the-child",
            "PATH": "/usr/bin"}
    env = claude_env.child_env(base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-should-reach-the-child"
    assert env["PATH"] == "/usr/bin"


def test_other_claude_code_prefixed_variables_are_still_scrubbed():
    """The exemption is named, not a blanket amnesty for the prefix — a
    leaked session id is exactly the recursive-session confusion the prefix
    scrub exists to prevent."""
    base = {"CLAUDE_CODE_SESSION_ID": "the-server's-own",
            "CLAUDE_CODE_OAUTH_TOKEN": "keep-me"}
    env = claude_env.child_env(base)
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me"


def test_an_api_key_alongside_the_oauth_token_is_still_scrubbed():
    """Both can be set on a machine mid-migration; the API key must never
    win by surviving the scrub next to the token that should be used."""
    base = {"CLAUDE_CODE_OAUTH_TOKEN": "keep-me", "ANTHROPIC_API_KEY": "sk-ant-drop-me"}
    env = claude_env.child_env(base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me"
    assert "ANTHROPIC_API_KEY" not in env
