"""Calendar sources (ICS links and files) and the busy time they imply. Thin: logic lives in core/service/calendar."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import ValidationError

from permita38.api.deps import Needs, ServicesDep
from permita38.core import validation as v
from permita38.core.models import CalendarEventOut, CalendarIn, CalendarOut, CalendarPatchIn, CalendarTitlePolicy

router = APIRouter(prefix="/calendars", tags=["calendars"])
routers = [router]
MAX_UPLOAD = 5 * 1024 * 1024
MAX_SPAN_DAYS = 400


def _flag(value: str, default: bool) -> bool:
    """Form flags are strings: an absent field must mean the default, not 'false' as with HTML checkboxes."""
    text = (value or "").strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no"):
        return False
    return default


# calendars are boss-only (as before the roles branch): a boss manages only their own sources
CalendarUser = Annotated[dict, Needs("planning", "write", full=True)]


@router.get("", response_model=list[CalendarOut], summary="your connected calendars (the link itself is never shown)")
def list_calendars(user: CalendarUser, services: ServicesDep):
    return services.calendar.list_sources(user)


@router.post("", response_model=CalendarOut, summary="connect a calendar by its ICS link (https)")
def add_calendar(body: CalendarIn, user: CalendarUser, services: ServicesDep):
    if not body.url:
        raise HTTPException(422, "Enter the calendar link, or upload a .ics file instead.")
    return services.calendar.add_url(user, body.model_dump(exclude={"url"}), body.url)


@router.post("/upload", response_model=CalendarOut, summary="connect a calendar from an uploaded .ics file")
def upload_calendar(
    request: Request,
    user: CalendarUser,
    services: ServicesDep,
    file: UploadFile,
    name: Annotated[str, Form(max_length=200)],
    title_policy: Annotated[CalendarTitlePolicy, Form()] = "none",
    refresh_minutes: Annotated[int, Form(ge=15, le=10080)] = 360,
    all_day_blocks: Annotated[str, Form(max_length=5)] = "false",  # "true" / "false" (absent = default)
    busy_default: Annotated[str, Form(max_length=5)] = "true",
    include_keywords: Annotated[str, Form(max_length=2000)] = "",
    exclude_keywords: Annotated[str, Form(max_length=2000)] = "",
):
    try:
        v.check_form_values(name, all_day_blocks, busy_default, include_keywords, exclude_keywords)
    except ValueError as err:
        raise HTTPException(422, f"form fields: {err}") from None
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD + 65536:  # refuse before the body is spooled to disk
        raise HTTPException(413, "The calendar file is too large (max 5 MB).")
    try:
        options = CalendarIn(
            name=name,
            title_policy=title_policy,
            refresh_minutes=refresh_minutes,
            all_day_blocks=_flag(all_day_blocks, False),
            busy_default=_flag(busy_default, True),
            include_keywords=[k.strip() for k in include_keywords.split(",") if k.strip()][:20],
            exclude_keywords=[k.strip() for k in exclude_keywords.split(",") if k.strip()][:20],
        ).model_dump(exclude={"url"})
    except ValidationError as err:  # built by hand from form fields: report it like any other invalid input
        raise HTTPException(
            422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
        ) from None
    blob = file.file.read(MAX_UPLOAD + 1)
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, "The calendar file is too large (max 5 MB).")
    return services.calendar.add_upload(user, options, blob)


@router.put(
    "/{source_id}", response_model=CalendarOut, summary="change name, filters, title policy or refresh interval"
)
def update_calendar(source_id: str, body: CalendarPatchIn, user: CalendarUser, services: ServicesDep):
    return services.calendar.update(user, source_id, body.model_dump(exclude_unset=True))


@router.delete("/{source_id}", status_code=204, summary="disconnect a calendar and delete its stored events")
def delete_calendar(source_id: str, user: CalendarUser, services: ServicesDep):
    services.calendar.delete(user, source_id)
    return Response(status_code=204)


@router.post("/{source_id}/refresh", response_model=CalendarOut, summary="download the calendar now")
def refresh_calendar(source_id: str, user: CalendarUser, services: ServicesDep):
    return services.calendar.sync(user, source_id)


@router.get("/busy", response_model=dict[str, float], summary="busy hours per day for your own capacity")
def busy_hours(
    user: CalendarUser,
    services: ServicesDep,
    user_id: Annotated[str, Query(max_length=32)],
    start: date,
    end: date,
) -> dict[str, Any]:
    if user_id != user["id"]:
        raise HTTPException(403, "Forbidden")
    if end < start or (end - start).days > MAX_SPAN_DAYS:
        raise HTTPException(422, f"The period must be at most {MAX_SPAN_DAYS} days and end after it starts.")
    hours = services.busy.busy_hours(user_id, start, end)
    return {day.isoformat(): value for day, value in hours.items()}


@router.get(
    "/suggestions", response_model=list[CalendarEventOut], summary="out-of-office entries to turn into absences"
)
def suggestions(user: CalendarUser, services: ServicesDep):
    return services.calendar.suggestions(user)


@router.get(
    "/{source_id}/preview", response_model=list[CalendarEventOut], summary="the next busy blocks of one calendar"
)
def preview(source_id: str, user: CalendarUser, services: ServicesDep, days: Annotated[int, Query(ge=1, le=60)] = 14):
    return services.calendar.preview(user, source_id, days)
