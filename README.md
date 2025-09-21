Dwarf gron: dump type layouts, one field at a time
==================================================

Tools like pahole can intepret bytes according to dwarf debug info and
even turn the debug info back into C type definitions.  However, much
like json, the nested structure of C-style struct can be hindrance.

The tools in this repository take a page from https://github.com/tomnomnom/gron and
work with layout information as a flattened list of records, with one record per
atomic field.  There is no explicit representation for compound types like structs,
unions, or arrays (except for arrays of chars, which are special cased as atomic
string values); all that nesting is represented in a path, an array of fields or
selectors.

The `dump_type_layout.py` script is invoked as a gdb script and dumps out type layout
as json.  It would have been nice to work directly with BTF, but pahole doesn't support
outputting C++ types to BTF. A gdb script is nice because gdb and gcc tend to adopt
updated DWARF encodings in tandem, so that's less busy work when gcc gets fancy.

```
$ cat input.json
["foo.o", "flag"]
$ gdb foo.o --batch -ex 'source dump_type_layout.py' \
    -ex "python dump_type_layout_jsonl('input.json', 'output.jsonl')"
```


One entry, formatted as json, looks like:

```
$ head -1 output.jsonl | jq .
{
  "scope": "foo.o",
  "name": "flag",
  "layout": [
    {
      "path": [
        "test",
        ".e@0"
      ],
      "kind": "enum",
      "pretty": "enum e1",
      "type": "enum e1",
      "bitpos": 0,
      "bitsize": 32,
      "signed": false,
      "members": {
        "E": 0
      }
    },
```

This gives us all the information to extract the bits for the field's value
and print that out next to a nicer looking path for the field.  The details
are in `dump_type_layout.py`

The script in `gron_cdata.py` shows how we can use these layouts to interpret
bytes according to a layout and print out the corresponding field values

```
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
```

or even in [logfmt style](https://brandur.org/logfmt)

```
e=(enum e1)1094795585 name=b'AAA' f0=true f1=false f2=false f3=false f4=false f5=false f6=true ...
```
