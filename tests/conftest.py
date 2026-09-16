import os
import shutil

import pytest

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
