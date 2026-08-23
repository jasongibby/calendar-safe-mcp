import asyncio
import json
import unittest
from datetime import datetime, timedelta
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

        with patch.object(server, "_eventkit", new=AsyncMock(return_value=[event])):
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

        with patch.object(server, "_eventkit", new=AsyncMock(return_value=[event])):
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


if __name__ == "__main__":
    unittest.main()
