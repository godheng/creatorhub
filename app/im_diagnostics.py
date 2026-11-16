"""Bounded, value-free summaries of unexpected IM response bodies."""
import hashlib
import json


def im_response_shape(raw: bytes) -> dict:
    result = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if len(raw) > 4096:
        return {**result, "format": "over_limit"}
    if not raw:
        return {**result, "format": "empty"}
    if raw.startswith(b'\x1f\x8b'):
        return {**result, "format": "gzip"}
    stripped = raw.lstrip()
    if stripped.startswith((b'{', b'[')):
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return {**result, "format": "invalid_json"}
        result["format"] = "json"
        if isinstance(value, dict):
            known = {"cmd", "status_code", "error_code", "code", "status", "body", "data", "message", "error_desc", "status_msg"}
            result["keys"] = sorted(known.intersection(value))
            result["other_keys"] = len(set(value) - known)
            result["numeric"] = {key: value[key] for key in ("cmd", "status_code", "error_code", "code", "status")
                                 if type(value.get(key)) is int and -(2**31) <= value[key] < 2**31}
        else:
            result["container"] = "array"
        return result
    if stripped.startswith(b'<'):
        return {**result, "format": "markup"}
    position = 0
    fields = []
    def varint():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(raw): raise ValueError()
            byte = raw[position]; position += 1
            if shift == 63 and byte > 1: raise ValueError()
            value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError()
    try:
        while position < len(raw):
            if len(fields) >= 32: raise ValueError()
            tag = varint(); number, wire = tag >> 3, tag & 7
            if not 0 < number < 2**29: raise ValueError()
            start = position
            if wire == 0: varint()
            elif wire in (1, 5): position += 8 if wire == 1 else 4
            elif wire == 2:
                length = varint(); position += length
            else: raise ValueError()
            if position > len(raw): raise ValueError()
            fields.append({"field": number, "wire": wire, "encoded_length": position - start})
    except ValueError:
        return {**result, "format": "unknown_or_truncated", "complete": False, "fields": fields}
    return {**result, "format": "protobuf_wire", "complete": position == len(raw), "fields": fields}
