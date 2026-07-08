import glob
import hashlib
import os
from datetime import datetime, timezone

import requests
import yaml

from geofeed import decode_geofeed_content, parse_geofeed_text


FEEDS_DIR = "feeds"
OUTPUT_FILE = "output/geofeed.csv"

# Country codes that will be excluded from the aggregated output.
BLOCKED_COUNTRIES = {"KP", "AQ", "IR"}


def load_previous_state():
    state = {}
    if not os.path.exists(OUTPUT_FILE):
        return state

    with open(OUTPUT_FILE, "r", encoding="utf-8") as output_file:
        current_asn = None
        current_last_mod = None
        current_lines = []

        for line in output_file:
            line = line.strip()
            if not line:
                continue

            if line.startswith("# ") and " - Last change: " in line:
                if current_asn is not None and current_lines:
                    content_hash = hashlib.sha256(
                        "\n".join(current_lines).encode("utf-8")
                    ).hexdigest()
                    state[(current_asn, content_hash)] = current_last_mod

                header = line[2:]
                parts = header.split(" - Last change: ")
                if len(parts) == 2:
                    current_last_mod = parts[1].strip()
                    asn_name = parts[0]
                    current_asn = asn_name.split(" - ")[0].strip()
                else:
                    current_asn = None
                current_lines = []
            elif line and not line.startswith("#"):
                current_lines.append(line)

        if current_asn is not None and current_lines:
            content_hash = hashlib.sha256(
                "\n".join(current_lines).encode("utf-8")
            ).hexdigest()
            state[(current_asn, content_hash)] = current_last_mod

    return state


def aggregate():
    all_blocks = []
    total_prefixes = 0
    seen_prefixes = set()
    prev_state = load_previous_state()

    files = sorted(glob.glob(os.path.join(FEEDS_DIR, "*.yml")))
    for filepath in files:
        if os.path.basename(filepath) == "example.yml":
            continue

        with open(filepath, "r", encoding="utf-8") as feed_file:
            try:
                data = yaml.safe_load(feed_file)
                urls = data.get("geofeed_urls", [])
                asn = data.get("asn", "UNKNOWN")
                name = data.get("name", "UNKNOWN")
            except Exception as exc:
                print(f"Error parsing {filepath}: {exc}")
                continue

        for url in urls:
            try:
                print(f"Fetching {url} for {asn}...")
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()

                text, decode_errors = decode_geofeed_content(resp.content, url)
                if decode_errors:
                    for error in decode_errors:
                        print(error)
                    continue

                entries, parse_errors, parse_warnings = parse_geofeed_text(text, url)
                for warning in parse_warnings:
                    print(f"Warning: {warning}")
                for error in parse_errors:
                    print(f"Invalid geofeed entry skipped: {error}")

                count = 0
                valid_lines = []
                for entry in entries:
                    country = entry.alpha2code.strip().upper()
                    if country in BLOCKED_COUNTRIES:
                        print(
                            f"Skipping blocked country {country} for prefix "
                            f"{entry.prefix} in {url}"
                        )
                        continue

                    if entry.network not in seen_prefixes:
                        seen_prefixes.add(entry.network)
                        valid_lines.append(entry.to_csv_line())
                        count += 1

                if count > 0:
                    content_hash = hashlib.sha256(
                        "\n".join(valid_lines).encode("utf-8")
                    ).hexdigest()
                    if (asn, content_hash) in prev_state:
                        last_modified = prev_state[(asn, content_hash)]
                    else:
                        last_modified = resp.headers.get("Last-Modified")
                        if not last_modified:
                            last_modified = datetime.now(timezone.utc).strftime(
                                "%a, %d %b %Y %H:%M:%S GMT"
                            )

                    block_lines = []
                    block_lines.append(f"\n# {asn} - {name} - Last change: {last_modified}")
                    block_lines.extend(valid_lines)

                    all_blocks.extend(block_lines)
                    total_prefixes += count
            except Exception as exc:
                print(f"Error fetching {url}: {exc}")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as output_file:
        now_str = datetime.now(timezone.utc).isoformat()
        output_file.write("# Geofeed Community Aggregation by MoeDove LLC\n")
        output_file.write(f"# Generated at: {now_str}\n")
        output_file.write(f"# Total prefixes: {total_prefixes}\n")
        for line in all_blocks:
            output_file.write(f"{line}\n")


if __name__ == "__main__":
    aggregate()
