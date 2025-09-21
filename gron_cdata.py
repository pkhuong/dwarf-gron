"""Demo gron-style dumper for raw bytes, given a layout extracted by
dump_type_layout.py.

```
$ cat foo.c
#include <stdbool.h>

typedef enum e1 { E } e1;

struct test
{
    e1 e;
    struct
    {
        char name[3];
        bool f0 : 1;
        bool f1 : 1;
        bool f2 : 1;
        bool f3 : 1;
        bool f4 : 1;
        bool f5 : 1;
        bool f6 : 1;
        bool f7 : 1;
        float f;
    };
    struct
    {
        int x;
        unsigned y;
    } z[2];
    char c;
};

struct test flag[0];
$ gcc -O -ggdb foo.c -c
$  cat input.json
["foo.o", "flag"]
$ gdb foo.o --batch -ex 'source dump_type_layout.py' -ex \
    "python dump_type_layout_jsonl('input.json', 'output.jsonl')"
$ cat output.jsonl
{"scope": "foo.o", "name": "flag", "layout": [{"path": ["test", ".e@0"], "kind": "enum", ...
$ python3 gron_cdata.py
e = (enum e1)1094795585
name = b'AAA'
f0 = true
f1 = false
f2 = false
f3 = false
f4 = false
f5 = false
f6 = true
f7 = false
f = 12.078431129455566
z[0].x = 1094795585
z[0].y = 1094795585
z[1].x = 1094795585
z[1].y = 1094795585
c = 65
e=(enum e1)1094795585 name=b'AAA' f0=true f1=false f2=false f3=false f4=false f5=false f6=true ...
"""

import json
import re
import struct


def _has_payload(path):
    for step in path:
        if step is None:
            continue
        # Nothing to do for zero-sized arrays
        if step[0] in ("!", "*"):
            # XXX: could try to handle flexible array members
            return False
        # Arbitrarily go down only the first element in a union
        if step[0] == "?" and step[-2:] != "@0":
            return False
    return True


def _join_path(path):
    ret = []
    for element in path[1:]:
        if not element:
            continue
        hits = re.match("^(.)(.*)@([0-9]+)$", element)
        if not hits:
            raise ValueError(f"failed to match {element}")
        kind = hits.group(1)
        info = hits.group(2)
        _rank = hits.group(3)

        if kind in (".", "?"):
            # struct or enum: field selector
            if ret:
                ret.append(f".{info}")
            else:
                # no leading dot for the first element
                ret.append(info)
        elif kind in ("!", "*"):
            # unsized array
            ret.append("[]")
        elif kind == "[":
            idx, _bitsize = info.split("*")
            ret.append(f"[{idx}]")
        else:
            raise ValueError(f"unexpected kind {kind} in {element}")

    return "".join(ret)


def _get_int(bitpos, bitsize, buf, signed=False):
    first = bitpos // 8
    end = (bitpos + bitsize + 7) // 8

    # decode as little endian
    acc = 0
    for byte_idx in range(first, end):
        value = int(buf[byte_idx])
        bit = 8 * byte_idx
        if bit < bitpos:
            value = value >> (bitpos - bit)
            shift = 0
        else:
            shift = bit - bitpos
        # We already shifted out the low bits if bit < bitpos,
        # compare against max(bit, bitpos).
        remaining = bitpos + bitsize - max(bit, bitpos)
        if remaining < 8:
            value &= (1 << remaining) - 1
        acc += value << shift

    assert acc < (1 << bitsize)

    if signed:
        sign_bit = 1 << (bitsize - 1)
        if (acc & sign_bit) != 0:
            acc |= -sign_bit
    return acc


def _get_bytes(bitpos, bitsize, buf):
    assert (bitpos % 8) == 0
    assert (bitsize % 8) == 0
    begin = bitpos // 8
    bytesize = (bitsize + 7) // 8
    return buf[begin : begin + bytesize]


def _extract_ptr(field, buf):
    address = _get_int(field["bitpos"], field["bitsize"], buf)
    pretty = field["pretty"]
    return f"({pretty}){address:#x}"


def _extract_string(field, buf):
    data = _get_bytes(field["bitpos"], field["bitsize"], buf)
    # return data.decode("iso-8859-1").encode("unicode-escape")
    return data


def _extract_enum(field, buf):
    value = _get_int(field["bitpos"], field["bitsize"], buf, field["signed"])
    pretty = field["pretty"]
    for name, enumval in field["members"].items():
        if enumval == value:
            return name
    return f"({pretty}){value}"


def _extract_integer(field, buf):
    return _get_int(field["bitpos"], field["bitsize"], buf, field["signed"])


def _extract_float(field, buf):
    bitsize = field["bitsize"]
    bits = _get_int(field["bitpos"], bitsize, buf)
    if bitsize == 64:
        return struct.unpack("<d", struct.pack("<Q", bits))[0]
    if bitsize == 32:
        return struct.unpack("<f", struct.pack("<L", bits))[0]
    if bitsize == 16:
        return struct.unpack("<e", struct.pack("<H", bits))[0]
    return f"f{bitsize}({bits:#x})"


def _extract_boolean(field, buf):
    value = _get_int(field["bitpos"], field["bitsize"], buf, field["signed"])
    if value == 0:
        return "false"
    if value == 1:
        return "true"
    if field["bitsize"] == 1 and value == -1:
        return "true"
    return f"bool({value:#x})"


_EXTRACT_FIELD = {
    "ptr": _extract_ptr,
    "string": _extract_string,
    "enum": _extract_enum,
    "integer": _extract_integer,
    "float": _extract_float,
    "boolean": _extract_boolean,
}


def _gron_field(field, buf):
    if not _has_payload(field["path"]):
        return None
    return _join_path(field["path"]), _EXTRACT_FIELD[field["kind"]](field, buf)


def gron_cdata(layout, buf):
    """Yields a series of key, value pairs for the contents of buf
    interpreted as `layout`.
    """
    for field in layout:
        kv = _gron_field(field, buf)
        if kv:
            yield kv


LAYOUT = json.loads(
    '[{"path": ["test", ".e@0"], "kind": "enum", "pretty": "enum e1", "type": "enum e1", "bitpos": 0, "bitsize": 32, "signed": false, "members": {"E": 0}}, {"path": ["test", ".name@0"], "kind": "string", "pretty": "char [3]", "type": "", "bitpos": 32, "bitsize": 24, "pointee": "char", "pointee_pretty": "char", "pointee_raw": "char", "pointee_sizeof": 1}, {"path": ["test", ".f0@1"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 56, "bitsize": 1, "signed": false}, {"path": ["test", ".f1@2"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 57, "bitsize": 1, "signed": false}, {"path": ["test", ".f2@3"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 58, "bitsize": 1, "signed": false}, {"path": ["test", ".f3@4"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 59, "bitsize": 1, "signed": false}, {"path": ["test", ".f4@5"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 60, "bitsize": 1, "signed": false}, {"path": ["test", ".f5@6"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 61, "bitsize": 1, "signed": false}, {"path": ["test", ".f6@7"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 62, "bitsize": 1, "signed": false}, {"path": ["test", ".f7@8"], "kind": "boolean", "pretty": "_Bool", "type": "_Bool", "bitpos": 63, "bitsize": 1, "signed": false}, {"path": ["test", ".f@9"], "kind": "float", "pretty": "float", "type": "float", "bitpos": 64, "bitsize": 32}, {"path": ["test", ".z@2", "[0*64@0", ".x@0"], "kind": "integer", "pretty": "int", "type": "int", "bitpos": 96, "bitsize": 32, "signed": true}, {"path": ["test", ".z@2", "[0*64@0", ".y@1"], "kind": "integer", "pretty": "unsigned int", "type": "unsigned int", "bitpos": 128, "bitsize": 32, "signed": false}, {"path": ["test", ".z@2", "[1*64@1", ".x@0"], "kind": "integer", "pretty": "int", "type": "int", "bitpos": 160, "bitsize": 32, "signed": true}, {"path": ["test", ".z@2", "[1*64@1", ".y@1"], "kind": "integer", "pretty": "unsigned int", "type": "unsigned int", "bitpos": 192, "bitsize":32, "signed": false}, {"path": ["test", ".c@3"], "kind": "integer", "pretty": "char", "type": "char", "bitpos": 224, "bitsize": 8, "signed": false}]'
)


if __name__ == "__main__":
    for k, v in gron_cdata(LAYOUT, b"A" * 100):
        print(f"{k} = {v}")

    print(
        " ".join(
            f"{k}={str(v).replace(' ', '\u00A0')}"
            for k, v in gron_cdata(LAYOUT, b"A" * 100)
        )
    )
