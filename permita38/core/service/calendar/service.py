"""Calendar sources: connect an ICS link or file, refresh it, and expose only what the policy allows.

Privacy rules implemented here (see docs/calendar.md):

* the subscription URL is a secret: it is stored in the database only and never returned (a masked form is);
* by default no titles and no categories are stored (``title_policy="none"``); UIDs are stored as hashes;
* a failed refresh keeps the previously stored events and records a safe error message (never the URL);
* an import replaces all events of a source atomically.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time as _time
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from permita38.core.errors import ServiceError
from permita38.core.netsafe import FetchedResource, FetchError, RequestBudget, parse_url
from permita38.core.repository import Repository
from permita38.core.service.calendar.ics import MAX_INPUT_BYTES, IcsError, Occurrence, parse_ics

MAX_WARNINGS = 20

log = logging.getLogger("permita38.calendar")

DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_REFRESH_MINUTES = 360
MIN_REFRESH_MINUTES = 15
MAX_REFRESH_MINUTES = 10080
MAX_SOURCES = 20
FULL_REFETCH_SECONDS = 24 * 3600  # conditional requests are used for at most a day after the last full import
PAST_DAYS = 30  # the stored horizon: 30 days back ...
FUTURE_DAYS = 548  # ... and about 18 months ahead
REDACTED_TITLE = "Busy"
TITLE_POLICIES = ("none", "redacted", "full")
ICS_ACCEPT = "text/calendar,text/plain;q=0.8,application/octet-stream;q=0.5"
ICS_TYPES = ("text/calendar", "application/ics", "text/plain", "application/octet-stream")

_FETCH_MESSAGES = {
    "invalid_url": "Please enter a valid calendar address (https://...).",
    "blocked_address": "This address is not allowed (private or internal network).",
    "insecure_url": "Only https:// calendar links are accepted (webcal:// is converted automatically).",
    "dns_error": "The calendar server could not be found.",
    "timeout": "The calendar server did not answer in time.",
    "network_error": "The calendar server could not be reached.",
    "tls_error": "The secure connection to the calendar server failed.",
    "too_large": "The calendar is too large (max 5 MB).",
    "bad_content_type": "The link does not point to a calendar file (.ics).",
    "http_error": "The calendar server returned an error.",
    "redirect_loop": "The calendar server redirects in a loop.",
    "too_many_redirects": "The calendar server redirects too often.",
    "budget_exceeded": "Too many requests for this calendar.",
}
_ICS_MESSAGES = {
    "invalid_ics": "This is not an iCalendar (.ics) file.",
    "too_large": "The calendar is too large (max 5 MB).",
    "too_deep": "The calendar is nested too deeply to be read safely.",
    "too_complex": "The calendar is too complex to process in time.",
    "too_many_events": "The calendar has more than 20,000 events in the stored period.",
}


class ResourceFetcher(Protocol):
    def fetch_resource(
        self,
        url: str,
        *,
        accept: str,
        allowed_types: tuple[str, ...],
        max_bytes: int,
        https_only: bool = False,
        conditional: dict[str, str] | None = None,
        budget: RequestBudget | None = None,
    ) -> FetchedResource: ...


def mask_url(url: str | None) -> str | None:
    """``https://calendar.example/…/****``: the host is visible, the secret path is not."""
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/…/****"


def _validator(value: str | None) -> str | None:
    """ETag / Last-Modified come from the remote server: keep them only if they fit the column."""
    return value if value and len(value) <= 255 else None


def uid_hash(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:24]


def user_zone(user: dict | None) -> ZoneInfo:
    name = (user or {}).get("timezone") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:  # unknown or unusable name: fall back to the default zone
        return ZoneInfo(DEFAULT_TIMEZONE)


def stored_window(zone: ZoneInfo, now: datetime) -> tuple[datetime, datetime]:
    """UTC bounds of the stored horizon (30 days back to about 18 months ahead, whole local days)."""
    today = now.astimezone(zone).date()
    start = datetime.combine(today - timedelta(days=PAST_DAYS), time(), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(today + timedelta(days=FUTURE_DAYS), time(), tzinfo=zone).astimezone(UTC)
    return start, end


def _iso_to_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def event_bounds(ev: dict) -> tuple[datetime, datetime]:
    """UTC bounds of a stored event (all-day events span whole UTC days, end exclusive)."""
    if ev.get("all_day"):
        return (
            datetime.combine(date.fromisoformat(ev["start_utc"]), time(), tzinfo=UTC),
            datetime.combine(date.fromisoformat(ev["end_utc"]), time(), tzinfo=UTC),
        )
    return _iso_to_datetime(ev["start_utc"]), _iso_to_datetime(ev["end_utc"])


class CalendarService:
    def __init__(
        self,
        store: Repository,
        fetcher: ResourceFetcher,
        *,
        clock: Callable[[], float] = _time.time,
    ):
        self.store = store
        self.fetcher = fetcher
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------------ ownership and views
    def _own(self, user: dict, source_id: str) -> dict:
        src = self.store.get("calendar_sources", source_id)
        if not src or src.get("user_id") != user["id"]:
            raise ServiceError(404, "Calendar not found")
        return src

    def _require(self, source_id: str) -> dict:
        src = self.store.get("calendar_sources", source_id)
        if src is None:
            raise ServiceError(404, "Calendar not found")
        return src

    def _view(self, src: dict) -> dict:
        return {
            "id": src["id"],
            "name": src.get("name"),
            "kind": src.get("kind"),
            "url_masked": mask_url(src.get("url")),
            "refresh_minutes": src.get("refresh_minutes"),
            "title_policy": src.get("title_policy"),
            "all_day_blocks": bool(src.get("all_day_blocks")),
            "include_keywords": list(src.get("include_keywords") or []),
            "exclude_keywords": list(src.get("exclude_keywords") or []),
            "busy_default": bool(src.get("busy_default", True)),
            "last_fetched": src.get("last_fetched"),
            "last_status": src.get("last_status"),
            "last_error": src.get("last_error"),
            "event_count": src.get("event_count") or 0,
            "last_warnings": list(src.get("last_warnings") or [])[:MAX_WARNINGS],
        }

    def list_sources(self, user: dict) -> list[dict]:
        return [self._view(s) for s in self.store.find("calendar_sources", user_id=user["id"])]

    def get_source(self, user: dict, source_id: str) -> dict:
        return self._view(self._own(user, source_id))

    # ----------------------------------------------------------------------------- create
    def add_url(self, user: dict, options: dict, url: str) -> dict:
        url = self._normalise_url(url)
        self._check_quota(user)
        now = self._clock()
        events, etag, last_modified, warnings = self._download(url, user, options, conditional=None)
        if events is None:  # cannot happen without validators; kept explicit for the type checker
            raise ServiceError(502, "The calendar could not be downloaded.")
        src = self._new_source(user, options, kind="ics_url", url=url, now=now)
        src.update(etag=etag, last_modified=last_modified, last_status="ok", last_error=None)
        return self._view(self._store_import(src, events, now, warnings))

    def add_upload(self, user: dict, options: dict, data: bytes) -> dict:
        self._check_quota(user)
        now = self._clock()
        events, warnings = self._parse(data, user, options)
        src = self._new_source(user, options, kind="ics_file", url=None, now=now)
        src.update(last_status="ok", last_error=None)
        return self._view(self._store_import(src, events, now, warnings))

    def _check_quota(self, user: dict) -> None:
        if len(self.store.find("calendar_sources", user_id=user["id"])) >= MAX_SOURCES:
            raise ServiceError(409, f"You can connect at most {MAX_SOURCES} calendars.")

    @staticmethod
    def _normalise_url(url: str) -> str:
        raw = (url or "").strip()
        if raw.lower().startswith("webcal://"):
            raw = "https://" + raw[len("webcal://") :]
        if not raw.lower().startswith("https://"):
            raise ServiceError(422, _FETCH_MESSAGES["insecure_url"])
        try:
            parsed = parse_url(raw, allowed_ports=(443,))
        except FetchError as err:
            raise ServiceError(422, _FETCH_MESSAGES.get(err.code, _FETCH_MESSAGES["invalid_url"])) from None
        return parsed.url

    def _new_source(self, user: dict, options: dict, *, kind: str, url: str | None, now: float) -> dict:
        return {
            "id": None,
            "user_id": user["id"],
            "name": options["name"],
            "kind": kind,
            "url": url,
            "refresh_minutes": options.get("refresh_minutes", DEFAULT_REFRESH_MINUTES),
            "title_policy": options.get("title_policy", "none"),
            "all_day_blocks": bool(options.get("all_day_blocks", False)),
            "include_keywords": list(options.get("include_keywords") or []),
            "exclude_keywords": list(options.get("exclude_keywords") or []),
            "busy_default": bool(options.get("busy_default", True)),
            "last_fetched": int(now),
            "event_count": 0,
        }

    # ----------------------------------------------------------------------------- update/delete
    def update(self, user: dict, source_id: str, changes: dict) -> dict:
        src = self._own(user, source_id)
        for key in ("name", "refresh_minutes", "all_day_blocks", "busy_default"):
            if changes.get(key) is not None:
                src[key] = changes[key]
        relabel = changes.get("title_policy") not in (None, src.get("title_policy"))
        if relabel:
            src["title_policy"] = changes["title_policy"]
        resync = relabel and src["title_policy"] == "full"  # full titles were never stored
        for key in ("include_keywords", "exclude_keywords"):
            if changes.get(key) is not None and list(changes[key]) != list(src.get(key) or []):
                src[key] = list(changes[key])
                resync = True  # keywords are applied while importing
        if resync and src.get("kind") == "ics_url":
            self.store.save("calendar_sources", src)
            return self.sync(user, source_id, force=True)  # the stored events must be rebuilt: no 304 shortcut
        if resync:
            src.update(last_status="needs_upload", last_error="The filter changed: upload the calendar file again.")
        elif relabel:
            self._apply_title_policy(source_id, src["title_policy"])
        self.store.save("calendar_sources", src)
        return self._view(self._require(source_id))

    def _apply_title_policy(self, source_id: str, policy: str) -> None:
        """Drop or redact the stored titles at once: a stricter policy must not leave titles behind."""
        with self.store.transaction():
            events = self.store.find("calendar_events", source_id=source_id)
            for ev in events:
                ev["title"] = None if policy == "none" else REDACTED_TITLE
                ev["categories"] = []
            self.store.save_many("calendar_events", events)

    def delete(self, user: dict, source_id: str) -> None:
        self._own(user, source_id)
        with self.store.transaction():
            self.store.delete_where("calendar_events", source_id=source_id)
            self.store.delete("calendar_sources", source_id)

    def delete_all_for_user(self, user_id: str) -> None:
        """Remove every calendar of a person (used when the person is deleted: no secret links or events remain)."""
        with self.store.transaction():
            for src in self.store.find("calendar_sources", user_id=user_id):
                self.store.delete_where("calendar_events", source_id=src["id"])
                self.store.delete("calendar_sources", src["id"])

    # ----------------------------------------------------------------------------- refresh
    def sync(self, user: dict, source_id: str, *, force: bool = False) -> dict:
        src = self._own(user, source_id)
        if src.get("kind") != "ics_url":
            raise ServiceError(409, "This calendar was imported from a file: upload the file again to refresh it.")
        if not self._begin(source_id):
            raise ServiceError(409, "This calendar is being refreshed already. Try again in a moment.")
        try:
            self._refresh_source(src, force=force)
        finally:
            self._end(source_id)
        return self._view(self._require(source_id))

    def is_stale(self, src: dict, now: float | None = None) -> bool:
        if src.get("kind") != "ics_url":
            return False
        last = src.get("last_fetched") or 0
        minutes = src.get("refresh_minutes") or DEFAULT_REFRESH_MINUTES
        return (now if now is not None else self._clock()) - last >= minutes * 60

    def schedule_stale_refresh(self, user_id: str) -> list[threading.Thread]:
        """Start background refreshes for this user's stale subscriptions (single-flight per source).

        Called from the request path of the planner: it returns at once. The returned threads let tests wait.
        """
        threads = []
        for src in self.store.find("calendar_sources", user_id=user_id):
            if not src.get("busy_default", True) or not self.is_stale(src) or not self._begin(src["id"]):
                continue  # info-only sources are refreshed by hand or by the command, not by planner requests
            thread = threading.Thread(target=self._background, args=(src["id"],), daemon=True)
            thread.start()
            threads.append(thread)
        return threads

    def refresh_all(self) -> list[tuple[str, str | None]]:
        """Refresh every subscription now (used by ``permita38 calendar refresh``). Returns (name, status) pairs."""
        out = []
        for src in self.store.list("calendar_sources"):
            if (
                src.get("kind") != "ics_url"
                or not src.get("user_id")
                or self.store.get("users", src["user_id"]) is None
            ):
                continue
            if self._begin(src["id"]):
                try:
                    self._refresh_source(src)
                finally:
                    self._end(src["id"])
            out.append(
                (src.get("name") or "", (self.store.get("calendar_sources", src["id"]) or {}).get("last_status"))
            )
        return out

    def _background(self, source_id: str) -> None:
        try:
            src = self.store.get("calendar_sources", source_id)
            if src is not None:
                self._refresh_source(src)
        except Exception:  # never let a worker thread die loudly with request data in it
            log.warning("calendar refresh failed unexpectedly (source %s)", source_id)
        finally:
            self._end(source_id)

    def _begin(self, source_id: str) -> bool:
        with self._lock:
            if source_id in self._inflight:
                return False
            self._inflight.add(source_id)
            return True

    def _end(self, source_id: str) -> None:
        with self._lock:
            self._inflight.discard(source_id)

    def _refresh_source(self, src: dict, *, force: bool = False) -> None:
        now = self._clock()
        owner = self.store.get("users", src["user_id"]) or {}
        # a "not modified" answer would keep a stored window that slides out of date: revalidate the full file regularly
        fresh = now - (src.get("imported_at") or 0) < FULL_REFETCH_SECONDS
        use_validators = fresh and not force
        conditional = {"etag": src.get("etag"), "last_modified": src.get("last_modified")} if use_validators else None
        try:
            events, etag, last_modified, warnings = self._download(src["url"], owner, src, conditional=conditional)
        except ServiceError as err:
            self._patch_source(src["id"], last_status="error", last_error=err.message, last_fetched=int(now))
            return
        if events is None:  # 304: nothing changed on the server
            self._patch_source(src["id"], last_status="not_modified", last_error=None, last_fetched=int(now))
            return
        self._store_import(
            {**src, "etag": etag, "last_modified": last_modified, "last_status": "ok", "last_error": None},
            events,
            now,
            warnings,
        )

    def _patch_source(self, source_id: str, **fields: object) -> dict | None:
        """Change only ``fields`` of a source that still exists (a refresh must never resurrect a deleted calendar)."""
        with self.store.transaction():
            current = self.store.get("calendar_sources", source_id)
            if current is None:
                return None
            current.update(fields)
            return self.store.save("calendar_sources", current)

    # ----------------------------------------------------------------------------- download/parse
    def _download(
        self, url: str, owner: dict, options: dict, *, conditional: dict | None
    ) -> tuple[list[Occurrence] | None, str | None, str | None, list[str]]:
        try:
            res = self.fetcher.fetch_resource(
                url,
                accept=ICS_ACCEPT,
                allowed_types=ICS_TYPES,
                max_bytes=MAX_INPUT_BYTES,
                https_only=True,
                conditional=conditional,
            )
        except FetchError as err:
            raise ServiceError(502, _FETCH_MESSAGES.get(err.code, "The calendar could not be downloaded.")) from None
        if res.status == 304:
            return None, (conditional or {}).get("etag"), None, []
        events, warnings = self._parse(res.body, owner, options)
        return events, _validator(res.etag), _validator(res.last_modified), warnings

    def _parse(self, data: bytes, owner: dict, options: dict) -> tuple[list[Occurrence], list[str]]:
        zone = user_zone(owner)
        start, end = stored_window(zone, datetime.fromtimestamp(self._clock(), UTC))
        try:
            result = parse_ics(
                data,
                default_tz=zone,
                window=(start, end),
                include_keywords=options.get("include_keywords") or [],
                exclude_keywords=options.get("exclude_keywords") or [],
            )
        except IcsError as err:
            raise ServiceError(422, _ICS_MESSAGES.get(err.code, "The calendar could not be read.")) from None
        return result.occurrences, list(result.warnings)[:MAX_WARNINGS]

    def _store_import(self, src: dict, occurrences: list[Occurrence], now: float, warnings: list[str]) -> dict:
        """Replace the source's events atomically and save the source (no titles unless the policy allows).

        A refresh of a calendar that was deleted meanwhile stores nothing (the source is re-read inside the
        transaction and only the fields that this import changes are written).
        """
        policy = src.get("title_policy", "none")
        stamp = int(now)
        with self.store.transaction():
            if src.get("id") is None:
                src = self.store.save("calendar_sources", src)
            else:
                current = self.store.get("calendar_sources", src["id"])
                if current is None:
                    return src
                current.update({k: src.get(k) for k in ("etag", "last_modified", "last_status", "last_error")})
                src = current
            self.store.delete_where("calendar_events", source_id=src["id"])
            rows = [
                {
                    "created": stamp,
                    "source_id": src["id"],
                    "uid": uid_hash(occ.uid),
                    "recurrence_id": None,
                    "start_utc": occ.start,
                    "end_utc": occ.end,
                    "all_day": occ.all_day,
                    "busy": True,
                    "ooo": occ.ooo,
                    "title": None if policy == "none" else (REDACTED_TITLE if policy == "redacted" else occ.summary),
                    "categories": list(occ.categories) if policy == "full" else [],
                }
                for occ in occurrences
            ]
            self.store.save_many("calendar_events", rows)
            src.update(
                event_count=len(rows),
                last_fetched=stamp,
                imported_at=stamp,
                last_warnings=list(warnings)[:MAX_WARNINGS],
            )
            return self.store.save("calendar_sources", src)

    # ----------------------------------------------------------------------------- read models
    def preview(self, user: dict, source_id: str, days: int = 14) -> list[dict]:
        src = self._own(user, source_id)
        zone = user_zone(user)
        today = datetime.fromtimestamp(self._clock(), UTC).astimezone(zone).date()
        lo = datetime.combine(today, time(), tzinfo=zone).astimezone(UTC)
        hi = lo + timedelta(days=days)
        out = []
        for ev in self.store.find("calendar_events", source_id=src["id"]):
            s, e = event_bounds(ev)
            if e > lo and s < hi:
                out.append(self._event_view(ev))
        return sorted(out, key=lambda item: item["start"])

    def suggestions(self, user: dict) -> list[dict]:
        zone = user_zone(user)
        now = datetime.fromtimestamp(self._clock(), UTC)
        today = now.astimezone(zone).date()
        lo = datetime.combine(today, time(), tzinfo=zone).astimezone(UTC)
        names = {s["id"]: s.get("name") for s in self.store.find("calendar_sources", user_id=user["id"])}
        out = []
        for source_id, name in names.items():
            for ev in self.store.find("calendar_events", source_id=source_id):
                if not ev.get("ooo"):
                    continue
                _s, e = event_bounds(ev)
                if e >= lo:
                    out.append({**self._event_view(ev), "source_id": source_id, "source_name": name})
        return sorted(out, key=lambda item: item["start"])

    def events_for(self, user_id: str) -> list[tuple[dict, dict]]:
        """(source, event) pairs of a user, for the busy-time computation."""
        pairs = []
        for src in self.store.find("calendar_sources", user_id=user_id):
            for ev in self.store.find("calendar_events", source_id=src["id"]):
                pairs.append((src, ev))
        return pairs

    @staticmethod
    def _event_view(ev: dict) -> dict:
        return {
            "start": ev["start_utc"],
            "end": ev["end_utc"],
            "all_day": bool(ev.get("all_day")),
            "title": ev.get("title"),
            "ooo": bool(ev.get("ooo")),
        }
