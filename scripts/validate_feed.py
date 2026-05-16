import os
import sys
import yaml
import requests
import ipaddress
import re
import csv
from collections import OrderedDict

# Country codes that will be excluded from aggregation
BLOCKED_COUNTRIES = {"KP", "AQ", "IR"}

def check_rdap_email(asn, target_email):
    try:
        asn_num = str(asn).upper().replace("AS", "")
        # Query each RIR's RDAP server directly (covers all ASNs globally)
        urls = [
            f"https://rdap.db.ripe.net/autnum/{asn_num}",
            f"https://rdap.arin.net/registry/autnum/{asn_num}",
            f"https://rdap.apnic.net/autnum/{asn_num}",
            f"https://rdap.lacnic.net/rdap/autnum/{asn_num}",
            f"https://rdap.afrinic.net/rdap/autnum/{asn_num}"
        ]
        
        emails = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for entity in data.get('entities', []):
                        vcard = entity.get('vcardArray', [])
                        if len(vcard) > 1:
                            for item in vcard[1]:
                                if item[0] == 'email':
                                    emails.append(item[3].lower())
                    break  # Found the registry, stop querying others
            except requests.exceptions.RequestException:
                continue  # This RIR timed out or errored, try the next one
                                
        if target_email.lower() in emails:
            return True, emails
        return False, emails
    except Exception as e:
        print(f"RDAP lookup error: {e}")
        return False, []

def validate_file(filepath, author_email=None, is_signed=False):
    errors = []
    warnings = []
    messages = []
    
    if not filepath.endswith(".yml"):
        errors.append("文件必须是 .yml 扩展名 / File must have .yml extension")
        return errors, messages
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        errors.append(f"无效的 YAML 格式 / Invalid YAML: {e}")
        return errors, messages
            
    if not isinstance(data, dict):
        errors.append("YAML 必须是一个字典 / YAML must be a dictionary")
        return errors, messages
        
    required_keys = ["name", "asn", "contact", "geofeed_urls"]
    for k in required_keys:
        if k not in data:
            errors.append(f"缺少必需的字段 / Missing required key: {k}")
            
    if errors:
        return errors, messages
        
    asn = data["asn"]
    if not re.match(r"^AS\d+$", str(asn), re.IGNORECASE):
        errors.append("ASN 格式必须为 'AS12345' / ASN must be in format 'AS12345'")
        
    if not isinstance(data.get("geofeed_urls"), list):
        errors.append("geofeed_urls 必须是一个列表 / geofeed_urls must be a list")
    else:
        for url in data["geofeed_urls"]:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    errors.append(f"Geofeed URL {url} 返回 HTTP {resp.status_code} / Returned HTTP {resp.status_code}")
                    continue
                    
                lines = resp.text.splitlines()
                # 过滤注释和空行
                csv_lines = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
                
                has_valid_entry = False
                reader = csv.reader(csv_lines)
                for row_num, parts in enumerate(reader, 1):
                    if len(parts) != 5:
                        errors.append(f"CSV 格式错误 (必须为5列) / Invalid CSV format (must be 5 columns) in {url} at valid row {row_num}")
                        continue
                        
                    prefix = parts[0].strip()
                    try:
                        ipaddress.ip_network(prefix, strict=False)
                        has_valid_entry = True
                    except ValueError:
                        errors.append(f"无效的 IP 前缀 / Invalid IP prefix '{prefix}' in {url} at valid row {row_num}")

                    country = parts[1].strip()
                    if country and country.upper() in BLOCKED_COUNTRIES:
                        warnings.append(
                            f"⚠️ 前缀 {prefix} 使用了受限国家代码 '{country}'，该条目将不会被汇总到聚合输出中。"
                            f" / Prefix {prefix} uses blocked country code '{country}', this entry will be excluded from aggregation."
                        )
                    if country and not re.match(r"^[A-Z]{2}$", country):
                        errors.append(f"无效的国家代码 (应为2位全大写英文字母) / Invalid country code '{country}' in {url} at valid row {row_num}")
                        
                    region = parts[2].strip()
                    if region and not re.match(r"^[A-Z]{2}-[A-Z0-9]{1,3}$", region):
                        errors.append(f"无效的地区代码 / Invalid region code '{region}' in {url} at valid row {row_num}")
                        
                if not has_valid_entry:
                    errors.append(f"Geofeed URL {url} 未包含任何有效的前缀条目 / Contains no valid prefix entries")
                    
            except Exception as e:
                errors.append(f"无法获取 URL / Failed to fetch {url}: {e}")

    contact_email = data.get("contact", "")
    
    if is_signed and author_email:
        if author_email.lower() != contact_email.lower():
            messages.append(f"⚠️ 提交者的邮箱 ({author_email}) 与 YAML 中的联系邮箱 ({contact_email}) 不一致。 / Author email does not match contact email.")
            messages.append("⚠️ 归属权验证失败，需要管理员人工审核。 / Ownership verification failed, requires manual review.")
        else:
            match_found, emails_found = check_rdap_email(asn, author_email)
            if match_found:
                messages.append(f"✅ GPG 签名已验证，且邮箱 {author_email} 匹配 WHOIS 记录，归属权验证通过！ / Ownership verified via GPG and RDAP.")
            else:
                messages.append(f"⚠️ 邮箱 {author_email} 未在 {asn} 的 WHOIS 记录中找到 (找到的邮箱: {', '.join(emails_found)})。 / Email not found in RDAP.")
                messages.append("⚠️ 归属权验证失败，需要管理员人工审核。 / Ownership verification failed, requires manual review.")
    else:
        messages.append("⚠️ 提交未进行 GPG 签名或未获取到邮箱，跳过自动化归属权验证。需要管理员人工审核。 / Commit not signed or missing email, skipping automated ownership verification.")

    return errors, warnings, messages

def _error_category(error_msg):
    """Extract a category key from an error message by removing row-specific details."""
    # Remove "at valid row N" suffix
    msg = re.sub(r"\s+at valid row \d+", "", error_msg)
    # Remove specific prefix/country/region values in quotes
    msg = re.sub(r"'[^']*'", "'...'", msg)
    # Remove specific URL content
    msg = re.sub(r"https?://\S+", "<url>", msg)
    # Remove HTTP status codes
    msg = re.sub(r"HTTP \d+", "HTTP <N>", msg)
    return msg

def format_errors(errors, max_examples=3):
    """Group similar errors and return formatted output with counts."""
    groups = OrderedDict()
    for err in errors:
        key = _error_category(err)
        if key not in groups:
            groups[key] = []
        groups[key].append(err)
    
    lines = []
    for key, group in groups.items():
        shown = group[:max_examples]
        remaining = len(group) - len(shown)
        for e in shown:
            lines.append(f" - {e}")
        if remaining > 0:
            lines.append(f"   ...及其他 {remaining} 个同类错误 / ...and {remaining} more similar errors")
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
    
    for m in messages:
        print(m)

    if warnings:
        print(f"\n⚠️ 警告 / Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print(f"\n❌ 校验失败 / Validation Failed (共 {len(errors)} 个错误 / {len(errors)} errors):")
        for line in format_errors(errors):
            print(line)
        sys.exit(1)
    else:
        print("\n✅ 格式校验通过 / Format Validation Passed.")
        sys.exit(0)

