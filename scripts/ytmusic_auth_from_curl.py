#!/usr/bin/env python3
"""Build a ytmusicapi browser-auth JSON from a Chrome "Copy as cURL" OR a cookies.txt.

This is the Walkman re-auth tool. YouTube Music cookie auth expires periodically;
when it does, re-run this to refresh the credential file. No secrets are printed;
the cookie only travels from your browser into this machine.

Two accepted inputs (auto-detected on stdin):
  1. A DevTools "Copy as cURL" command for a music.youtube.com /browse POST.
     NOTE: recent Chrome/Edge REDACT the cookie from copied cURL. If that happens,
     use input #2 instead.
  2. A Netscape cookies.txt exported for youtube.com (e.g. via the open-source
     "Get cookies.txt LOCALLY" extension). Robust to Chrome's redaction.

Usage:
  # on the Mac, straight from the clipboard (most reliable, no SSH paste):
  pbpaste | python3 ytmusic_auth_from_curl.py -o /tmp/ytmusic-auth.json
  # or from a file:
  python3 ytmusic_auth_from_curl.py -o out.json < input.txt
Then copy the JSON to the Pi at the path mopidy.conf points to.
"""
import argparse
import json
import os
import re
import sys

DEFAULT_OUT = os.path.expanduser("~/.config/walkman/ytmusic-auth.json")

# Not carried over: authorization (stale SAPISIDHASH; ytmusicapi recomputes it),
# content-length (belongs to the captured body), accept-encoding (avoid br/zstd).
DROP = {"authorization", "content-length", "accept-encoding"}
PSEUDO = {"authority", "method", "path", "scheme"}
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://music.youtube.com",
    "x-origin": "https://music.youtube.com",
    "user-agent": UA,
}


def parse_curl(text):
    headers = {}
    # -H 'name: value' / -H "name: value" / --header ... ; allow $'...' (bash ANSI-C)
    for m in re.finditer(r"(?:-H|--header)\s+\$?(['\"])(.*?)\1", text, re.S):
        raw = m.group(2)
        if ":" not in raw:
            continue
        name, _, value = raw.partition(":")
        name, value = name.strip(), value.strip()
        low = name.lower()
        if not name or name.startswith(":") or low in PSEUDO or low in DROP:
            continue
        headers[name] = value
    m = re.search(r"(?:-b|--cookie)\s+\$?(['\"])(.*?)\1", text, re.S)
    if m and not any(k.lower() == "cookie" for k in headers):
        headers["cookie"] = m.group(2).strip()
    return headers


def parse_cookies_txt(text):
    # Netscape: domain \t flag \t path \t secure \t expiry \t name \t value
    pairs = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and "youtube.com" in parts[0]:
            pairs.append(f"{parts[5]}={parts[6]}")
    if not pairs:
        return {}
    headers = dict(BASE_HEADERS)
    headers["cookie"] = "; ".join(pairs)
    return headers


def has_sapisid(cookie):
    return "SAPISID" in cookie or "__Secure-3PAPISID" in cookie


def main():
    ap = argparse.ArgumentParser(description="Make a ytmusicapi auth JSON from cURL or cookies.txt.")
    ap.add_argument("-o", "--output", default=DEFAULT_OUT)
    args = ap.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        print("ERROR: no input. Paste a cURL or cookies.txt, then Ctrl-D.", file=sys.stderr)
        sys.exit(2)

    looks_like_curl = ("curl " in text) or ("-H " in text) or ("--header" in text)
    headers = parse_curl(text) if looks_like_curl else {}
    if not any(k.lower() == "cookie" for k in headers):
        ck = parse_cookies_txt(text)          # fall back / alternate input
        if ck:
            headers = ck

    cookie_key = next((k for k in headers if k.lower() == "cookie"), None)
    cookie = headers.get(cookie_key, "") if cookie_key else ""

    if not cookie or not has_sapisid(cookie):
        # Safe diagnostics only: names + lengths + booleans, never values.
        names = sorted({k.lower() for k in headers})
        print(f"DIAG: input looked like {'cURL' if looks_like_curl else 'cookies.txt'}; "
              f"parsed {len(headers)} header(s): {names}", file=sys.stderr)
        if cookie_key:
            print(f"DIAG: a 'cookie' header was found ({len(cookie)} chars), "
                  f"SAPISID present: {has_sapisid(cookie)}", file=sys.stderr)
        else:
            print("DIAG: NO cookie parsed -> Chrome likely redacted it from the cURL. "
                  "Use a cookies.txt export instead.", file=sys.stderr)
        print("ERROR: no usable auth cookie (need SAPISID / __Secure-3PAPISID).", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(headers, f, indent=2)
    os.chmod(args.output, 0o600)
    print(f"OK: wrote {len(headers)} headers to {args.output} "
          f"(cookie {len(cookie)} chars). No secrets shown.")


if __name__ == "__main__":
    main()
