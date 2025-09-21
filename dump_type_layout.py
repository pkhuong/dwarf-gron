"""Walks down GDB type info to explode all the fields in a type to a
flat list of field descriptors for atomic (in the platonic sense, not
like std::atomic) data (integers, floats, enums, pointers, or C
strings).  Descriptor reflects the contents of the type (including any
inline array), but not its pointees.

Sample usage:

```
$ cat input.jsonl
[null, "test2"]
["0x4006e0", "test_record2"]
$ gdb a.out --batch -ex 'source dump_type_layout.py' -ex \
    "python dump_type_layout_jsonl('input.jsonl', 'output.jsonl')"
$  cat output.jsonl
{"scope": null, "name": "test2", "layout": [{"path": ["test2", ".e@0"], "kind": "enum", ...
{"scope": 4196064, "name": "test_record2", "layout": [{"path": [null, ...
...
```

Descriptors are flattened and reflect only atoms, so we describe field
locations with *paths* (much like, e.g., offsetof).

The main entry point is `dump_type_layout_jsonl`.  Call that from gdb
with a path for an input file (or pipe) in jsonl format, and another
path for an output file in jsonl format.

Each line of input yields one line of output.

The input lines must be JSON arrays of length 2, where the first
element is a scope, and the second a name to look up in that scope.
The scope can be `null` for a global type lookup, an object file, or
an address (integer or hex string) to map to an object file.  Given an
object file as scope, we look up the name in that scope, first as a
file-local type or value, and second as a global type or value.  If we
get a value, the layout is for that value's type.

Each output line is a json object, or `null` if we failed to dump the
type layout.  That object has

- scope: same as the input scope (first array element)
- name: same as the input name (second array element)
- layout: array of field descriptor objects

By default, `dump_type_layout_jsonl` has `deref=True`: when the scope
and name yield an array or pointer, the code describes the type for
the array's elements, or the pointer's pointees.

Field descriptor
----------------

A field descriptor always has:

- path: the field path array for that field (see next section)
- kind: "ptr" (pointer), "string" (C string), "enum", "integer", or "float"
- pretty: pretty printed type string for that field
- type: more complete name (including C tag) for the field's type
- bitpos: bit offset of the field, from the beginning of the root struct
- bitsize: size of the field in bits

Pointers ("ptr") and strings ("string") have:
- pointee: complete type name for the pointee (underlying character type for strings)
- pointee_pretty: pretty printed type string for the pointee
- pointee_raw: complete type name for the pointee, after stripping typedefs
- pointee_sizeof: size *IN BYTES* of the pointee

When strings are flexible array members, their bitsize is reported as 0.

Enums ("enum"), integers ("integer"), and booleans ("boolean") have:
- signed: true if the bitrange should be treated like a signed integer range

In addition, `enums` have:
- members: dictionary from enum name to integer value

Finally, `float` don't have any special field.

Field path
----------

A field path is a sequence that starts with the root type's name (or
null if anonymous), followed by a field descriptor string for each
field in the path.

The first character of each field descriptor tells us what kind of
field it is:
- `.` for structs members
- `?` for union members
- `!` for zero-sized arrays (those still get a description, even though they don't take any space)
- `*` for flexible array members
- `[` for regular arrays.

After the first character, struct and union members have the
field name.  Arrays instead have `index*bitsize`, the array index,
an asterisk, and the bitsize of each element in the array.

All field descriptors end with a string of the form `@[:digits:]+`. The
decimal digits give us the rank of the field in its parent; this
is populated even for arrays.

"""

import json

CODE_TAGS = {
    gdb.TYPE_CODE_STRUCT: "struct",
    gdb.TYPE_CODE_UNION: "union",
    gdb.TYPE_CODE_ENUM: "enum",
}


def type_name(ctype):
    """Returns a complete name for `ctype`"""
    ctype = ctype.unqualified()
    name = ctype.name
    if not name:
        return ""
    prefix = CODE_TAGS.get(ctype.code, "")
    if prefix:
        name = f"{prefix} {name}"
    return name


def list_ptr(path, ctype, bitpos, bitsize):
    """Dumps the layout information for a pointer field."""
    target = ctype.target()
    yield dict(
        path=path,
        kind="ptr",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
        pointee=type_name(target),
        pointee_pretty=str(target),
        pointee_raw=type_name(target.strip_typedefs()),
        pointee_sizeof=target.sizeof,
    )


def list_string(path, ctype, bitpos, bitsize):
    """Dumps the layout information for a string field."""
    target = ctype.target()
    yield dict(
        path=path,
        kind="string",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
        pointee=type_name(target),
        pointee_pretty=str(target),
        pointee_raw=type_name(target.strip_typedefs()),
        pointee_sizeof=target.sizeof,
    )


def list_array(path, ctype, bitpos, bitsize):
    """Dumps layout information for an array's contents."""
    if ctype.is_string_like:
        yield from list_string(path, ctype, bitpos, bitsize)
        return
    eltype = ctype.target()
    lo, hi = ctype.range()
    flexible = ctype.sizeof == 0
    empty = hi < lo
    for rank, idx in enumerate(range(lo, lo + 1 if flexible or empty else hi + 1)):
        if empty:
            kind = "!"
        elif flexible:
            kind = "*"
        else:
            kind = "["

        bits = 8 * eltype.sizeof
        yield from _list_type(
            path + (f"{kind}{idx}*{bits}@{rank}",),
            eltype,
            bitpos + rank * bits,
            eltype.sizeof * 8,
        )


def list_struct(path, ctype, bitpos, bitsize):
    """Dumps layout information for a struct's contents."""
    for rank, field in enumerate(ctype.fields()):
        if field.is_base_class or field.name is None:
            subpath = path
        else:
            subpath = path + (f".{field.name}@{rank}",)
        yield from _list_type(subpath, field.type, bitpos + field.bitpos, field.bitsize)


def list_union(path, ctype, bitpos, _bitsize):
    """Dumps layout information for a union's contents."""
    for rank, field in enumerate(ctype.fields()):
        if field.is_base_class or field.name is None:
            subpath = path
        else:
            subpath = path + (f"?{field.name}@{rank}",)
        yield from _list_type(subpath, field.type, bitpos + field.bitpos, field.bitsize)


def list_enum(path, ctype, bitpos, bitsize):
    """Dumps layout information for an enum."""
    members = {field.name: field.enumval for field in ctype.fields() if field.name}
    yield dict(
        path=path,
        kind="enum",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
        signed=ctype.is_signed,
        members=members,
    )


def list_int(path, ctype, bitpos, bitsize):
    """Dumps layout information for an integer."""
    yield dict(
        path=path,
        kind="integer",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
        signed=ctype.is_signed,
    )


def list_flt(path, ctype, bitpos, bitsize):
    """Dumps layout information for a float point value."""
    yield dict(
        path=path,
        kind="float",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
    )


def list_bool(path, ctype, bitpos, bitsize):
    """Dumps layout information for a bool."""
    yield dict(
        path=path,
        kind="boolean",
        pretty=str(ctype),
        type=type_name(ctype),
        bitpos=bitpos,
        bitsize=bitsize if bitsize > 0 else 8 * ctype.sizeof,
        signed=ctype.is_signed,
    )


# See https://sourceware.org/gdb/current/onlinedocs/gdb.html/Types-In-Python.html
# when adding entries.
CODE_TABLE = {
    gdb.TYPE_CODE_PTR: list_ptr,
    gdb.TYPE_CODE_ARRAY: list_array,
    gdb.TYPE_CODE_STRUCT: list_struct,
    gdb.TYPE_CODE_UNION: list_union,
    gdb.TYPE_CODE_ENUM: list_enum,
    gdb.TYPE_CODE_INT: list_int,
    gdb.TYPE_CODE_FLT: list_flt,
    gdb.TYPE_CODE_BOOL: list_bool,
}


def _list_type(path, ctype, bitpos, bitsize):
    path = tuple(path)
    ctype = ctype.strip_typedefs()
    lister = CODE_TABLE.get(ctype.code, path)
    if lister is path:
        print(f"# failed to handle type={ctype.name} code={ctype.code}")
    elif lister is not None:
        yield from lister(path, ctype, bitpos, bitsize)


def list_fields(ctype, path=()):
    """Dumps all the atomic fields in `ctype` to an array of dicts."""
    return list(_list_type(tuple(path), ctype, 0, 0))


def list_one_var(scope, name, deref=True):
    """Dumps the type layout for `name` in `scope`. When `deref` is
    true and the resulting type is an array or pointer type, dumps
    the array element/pointee type.
    """
    result = dict(scope=scope, name=name)
    if scope is None:
        # treat name as a type name
        ctype = gdb.lookup_type(name)
        # See also lookup_global_symbol, etc.
        # https://sourceware.org/gdb/current/onlinedocs/gdb.html/Symbols-In-Python.html
    else:
        # You'd think we want
        # https://sourceware.org/gdb/current/onlinedocs/gdb.html/Blocks-In-Python.html,
        # but that's really for unwinding. We have to use
        # https://sourceware.org/gdb/current/onlinedocs/gdb.html/Objfiles-In-Python.html
        if isinstance(scope, str):
            try:
                objfile = gdb.lookup_objfile(scope)
            except ValueError as exc:
                print(f"failed to find objfile {scope} for {name}. exc={exc}")
                return None
        else:
            try:
                objfile = gdb.current_progspace().objfile_for_address(scope)
            except ValueError as exc:
                print(f"failed to find objfile for {scope:x} {name}. exc={exc}")
                return None

        value = objfile.lookup_static_symbol(name)
        if value is None:
            value = objfile.lookup_global_symbol(name)
        if value is None:
            print(f"failed to find value or type for {result}")
            return None
        ctype = value.type
    try:
        if deref and ctype.code in (gdb.TYPE_CODE_PTR, gdb.TYPE_CODE_ARRAY):
            ctype = ctype.target()
        result["layout"] = list_fields(ctype, [ctype.unqualified().name])
        return result
    except Exception as exc:
        print(f"# failed to find fields for {name}@{scope} type={ctype}, exc={exc}")
    return None


def list_var_fields(addresses_and_names, deref=True):
    ret = []
    for address, name in addresses_and_names:
        result = list_one_var(address, name, deref)
        if result:
            ret.append(result)
    return ret


def print_var_types(path, inputs, deref=True):
    with open(path, "wt", encoding="utf-8") as out:
        for address, name in inputs:
            try:
                result = list_one_var(address, name, deref)
                json.dump(result, out)
                print("", file=out, flush=True)
            except Exception as exc:
                print(f"failed to handle input {address} {name}. exc={exc}")


def dump_type_layout_jsonl(inpath, outpath, deref=True):
    with open(inpath, "rt", encoding="utf-8") as inp:
        with open(outpath, "wt", encoding="utf-8") as out:
            for line in inp.readlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    address, name = json.loads(line)
                    if isinstance(address, str) and address[:2] == "0x":
                        address = int(address, base=0)
                    result = list_one_var(address, name, deref)
                except Exception as exc:
                    print(f"failed to handle line {line}. exc={exc}")
                    result = None

                json.dump(result, out)
                print("", file=out, flush=True)
