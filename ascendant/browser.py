"""Browser opening shared by the dashboard and lightweight fetching node."""

import subprocess
import webbrowser


def open_browser(url: str, browser_name: str) -> None:
    if browser_name == "none":
        return
    if browser_name == "chrome":
        try:
            subprocess.run(["open", "-a", "Google Chrome", url], check=True)
            return
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Could not open Google Chrome: {exc}")
    webbrowser.open(url)
