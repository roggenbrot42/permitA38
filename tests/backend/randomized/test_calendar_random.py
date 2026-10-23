"""Random calendar operations through the API: never a server error, never a leaked subscription secret.

A hypothesis state machine connects, refreshes (with a flaky fake server), edits, previews and deletes calendars
of one boss. After every step the responses are checked for 5xx answers and for the secret token.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.netsafe import FetchError
from tests.backend.calendar_helpers import SECRET_TOKEN, SECRET_URL, FakeFetcher, all_day, ics, timed
from tests.backend.conftest import PASSWORD, _login


class CalendarMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        import tempfile
        from pathlib import Path

        self.tmp = Path(tempfile.mkdtemp())
        self.fetcher = FakeFetcher(ics())
        app = get_app(
            Config(data_dir=self.tmp / "data", secret_key="calendar-random-secret-key", web_dir=None),
            calendar_fetcher=self.fetcher,
        )
        self.anon = TestClient(app)
        self.anon.post(
            "/api/auth/setup", json={"username": "boss", "name": "Boss", "password": PASSWORD, "company_name": "Demo"}
        )
        self.client = _login(app, "boss")
        self.sources: list[str] = []

    def teardown(self):
        self.client.close()
        self.anon.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self, response):
        # 502 is the honest answer when the calendar server fails; anything else at 5xx is a bug
        assert response.status_code < 500 or response.status_code == 502, response.text
        assert SECRET_TOKEN not in response.text

    @initialize()
    def start(self):
        self.fetcher.body = ics()

    @rule(days=st.integers(min_value=1, max_value=200), with_all_day=st.booleans())
    def new_body(self, days, with_all_day):
        day = date.today() + timedelta(days=days)
        events = [timed(f"r{days}@example.test", "Meeting", _at(day, 10), _at(day, 11))]
        if with_all_day:
            events.append(all_day(f"d{days}@example.test", "Out of office", day))
        self.fetcher.body = ics(*events)
        self.fetcher.etag = f'"v{days}"'

    @rule(flaky=st.sampled_from(["none", "timeout", "gone"]))
    def network(self, flaky):
        self.fetcher.error = None
        if flaky == "timeout":
            self.fetcher.error = FetchError("timeout", "The calendar server did not answer in time.")
        elif flaky == "gone":
            self.fetcher.error = FetchError("http_error", "The calendar server returned an error.")

    @rule(
        policy=st.sampled_from(["none", "redacted", "full"]),
        url=st.sampled_from(
            [SECRET_URL, "http://x.example/a.ics", "webcal://cal.example.test/" + SECRET_TOKEN + ".ics"]
        ),
    )
    def connect(self, policy, url):
        r = self.client.post("/api/calendars", json={"name": "Random", "url": url, "title_policy": policy})
        self._check(r)
        if r.status_code == 200:
            self.sources.append(r.json()["id"])

    @rule(data=st.data())
    def refresh(self, data):
        if not self.sources:
            return
        sid = data.draw(st.sampled_from(self.sources))
        self._check(self.client.post(f"/api/calendars/{sid}/refresh"))

    @rule(data=st.data(), policy=st.sampled_from(["none", "redacted", "full"]))
    def edit(self, data, policy):
        if not self.sources:
            return
        sid = data.draw(st.sampled_from(self.sources))
        self._check(self.client.put(f"/api/calendars/{sid}", json={"title_policy": policy, "exclude_keywords": ["x"]}))

    @rule(data=st.data())
    def delete(self, data):
        if not self.sources:
            return
        sid = data.draw(st.sampled_from(self.sources))
        self.sources.remove(sid)
        self._check(self.client.delete(f"/api/calendars/{sid}"))

    @rule()
    def views(self):
        self._check(self.client.get("/api/calendars"))
        self._check(self.client.get("/api/calendars/suggestions"))
        me = self.client.get("/api/auth/me").json()
        today = date.today()
        self._check(
            self.client.get(
                "/api/calendars/busy",
                params={
                    "user_id": me["id"],
                    "start": today.isoformat(),
                    "end": (today + timedelta(days=30)).isoformat(),
                },
            )
        )

    @invariant()
    def listing_never_shows_the_secret(self):
        assert SECRET_TOKEN not in self.client.get("/api/calendars").text


def _at(day: date, hour: int):
    from datetime import datetime

    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


CalendarMachine.TestCase.settings = settings(max_examples=20, stateful_step_count=15, deadline=None)
TestCalendarRandom = CalendarMachine.TestCase
