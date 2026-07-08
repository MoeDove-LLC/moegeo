import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from geofeed import parse_geofeed_text  # noqa: E402


class GeofeedParserTests(unittest.TestCase):
    def test_accepts_inline_comments_and_case_insensitive_location_codes(self):
        text = """
# comment
8.8.8.0/24,us,us-ca,Mountain View, # trailing comment
"""
        entries, errors, warnings = parse_geofeed_text(text)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].alpha2code, "us")
        self.assertEqual(entries[0].region, "us-ca")

    def test_rejects_prefixes_with_host_bits(self):
        text = "8.8.8.1/24,US,,,\n"
        entries, errors, _warnings = parse_geofeed_text(text)

        self.assertEqual(entries, [])
        self.assertTrue(any("Invalid IP prefix" in error for error in errors))

    def test_rejects_bogon_space_and_overlapping_supernets(self):
        text = """
10.0.0.0/8,US,,,
100.64.0.0/10,US,,,
127.0.0.1,US,,,
172.28.30.1,US,,,
192.168.1.0/24,US,,,
198.51.100.0/24,US,,,
0.0.0.0/0,US,,,
172.15.30.1,US,US-CA,,
"""
        entries, errors, _warnings = parse_geofeed_text(text)

        self.assertEqual([entry.prefix for entry in entries], ["172.15.30.1"])
        self.assertEqual(
            sum("Bogon IPv4 address space" in error for error in errors), 7
        )

    def test_duplicate_networks_are_errors(self):
        text = """
8.8.8.8,US,,,
8.8.8.8/32,US,,,
"""
        entries, errors, _warnings = parse_geofeed_text(text)

        self.assertEqual(len(entries), 1)
        self.assertTrue(any("Duplicate IP prefix" in error for error in errors))

    def test_extra_fields_warn_and_are_ignored(self):
        text = "8.8.8.0/24,US,,,ignored,extra\n"
        entries, errors, warnings = parse_geofeed_text(text)

        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].to_csv_line(), "8.8.8.0/24,US,,,ignored")
        self.assertTrue(any("Expected 5 RFC 8805 fields" in warning for warning in warnings))

    def test_rejects_invented_country_and_region_codes(self):
        text = """
8.8.4.0/24,OO,,,
1.1.1.0/24,US,US-LOL,,
9.9.9.0/24,EU,,,
9.9.8.0/24,UK,,,
9.9.7.0/24,XK,,,
"""
        entries, errors, _warnings = parse_geofeed_text(text)

        self.assertEqual(entries, [])
        self.assertTrue(any("Invalid alpha2code 'OO'" in error for error in errors))
        self.assertTrue(any("Invalid alpha2code 'EU'" in error for error in errors))
        self.assertTrue(any("Invalid alpha2code 'UK'" in error for error in errors))
        self.assertTrue(any("Invalid alpha2code 'XK'" in error for error in errors))
        self.assertTrue(any("Invalid region code 'US-LOL'" in error for error in errors))

    def test_accepts_rfc8805_zz_no_geolocation_marker(self):
        entries, errors, warnings = parse_geofeed_text("8.8.4.4,ZZ,,,\n")

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
