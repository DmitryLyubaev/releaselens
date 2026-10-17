"""Bounded private authority transport for the platform's 4,096-character limit."""
import argparse
import base64
import binascii
import gzip
import hashlib
import json
from pathlib import Path
import zlib

from app.delivery_search.contracts import DeliveryRefused, canonical

MAX_VARIABLE_BYTES = 4096
MAX_AUTHORITY_BYTES = 32768
ENCODING = "gzip+base64-v1"


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DeliveryRefused("authority")
        value[key] = item
    return value


def _nonfinite(value):
    raise DeliveryRefused("authority")


def _object(raw):
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if not isinstance(value, dict):
        raise DeliveryRefused("authority")
    return value


def decode_authority(raw: bytes) -> dict:
    try:
        if len(raw) > MAX_AUTHORITY_BYTES:
            raise DeliveryRefused("authority")
        value = _object(raw)
        if "encoding" not in value:
            return value
        if (len(raw) > MAX_VARIABLE_BYTES or set(value) != {"encoding", "data"}
            or value["encoding"] != ENCODING or not isinstance(value["data"], str)):
            raise DeliveryRefused("authority")
        compressed = base64.b64decode(value["data"], validate=True)
        stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = stream.decompress(compressed, MAX_AUTHORITY_BYTES + 1)
        if (len(decoded) > MAX_AUTHORITY_BYTES or not stream.eof
            or stream.unconsumed_tail or stream.unused_data):
            raise DeliveryRefused("authority")
        authority = _object(decoded)
        if "encoding" in authority:
            raise DeliveryRefused("authority")
        return authority
    except (ValueError, TypeError, UnicodeError, RecursionError, zlib.error, binascii.Error):
        raise DeliveryRefused("authority") from None


def encode_authority(authority: dict) -> bytes:
    try:
        raw = canonical(authority)
        if len(raw) > MAX_AUTHORITY_BYTES or not isinstance(authority, dict) or "encoding" in authority:
            raise DeliveryRefused("authority")
        value = canonical({"encoding": ENCODING, "data":
            base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")})
        if len(value) > MAX_VARIABLE_BYTES:
            raise DeliveryRefused("authority")
        return value
    except (ValueError, TypeError, RecursionError):
        raise DeliveryRefused("authority") from None


def read_authority(path: Path) -> dict:
    with path.open("rb") as source:
        return decode_authority(source.read(MAX_AUTHORITY_BYTES + 1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="Reviewed canonical full authority digest")
    args = parser.parse_args(argv)
    try:
        authority = read_authority(args.input)
        if hashlib.sha256(canonical(authority)).hexdigest() != args.sha256:
            raise DeliveryRefused("authority")
        value = encode_authority(authority)
        with args.output.open("xb") as destination:
            destination.write(value)
        print(f"status=pass authority_transport_bytes={len(value)}")
        return 0
    except (DeliveryRefused, OSError):
        print("status=refused code=authority")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
