import pytest


@pytest.fixture(scope="session")
def tk_root():
    """One Tk interpreter for the whole run.

    Destroying a Tk interpreter closes the standard channels it adopted, and
    under pytest's descriptor capture the next interpreter then fails to read
    its own library files ("Can't find a usable init.tcl") — intermittently,
    so the Tk tests used to skip themselves at random. Sharing one avoids it.
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:
        pytest.skip(f"no display for Tk: {e!r}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass
