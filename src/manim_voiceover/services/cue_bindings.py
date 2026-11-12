"""Bind stable cue IDs to local WAV recordings through a versioned JSON manifest.

Manifest format, version 1::

    {
      "version": 1,
      "cues": [
        {"id": "question", "wav": "section_0.wav"},
        {"id": "calculation", "wav": "section_2.wav"},
        {"id": "decision", "wav": "section_3.wav"}
      ]
    }

Each ``wav`` is the base name of a WAV file in the directory that contains the
manifest. The scene chooses which cues it plays and in which order, and may play
a cue more than once; the order of ``cues`` in the file has no meaning. A manifest
may bind cues that a scene does not use, and several cues may share one
recording, but each ID may appear only once.

A base name must name the same file on Linux, macOS and Windows, so path
separators (``/`` and ``\\``), drive letters, control characters, characters and
device names that Windows reserves, and names ending in a dot or space are
rejected. After symbolic links are resolved, the recording must still be inside
the manifest directory.

Recordings are checked and played by :class:`LocalWavService` (S01), so its
validation, source preservation and content-addressed caching apply unchanged.
The SHA-256 and duration recorded when a cue is resolved are evidence of which
recording was selected; the manifest itself does not contain hashes.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from manim_voiceover._typing import JsonValue, json_value
from manim_voiceover.services.base import PathLike, path_to_string
from manim_voiceover.services.local_wav import (
    AudioDecoderDependencyError,
    InvalidRecordingError,
    LocalWavService,
    ensure_decodable,
    read_recording,
)

MANIFEST_VERSION = 1
_MANIFEST_ENCODING = "utf-8-sig"  # UTF-8, accepting the optional Windows BOM.
_CUE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MANIFEST_KEYS = frozenset({"version", "cues"})
_ENTRY_KEYS = frozenset({"id", "wav"})
_SEPARATORS = frozenset("/\\")
_WINDOWS_RESERVED_CHARACTERS = frozenset('<>:"|?*')
# Device names that Windows reserves in any directory and with any extension
# (the same list as Python 3.13's ntpath.isreserved).
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{device}{digit}" for device in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"}
)


class CueManifestError(ValueError):
    """The cue manifest is not a valid version 1 manifest, or a cue cannot be selected from it."""


class DuplicateCueError(CueManifestError):
    """A cue ID appears more than once in a manifest."""


class UnknownCueError(CueManifestError):
    """A requested cue ID is not bound in the manifest."""


class UnsafeRecordingPathError(CueManifestError):
    """A WAV binding is not a portable base name, or its recording resolves outside the manifest directory."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CueManifestError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _character_problem(name: str) -> str | None:
    for character in name:
        if character in _SEPARATORS:
            return f"contains the path separator {character!r}; bind a WAV file name in the manifest directory"
        if ord(character) < 32 or ord(character) == 127:
            return "contains a control character"
        if character in _WINDOWS_RESERVED_CHARACTERS:
            return f"contains {character!r}, which Windows does not allow in file names"
    return None


def _name_problem(name: str) -> str | None:
    if not name:
        return "must not be empty"
    problem = _character_problem(name)
    if problem is not None:
        return problem
    if name[-1] in ". ":
        return "must not end in a dot or space"
    if name.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
        return "uses a device name reserved by Windows"
    if len(name) <= len(".wav") or not name.lower().endswith(".wav"):
        return "must name a .wav file"
    return None


def check_wav_name(value: object) -> str:
    """Return ``value`` if it is a portable WAV base name, for a file in the manifest directory.

    Raises:
        UnsafeRecordingPathError: The value is not a WAV file name that means the
            same file on Linux, macOS and Windows, or it contains a path separator.
    """
    if not isinstance(value, str):
        raise UnsafeRecordingPathError(f"Unsafe WAV name {value!r}: must be a string")
    problem = _name_problem(value)
    if problem is not None:
        raise UnsafeRecordingPathError(f"Unsafe WAV name {value!r}: {problem}")
    return value


def _parse_entry(entry: JsonValue, index: int, source: Path) -> tuple[str, str]:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        raise CueManifestError(f"Cue manifest entry {index} must be an object with exactly the keys 'id' and 'wav': {source}")
    cue_id, wav = entry["id"], entry["wav"]
    if not isinstance(cue_id, str) or _CUE_ID.fullmatch(cue_id) is None:
        raise CueManifestError(
            f"Cue manifest entry {index} has an invalid ID {cue_id!r}; use 1-64 lowercase ASCII letters, "
            f"digits or underscores, starting with a letter: {source}"
        )
    try:
        wav_name = check_wav_name(wav)
    except UnsafeRecordingPathError as exc:
        exc.add_note(f"Cue {cue_id!r} in cue manifest {source}")
        raise
    return cue_id, wav_name


def _collect_bindings(cues: list[JsonValue], source: Path) -> dict[str, str]:
    bindings: dict[str, str] = {}
    first_index: dict[str, int] = {}
    for index, entry in enumerate(cues):
        cue_id, wav = _parse_entry(entry, index, source)
        if cue_id in bindings:
            raise DuplicateCueError(
                f"Cue ID {cue_id!r} appears more than once (entries {first_index[cue_id]} and {index}): {source}"
            )
        bindings[cue_id] = wav
        first_index[cue_id] = index
    return bindings


def _parse_bindings(document: JsonValue, source: Path) -> dict[str, str]:
    if not isinstance(document, dict) or set(document) != _MANIFEST_KEYS:
        raise CueManifestError(f"Cue manifest must be a JSON object with exactly the keys 'version' and 'cues': {source}")
    version = document["version"]
    if type(version) is not int or version != MANIFEST_VERSION:
        raise CueManifestError(f"Unsupported cue manifest version {version!r}; expected {MANIFEST_VERSION}: {source}")
    cues = document["cues"]
    if not isinstance(cues, list) or not cues:
        raise CueManifestError(f"Cue manifest 'cues' must be a non-empty list: {source}")
    return _collect_bindings(cues, source)


@dataclass(frozen=True)
class ResolvedCue:
    """A cue whose recording exists, is inside the manifest directory and passed S01 validation."""

    cue_id: str
    recording: Path
    sha256: str
    duration: float

    def service(self, **kwargs: object) -> LocalWavService:
        """A :class:`LocalWavService` for this cue's recording; ``kwargs`` are passed on (for example ``cache_dir``)."""
        return LocalWavService(self.recording, **kwargs)


@dataclass(frozen=True)
class CueManifest:
    """A parsed manifest: cue IDs bound to WAV base names in the manifest directory."""

    path: Path
    bindings: Mapping[str, str]

    def resolve(self, cue_order: Sequence[str]) -> tuple[ResolvedCue, ...]:
        """Resolve the requested cues, in the requested order, and validate their recordings.

        A cue may be requested more than once; its recording is validated once
        and the same :class:`ResolvedCue` is returned at each position. Every
        requested recording is validated before this returns, so an invalid
        selection fails before anything is rendered or cached. Unused bindings
        are not opened.

        Raises:
            UnknownCueError: A requested cue is not bound in the manifest.
            UnsafeRecordingPathError: A recording resolves outside the manifest directory.
            FileNotFoundError, InvalidRecordingError, AudioDecoderDependencyError:
                From S01's recording validation, with a note naming the cue.
        """
        self._check_order(cue_order)
        resolved = {cue_id: self._resolve_cue(cue_id) for cue_id in dict.fromkeys(cue_order)}
        return tuple(resolved[cue_id] for cue_id in cue_order)

    def _check_order(self, cue_order: Sequence[str]) -> None:
        if isinstance(cue_order, str):
            raise TypeError("cue_order must be a sequence of cue IDs, not a single string")
        if not cue_order:
            raise CueManifestError("cue_order must name at least one cue")
        for cue_id in cue_order:
            if cue_id not in self.bindings:
                available = ", ".join(sorted(self.bindings))
                raise UnknownCueError(f"Unknown cue {cue_id!r}; cue manifest {self.path} binds: {available}")

    def _resolve_cue(self, cue_id: str) -> ResolvedCue:
        directory = self.path.parent.resolve()
        recording = (directory / check_wav_name(self.bindings[cue_id])).resolve()
        if not recording.is_relative_to(directory):
            raise UnsafeRecordingPathError(
                f"Recording for cue {cue_id!r} resolves outside the cue manifest directory {directory}: {recording}"
            )
        try:
            validated = read_recording(recording)
            ensure_decodable(validated, recording)
        except (OSError, InvalidRecordingError, AudioDecoderDependencyError) as exc:
            exc.add_note(f"Cue {cue_id!r} in cue manifest {self.path}")
            raise
        return ResolvedCue(cue_id=cue_id, recording=recording, sha256=validated.sha256, duration=validated.duration)


def load_cue_manifest(path: PathLike) -> CueManifest:
    """Read and validate a version 1 cue manifest. Recordings are not opened until cues are resolved.

    Raises:
        FileNotFoundError: The manifest does not exist.
        CueManifestError: The manifest is not UTF-8 JSON (a byte order mark is allowed), repeats a JSON key,
            or is not a valid version 1 manifest.
        DuplicateCueError: A cue ID appears more than once.
        UnsafeRecordingPathError: A WAV binding, used or not, is not a portable base name.
    """
    source = Path(path_to_string(path)).absolute()
    data = source.read_bytes()
    try:
        document = json_value(json.loads(data.decode(_MANIFEST_ENCODING), object_pairs_hook=_reject_duplicate_keys))
    except ValueError as exc:  # invalid UTF-8, invalid JSON or a repeated key
        raise CueManifestError(f"Cue manifest is not valid UTF-8 JSON: {source} ({exc})") from exc
    return CueManifest(path=source, bindings=MappingProxyType(_parse_bindings(document, source)))
