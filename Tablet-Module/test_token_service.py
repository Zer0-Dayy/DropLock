import sys
import types
import unittest

sys.modules.setdefault("requests", types.SimpleNamespace())

from token_service import TokenService


class FakeRepo:
    def __init__(self, booking):
        self.booking = booking
        self.saved = {}
        self.status = None

    def get_booking(self, booking_id):
        return self.booking

    def put_json(self, path, data):
        self.saved[path] = data

    def update_booking_status(self, *, booking_id, status, updated_at_ms=None):
        self.status = status


class TokenServiceTests(unittest.TestCase):
    def test_issue_user_pickup_token(self):
        repo = FakeRepo({"status": "OCCUPIED", "sectorId": "S1", "lockerId": "L1", "userId": "U1"})
        svc = TokenService(repo, courier_ttl_sec=100, pickup_ttl_sec=200)
        issued = svc.issue_user_pickup_token("B1")
        self.assertEqual(issued.purpose, "USER_PICKUP")
        self.assertEqual(repo.status, "PICKUP_PENDING")
        self.assertTrue(any(k.startswith("qrTokens/upk_") for k in repo.saved.keys()))


if __name__ == "__main__":
    unittest.main()
