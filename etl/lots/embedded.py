"""Pull a JSON or JS object out of a lot page without parsing the whole document."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_balanced(text: str, start: int, opener: str = "{") -> str:
    """Slice from `start` (an opener) through its matching closer. Strings are skipped."""
    closer = "}" if opener == "{" else "]"
    if start >= len(text) or text[start] != opener:
        raise ValueError(f"expected {opener!r} at offset {start}")
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in "\"'":
            in_string = True
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("unbalanced object")


def loads_balanced(text: str, start: int) -> Any:
    return json.loads(extract_balanced(text, start))


def script_json(html: str, element_id: str) -> Any | None:
    """Parse the body of `<script id="...">` when it is JSON."""
    match = re.search(
        rf'<script[^>]*\bid=["\']{re.escape(element_id)}["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    body = match.group(1).strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def assignment_json(html: str, name: str) -> Any | None:
    """Parse `name = { ... }` or `name = [ ... ]` when the value is JSON."""
    match = re.search(rf"{re.escape(name)}\s*=\s*", html)
    if match is None:
        return None
    start = match.end()
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] not in "{[":
        return None
    opener = html[start]
    try:
        return json.loads(extract_balanced(html, start, opener))
    except (ValueError, json.JSONDecodeError):
        return None


_JS_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


class JsParser:
    """A small subset of JS object literals: objects, arrays, strings, numbers, void 0, new Date."""

    def __init__(self, text: str, index: int = 0) -> None:
        self.text = text
        self.index = index
        self.length = len(text)

    def parse(self) -> Any:
        self._skip()
        return self._value()

    def _peek(self) -> str:
        return self.text[self.index] if self.index < self.length else ""

    def _skip(self) -> None:
        while self.index < self.length and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _starts(self, token: str) -> bool:
        return self.text.startswith(token, self.index)

    def _value(self) -> Any:
        self._skip()
        if self.index >= self.length:
            raise ValueError("unexpected end of JS value")
        char = self._peek()
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char in "\"'":
            return self._string(char)
        if self._starts("void"):
            self.index += 4
            self._skip()
            self._value()
            return None
        if self._starts("new Date("):
            self.index += len("new Date(")
            number = self._number()
            self._skip()
            if self._peek() == ")":
                self.index += 1
            return number
        if self._starts("true") and not self._ident_continues(4):
            self.index += 4
            return True
        if self._starts("false") and not self._ident_continues(5):
            self.index += 5
            return False
        if self._starts("null") and not self._ident_continues(4):
            self.index += 4
            return None
        if self._starts("undefined") and not self._ident_continues(9):
            self.index += 9
            return None
        if char == "-" or char.isdigit():
            return self._number()
        raise ValueError(f"unexpected JS at {self.text[self.index:self.index + 24]!r}")

    def _ident_continues(self, offset: int) -> bool:
        next_index = self.index + offset
        return next_index < self.length and (self.text[next_index].isalnum() or self.text[next_index] == "_")

    def _object(self) -> dict[str, Any]:
        self.index += 1
        result: dict[str, Any] = {}
        while True:
            self._skip()
            if self._peek() == "}":
                self.index += 1
                return result
            key = self._key()
            self._skip()
            if self._peek() != ":":
                raise ValueError("expected ':' after JS key")
            self.index += 1
            result[key] = self._value()
            self._skip()
            if self._peek() == ",":
                self.index += 1
                continue
            if self._peek() == "}":
                self.index += 1
                return result
            raise ValueError("expected ',' or '}' in JS object")

    def _array(self) -> list[Any]:
        self.index += 1
        result: list[Any] = []
        while True:
            self._skip()
            if self._peek() == "]":
                self.index += 1
                return result
            result.append(self._value())
            self._skip()
            if self._peek() == ",":
                self.index += 1
                continue
            if self._peek() == "]":
                self.index += 1
                return result
            raise ValueError("expected ',' or ']' in JS array")

    def _key(self) -> str:
        self._skip()
        if self._peek() in "\"'":
            return str(self._string(self._peek()))
        start = self.index
        while self.index < self.length and (self.text[self.index].isalnum() or self.text[self.index] in "_$"):
            self.index += 1
        if self.index == start:
            raise ValueError("expected JS object key")
        return self.text[start : self.index]

    def _string(self, quote: str) -> str:
        self.index += 1
        chars: list[str] = []
        while self.index < self.length:
            char = self.text[self.index]
            self.index += 1
            if char == "\\":
                if self.index >= self.length:
                    break
                escaped = self.text[self.index]
                self.index += 1
                chars.append(_JS_ESCAPES.get(escaped, escaped))
                continue
            if char == quote:
                return "".join(chars)
            chars.append(char)
        raise ValueError("unterminated JS string")

    def _number(self) -> int | float:
        start = self.index
        if self._peek() == "-":
            self.index += 1
        while self.index < self.length and self.text[self.index] in "0123456789.eE+-":
            # Don't swallow a following `+` that isn't an exponent sign. The first char is already consumed
            # only when it is `-` above; `+` here is only valid right after e/E.
            if self.text[self.index] in "+-" and self.text[self.index - 1] not in "eE":
                break
            self.index += 1
        token = self.text[start : self.index]
        if not token or token in "+-":
            raise ValueError("expected number")
        number = float(token) if any(mark in token for mark in ".eE") else int(token)
        return number


def parse_js_value(text: str, index: int = 0) -> Any:
    return JsParser(text, index).parse()


_FLIGHT_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)')


def next_flight_chunks(html: str) -> list[str]:
    """Unescape Next.js flight payloads (`self.__next_f.push([1, "..."])`)."""
    chunks: list[str] = []
    for match in _FLIGHT_PUSH.finditer(html):
        try:
            chunks.append(json.loads(f'"{match.group(1)}"'))
        except json.JSONDecodeError:
            continue
    return chunks


def first_json_object(chunks: list[str], key: str) -> dict[str, Any] | None:
    """The first JSON object assigned to `key` inside any flight chunk."""
    needle = f'"{key}":'
    for chunk in chunks:
        start = 0
        while True:
            found = chunk.find(needle, start)
            if found < 0:
                break
            brace = found + len(needle)
            while brace < len(chunk) and chunk[brace].isspace():
                brace += 1
            if brace < len(chunk) and chunk[brace] == "{":
                try:
                    value = loads_balanced(chunk, brace)
                except (ValueError, json.JSONDecodeError):
                    start = found + len(needle)
                    continue
                if isinstance(value, dict):
                    return value
            start = found + len(needle)
    return None
