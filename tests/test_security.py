"""Sign-in sessions and limits for paired devices, against a real running Pumpkin.
Run with: python3 -m unittest tests.test_security"""
import base64
import json
import os
import sys
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_devices import Instance  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_open = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open
UA_PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"


class TestSessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Instance("Box", with_model=True)
        lib = cls.p.api("models")["models"]
        cls.p.api("presets/from-model", {"path": lib[0]["path"], "name": "Tiny"})

    @classmethod
    def tearDownClass(cls):
        cls.p.stop()

    def req(self, path, cookie=None, bearer=None, accept="application/json", ua=UA_PHONE, body=None, method=None):
        h = {"Accept": accept, "User-Agent": ua}
        if cookie:
            h["Cookie"] = "pumpkin_key=" + cookie
        if bearer:
            h["Authorization"] = "Bearer " + bearer
        if body is not None:
            h.update({"Content-Type": "application/json", "X-Pumpkin": "1"})
        r = urllib.request.Request(f"http://127.0.0.1:{self.p.port}{path}", headers=h, method=method,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with _open(r, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def login(self, ua=UA_PHONE):
        code, headers, _ = self.req("/login?key=" + self.p.token, ua=ua)
        self.assertEqual(code, 302)
        cookie = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        return cookie

    def test_login_gives_own_key(self):
        c = self.login()
        self.assertNotEqual(c, self.p.token)
        self.assertNotIn(self.p.token, c)
        self.assertEqual(self.req("/pumpkin/api/state", cookie=c)[0], 200)
        lst = json.loads(self.req("/pumpkin/api/sessions", cookie=c)[2])["sessions"]
        me = [s for s in lst if s["current"]]
        self.assertEqual(len(me), 1)
        self.assertEqual(me[0]["name"], "Chrome on Android")
        self.assertNotIn("hash", me[0])
        # the file holds only hashes
        with open(f"{self.p.home}/.config/pumpkin/sessions.json") as f:
            self.assertNotIn(c.split(".")[1], f.read())

    def test_sign_out_one(self):
        a, b = self.login(), self.login(ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/131.0")
        lst = json.loads(self.req("/pumpkin/api/sessions", cookie=a)[2])["sessions"]
        b_id = b.split(".")[0]
        self.assertTrue(any(s["id"] == b_id for s in lst))
        self.assertEqual(self.req(f"/pumpkin/api/sessions/{b_id}", cookie=a, method="DELETE", body={})[0], 200)
        self.assertEqual(self.req("/pumpkin/api/state", cookie=b)[0], 401)
        self.assertEqual(self.req("/pumpkin/api/state", cookie=a)[0], 200)

    def test_desktop_window_does_not_pile_up(self):
        ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) PumpkinApp"
        for _ in range(3):
            self.login(ua=ua)
        lst = json.loads(self.req("/pumpkin/api/state", bearer=self.p.token)[2]) and \
            json.loads(self.req("/pumpkin/api/sessions", bearer=self.p.token)[2])["sessions"]
        self.assertEqual(sum(1 for s in lst if s["name"] == "Pumpkin app on Linux"), 1)

    def test_old_cookie_is_swapped(self):
        code, headers, _ = self.req("/", cookie=self.p.token, accept="text/html")
        self.assertEqual(code, 302)
        new = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        self.assertNotEqual(new, self.p.token)
        self.assertEqual(self.req("/pumpkin/api/state", cookie=new)[0], 200)

    def test_new_key_keeps_sessions_and_sign_out_everywhere(self):
        a, b = self.login(), self.login()
        old = self.p.token
        self.assertEqual(self.req("/pumpkin/api/token/reset", cookie=a, body={})[0], 200)
        with open(f"{self.p.home}/.config/pumpkin/token") as f:
            self.p.token = f.read().strip()
        self.assertNotEqual(old, self.p.token)
        self.assertEqual(self.req("/login?key=" + old)[0], 401)          # old links stop working
        self.assertEqual(self.req("/pumpkin/api/state", cookie=b)[0], 200)  # signed-in phones stay in
        self.req("/pumpkin/api/sessions/clear", cookie=a, body={"keep_this": True})
        self.assertEqual(self.req("/pumpkin/api/state", cookie=b)[0], 401)
        self.assertEqual(self.req("/pumpkin/api/state", cookie=a)[0], 200)

    def test_wrong_cookie(self):
        self.assertEqual(self.req("/pumpkin/api/state", cookie="abc.def")[0], 401)
        self.assertEqual(self.req("/pumpkin/api/state", cookie="")[0], 401)


class TestPeerLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Instance("Helper", with_model=True)
        lib = cls.p.api("models")["models"]
        cls.p.api("presets/from-model", {"path": lib[0]["path"], "name": "Tiny"})
        cls.p.api("settings", {"share_per_hour": 2}, method="PUT")
        cls.p.api("devices/share", {"on": True})
        time.sleep(1.0)
        code = cls.p.api("devices/share")["code"]
        cls.key = json.loads(base64.urlsafe_b64decode(code.split(":", 1)[1] + "==="))["k"]

    @classmethod
    def tearDownClass(cls):
        cls.p.stop()

    def ask(self):
        r = urllib.request.Request(f"http://127.0.0.1:{self.p.port}/pumpkin/peer/ask",
                                   data=json.dumps({"task": "hi"}).encode(),
                                   headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with _open(r, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_hourly_limit(self):
        self.assertEqual(self.ask()[0], 200)
        self.assertEqual(self.ask()[0], 200)
        code, body = self.ask()
        self.assertEqual(code, 429)
        self.assertIn("limit", body["error"])


if __name__ == "__main__":
    unittest.main()
