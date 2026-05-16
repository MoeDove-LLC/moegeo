import os
import glob
import yaml
import requests
import ipaddress
import hashlib
from datetime import datetime, timezone

FEEDS_DIR = "feeds"
OUTPUT_FILE = "output/geofeed.csv"

# Country codes that will be excluded from the aggregated output
BLOCKED_COUNTRIES = {"KP", "AQ", "IR"}

def load_previous_state():
    state = {}
    if not os.path.exists(OUTPUT_FILE):
        return state
        
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        current_asn = None
        current_last_mod = None
        current_lines = []
        
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("# ") and " - Last change: " in line:
                if current_asn is not None and current_lines:
                    h = hashlib.sha256("\n".join(current_lines).encode('utf-8')).hexdigest()
                    state[(current_asn, h)] = current_last_mod
                
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
            h = hashlib.sha256("\n".join(current_lines).encode('utf-8')).hexdigest()
            state[(current_asn, h)] = current_last_mod
            
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
            
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
                urls = data.get("geofeed_urls", [])
                asn = data.get("asn", "UNKNOWN")
                name = data.get("name", "UNKNOWN")
            except Exception as e:
                print(f"Error parsing {filepath}: {e}")
                continue
                
        for url in urls:
            try:
                print(f"Fetching {url} for {asn}...")
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                
                lines = resp.text.splitlines()
                count = 0
                valid_lines = []
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(",")
                    if len(parts) >= 2:
                        prefix = parts[0].strip()
                        country = parts[1].strip().upper()
                        if country in BLOCKED_COUNTRIES:
                            print(f"Skipping blocked country {country} for prefix {prefix} in {url}")
                            continue
                        try:
                            net = ipaddress.ip_network(prefix, strict=False)
                            if net not in seen_prefixes:
                                seen_prefixes.add(net)
                                valid_lines.append(line)
                                count += 1
                        except ValueError:
                            print(f"Invalid prefix {prefix} in {url}")
                    elif len(parts) == 1:
                        prefix = parts[0].strip()
                        try:
                            net = ipaddress.ip_network(prefix, strict=False)
                            if net not in seen_prefixes:
                                seen_prefixes.add(net)
                                valid_lines.append(line)
                                count += 1
                        except ValueError:
                            print(f"Invalid prefix {prefix} in {url}")
                            
                if count > 0:
                    h = hashlib.sha256("\n".join(valid_lines).encode('utf-8')).hexdigest()
                    if (asn, h) in prev_state:
                        last_modified = prev_state[(asn, h)]
                    else:
                        last_modified = resp.headers.get("Last-Modified")
                        if not last_modified:
                            last_modified = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
                            
                    block_lines = []
                    block_lines.append(f"\n# {asn} - {name} - Last change: {last_modified}")
                    block_lines.extend(valid_lines)
                    
                    all_blocks.extend(block_lines)
                    total_prefixes += count
            except Exception as e:
                print(f"Error fetching {url}: {e}")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        now_str = datetime.now(timezone.utc).isoformat()
        f.write(f"# Geofeed Community Aggregation by MoeDove LLC\n")
        f.write(f"# Generated at: {now_str}\n")
        f.write(f"# Total prefixes: {total_prefixes}\n")
        for line in all_blocks:
            f.write(f"{line}\n")

if __name__ == "__main__":
    aggregate()
