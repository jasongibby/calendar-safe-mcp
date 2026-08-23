"""Lean Apple Calendar MCP server using only Apple's EventKit framework."""

from __future__ import annotations

import asyncio
import json
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Semaphore
from typing import Any, Literal

from EventKit import (
    EKAlarm,
    EKAuthorizationStatusDenied,
    EKAuthorizationStatusFullAccess,
    EKAuthorizationStatusRestricted,
    EKEntityTypeEvent,
    EKEvent,
    EKEventStore,
    EKSpanFutureEvents,
    EKSpanThisEvent,
)

warnings.filterwarnings("ignore", message="Field 'lifespan' has an incomplete definition.*")

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Apple Calendar (safe)")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eventkit")
_manager: CalendarManager | None = None
MAX_EVENT_RANGE = timedelta(days=366)


def _iso(value: Any) -> str:
    return datetime.fromtimestamp(value.timeIntervalSince1970()).astimezone().isoformat(timespec="seconds")


def _native_date(value: datetime) -> datetime:
    """Convert an aware timestamp to the local wall time PyObjC expects."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def _occurrence_id(event: Any) -> str:
    occurrence = event.occurrenceDate() or event.startDate()
    return f"{event.eventIdentifier()}::{_iso(occurrence)}"


def _serialize_event(event: Any, include_details: bool = False) -> dict[str, Any]:
    result = {
        "id": _occurrence_id(event),
        "title": event.title() or "",
        "start": _iso(event.startDate()),
        "end": _iso(event.endDate()),
        "calendar": event.calendar().title(),
        "all_day": bool(event.isAllDay()),
        "location": event.location() or None,
        "recurring": bool(event.hasRecurrenceRules()),
    }
    if include_details:
        event_url = event.URL()
        result.update(
            notes=event.notes() or None,
            url=str(event_url) if event_url else None,
            alarms_minutes_before=[int(-alarm.relativeOffset() / 60) for alarm in (event.alarms() or [])],
        )
    return result


class CalendarManager:
    def __init__(self) -> None:
        self.store = EKEventStore.alloc().init()
        self._ensure_access()

    def _ensure_access(self) -> None:
        status = EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent)
        if status == EKAuthorizationStatusFullAccess:
            return
        if status == EKAuthorizationStatusRestricted:
            raise PermissionError("Calendar access is restricted by macOS policy")
        if status == EKAuthorizationStatusDenied:
            raise PermissionError("Calendar access was denied; enable Full Access in System Settings")

        semaphore = Semaphore(0)
        outcome: dict[str, Any] = {"granted": False, "error": None}

        def completed(granted: bool, error: Any) -> None:
            outcome.update(granted=granted, error=error)
            semaphore.release()

        self.store.requestFullAccessToEventsWithCompletion_(completed)
        if not semaphore.acquire(timeout=30):
            raise TimeoutError("Calendar permission request timed out")
        if not outcome["granted"]:
            detail = f": {outcome['error']}" if outcome["error"] else ""
            raise PermissionError(f"Calendar Full Access was not granted{detail}")

    def calendars(self) -> list[Any]:
        return list(self.store.calendarsForEntityType_(EKEntityTypeEvent))

    def calendar(self, name: str | None) -> Any:
        if name is None:
            calendar = self.store.defaultCalendarForNewEvents()
            if calendar is None:
                raise ValueError("No default writable calendar is configured")
            return calendar
        matches = [calendar for calendar in self.calendars() if calendar.title() == name]
        if not matches:
            raise ValueError(f"Calendar does not exist: {name}")
        if len(matches) > 1:
            raise ValueError(f"Calendar name is ambiguous: {name}")
        return matches[0]

    def events(self, start: datetime, end: datetime, calendar_names: list[str] | None) -> list[Any]:
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > MAX_EVENT_RANGE:
            raise ValueError("event ranges cannot exceed 366 days; request a smaller window")
        calendars = [self.calendar(name) for name in calendar_names] if calendar_names else None
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(
            _native_date(start), _native_date(end), calendars
        )
        return sorted(self.store.eventsMatchingPredicate_(predicate), key=lambda event: event.startDate())

    def event(self, occurrence_id: str) -> Any:
        try:
            identifier, occurrence_text = occurrence_id.rsplit("::", 1)
            occurrence = datetime.fromisoformat(occurrence_text)
        except (ValueError, TypeError) as error:
            raise ValueError("Use the occurrence ID returned by list_events") from error

        for event in self.events(occurrence - timedelta(seconds=1), occurrence + timedelta(seconds=1), None):
            event_occurrence = event.occurrenceDate() or event.startDate()
            if event.eventIdentifier() == identifier and abs(
                event_occurrence.timeIntervalSince1970() - occurrence.timestamp()
            ) < 1:
                return event
        raise ValueError("Calendar event was not found; refresh it with list_events")


def _get_manager() -> CalendarManager:
    global _manager
    if _manager is None:
        _manager = CalendarManager()
    return _manager


async def _eventkit(method: str, *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: getattr(_get_manager(), method)(*args))


@mcp.tool()
async def list_calendars() -> str:
    """List calendars and whether each permits event changes."""
    calendars = await _eventkit("calendars")
    result = [
        {
            "name": calendar.title(),
            "id": calendar.calendarIdentifier(),
            "writable": bool(calendar.allowsContentModifications()),
        }
        for calendar in sorted(calendars, key=lambda item: item.title().casefold())
    ]
    return json.dumps({"calendars": result}, ensure_ascii=False)


@mcp.tool()
async def list_events(
    start: datetime,
    end: datetime,
    calendar_names: list[str] | None = None,
    query: str | None = None,
    search_notes: bool = False,
    include_details: bool = False,
    limit: int = 200,
) -> str:
    """Read events in a range. Details are excluded unless explicitly requested."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    events = await _eventkit("events", start, end, calendar_names)
    if query:
        needle = query.casefold()
        events = [
            event
            for event in events
            if needle in (event.title() or "").casefold()
            or (search_notes and needle in (event.notes() or "").casefold())
        ]
    total = len(events)
    return json.dumps(
        {
            "events": [_serialize_event(event, include_details) for event in events[:limit]],
            "total": total,
            "truncated": total > limit,
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def create_event(
    title: str,
    start: datetime,
    end: datetime,
    calendar_name: str | None = None,
    all_day: bool = False,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    alarms_minutes_before: list[int] | None = None,
) -> str:
    """Create one non-recurring event through EventKit."""
    if not title.strip():
        raise ValueError("title must not be empty")
    if end <= start:
        raise ValueError("end must be after start")

    def create(manager: CalendarManager) -> Any:
        calendar = manager.calendar(calendar_name)
        if not calendar.allowsContentModifications():
            raise ValueError(f"Calendar is read-only: {calendar.title()}")
        event = EKEvent.eventWithEventStore_(manager.store)
        event.setTitle_(title)
        event.setStartDate_(_native_date(start))
        event.setEndDate_(_native_date(end))
        event.setCalendar_(calendar)
        event.setAllDay_(all_day)
        if location is not None:
            event.setLocation_(location)
        if notes is not None:
            event.setNotes_(notes)
        if url is not None:
            from Foundation import NSURL

            event.setURL_(NSURL.URLWithString_(url))
        for minutes in alarms_minutes_before or []:
            if minutes < 0:
                raise ValueError("alarm offsets must be non-negative")
            event.addAlarm_(EKAlarm.alarmWithRelativeOffset_(-60 * minutes))
        success, error = manager.store.saveEvent_span_error_(event, EKSpanThisEvent, None)
        if not success:
            raise RuntimeError(f"EventKit could not save the event: {error}")
        return event

    loop = asyncio.get_running_loop()
    event = await loop.run_in_executor(_executor, lambda: create(_get_manager()))
    return json.dumps({"event": _serialize_event(event)}, ensure_ascii=False)


@mcp.tool()
async def update_event(
    id: str,
    title: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    calendar_name: str | None = None,
    all_day: bool | None = None,
    location: str | None = None,
    notes: str | None = None,
    url: str | None = None,
    alarms_minutes_before: list[int] | None = None,
    span: Literal["this", "future"] | None = None,
) -> str:
    """Update an occurrence. Recurring events require span=this or span=future."""

    def update(manager: CalendarManager) -> Any:
        event = manager.event(id)
        recurring = bool(event.hasRecurrenceRules())
        if recurring and span is None:
            raise ValueError("Recurring events require span='this' or span='future'; no default is safe")
        if title is not None:
            if not title.strip():
                raise ValueError("title must not be empty")
            event.setTitle_(title)
        if start is not None:
            event.setStartDate_(_native_date(start))
        if end is not None:
            event.setEndDate_(_native_date(end))
        if event.endDate().timeIntervalSince1970() <= event.startDate().timeIntervalSince1970():
            raise ValueError("end must be after start")
        if calendar_name is not None:
            calendar = manager.calendar(calendar_name)
            if not calendar.allowsContentModifications():
                raise ValueError(f"Calendar is read-only: {calendar.title()}")
            event.setCalendar_(calendar)
        if all_day is not None:
            event.setAllDay_(all_day)
        if location is not None:
            event.setLocation_(location)
        if notes is not None:
            event.setNotes_(notes)
        if url is not None:
            from Foundation import NSURL

            event.setURL_(NSURL.URLWithString_(url) if url else None)
        if alarms_minutes_before is not None:
            if any(minutes < 0 for minutes in alarms_minutes_before):
                raise ValueError("alarm offsets must be non-negative")
            event.setAlarms_([EKAlarm.alarmWithRelativeOffset_(-60 * minutes) for minutes in alarms_minutes_before])
        event_span = EKSpanFutureEvents if span == "future" else EKSpanThisEvent
        success, error = manager.store.saveEvent_span_error_(event, event_span, None)
        if not success:
            raise RuntimeError(f"EventKit could not update the event: {error}")
        return event

    loop = asyncio.get_running_loop()
    event = await loop.run_in_executor(_executor, lambda: update(_get_manager()))
    return json.dumps({"event": _serialize_event(event)}, ensure_ascii=False)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
