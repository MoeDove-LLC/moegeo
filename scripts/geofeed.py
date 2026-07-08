import csv
import io
import ipaddress
from dataclasses import dataclass

import pycountry


EXPECTED_FIELD_COUNT = 5

BOGON_PREFIXES_V4 = (
    (ipaddress.ip_network("0.0.0.0/8"), "RFC 1122 'this' network"),
    (ipaddress.ip_network("10.0.0.0/8"), "RFC 1918 private space"),
    (ipaddress.ip_network("100.64.0.0/10"), "RFC 6598 carrier-grade NAT space"),
    (ipaddress.ip_network("127.0.0.0/8"), "RFC 1122 localhost"),
    (ipaddress.ip_network("169.254.0.0/16"), "RFC 3927 link-local"),
    (ipaddress.ip_network("172.16.0.0/12"), "RFC 1918 private space"),
    (ipaddress.ip_network("192.0.2.0/24"), "RFC 5737 TEST-NET-1"),
    (ipaddress.ip_network("192.168.0.0/16"), "RFC 1918 private space"),
    (ipaddress.ip_network("198.18.0.0/15"), "RFC 2544 benchmarking"),
    (ipaddress.ip_network("198.51.100.0/24"), "RFC 5737 TEST-NET-2"),
    (ipaddress.ip_network("203.0.113.0/24"), "RFC 5737 TEST-NET-3"),
    (ipaddress.ip_network("224.0.0.0/4"), "multicast"),
    (ipaddress.ip_network("240.0.0.0/4"), "reserved"),
)

ADDITIONAL_ALPHA2_CODES = (
    # ISO 3166 exceptionally reserved / transitional codes commonly seen online.
    "AC",
    "CP",
    "DG",
    "EA",
    "EU",
    "EZ",
    "FX",
    "IC",
    "SU",
    "TA",
    "UK",
    "UN",
    "XK",
    # RFC 8805 section 2.1.2 notes this historical no-geolocation marker.
    "ZZ",
)


@dataclass(frozen=True)
class GeofeedEntry:
    line_number: int
    prefix: str
    network: object
    alpha2code: str
    region: str
    city: str
    postal_code: str

    def to_csv_line(self):
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="")
        writer.writerow(
            [self.prefix, self.alpha2code, self.region, self.city, self.postal_code]
        )
        return output.getvalue()


def decode_geofeed_content(content, source):
    try:
        return content.decode("utf-8-sig"), []
    except UnicodeDecodeError as exc:
        return "", [f"Geofeed {source} is not valid UTF-8: {exc}"]


def find_bogon_prefix(network):
    if network.version != 4:
        return None

    for bogon_prefix, reason in BOGON_PREFIXES_V4:
        if network.overlaps(bogon_prefix):
            return bogon_prefix, reason

    return None


def is_valid_alpha2code(alpha2code):
    normalized = alpha2code.upper()
    if normalized in ADDITIONAL_ALPHA2_CODES:
        return True
    return pycountry.countries.get(alpha_2=normalized) is not None


def is_valid_region_code(region):
    return pycountry.subdivisions.get(code=region.upper()) is not None


def parse_ip_prefix(prefix):
    if "%" in prefix:
        raise ValueError("IPv6 zone identifiers are not valid in geofeed prefixes")

    if "/" in prefix:
        return ipaddress.ip_network(prefix, strict=True)

    address = ipaddress.ip_address(prefix)
    return ipaddress.ip_network(f"{address}/{address.max_prefixlen}", strict=True)


def strip_comment(raw_line):
    return raw_line.rstrip("\r\n").split("#", 1)[0].strip()


def parse_geofeed_text(text, source="geofeed"):
    entries = []
    errors = []
    warnings = []
    seen_networks = {}

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = strip_comment(raw_line)
        if not line:
            continue

        try:
            fields = next(csv.reader([line], strict=True))
        except csv.Error as exc:
            errors.append(f"Invalid CSV in {source} at line {line_number}: {exc}")
            continue

        if len(fields) != EXPECTED_FIELD_COUNT:
            warnings.append(
                f"Expected 5 RFC 8805 fields in {source} at line {line_number}, "
                f"got {len(fields)}; extra fields will be ignored and missing "
                "optional fields are treated as empty."
            )

        fields = [field.strip() for field in fields[:EXPECTED_FIELD_COUNT]]
        fields.extend([""] * (EXPECTED_FIELD_COUNT - len(fields)))
        prefix, alpha2code, region, city, postal_code = fields

        line_has_error = False
        if not prefix:
            errors.append(f"Missing IP prefix in {source} at line {line_number}")
            line_has_error = True
            network = None
        else:
            try:
                network = parse_ip_prefix(prefix)
            except ValueError as exc:
                errors.append(
                    f"Invalid IP prefix '{prefix}' in {source} at line "
                    f"{line_number}: {exc}"
                )
                line_has_error = True
                network = None

        if network is not None:
            if network in seen_networks:
                errors.append(
                    f"Duplicate IP prefix '{prefix}' in {source} at line "
                    f"{line_number}; first seen at line {seen_networks[network]}"
                )
                line_has_error = True
            else:
                seen_networks[network] = line_number

            bogon_match = find_bogon_prefix(network)
            if bogon_match:
                bogon_prefix, bogon_reason = bogon_match
                errors.append(
                    f"Bogon IPv4 address space is not allowed in geofeeds: "
                    f"'{prefix}' in {source} at line {line_number} overlaps "
                    f"{bogon_prefix} ({bogon_reason})"
                )
                line_has_error = True

        if alpha2code and not is_valid_alpha2code(alpha2code):
            errors.append(
                f"Invalid alpha2code '{alpha2code}' in {source} at line "
                f"{line_number}; expected a real ISO 3166-1 alpha-2 code"
            )
            line_has_error = True

        if region:
            if not is_valid_region_code(region):
                errors.append(
                    f"Invalid region code '{region}' in {source} at line "
                    f"{line_number}; expected a real ISO 3166-2 subdivision code"
                )
                line_has_error = True
            elif alpha2code and region.split("-", 1)[0].upper() != alpha2code.upper():
                errors.append(
                    f"Region code '{region}' does not match alpha2code "
                    f"'{alpha2code}' in {source} at line {line_number}"
                )
                line_has_error = True

        if "," in city:
            errors.append(
                f"City field must not contain commas in {source} at line {line_number}"
            )
            line_has_error = True

        if "," in postal_code:
            errors.append(
                f"Postal code field must not contain commas in {source} at line "
                f"{line_number}"
            )
            line_has_error = True

        if not line_has_error:
            entries.append(
                GeofeedEntry(
                    line_number=line_number,
                    prefix=prefix,
                    network=network,
                    alpha2code=alpha2code,
                    region=region,
                    city=city,
                    postal_code=postal_code,
                )
            )

    return entries, errors, warnings
