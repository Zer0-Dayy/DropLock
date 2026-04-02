import unittest
from pathlib import Path
from types import SimpleNamespace

from ui_controller import UIController


class SignatureSourceTests(unittest.TestCase):
    def test_pen_down_keeps_existing_strokes(self):
        source = Path("signature_capture.py").read_text()
        self.assertIn('self._canvas.delete("signature_placeholder")', source)
        self.assertNotIn('self._canvas.delete("all")\n\n        self._stats.stroke_count += 1', source)


class UIControllerStateTests(unittest.TestCase):
    def test_explicit_processing_request_state(self):
        ui = UIController(enable_tk=False)
        ui.show_processing_request(token_id="tok-42")

        self.assertEqual(ui._state.name, "processing_request")
        self.assertEqual(ui._state.title, "Processing Request")

    def test_explicit_locker_open_state(self):
        ui = UIController(enable_tk=False)
        ui.show_locker_open(session=SimpleNamespace(locker_id="A7"))

        self.assertEqual(ui._state.name, "locker_open")
        self.assertEqual(ui._state.title, "Locker A7 Open")

    def test_denied_screen_title(self):
        ui = UIController(enable_tk=False)
        ui.show_denied(token_id="tok-99", reason="INVALID")

        self.assertEqual(ui._state.title, "Request Denied")

    def test_operation_cancelled_screen_title(self):
        ui = UIController(enable_tk=False)
        ui.show_operation_cancelled()

        self.assertEqual(ui._state.name, "operation_cancelled")
        self.assertEqual(ui._state.title, "OPERATION CANCELLED")


if __name__ == "__main__":
    unittest.main()
