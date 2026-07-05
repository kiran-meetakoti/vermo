from __future__ import annotations

import sys


def patch_streamlit_for_python39_protocol_dataclass() -> None:
    """Work around Python 3.9's dataclass + Protocol constructor issue."""
    from streamlit.runtime.session_manager import SessionClient
    from streamlit.runtime.state.session_state import SessionStateStatProvider

    if getattr(SessionClient.__init__, "__name__", "") == "_no_init":

        def protocol_init(self, *args, **kwargs):
            return None

        SessionClient.__init__ = protocol_init

    if SessionStateStatProvider.__init__ is object.__init__:

        def __init__(self, session_mgr):
            self._session_mgr = session_mgr

        SessionStateStatProvider.__init__ = __init__


if __name__ == "__main__":
    patch_streamlit_for_python39_protocol_dataclass()

    from streamlit.web.cli import main

    if len(sys.argv) == 1:
        sys.argv.extend(
            [
                "run",
                "streamlit_app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                "8501",
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ]
        )
    raise SystemExit(main())
