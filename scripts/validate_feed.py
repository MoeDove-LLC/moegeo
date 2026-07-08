import re
import sys
from collections import OrderedDict
from urllib.parse import urlparse

import requests
import yaml

from geofeed import decode_geofeed_content, parse_geofeed_text


# Country codes that will be excluded from aggregation.
BLOCKED_COUNTRIES = {"KP", "AQ", "IR"}


def check_rdap_email(asn, target_email):
    try:
        asn_num = str(asn).upper().replace("AS", "")
        urls = [
            f"https://rdap.db.ripe.net/autnum/{asn_num}",
            f"https://rdap.arin.net/registry/autnum/{asn_num}",
            f"https://rdap.apnic.net/autnum/{asn_num}",
            f"https://rdap.lacnic.net/rdap/autnum/{asn_num}",
            f"https://rdap.afrinic.net/rdap/autnum/{asn_num}",
        ]

        emails = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for entity in data.get("entities", []):
                        vcard = entity.get("vcardArray", [])
                        if len(vcard) > 1:
                            for item in vcard[1]:
                                if item[0] == "email":
                                    emails.append(item[3].lower())
                    break
            except requests.exceptions.RequestException:
                continue

        if target_email.lower() in emails:
            return True, emails
        return False, emails
    except Exception as exc:
        print(f"RDAP lookup error: {exc}")
        return False, []


def validate_geofeed_url(url):
    if not isinstance(url, str) or not url.strip():
        return None, [f"Invalid Geofeed URL value: {url!r}"]

    url = url.strip()
    parsed_url = urlparse(url)
    if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
        return None, [f"Geofeed URL must be HTTP or HTTPS with a host: {url}"]

    return url, []


def validate_file(filepath, author_email=None, is_signed=False):
    errors = []
    warnings = []
    messages = []

    if not filepath.endswith(".yml"):
        errors.append("File must have .yml extension")
        return errors, warnings, messages

    try:
        with open(filepath, "r", encoding="utf-8") as feed_file:
            data = yaml.safe_load(feed_file)
    except Exception as exc:
        errors.append(f"Invalid YAML: {exc}")
        return errors, warnings, messages

    if not isinstance(data, dict):
        errors.append("YAML must be a dictionary")
        return errors, warnings, messages

    required_keys = ["name", "asn", "contact", "geofeed_urls"]
    for key in required_keys:
        if key not in data:
            errors.append(f"Missing required key: {key}")

    if errors:
        return errors, warnings, messages

    asn = data["asn"]
    if not re.match(r"^AS\d+$", str(asn), re.IGNORECASE):
        errors.append("ASN must be in format 'AS12345'")

    raw_contact_email = data.get("contact", "")
    if isinstance(raw_contact_email, str):
        contact_email = raw_contact_email.strip()
    else:
        contact_email = ""

    if not contact_email:
        errors.append("contact must be a non-empty email string")

    geofeed_urls = data.get("geofeed_urls")
    if not isinstance(geofeed_urls, list):
        errors.append("geofeed_urls must be a list")
    elif not geofeed_urls:
        errors.append("geofeed_urls must contain at least one URL")
    else:
        for raw_url in geofeed_urls:
            url, url_errors = validate_geofeed_url(raw_url)
            errors.extend(url_errors)
            if not url:
                continue

            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    errors.append(f"Geofeed URL {url} returned HTTP {resp.status_code}")
                    continue

                text, decode_errors = decode_geofeed_content(resp.content, url)
                if decode_errors:
                    errors.extend(decode_errors)
                    continue

                entries, parse_errors, parse_warnings = parse_geofeed_text(text, url)
                errors.extend(parse_errors)
                warnings.extend(parse_warnings)

                for entry in entries:
                    country = entry.alpha2code.strip()
                    if country and country.upper() in BLOCKED_COUNTRIES:
                        warnings.append(
                            f"Prefix {entry.prefix} uses blocked country code "
                            f"'{country}', this entry will be excluded from aggregation."
                        )

                if not entries:
                    errors.append(f"Geofeed URL {url} contains no valid prefix entries")

            except Exception as exc:
                errors.append(f"Failed to fetch {url}: {exc}")

    if is_signed and author_email:
        if author_email.lower() != contact_email.lower():
            messages.append("Author email does not match contact email.")
            messages.append("Ownership verification failed; manual review required.")
        else:
            match_found, emails_found = check_rdap_email(asn, author_email)
            if match_found:
                messages.append("Ownership verified via GPG and RDAP.")
            else:
                messages.append(
                    f"Email {author_email} was not found in RDAP for {asn}. "
                    f"Found: {', '.join(emails_found)}"
                )
                messages.append("Ownership verification failed; manual review required.")
    else:
        messages.append(
            "Commit not signed or missing email; skipping automated ownership "
            "verification. Manual review required."
        )

    return errors, warnings, messages


def _error_category(error_msg):
    """Extract a category key from an error message by removing row-specific details."""
    msg = re.sub(r"\s+at valid row \d+", "", error_msg)
    msg = re.sub(r"\s+at line \d+", "", msg)
    msg = re.sub(r"; first seen at line \d+", "", msg)
    msg = re.sub(r"'[^']*'", "'...'", msg)
    msg = re.sub(r"https?://\S+", "<url>", msg)
    msg = re.sub(r"HTTP \d+", "HTTP <N>", msg)
    return msg


def format_errors(errors, max_examples=3):
    """Group similar errors and return formatted output with counts."""
    groups = OrderedDict()
    for error in errors:
        key = _error_category(error)
        if key not in groups:
            groups[key] = []
        groups[key].append(error)

    lines = []
    for group in groups.values():
        shown = group[:max_examples]
        remaining = len(group) - len(shown)
        for error in shown:
            lines.append(f" - {error}")
        if remaining > 0:
            lines.append(f"   ...and {remaining} more similar errors")
    return lines


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_feed.py <file.yml> [author_email] [is_signed_true_false]")
        sys.exit(1)

    filepath = sys.argv[1]
    author_email = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "null" else None
    is_signed_str = sys.argv[3] if len(sys.argv) > 3 else "false"
    is_signed = is_signed_str.lower() == "true"

    errors, warnings, messages = validate_file(filepath, author_email, is_signed)

    for message in messages:
        print(message)

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  {warning}")

    if errors:
        print(f"\nValidation Failed ({len(errors)} errors):")
        for line in format_errors(errors):
            print(line)
        sys.exit(1)

    print("\nFormat Validation Passed.")
    sys.exit(0)
