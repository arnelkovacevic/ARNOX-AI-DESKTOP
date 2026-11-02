import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main
from actions import youtube_video
from core import url_opener
from core import workspace_router as router
from tests.test_phase1_decoupling import StubClient


class _Opener:
    """Remembers what was opened; stands in for the workspace."""

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return f"Opened {url} in the workspace."


class WebAppUrlTests(unittest.TestCase):
    def test_site_names_and_addresses_resolve(self):
        self.assertEqual(router.web_app_url("instagram"), "https://www.instagram.com")
        self.assertEqual(router.web_app_url("Facebook"), "https://www.facebook.com")
        self.assertEqual(router.web_app_url("facebook.com"), "https://facebook.com")
        self.assertEqual(router.web_app_url("https://example.com/a?b=1"), "https://example.com/a?b=1")
        self.assertEqual(router.web_app_url("x"), "https://x.com")

    def test_native_apps_files_and_spotify_are_not_websites(self):
        for name in ("calculator", "Notes", "report.pdf", "data.csv", "spotify", "", "my.script.py"):
            self.assertIsNone(router.web_app_url(name), name)

    def test_unsafe_addresses_are_not_resolved(self):
        for name in ("javascript:alert(1)", "file:///etc/passwd", "http://evil.test"):
            self.assertIsNone(router.web_app_url(name), name)

    def test_search_url_is_encoded(self):
        self.assertEqual(router.search_url("weather in Sarajevo"), "https://duckduckgo.com/?q=weather+in+Sarajevo")


class OpenerTests(unittest.TestCase):
    def tearDown(self):
        url_opener.set_in_app_opener(None)

    def test_registered_opener_receives_the_address_and_nothing_launches_outside(self):
        opener = _Opener()
        url_opener.set_in_app_opener(opener)
        with mock.patch.object(url_opener.subprocess, "Popen", side_effect=AssertionError("no outside browser")):
            url_opener.open_url("https://example.com")
        self.assertEqual(opener.urls, ["https://example.com"])

    def test_headless_runs_without_an_app_still_work(self):
        with mock.patch.object(url_opener.subprocess, "Popen") as popen:
            url_opener.open_url("https://example.com")
        popen.assert_called_once()

    def test_youtube_opens_in_the_workspace(self):
        opener = _Opener()
        url_opener.set_in_app_opener(opener)
        with mock.patch.object(youtube_video.subprocess, "Popen", side_effect=AssertionError("no outside browser")):
            youtube_video._open_url("https://www.youtube.com/watch?v=abc")
        self.assertEqual(opener.urls, ["https://www.youtube.com/watch?v=abc"])

    def test_the_live_engine_registers_the_workspace_as_the_opener(self):
        client = StubClient()
        client.workspace_open_web = _Opener()
        main.JarvisLive(client)
        self.assertTrue(url_opener.has_in_app_opener())
        url_opener.open_url("https://example.com")
        self.assertEqual(client.workspace_open_web.urls, ["https://example.com"])

    def test_a_client_without_a_workspace_registers_nothing(self):
        url_opener.set_in_app_opener(None)
        main.JarvisLive(StubClient())
        self.assertFalse(url_opener.has_in_app_opener())


class DispatcherRuleOneTests(unittest.TestCase):
    def setUp(self):
        self.web = _Opener()
        self.live = main.JarvisLive.__new__(main.JarvisLive)
        self.live.ui = SimpleNamespace(
            muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None,
            workspace_open_web=self.web,
            workspace_show_data=lambda kind, payload, title: self.shown.append((kind, title)),
        )
        self.live.cloud_safe = False
        self.shown = []
        for target in ("open_app", "browser_control"):
            patcher = mock.patch.object(main, target, side_effect=AssertionError(f"{target} must not run"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, name, args):
        return asyncio.run(self.live._execute_tool(SimpleNamespace(name=name, args=args, id="1"))).response["result"]

    def test_open_instagram_by_name_goes_to_the_workspace(self):
        result = self._run("open_app", {"app_name": "instagram"})
        self.assertEqual(self.web.urls, ["https://www.instagram.com"])
        self.assertIn("workspace", result)

    def test_open_a_website_address_goes_to_the_workspace(self):
        self._run("open_app", {"app_name": "facebook.com"})
        self.assertEqual(self.web.urls, ["https://facebook.com"])

    def test_open_a_browser_never_launches_one(self):
        for browser in ("Chrome", "safari", "browser"):
            result = self._run("open_app", {"app_name": browser})
            self.assertIn("inside ARNOX", result)
        self.assertEqual(self.web.urls, [])

    def test_open_gmail_and_calendar_show_the_views_not_the_websites(self):
        frame = SimpleNamespace()
        with mock.patch("ui_workspace.data_views.fetch", return_value=("inbox", frame, "Inbox", "Inbox shown.")) as fetch:
            self.assertEqual(self._run("open_app", {"app_name": "Gmail"}), "Inbox shown.")
            fetch.assert_called_with("inbox", {})
        with mock.patch("ui_workspace.data_views.fetch", return_value=("agenda", frame, "Calendar", "Calendar shown.")) as fetch:
            self._run("open_app", {"app_name": "calendar"})
            fetch.assert_called_with("agenda", {})
        self.assertEqual([k for k, _ in self.shown], ["inbox", "agenda"])
        self.assertEqual(self.web.urls, [])

    def test_a_failing_mail_view_is_reported(self):
        with mock.patch("ui_workspace.data_views.fetch", side_effect=ValueError("Not connected")):
            self.assertIn("Could not show that: Not connected", self._run("open_app", {"app_name": "gmail"}))

    def test_browser_control_go_to_new_tab_and_search_stay_in_the_workspace(self):
        self._run("browser_control", {"action": "go_to", "url": "https://www.instagram.com"})
        self._run("browser_control", {"action": "new_tab", "url": "facebook.com"})
        self._run("browser_control", {"action": "search", "query": "sarajevo weather", "browser": "chrome"})
        self.assertEqual(self.web.urls, [
            "https://www.instagram.com", "https://facebook.com", "https://duckduckgo.com/?q=sarajevo+weather",
        ])

    def test_native_apps_still_use_open_app(self):
        with mock.patch.object(main, "open_app", return_value="Opened Calculator.") as real:
            self.assertEqual(self._run("open_app", {"app_name": "Calculator"}), "Opened Calculator.")
        real.assert_called_once()
        self.assertEqual(self.web.urls, [])

    def test_browser_automation_actions_still_reach_the_automation_browser(self):
        with mock.patch.object(main, "browser_control", return_value="Clicked.") as real:
            self.assertEqual(self._run("browser_control", {"action": "click", "selector": "#go"}), "Clicked.")
        real.assert_called_once()

    def test_without_a_workspace_the_old_behaviour_remains_for_the_hosted_service(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = SimpleNamespace(muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None)
        live.cloud_safe = False
        with mock.patch.object(main, "open_app", return_value="Opened Instagram.") as real:
            result = asyncio.run(live._execute_tool(SimpleNamespace(name="open_app", args={"app_name": "instagram"}, id="1"))).response["result"]
        self.assertEqual(result, "Opened Instagram.")
        real.assert_called_once()


class DescriptionTests(unittest.TestCase):
    def test_tool_descriptions_no_longer_invite_opening_websites_externally(self):
        decls = {d["name"]: d["description"] for d in main.TOOL_DECLARATIONS}
        self.assertNotIn("opening websites", decls["browser_control"])
        self.assertIn("workspace_open", decls["browser_control"])
        self.assertIn("NATIVE", decls["open_app"])
        self.assertIn("workspace_open", decls["open_app"])

    def test_prompt_routes_websites_to_the_workspace(self):
        prompt = open(os.path.join(os.path.dirname(main.__file__), "core", "prompt.txt"), encoding="utf-8").read()
        self.assertIn("Show inside ARNOX", prompt)
        self.assertIn("Any site or address (instagram, facebook.com, youtube, a URL): workspace_open with url", prompt)


if __name__ == "__main__":
    unittest.main()


class WorkspaceOpenUrlPrecedenceTests(unittest.TestCase):
    """A model may send both url and view; an explicit address must win."""

    def test_url_with_view_search_opens_the_site_not_an_empty_search(self):
        import asyncio
        from types import SimpleNamespace as NS

        opened = []
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = NS(muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None,
                     workspace_open_web=lambda url: opened.append(url) or "Opened.",
                     workspace_show_data=lambda *a: (_ for _ in ()).throw(AssertionError("no data view")))
        live.cloud_safe = False
        call = NS(name="workspace_open", args={"url": "https://www.instagram.com", "view": "search"}, id="1")
        result = asyncio.run(live._execute_tool(call)).response["result"]
        self.assertEqual(opened, ["https://www.instagram.com"])
        self.assertEqual(result, "Opened.")
