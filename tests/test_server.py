import asyncio
import json
import unittest
from datetime import datetime, timedelta
from threading import get_ident
from unittest.mock import AsyncMock, Mock, patch

from calendar_safe_mcp import server


class ServerTests(unittest.TestCase):
    def test_compact_serialization_does_not_read_private_details(self):
        event = Mock()
        event.occurrenceDate.return_value = None
        event.eventIdentifier.return_value = "event-id"
        event.startDate.return_value.timeIntervalSince1970.return_value = 1_767_268_800
        event.endDate.return_value.timeIntervalSince1970.return_value = 1_767_272_400
        event.title.return_value = "Planning"
        event.calendar.return_value.title.return_value = "Work"
        event.isAllDay.return_value = False
        event.location.return_value = None
        event.hasRecurrenceRules.return_value = False

        result = server._serialize_event(event)

        self.assertNotIn("notes", result)
        self.assertNotIn("url", result)
        self.assertNotIn("alarms_minutes_before", result)
        event.notes.assert_not_called()
        event.URL.assert_not_called()
        event.alarms.assert_not_called()

    def test_event_range_is_capped_before_eventkit_query(self):
        manager = object.__new__(server.CalendarManager)
        manager.store = Mock()
        start = datetime(2026, 1, 1)

        with self.assertRaisesRegex(ValueError, "366 days"):
            manager.events(start, start + timedelta(days=367), None)

        manager.store.predicateForEventsWithStartDate_endDate_calendars_.assert_not_called()

    def test_default_query_does_not_read_notes(self):
        event = Mock()
        event.title.return_value = "Planning"

        with patch.object(
            server, "_get_manager", return_value=Mock(events=Mock(return_value=[event]))
        ):
            result = json.loads(
                asyncio.run(
                    server.list_events(
                        datetime(2026, 1, 1),
                        datetime(2026, 1, 2),
                        query="missing",
                    )
                )
            )

        self.assertEqual(result["total"], 0)
        event.notes.assert_not_called()

    def test_note_search_is_explicit(self):
        event = Mock()
        event.title.return_value = "Planning"
        event.notes.return_value = "Project lighthouse"
        event.startDate.return_value.timeIntervalSince1970.return_value = 1_767_268_800
        event.endDate.return_value.timeIntervalSince1970.return_value = 1_767_272_400
        event.occurrenceDate.return_value = None
        event.eventIdentifier.return_value = "event-id"
        event.calendar.return_value.title.return_value = "Work"
        event.isAllDay.return_value = False
        event.location.return_value = None
        event.hasRecurrenceRules.return_value = False

        with patch.object(
            server, "_get_manager", return_value=Mock(events=Mock(return_value=[event]))
        ):
            result = json.loads(
                asyncio.run(
                    server.list_events(
                        datetime(2026, 1, 1),
                        datetime(2026, 1, 2),
                        query="lighthouse",
                        search_notes=True,
                    )
                )
            )

        self.assertEqual(result["total"], 1)
        event.notes.assert_called_once()

    def test_limit_is_validated_before_calendar_access(self):
        with patch.object(server, "_eventkit", new=AsyncMock()) as eventkit:
            with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
                asyncio.run(server.list_events(datetime(2026, 1, 1), datetime(2026, 1, 2), limit=0))
        eventkit.assert_not_awaited()

    def test_invalid_ranges_are_rejected_before_calendar_access(self):
        start = datetime(2026, 1, 1)
        for end in (start, start + timedelta(days=367)):
            with self.subTest(end=end):
                with patch.object(server, "_get_manager") as get_manager:
                    with self.assertRaises(ValueError):
                        asyncio.run(server.list_events(start, end))
                get_manager.assert_not_called()

    def test_negative_alarms_are_rejected_before_calendar_access(self):
        with patch.object(server, "_get_manager") as get_manager:
            with self.assertRaisesRegex(ValueError, "alarm offsets"):
                asyncio.run(
                    server.create_event(
                        "Planning",
                        datetime(2026, 1, 1),
                        datetime(2026, 1, 2),
                        alarms_minutes_before=[-1],
                    )
                )
        get_manager.assert_not_called()

    def test_failed_update_does_not_mutate_event(self):
        event = Mock()
        event.hasRecurrenceRules.return_value = False
        event.startDate.return_value.timeIntervalSince1970.return_value = datetime(
            2026, 1, 2
        ).timestamp()
        event.endDate.return_value.timeIntervalSince1970.return_value = datetime(
            2026, 1, 3
        ).timestamp()
        event.setEndDate_.side_effect = lambda value: setattr(
            event.endDate.return_value.timeIntervalSince1970, "return_value", value.timestamp()
        )
        manager = Mock()
        manager.event.return_value = event
        with patch.object(server, "_get_manager", return_value=manager):
            with self.assertRaisesRegex(ValueError, "end must be after start"):
                asyncio.run(
                    server.update_event("event-id", title="Changed", end=datetime(2026, 1, 1))
                )
        event.setTitle_.assert_not_called()
        event.setEndDate_.assert_not_called()
        manager.store.saveEvent_span_error_.assert_not_called()

    def test_invalid_update_alarms_do_not_mutate_event(self):
        event = Mock()
        event.hasRecurrenceRules.return_value = False
        event.startDate.return_value.timeIntervalSince1970.return_value = 1
        event.endDate.return_value.timeIntervalSince1970.return_value = 2
        manager = Mock()
        manager.event.return_value = event
        with patch.object(server, "_get_manager", return_value=manager):
            with self.assertRaisesRegex(ValueError, "alarm offsets"):
                asyncio.run(
                    server.update_event("event-id", title="Changed", alarms_minutes_before=[-1])
                )
        event.setTitle_.assert_not_called()
        manager.store.saveEvent_span_error_.assert_not_called()

    def test_event_serialization_stays_on_eventkit_thread(self):
        caller_thread = get_ident()
        threads = []
        event = Mock()
        event.title.side_effect = lambda: threads.append(get_ident()) or "Planning"
        event.occurrenceDate.return_value = None
        event.eventIdentifier.return_value = "event-id"
        event.startDate.return_value.timeIntervalSince1970.return_value = 1
        event.endDate.return_value.timeIntervalSince1970.return_value = 2
        event.calendar.return_value.title.return_value = "Work"
        event.isAllDay.return_value = False
        event.location.return_value = None
        event.hasRecurrenceRules.return_value = False
        manager = Mock()
        manager.events.side_effect = lambda *args: threads.append(get_ident()) or [event]
        with patch.object(server, "_get_manager", return_value=manager):
            result = json.loads(
                asyncio.run(server.list_events(datetime(2026, 1, 1), datetime(2026, 1, 2)))
            )
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], caller_thread)


if __name__ == "__main__":
    unittest.main()
