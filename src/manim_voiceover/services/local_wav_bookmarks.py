"""Manual Narration Bookmarks: named times, in seconds from the start of a local recording.

A bookmark names a moment in a recording played by :class:`LocalWavService`, for example
where a phrase begins. A scene waits for it with the existing
``VoiceoverScene.wait_until_bookmark(name)`` (or ``tracker.time_until_bookmark(name)``) and
then starts an animation. Recordings without bookmarks work exactly as before.

Bookmarks are given to ``LocalWavService(path, bookmarks=...)`` as a mapping of names to
seconds, a sequence of ``(name, seconds)`` pairs, or the path of a version 1 JSON file::

    {
      "version": 1,
      "bookmarks": [
        {"name": "quarter_times_zero", "seconds": 0.12},
        {"name": "expected_reward_three", "seconds": 4.57}
      ]
    }

Rules:

- a name is 1-64 lowercase ASCII letters, digits or underscores, starting with a letter
  (the same rule as cue IDs);
- each name appears once;
- a time is a finite number of seconds (not a boolean or a string) from 0 to the
  recording's duration, inclusive;
- at least one bookmark is given (a recording without bookmarks simply omits them).

Bookmarks are checked against the recording when the service is created and again on
every render, because the recording can be replaced. They are not part of the cache key:
changing bookmarks reuses the cached copy of the recording, and the source recording and
bookmark file are only read.

A bookmark whose time has already passed when the scene waits for it does not wait (the
existing ``wait_until_bookmark`` behaviour), so its animation starts late. Place bookmarks
at least one animation apart to keep every animation on time.
"""

import json
import math
import numbers
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from manim_voiceover._typing import JsonValue, json_value
from manim_voiceover.services.base import PathLike, path_to_string
from manim_voiceover.tracker import BookmarkError, UnknownBookmarkError

__all__ = [
    "BOOKMARKS_VERSION",
    "BookmarkError",
    "BookmarkFileError",
    "BookmarkOutOfRangeError",
    "Bookmarks",
    "BookmarksInput",
    "DuplicateBookmarkError",
    "InvalidBookmarkNameError",
    "InvalidBookmarkTimeError",
    "UnknownBookmarkError",
    "load_bookmarks",
    "parse_bookmarks",
]

BOOKMARKS_VERSION = 1
_FILE_ENCODING = "utf-8-sig"  # UTF-8, accepting the optional Windows BOM.
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_FILE_KEYS = frozenset({"version", "bookmarks"})
_ENTRY_KEYS = frozenset({"name", "seconds"})


class InvalidBookmarkNameError(BookmarkError):
    """A bookmark name is not 1-64 lowercase ASCII letters, digits or underscores starting with a letter."""


class DuplicateBookmarkError(BookmarkError):
    """A bookmark name appears more than once."""


class InvalidBookmarkTimeError(BookmarkError):
    """A bookmark time is not a finite number of seconds."""


class BookmarkOutOfRangeError(InvalidBookmarkTimeError):
    """A bookmark time is before the start or after the end of its recording."""


class BookmarkFileError(BookmarkError):
    """A bookmark file is not a valid version 1 bookmark file."""


@dataclass(frozen=True)
class Bookmarks:
    """Validated bookmark names and times (seconds from the start of the recording)."""

    times: Mapping[str, float]
    source: str = "bookmarks"

    def check_within(self, duration: float, recording: PathLike) -> None:
        """Raise BookmarkOutOfRangeError unless every time is within ``[0, duration]``."""
        for name, seconds in self.times.items():
            if seconds > duration:
                raise BookmarkOutOfRangeError(
                    f"Bookmark {name!r} at {seconds:g} s is after the end of the recording "
                    f"({duration:g} s): {path_to_string(recording)} ({self.source})"
                )


BookmarksInput = Bookmarks | Mapping[str, object] | Sequence[tuple[object, object]] | PathLike


def _check_name(name: object, where: str) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise InvalidBookmarkNameError(
            f"Invalid bookmark name {name!r} ({where}); use 1-64 lowercase ASCII letters, digits or underscores, "
            "starting with a letter"
        )
    return name


def _check_seconds(name: str, seconds: object, where: str) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, numbers.Real):
        raise InvalidBookmarkTimeError(f"Bookmark {name!r} ({where}) must have a time in seconds, got {seconds!r}")
    value = float(seconds)
    if not math.isfinite(value):
        raise InvalidBookmarkTimeError(f"Bookmark {name!r} ({where}) must have a finite time, got {seconds!r}")
    if value < 0:
        raise BookmarkOutOfRangeError(f"Bookmark {name!r} ({where}) is before the start of the recording: {value:g} s")
    return value


def _collect(pairs: Sequence[tuple[object, object]], source: str) -> Bookmarks:
    if not pairs:
        raise BookmarkError(f"No bookmarks given ({source}); omit bookmarks for a recording without them")
    times: dict[str, float] = {}
    first_index: dict[str, int] = {}
    for index, (raw_name, raw_seconds) in enumerate(pairs):
        where = f"{source}, entry {index}"
        name = _check_name(raw_name, where)
        if name in times:
            raise DuplicateBookmarkError(
                f"Bookmark {name!r} appears more than once ({source}, entries {first_index[name]} and {index})"
            )
        times[name] = _check_seconds(name, raw_seconds, where)
        first_index[name] = index
    return Bookmarks(times=MappingProxyType(times), source=source)


def _pair(entry: object, index: int) -> tuple[object, object]:
    if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence) or len(entry) != 2:
        raise BookmarkError(f"Bookmark entry {index} must be a (name, seconds) pair, got {entry!r}")
    return entry[0], entry[1]


def parse_bookmarks(value: Mapping[str, object] | Sequence[tuple[object, object]], source: str = "bookmarks") -> Bookmarks:
    """Validate bookmarks given as a mapping of names to seconds or a sequence of (name, seconds) pairs.

    Raises:
        InvalidBookmarkNameError, DuplicateBookmarkError, InvalidBookmarkTimeError,
        BookmarkOutOfRangeError (a negative time), BookmarkError (no bookmarks or a malformed entry).
    """
    if isinstance(value, Mapping):
        return _collect(list(value.items()), source)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("bookmarks must be a mapping, a sequence of (name, seconds) pairs, or a bookmark file path")
    return _collect([_pair(entry, index) for index, entry in enumerate(value)], source)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise BookmarkFileError(f"duplicate key {key!r}")
        result[key] = item
    return result


def _file_entries(document: JsonValue, source: Path) -> list[tuple[object, object]]:
    if not isinstance(document, dict) or set(document) != _FILE_KEYS:
        raise BookmarkFileError(
            f"Bookmark file must be a JSON object with exactly the keys 'version' and 'bookmarks': {source}"
        )
    version = document["version"]
    if type(version) is not int or version != BOOKMARKS_VERSION:
        raise BookmarkFileError(f"Unsupported bookmark file version {version!r}; expected {BOOKMARKS_VERSION}: {source}")
    entries = document["bookmarks"]
    if not isinstance(entries, list):
        raise BookmarkFileError(f"Bookmark file 'bookmarks' must be a list: {source}")
    pairs: list[tuple[object, object]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise BookmarkFileError(
                f"Bookmark file entry {index} must be an object with exactly the keys 'name' and 'seconds': {source}"
            )
        pairs.append((entry["name"], entry["seconds"]))
    return pairs


def load_bookmarks(path: PathLike) -> Bookmarks:
    """Read and validate a version 1 bookmark file. The file is only read.

    Raises:
        FileNotFoundError: The file does not exist.
        BookmarkFileError: The file is not UTF-8 JSON (a byte order mark is allowed), repeats a JSON key,
            or is not a valid version 1 bookmark file.
        InvalidBookmarkNameError, DuplicateBookmarkError, InvalidBookmarkTimeError, BookmarkOutOfRangeError,
        BookmarkError: As for :func:`parse_bookmarks`.
    """
    source = Path(path_to_string(path)).absolute()
    data = source.read_bytes()
    try:
        document = json_value(json.loads(data.decode(_FILE_ENCODING), object_pairs_hook=_reject_duplicate_keys))
    except BookmarkFileError as exc:
        raise BookmarkFileError(f"Bookmark file repeats a JSON key: {source} ({exc})") from exc
    except ValueError as exc:  # invalid UTF-8 or invalid JSON
        raise BookmarkFileError(f"Bookmark file is not valid UTF-8 JSON: {source} ({exc})") from exc
    return _collect(_file_entries(document, source), str(source))


def bookmarks_from(value: BookmarksInput) -> Bookmarks:
    """Bookmarks from any accepted form: validated bookmarks, a file path, a mapping or (name, seconds) pairs."""
    if isinstance(value, Bookmarks):  # validated again: a Bookmarks can also be constructed directly
        return _collect(list(value.times.items()), value.source)
    if isinstance(value, (str, os.PathLike)):
        path = os.fspath(value)
        if not isinstance(path, str):
            raise TypeError("A bookmark file path must be a text path")
        return load_bookmarks(path)
    return parse_bookmarks(value)
