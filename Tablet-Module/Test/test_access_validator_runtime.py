import sys
import types
import unittest
from types import SimpleNamespace

sys.modules.setdefault("requests", types.SimpleNamespace())

from access_validator import validate_qr_token


class FakeRepo:
    def get_qr_token(self, token_id):
        return {
            "bookingId": "B1",
            "purpose": "COURIER_DROP",
            "sectorId": "S1",
            "lockerId": "L1",
            "expiresAt": 9999999999999,
            "usedAt": None,
        }

    def get_booking(self, booking_id):
        return {"bookingId": "B1", "sectorId": "S1", "lockerId": "L1", "status": "DROP_PENDING"}

    def get_locker(self, sector_id, locker_id):
        return {"state": "RESERVED"}


class AccessValidatorRuntimeTests(unittest.TestCase):
    def test_validator_allows_valid_token(self):
        ctx = SimpleNamespace(sector_id="S1")
        result = validate_qr_token("TOK", ctx, FakeRepo())
        self.assertTrue(result.allowed)


if __name__ == "__main__":
    unittest.main()
