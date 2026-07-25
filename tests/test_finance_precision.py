import unittest
from decimal import Decimal
from datetime import datetime, timezone, timedelta


class TestFinancePrecision(unittest.TestCase):

    def test_decimal_addition_precision(self):
        val1 = Decimal("100.50")
        val2 = Decimal("200.75")
        self.assertEqual(val1 + val2, Decimal("301.25"))

    def test_date_half_open_interval(self):
        start = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)

        t_july = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        t_august = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)

        self.assertTrue(start <= t_july < end)
        self.assertFalse(start <= t_august < end)


if __name__ == "__main__":
    unittest.main()
