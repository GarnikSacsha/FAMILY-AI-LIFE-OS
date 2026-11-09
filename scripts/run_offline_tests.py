"""Run this checkout's tests without production configuration or outbound sockets."""

import os
import socket
import sys
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for name in list(os.environ):
    if name.upper() not in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}:
        del os.environ[name]
os.environ.update(ENVIRONMENT="test", PYTHONDONTWRITEBYTECODE="1")
sys.dont_write_bytecode = True

import pytest  # noqa: E402
from pydantic_settings import DotEnvSettingsSource  # noqa: E402

original_connect = socket.socket.connect
original_socketpair = socket.socketpair
local_state = threading.local()


def local_socketpair(*args, **kwargs):
    local_state.creating_pair = True
    try:
        return original_socketpair(*args, **kwargs)
    finally:
        local_state.creating_pair = False


def deny_network(sock, address):
    if getattr(local_state, "creating_pair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original_connect(sock, address)
    raise AssertionError("Outbound network is disabled for offline tests")


temporary = ROOT / "outputs" / "consolidation" / ("pytest-" + uuid.uuid4().hex)
assert temporary.resolve().is_relative_to(ROOT.resolve())
assert not temporary.exists()
temporary.parent.mkdir(parents=True, exist_ok=True)
os.environ["COVERAGE_FILE"] = str(temporary.parent / ".coverage")
with (
    patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
    patch.object(socket, "socketpair", local_socketpair),
    patch.object(socket.socket, "connect", deny_network),
    patch.object(socket.socket, "connect_ex", deny_network),
):
    raise SystemExit(pytest.main(["-p", "no:cacheprovider", "--basetemp", str(temporary), *sys.argv[1:]]))
