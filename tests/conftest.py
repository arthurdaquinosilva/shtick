import os
import shutil

import pytest

from shtick.config import Settings
from shtick.engine import Session


def installed(*names):
    return [n for n in names if shutil.which(n)]


# Every shell dialect we support, plus macOS's bash 3.2 when it's there.
SHELLS = installed("bash", "dash", "zsh") + (["/bin/bash"] if os.path.exists("/bin/bash") and shutil.which("bash") != "/bin/bash" else [])


@pytest.fixture(params=SHELLS)
def session(request, tmp_path):
    s = Session(request.param, cwd=str(tmp_path))
    yield s
    s.close()


@pytest.fixture
def bash(tmp_path):
    s = Session("bash", cwd=str(tmp_path))
    yield s
    s.close()


def make_shell(tmp_path, monkeypatch, shell="bash"):
    from shtick.shell import Shell

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "100")
    return Shell(Settings(shell=shell), profile=None, cwd=str(tmp_path))


@pytest.fixture
def shell(tmp_path, monkeypatch):
    sh = make_shell(tmp_path, monkeypatch)
    yield sh
    sh.close()


@pytest.fixture(params=SHELLS)
def any_shell(request, tmp_path, monkeypatch):
    sh = make_shell(tmp_path, monkeypatch, request.param)
    yield sh
    sh.close()
