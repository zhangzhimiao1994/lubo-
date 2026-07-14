import unittest

from douyinliverecorder.core.events import EventBus, RecorderEvent, RecorderEventType


class EventBusTests(unittest.TestCase):
    def test_event_payload_is_defensively_copied(self):
        payload = {"url": "https://example.com/live"}

        event = RecorderEvent(
            type=RecorderEventType.LIVE_DETECTED,
            target_id="abc",
            payload=payload,
        )
        payload["url"] = "https://example.com/changed"

        self.assertEqual(event.payload["url"], "https://example.com/live")

    def test_event_payload_is_immutable(self):
        event = RecorderEvent(
            type=RecorderEventType.LIVE_DETECTED,
            target_id="abc",
            payload={"url": "https://example.com/live"},
        )

        with self.assertRaises(TypeError):
            event.payload["url"] = "https://example.com/changed"

    def test_event_payload_deeply_copies_nested_mutable_values(self):
        payload = {"nested": {"items": ["a"]}}

        event = RecorderEvent(
            type=RecorderEventType.LIVE_DETECTED,
            target_id="abc",
            payload=payload,
        )
        payload["nested"]["items"].append("b")
        payload["nested"]["extra"] = "changed"

        self.assertEqual(event.payload["nested"]["items"], ("a",))
        self.assertNotIn("extra", event.payload["nested"])

    def test_event_payload_nested_list_is_tuple(self):
        event = RecorderEvent(
            type=RecorderEventType.LIVE_DETECTED,
            target_id="abc",
            payload={"items": ["a"]},
        )

        self.assertIsInstance(event.payload["items"], tuple)
        with self.assertRaises(AttributeError):
            event.payload["items"].append("b")

    def test_event_payload_nested_dict_is_immutable(self):
        event = RecorderEvent(
            type=RecorderEventType.LIVE_DETECTED,
            target_id="abc",
            payload={"nested": {"item": "a"}},
        )

        with self.assertRaises(TypeError):
            event.payload["nested"]["item"] = "b"

    def test_publish_sends_event_to_subscribers(self):
        bus = EventBus()
        received = []

        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.TARGET_ADDED, target_id="abc", message="added")
        bus.publish(event)

        self.assertEqual(received, [event])

    def test_duplicate_subscribe_delivers_once(self):
        bus = EventBus()
        received = []

        bus.subscribe(received.append)
        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.TARGET_ADDED, target_id="abc")
        bus.publish(event)

        self.assertEqual(received, [event])

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        received = []

        bus.subscribe(received.append)
        bus.unsubscribe(received.append)
        bus.publish(RecorderEvent(type=RecorderEventType.ERROR, target_id="abc", message="failed"))

        self.assertEqual(received, [])

    def test_subscriber_error_does_not_stop_other_subscribers(self):
        bus = EventBus()
        received = []

        def broken(_event):
            raise RuntimeError("subscriber failed")

        bus.subscribe(broken)
        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.RECORDING_STARTED, target_id="abc")

        with self.assertLogs("douyinliverecorder.core.events", level="ERROR"):
            bus.publish(event)

        self.assertEqual(received, [event])

    def test_subscriber_error_is_logged_and_does_not_stop_other_subscribers(self):
        bus = EventBus()
        received = []

        def broken(_event):
            raise RuntimeError("subscriber failed")

        bus.subscribe(broken)
        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.RECORDING_STARTED, target_id="abc")

        with self.assertLogs("douyinliverecorder.core.events", level="ERROR") as logs:
            bus.publish(event)

        self.assertIn("Recorder event subscriber failed", logs.output[0])
        self.assertEqual(received, [event])


if __name__ == "__main__":
    unittest.main()
