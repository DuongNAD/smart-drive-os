"""tests/yaml_subset.py - a strict loader for the block-style YAML of the repository's GitHub issue forms.

100% Python Standard Library. Zero external dependencies.
The forms in .github/ISSUE_TEMPLATE/ have to be read to be checked, and CI installs nothing, so there is no PyYAML.
`load(text)` understands the small subset those files are written in and returns what a YAML parser returns for it.
For everything else it raises ValueError and names the line, so a construct the loader does not know can never be
taken for a value. The file has no test_ prefix: it is a helper and is not collected as tests.

Understood:

* full-line comments, and ` # comments` after a value;
* block mappings (`key: value`, or `key:` followed by a more indented block) and block sequences (`- value`, and
  `- key: value` whose following keys are aligned with the first one), indented with spaces;
* plain scalars (strings, `true` and `false` as bool, decimal integers as int), 'single-quoted' strings ('' is a quote)
  and "double-quoted" strings (\\" \\\\ \\n and \\t are the escapes);
* literal block scalars: `|` (one final newline), `|-` (none) and `|+` (all the trailing blank lines are kept).

Refused, because they are not used here or because a YAML 1.1 parser such as GitHub's would read them differently:
tabs in the indentation, flow collections (`[...]`, `{...}`), anchors, aliases and tags, document markers (`---`,
`...`), duplicate keys, empty values, the folded block scalar `>`, a plain scalar that starts with an indicator
character or contains ": ", the plain words that YAML 1.1 reads as booleans or null (y, n, yes, no, on, off, null, ~,
any case: quote them) and plain scalars that read as another number than a decimal integer (3.9, 0x1F, 2026-10-21:
quote them).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# A plain scalar cannot start with one of these (YAML indicators), nor with "- " (a list entry).
_INDICATORS = "`@%|>[]{},?:#&*!"

# What YAML 1.1 parsers read as a boolean or as null when it is not quoted (any case), and two more special values.
_YAML11_WORDS = frozenset({"y", "n", "yes", "no", "on", "off", "null", "~", "=", "<<"})
_BOOLEAN_SPELLINGS = {"true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False}

_DECIMAL = re.compile(r"[-+]?(?:0|[1-9][0-9]*)")
# Everything else that YAML 1.1 would turn into a number or a date: floats, octal, hex, binary, 1_000, 12:30,
# 2026-10-21.
_NUMBER_LIKE = re.compile(
    r"[-+]?(?:\.?[0-9][0-9_]*(?:[.:eE+-][0-9_]*)*|\.(?:inf|nan)|0[xXoObB][0-9a-fA-F_]+)", re.IGNORECASE
)
_PLAIN_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?:[ ]+(.*))?")
_QUOTED_KEY_TAIL = re.compile(r":(?:[ ]+(.*))?")
_BLOCK_HEADER = re.compile(r"\|([-+]?)(?:[ ]+#.*)?[ ]*")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


class _Line(NamedTuple):
    """One line of the text: its number (from 1), how many spaces it is indented by, and the rest of it."""

    number: int
    indent: int
    text: str


def _read_quoted(text: str, number: int) -> Tuple[str, int]:
    """Decodes the quoted string that `text` starts with; returns it and the index just after its closing quote."""
    quote = text[0]
    out: List[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                out.append("'")
                index += 2
                continue
            return "".join(out), index + 1
        if quote == '"' and char == "\\":
            escape = text[index + 1 : index + 2]
            if escape not in _ESCAPES:
                raise ValueError(f"line {number}: the escape \\{escape} is not supported (only \\\" \\\\ \\n \\t are)")
            out.append(_ESCAPES[escape])
            index += 2
            continue
        out.append(char)
        index += 1
    raise ValueError(
        f"line {number}: the quoted string is not closed on this line (multi-line strings are not supported)"
    )


def _unsupported_start(text: str, number: int) -> Optional[ValueError]:
    """The error for text that starts a flow collection, an anchor, an alias, a tag or a folded scalar; else None."""
    first = text[0]
    if first in "[{":
        return ValueError(
            f"line {number}: flow collections ([...] and {{...}}) are not supported; use a block list or mapping"
        )
    if first in "&*!":
        return ValueError(f"line {number}: anchors, aliases and tags are not supported")
    if first == ">":
        return ValueError(f"line {number}: the folded block scalar > is not supported; use | or a quoted string")
    return None


def _plain(text: str, number: int) -> Any:
    """A plain scalar: the text up to a ` #` comment, as a string, a bool or an int."""
    value = re.split(r"[ ]#", text, maxsplit=1)[0].rstrip(" ")
    first = value[0]
    if first in _INDICATORS or (first == "-" and value[1:2] in ("", " ")):
        raise ValueError(f"line {number}: a plain scalar cannot start with {first!r}; put {value!r} in quotes")
    if ": " in value or value.endswith(":"):
        raise ValueError(
            f"line {number}: a plain scalar cannot contain ': ' (YAML reads a mapping); put {value!r} in quotes"
        )
    lowered = value.lower()
    if lowered in _YAML11_WORDS:
        raise ValueError(
            f"line {number}: the plain word {value!r} is read as a boolean or null by YAML 1.1 parsers, "
            "GitHub's among them; put it in quotes"
        )
    if lowered in ("true", "false"):
        if value not in _BOOLEAN_SPELLINGS:
            raise ValueError(f"line {number}: {value!r} is ambiguous; write true or false, or put it in quotes")
        return _BOOLEAN_SPELLINGS[value]
    if _DECIMAL.fullmatch(value):
        return int(value)
    if _NUMBER_LIKE.fullmatch(value):
        raise ValueError(f"line {number}: {value!r} reads as a number or a date in YAML; put it in quotes")
    return value


class _Parser:
    """Reads the lines of a text as nested block mappings and block sequences."""

    def __init__(self, text: str) -> None:
        if text.startswith("\ufeff"):
            text = text[1:]
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        rows = normalized.split("\n")
        if rows[-1] == "":
            rows.pop()
        self.final_newline = normalized.endswith("\n")
        self.lines = [self._line(number, row) for number, row in enumerate(rows, 1)]
        self.pos = 0

    @staticmethod
    def _line(number: int, row: str) -> _Line:
        text = row.lstrip(" \t")
        lead = row[: len(row) - len(text)]
        if "\t" in lead:
            raise ValueError(f"line {number}: tab in the indentation; indent with spaces")
        if not lead and row[:3] in ("---", "...") and row[3:4] in ("", " ", "\t"):
            raise ValueError(f"line {number}: document markers ({row[:3]}) are not supported")
        return _Line(number, len(lead), text)

    def _peek(self) -> Optional[_Line]:
        """The next line that has structure: blank lines and full-line comments are stepped over."""
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.text == "" or line.text.startswith("#"):
                self.pos += 1
            else:
                return line
        return None

    @staticmethod
    def _is_entry(text: str) -> bool:
        """Whether a line is an entry of a block sequence."""
        return text == "-" or text.startswith("- ")

    def document(self) -> Any:
        """The whole text: one block mapping or block sequence."""
        first = self._peek()
        if first is None:
            raise ValueError("line 1: the document is empty")
        value = self._block(first.indent)
        extra = self._peek()
        if extra is not None:
            raise ValueError(f"line {extra.number}: unexpected indentation")
        return value

    def _block(self, indent: int) -> Any:
        """The block mapping or block sequence whose first line is the current one, written at `indent`."""
        if self._is_entry(self.lines[self.pos].text):
            return self._sequence(indent)
        return self._mapping(indent)

    def _mapping(self, indent: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return result
            if line.indent > indent:
                raise ValueError(
                    f"line {line.number}: unexpected indentation (a plain value cannot go on in the next line: "
                    "use quotes or a | block)"
                )
            if self._is_entry(line.text):
                raise ValueError(f"line {line.number}: a '- ' entry where a 'key: value' line is expected")
            found = self._split_key(line.text, line.number)
            if found is None:
                raise _unsupported_start(line.text, line.number) or ValueError(
                    f"line {line.number}: expected 'key: value' with the colon right after the key, found {line.text!r}"
                )
            key, rest = found
            if key in result:
                raise ValueError(f"line {line.number}: duplicate key {key!r}")
            self.pos += 1
            result[key] = self._value(rest, indent, line.number)

    def _sequence(self, indent: int) -> List[Any]:
        items: List[Any] = []
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return items
            if line.indent > indent:
                raise ValueError(f"line {line.number}: unexpected indentation")
            if not self._is_entry(line.text):
                raise ValueError(f"line {line.number}: a '- ' entry of the list is expected, found {line.text!r}")
            after = line.text[1:]
            rest = after.lstrip(" ")
            if rest == "" or rest.startswith("#"):
                raise ValueError(f"line {line.number}: a list entry needs its value on the same line as its '-'")
            column = indent + 1 + len(after) - len(rest)
            if self._is_entry(rest) or self._split_key(rest, line.number) is not None:
                # A mapping or a list that starts after the dash: its next lines are aligned with `rest`.
                self.lines[self.pos] = _Line(line.number, column, rest)
                items.append(self._block(column))
            else:
                self.pos += 1
                items.append(self._scalar(rest, indent, line.number))

    @staticmethod
    def _split_key(text: str, number: int) -> Optional[Tuple[str, str]]:
        """(key, the text after its colon) when the line is `key: ...`, else None."""
        if text[0] in "\"'":
            key, end = _read_quoted(text, number)
            tail = _QUOTED_KEY_TAIL.fullmatch(text[end:])
            return None if tail is None else (key, tail.group(1) or "")
        match = _PLAIN_KEY.fullmatch(text)
        if match is None:
            return None
        key = match.group(1)
        if key.lower() in _YAML11_WORDS:
            raise ValueError(
                f"line {number}: the key {key!r} is read as a boolean or null by YAML 1.1 parsers, "
                "GitHub's among them; put it in quotes"
            )
        return key, match.group(2) or ""

    def _value(self, rest: str, parent_indent: int, number: int) -> Any:
        """The value of a key at `parent_indent` whose line holds `rest` after the colon: inline, or a block below."""
        if rest != "" and not rest.startswith("#"):
            return self._scalar(rest, parent_indent, number)
        following = self._peek()
        if following is None or following.indent <= parent_indent:
            hint = ""
            if following is not None and following.indent == parent_indent and self._is_entry(following.text):
                hint = " (a list under a key has to be indented deeper than the key)"
            raise ValueError(f"line {number}: the key has no value{hint}")
        return self._block(following.indent)

    def _scalar(self, text: str, parent_indent: int, number: int) -> Any:
        """A scalar written on the line: quoted, a `|` block (its lines follow) or plain."""
        first = text[0]
        if first in "\"'":
            value, end = _read_quoted(text, number)
            tail = text[end:]
            if tail.strip(" ") != "" and not (tail.startswith(" ") and tail.lstrip(" ").startswith("#")):
                raise ValueError(f"line {number}: unexpected text after the closing quote")
            return value
        if first == "|":
            return self._literal(text, parent_indent, number)
        refusal = _unsupported_start(text, number)
        if refusal is not None:
            raise refusal
        return _plain(text, number)

    def _literal(self, header: str, parent_indent: int, number: int) -> str:
        """A literal block scalar: the lines below the header that are indented deeper than `parent_indent`."""
        match = _BLOCK_HEADER.fullmatch(header)
        if match is None:
            raise ValueError(
                f"line {number}: only the block scalar headers |, |- and |+ are supported, found {header!r}"
            )
        chomping = match.group(1)
        block: List[_Line] = []
        width: Optional[int] = None
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.text != "":
                if width is None:
                    if line.indent <= parent_indent:
                        break
                    width = line.indent
                elif line.indent < width:
                    break
            block.append(line)
            self.pos += 1
        # The last line of a file that does not end in a line break has no break of its own, so it adds no newline.
        unterminated = not self.final_newline and self.pos >= len(self.lines)
        if width is None:
            return "\n" * (len(block) - int(unterminated and bool(block))) if chomping == "+" else ""
        lines = [" " * max(0, line.indent - width) + line.text for line in block]
        end = len(lines)
        while end > 0 and lines[end - 1] == "":
            end -= 1
        text, blank = "\n".join(lines[:end]), len(lines) - end
        newline = "\n"
        if unterminated and blank == 0:
            newline = ""
        elif unterminated:
            blank -= 1
        if chomping == "-":
            return text
        if chomping == "+":
            return text + newline + "\n" * blank
        return text + newline


def load(text: str) -> Any:
    """The mapping or list that `text` (block-style YAML, see the module docstring) stands for.

    Raises ValueError, with the number of the line, for anything outside the understood subset.
    """
    return _Parser(text).document()
