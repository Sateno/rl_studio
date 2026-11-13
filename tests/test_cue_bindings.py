"""Cue Bindings: stable cue IDs bound to local WAV recordings by a version 1 JSON manifest.

Sections:
- manifest format and structure;
- portable WAV base names, checked for POSIX and Windows meaning on any host;
- symbolic-link containment on the host file system;
- cue selection (the scene's order, repeated requests, unknown/duplicate IDs, unused and shared bindings)
  and reuse of the local WAV recording validation;
- playback and local WAV caching across a calculation rebinding, A -> B -> B, with one shared cache;
- the repository's lesson recordings, and pins that keep the original lesson unchanged.

Most playback tests use a MOCK scene (as in test_local_wav_service.py), which records
add_sound/wait/subcaption calls without decoding audio. The test marked ``integration``
runs a real Manim render.
"""

import hashlib
import itertools
import json
import ntpath
import os
import posixpath
import shutil
import sys
import wave
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import av
import numpy as np
import pytest
from manim import tempconfig

import manim_voiceover.services.cue_bindings as cue_bindings
import manim_voiceover.services.local_wav as local_wav
from manim_voiceover import VoiceoverScene
from manim_voiceover.defaults import DEFAULT_VOICEOVER_CACHE_JSON_FILENAME
from manim_voiceover.services.cue_bindings import (
    MANIFEST_VERSION,
    CueManifest,
    CueManifestError,
    DuplicateCueError,
    ResolvedCue,
    UnknownCueError,
    UnsafeRecordingPathError,
    check_wav_name,
    load_cue_manifest,
)
from manim_voiceover.services.local_wav import AudioDecoderDependencyError, InvalidRecordingError, LocalWavService
from tests.test_local_wav_service import (
    FFMPEG_LAYOUTS,
    INVALID_RECORDINGS,
    audio_files,
    file_identity,
    sha256,
    tone_wav,
    write_pcm16,
)

ROOT = Path(__file__).resolve().parents[1]
ORDER = ("question", "calculation", "decision")
TRANSCRIPTS = {
    "question": "Which uncertain choice is better?",
    "calculation": "One quarter times zero, plus three quarters times four, gives an expected reward of three.",
    "decision": "Three is greater than two.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_manifest(directory, bindings, name="cues.json", version=MANIFEST_VERSION):
    """Write a manifest; ``bindings`` is a sequence of (id, wav) pairs, in file order."""
    path = Path(directory) / name
    document = {"version": version, "cues": [{"id": cue_id, "wav": wav} for cue_id, wav in bindings]}
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def write_raw(directory, text, name="cues.json"):
    path = Path(directory) / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def write_tone(path, frequency, seconds, rate=8000):
    """Mono 16-bit PCM sine tone: a distinct, identifiable recording."""
    t = np.arange(int(seconds * rate)) / rate
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes((np.sin(2 * np.pi * frequency * t) * 8000).astype("<i2").tobytes())
    return path


@pytest.fixture
def lesson(tmp_path):
    """Four distinct recordings (different bytes and durations) and manifest A."""
    directory = tmp_path / "lesson"
    directory.mkdir()
    write_pcm16(directory / "question.wav", frames=4000, value=1000)  # 0.5 s
    write_pcm16(directory / "calculation.wav", frames=6000, value=2000)  # 0.75 s
    write_pcm16(directory / "decision.wav", frames=8000, value=3000)  # 1.0 s
    write_pcm16(directory / "calculation_alt.wav", frames=10000, value=4000)  # 1.25 s
    manifest = write_manifest(
        directory,
        [("question", "question.wav"), ("calculation", "calculation.wav"), ("decision", "decision.wav")],
    )
    return SimpleNamespace(directory=directory, manifest=manifest)


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


def play_cues(cues, cache_dir):
    """Play resolved cues in a MOCK VoiceoverScene, one voiceover block per cue (validation only)."""
    scene = VoiceoverScene.__new__(VoiceoverScene)
    scene.renderer = SimpleNamespace(time=0.0, skip_animations=False, _original_skipping_status=False)
    scene.sounds, scene.waits, scene.captions, scene.tracker_durations = [], [], [], []
    scene.add_sound = lambda path: scene.sounds.append(Path(path))
    scene.wait = lambda duration: scene.waits.append(duration)
    scene.add_subcaption = lambda text, duration, offset: scene.captions.append(text)
    for cue in cues:
        scene.set_speech_service(cue.service(cache_dir=cache_dir))
        with scene.voiceover(text=TRANSCRIPTS[cue.cue_id]) as tracker:
            scene.tracker_durations.append(tracker.duration)
    return scene


def cache_entries(cache_dir):
    return json.loads((Path(cache_dir) / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text(encoding="utf-8"))


def tree_identity(directory):
    return {str(path.relative_to(directory)): file_identity(path) for path in sorted(Path(directory).rglob("*"))}


def record_cache_writes(monkeypatch):
    """Record every cached copy that LocalWavService writes, while still writing it."""
    writes = []
    original = local_wav._write_copy

    def recording_write(destination, data):
        writes.append(Path(destination).name)
        original(destination, data)

    monkeypatch.setattr(local_wav, "_write_copy", recording_write)
    return writes


# ---------------------------------------------------------------------------
# Manifest format and structure
# ---------------------------------------------------------------------------


def test_manifest_binds_ids_to_wav_paths_as_written(lesson):
    manifest = load_cue_manifest(lesson.manifest)

    assert isinstance(manifest, CueManifest)
    assert manifest.path == lesson.manifest
    assert dict(manifest.bindings) == {
        "question": "question.wav",
        "calculation": "calculation.wav",
        "decision": "decision.wav",
    }
    with pytest.raises(TypeError):
        manifest.bindings["question"] = "decision.wav"


def test_relative_manifest_path_is_anchored_when_loaded(lesson, monkeypatch):
    # mutmut resolves its configured source roots at each instrumented call.
    # Keep those roots anchored while this test deliberately changes cwd.
    if "mutmut.configuration" in sys.modules:
        configuration = sys.modules["mutmut.configuration"].Config.get()
        monkeypatch.setattr(configuration, "source_paths", [path.resolve() for path in configuration.source_paths])
    monkeypatch.chdir(lesson.directory)
    manifest = load_cue_manifest("cues.json")
    monkeypatch.chdir(lesson.directory.parent)

    assert manifest.path.resolve() == lesson.manifest.resolve()
    expected = [(lesson.directory / f"{cue}.wav").resolve() for cue in ORDER]
    assert [cue.recording for cue in manifest.resolve(ORDER)] == expected


def test_path_like_manifest_argument_is_accepted(lesson):
    assert load_cue_manifest(os.fspath(lesson.manifest)).bindings == load_cue_manifest(lesson.manifest).bindings


def test_utf8_byte_order_mark_is_accepted(lesson):
    # Windows PowerShell 5.1 writes UTF-8 files with a byte order mark.
    lesson.manifest.write_bytes(b"\xef\xbb\xbf" + lesson.manifest.read_bytes())
    assert load_cue_manifest(lesson.manifest).bindings["question"] == "question.wav"


def test_manifest_loading_does_not_open_recordings(tmp_path):
    manifest = write_manifest(tmp_path, [("question", "missing_question.wav")])
    assert load_cue_manifest(manifest).bindings == {"question": "missing_question.wav"}


def test_missing_manifest_is_a_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cue_manifest(tmp_path / "absent.json")


ENTRY = '{"id": "question", "wav": "question.wav"}'
STRUCTURE_ERRORS = {
    "truncated JSON": ('{"version": 1, "cues": [', "is not valid UTF-8 JSON"),
    "empty file": ("", "is not valid UTF-8 JSON"),
    "invalid UTF-8": (b'{"version": 1, "cues": [{"id": "q\xff", "wav": "q.wav"}]}', "is not valid UTF-8 JSON"),
    "repeated top-level key": (f'{{"version": 1, "version": 1, "cues": [{ENTRY}]}}', "duplicate key 'version'"),
    "repeated cues key": (f'{{"version": 1, "cues": [], "cues": [{ENTRY}]}}', "duplicate key 'cues'"),
    "repeated entry key": ('{"version": 1, "cues": [{"id": "a", "id": "b", "wav": "q.wav"}]}', "duplicate key 'id'"),
    "top-level list": (f"[{ENTRY}]", "exactly the keys 'version' and 'cues'"),
    "top-level string": ('"cues"', "exactly the keys 'version' and 'cues'"),
    "missing version": (f'{{"cues": [{ENTRY}]}}', "exactly the keys 'version' and 'cues'"),
    "missing cues": ('{"version": 1}', "exactly the keys 'version' and 'cues'"),
    "unknown top-level key": (f'{{"version": 1, "cues": [{ENTRY}], "title": "x"}}', "exactly the keys 'version' and 'cues'"),
    "version 0": (f'{{"version": 0, "cues": [{ENTRY}]}}', "Unsupported cue manifest version 0;"),
    "version 2": (f'{{"version": 2, "cues": [{ENTRY}]}}', "Unsupported cue manifest version 2;"),
    "version as string": (f'{{"version": "1", "cues": [{ENTRY}]}}', "Unsupported cue manifest version '1';"),
    "version as float": (f'{{"version": 1.0, "cues": [{ENTRY}]}}', "Unsupported cue manifest version 1.0;"),
    "version as boolean": (f'{{"version": true, "cues": [{ENTRY}]}}', "Unsupported cue manifest version True;"),
    "version null": (f'{{"version": null, "cues": [{ENTRY}]}}', "Unsupported cue manifest version None;"),
    "empty cue list": ('{"version": 1, "cues": []}', "'cues' must be a non-empty list"),
    "cues as object": (f'{{"version": 1, "cues": {{"question": {ENTRY}}}}}', "'cues' must be a non-empty list"),
    "cues as string": ('{"version": 1, "cues": "question.wav"}', "'cues' must be a non-empty list"),
    "entry as string": ('{"version": 1, "cues": ["question.wav"]}', "entry 0 must be an object with exactly the keys"),
    "entry as list": ('{"version": 1, "cues": [["question", "q.wav"]]}', "entry 0 must be an object with exactly the keys"),
    "entry without wav": ('{"version": 1, "cues": [{"id": "question"}]}', "entry 0 must be an object with exactly the keys"),
    "entry without id": ('{"version": 1, "cues": [{"wav": "q.wav"}]}', "entry 0 must be an object with exactly the keys"),
    "entry with hash": (
        '{"version": 1, "cues": [{"id": "question", "wav": "q.wav", "sha256": "00"}]}',
        "entry 0 must be an object with exactly the keys",
    ),
    "second entry invalid": (f'{{"version": 1, "cues": [{ENTRY}, {{}}]}}', "entry 1 must be an object with exactly the keys"),
}


@pytest.mark.parametrize("case", STRUCTURE_ERRORS)
def test_invalid_manifest_structure_is_rejected(tmp_path, case):
    text, message = STRUCTURE_ERRORS[case]
    path = write_raw(tmp_path, text)

    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)

    assert type(error.value) is CueManifestError
    assert message in str(error.value)
    assert str(error.value).endswith(str(path)) or f"{path} (" in str(error.value)


def test_structure_error_messages_are_exact(tmp_path):
    path = write_raw(tmp_path, f'{{"version": 2, "cues": [{ENTRY}]}}')
    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)
    assert str(error.value) == f"Unsupported cue manifest version 2; expected 1: {path}"

    path.write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)
    assert str(error.value) == f"Cue manifest must be a JSON object with exactly the keys 'version' and 'cues': {path}"

    path.write_text('{"version": 1, "cues": []}', encoding="utf-8")
    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)
    assert str(error.value) == f"Cue manifest 'cues' must be a non-empty list: {path}"

    path.write_text('{"version": 1, "cues": [{"id": "question"}]}', encoding="utf-8")
    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)
    assert str(error.value) == f"Cue manifest entry 0 must be an object with exactly the keys 'id' and 'wav': {path}"

    path.write_text('{"version": 1, "version": 1, "cues": []}', encoding="utf-8")
    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)
    assert str(error.value) == f"Cue manifest is not valid UTF-8 JSON: {path} (duplicate key 'version')"
    assert isinstance(error.value.__cause__, CueManifestError)


VALID_IDS = ["question", "q", "calculation_2", "a" * 64]
INVALID_IDS = [
    "",
    "Question",
    "QUESTION",
    " question",
    "question ",
    "ques-tion",
    "ques tion",
    "1st",
    "_question",
    "quest\u0456on",
    "caf\u00e9",
    "a" * 65,
    1,
    None,
    True,
    ["question"],
]


@pytest.mark.parametrize("cue_id", VALID_IDS)
def test_valid_cue_ids(tmp_path, cue_id):
    assert list(load_cue_manifest(write_manifest(tmp_path, [(cue_id, "q.wav")])).bindings) == [cue_id]


def stable_case_id(value):
    # Stable ASCII node IDs let repeated in-process pytest runs (mutmut) select
    # cases containing Unicode, brackets and backslashes without re-escaping.
    return type(value).__name__ + "-" + hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:12]


@pytest.mark.parametrize("cue_id", INVALID_IDS, ids=stable_case_id)
def test_invalid_cue_ids_are_rejected(tmp_path, cue_id):
    path = write_manifest(tmp_path, [("question", "q.wav"), (cue_id, "q.wav")])

    with pytest.raises(CueManifestError) as error:
        load_cue_manifest(path)

    assert type(error.value) is CueManifestError
    assert str(error.value) == (
        f"Cue manifest entry 1 has an invalid ID {cue_id!r}; use 1-64 lowercase ASCII letters, "
        f"digits or underscores, starting with a letter: {path}"
    )


@pytest.mark.parametrize(
    "bindings",
    [
        [("question", "a.wav"), ("decision", "d.wav"), ("question", "b.wav")],
        [("question", "a.wav"), ("decision", "d.wav"), ("question", "a.wav")],  # even with the same recording
    ],
    ids=["different recordings", "same recording"],
)
def test_duplicate_cue_id_is_rejected(tmp_path, bindings):
    path = write_manifest(tmp_path, bindings)

    with pytest.raises(DuplicateCueError) as error:
        load_cue_manifest(path)

    assert str(error.value) == f"Cue ID 'question' appears more than once (entries 0 and 2): {path}"


def test_duplicate_unused_cue_id_is_rejected(lesson):
    path = write_manifest(
        lesson.directory,
        [*[(cue, f"{cue}.wav") for cue in ORDER], ("recap", "question.wav"), ("recap", "decision.wav")],
        name="dup.json",
    )
    with pytest.raises(DuplicateCueError, match=r"^Cue ID 'recap' appears more than once \(entries 3 and 4\)"):
        load_cue_manifest(path)


# ---------------------------------------------------------------------------
# Portable WAV base names (pure checks: identical on every host OS)
# ---------------------------------------------------------------------------

VALID_NAMES = [
    "section_0.wav",
    "calculation_alt.wav",
    "take 2.wav",
    "TAKE.WAV",
    "take.Wav",
    "v1.2.take.wav",
    "na\u00efve.wav",
    ".hidden.wav",
    "console.wav",  # starts with CON, but is not a device name
    "CONX.wav",  # stripping more than ASCII spaces would incorrectly reserve this name
    "CON\u00a0.wav",  # a non-breaking space is not a trailing ASCII space
    "com10.wav",
    "lpt0.wav",
    "nul_take.wav",
    "con_take.wav",
]

# Rejected bindings that, joined naively to the manifest directory, would leave it on POSIX or on Windows.
ESCAPES = {
    "/etc/x.wav": "posix",
    "../x.wav": "posix",
    "a/../../x.wav": "posix",
    "C:\\x.wav": "windows",
    "C:/x.wav": "windows",
    "D:x.wav": "windows",  # drive-relative on another drive; "C:x.wav" is rejected too, but stays inside C:\lesson
    "\\\\server\\share\\x.wav": "windows",
    "//server/share/x.wav": "windows",
    "\\x.wav": "windows",
    "..\\x.wav": "windows",
    "a\\..\\..\\x.wav": "windows",
}

SLASH = "contains the path separator '/'"
BACKSLASH = "contains the path separator '\\\\'"
UNSAFE_REASONS = {
    "clipX": "must name a .wav file",
    # Any path separator: bindings are base names only.
    "takes/calculation.wav": SLASH,
    "a/b/c.wav": SLASH,
    "/etc/x.wav": SLASH,
    "//server/share/x.wav": SLASH,
    "../x.wav": SLASH,
    "a/../../x.wav": SLASH,
    "./x.wav": SLASH,
    "x.wav/": SLASH,
    "takes\\x.wav": BACKSLASH,
    "\\x.wav": BACKSLASH,
    "\\\\server\\share\\x.wav": BACKSLASH,
    "..\\x.wav": BACKSLASH,
    "a\\..\\..\\x.wav": BACKSLASH,
    # Drive letters and NTFS alternate data streams.
    "C:\\x.wav": "contains ':'",
    "C:/x.wav": "contains ':'",
    "C:x.wav": "contains ':'",
    "D:x.wav": "contains ':'",
    "x.wav:stream": "contains ':'",
    # Other characters that Windows does not allow in file names.
    "x<1>.wav": "contains '<'",
    "x>.wav": "contains '>'",
    'x".wav': "contains '\"'",
    "x|y.wav": "contains '|'",
    "x?.wav": "contains '?'",
    "x*.wav": "contains '*'",
    # Control characters.
    "x\x00.wav": "contains a control character",
    "x\n.wav": "contains a control character",
    "x\t.wav": "contains a control character",
    "x\x1f.wav": "contains a control character",
    "x\x7f.wav": "contains a control character",
    # Empty, current and parent directory names.
    "": "must not be empty",
    ".": "must not end in a dot or space",
    "..": "must not end in a dot or space",
    # Names that Windows silently strips.
    "x.wav.": "must not end in a dot or space",
    "x.wav ": "must not end in a dot or space",
    # Device names reserved by Windows in every directory, with any extension.
    "CON.wav": "uses a device name reserved by Windows",
    "con.wav": "uses a device name reserved by Windows",
    "Aux.wav": "uses a device name reserved by Windows",
    "PRN.wav": "uses a device name reserved by Windows",
    "nul.take.wav": "uses a device name reserved by Windows",
    "COM1.wav": "uses a device name reserved by Windows",
    "com9.wav": "uses a device name reserved by Windows",
    "LPT1.wav": "uses a device name reserved by Windows",
    "lpt9.wav": "uses a device name reserved by Windows",
    "COM\u00b9.wav": "uses a device name reserved by Windows",
    "LPT\u00b3.wav": "uses a device name reserved by Windows",
    "CONIN$.wav": "uses a device name reserved by Windows",
    "conout$.wav": "uses a device name reserved by Windows",
    "CON .wav": "uses a device name reserved by Windows",
    # Not a WAV file name.
    "x.mp3": "must name a .wav file",
    "x": "must name a .wav file",
    ".wav": "must name a .wav file",
    "x.wav.bak": "must name a .wav file",
    "x.wave": "must name a .wav file",
}


@pytest.mark.parametrize("name", VALID_NAMES)
@pytest.mark.parametrize(
    "flavour, directory",
    [(PurePosixPath, "/srv/lesson/assets"), (PureWindowsPath, "C:/Fixtures/lesson/assets")],
    ids=["posix", "windows"],
)
def test_valid_name_is_one_file_in_the_manifest_directory_on_posix_and_windows(name, flavour, directory):
    assert check_wav_name(name) == name
    assert flavour(name).parts == (name,) and not flavour(name).anchor
    assert flavour(directory).joinpath(name).parent == flavour(directory)


@pytest.mark.parametrize("value", UNSAFE_REASONS, ids=stable_case_id)
def test_unsafe_name_is_rejected_with_its_reason(value):
    with pytest.raises(UnsafeRecordingPathError) as error:
        check_wav_name(value)

    message = str(error.value)
    assert message.startswith(f"Unsafe WAV name {value!r}: ")
    assert UNSAFE_REASONS[value] in message


@pytest.mark.parametrize(
    "value, message",
    [
        ("takes/x.wav", "contains the path separator '/'; bind a WAV file name in the manifest directory"),
        ("takes\\x.wav", "contains the path separator '\\\\'; bind a WAV file name in the manifest directory"),
        ("C:x.wav", "contains ':', which Windows does not allow in file names"),
        ("x\x00.wav", "contains a control character"),
        ("", "must not be empty"),
        ("x.wav.", "must not end in a dot or space"),
        ("CON.wav", "uses a device name reserved by Windows"),
        ("x.mp3", "must name a .wav file"),
    ],
    ids=stable_case_id,
)
def test_unsafe_name_messages_are_exact(value, message):
    with pytest.raises(UnsafeRecordingPathError) as error:
        check_wav_name(value)
    assert str(error.value) == f"Unsafe WAV name {value!r}: {message}"


@pytest.mark.parametrize("value", [1, None, True, ["x.wav"], {"name": "x.wav"}], ids=stable_case_id)
def test_non_string_name_is_rejected(value):
    with pytest.raises(UnsafeRecordingPathError) as error:
        check_wav_name(value)
    assert str(error.value) == f"Unsafe WAV name {value!r}: must be a string"


@pytest.mark.parametrize("value", ESCAPES, ids=stable_case_id)
def test_rejected_escape_would_leave_the_directory_on_its_platform(value):
    """Each escape case is a real escape for the named platform, so the rule matters there."""
    module, directory = (posixpath, "/srv/lesson") if ESCAPES[value] == "posix" else (ntpath, "C:\\lesson")
    joined = module.normpath(module.join(directory, value))

    assert module.normcase(joined) != module.normcase(directory)
    assert not module.normcase(joined).startswith(module.normcase(directory) + module.sep)
    with pytest.raises(UnsafeRecordingPathError):
        check_wav_name(value)


def test_backslash_would_name_different_files_on_posix_and_windows():
    assert PurePosixPath("takes\\x.wav").parts != PureWindowsPath("takes\\x.wav").parts
    with pytest.raises(UnsafeRecordingPathError):
        check_wav_name("takes\\x.wav")


def test_unsafe_name_in_an_unused_entry_rejects_the_manifest(lesson):
    path = write_manifest(
        lesson.directory,
        [*[(cue, f"{cue}.wav") for cue in ORDER], ("unused", "../outside.wav")],
        name="unsafe.json",
    )
    with pytest.raises(UnsafeRecordingPathError) as error:
        load_cue_manifest(path)

    assert str(error.value) == (
        "Unsafe WAV name '../outside.wav': contains the path separator '/'; bind a WAV file name in the manifest directory"
    )
    assert error.value.__notes__ == [f"Cue 'unused' in cue manifest {path}"]


def test_resolution_rechecks_names_of_a_manifest_built_without_the_loader(lesson):
    manifest = CueManifest(path=lesson.manifest, bindings={"question": "../question.wav"})

    with pytest.raises(UnsafeRecordingPathError, match="^Unsafe WAV name '../question.wav': contains the path separator"):
        manifest.resolve(["question"])


# ---------------------------------------------------------------------------
# Symbolic links on the host file system
# ---------------------------------------------------------------------------


def symlink_or_skip(link, target, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:  # e.g. Windows without symlink privilege
        pytest.skip(f"cannot create symbolic links here: {exc}")


def test_symlink_to_a_recording_outside_the_manifest_directory_is_rejected(tmp_path):
    outside = write_pcm16(tmp_path / "outside.wav", frames=800)
    directory = tmp_path / "lesson"
    directory.mkdir()
    symlink_or_skip(directory / "question.wav", outside)
    manifest = load_cue_manifest(write_manifest(directory, [("question", "question.wav")]))

    with pytest.raises(UnsafeRecordingPathError) as error:
        manifest.resolve(["question"])

    assert str(error.value) == (
        f"Recording for cue 'question' resolves outside the cue manifest directory {directory.resolve()}: {outside.resolve()}"
    )


def test_symlink_that_stays_inside_the_manifest_directory_is_followed(tmp_path):
    directory = tmp_path / "lesson"
    (directory / "takes").mkdir(parents=True)
    target = write_pcm16(directory / "takes" / "question_v3.wav", frames=800)
    symlink_or_skip(directory / "question.wav", target)
    manifest = load_cue_manifest(write_manifest(directory, [("question", "question.wav")]))

    (cue,) = manifest.resolve(["question"])

    assert cue.recording == target.resolve()
    assert cue.sha256 == sha256(target)


def test_manifest_directory_reached_through_a_symlink_is_allowed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    write_pcm16(real / "question.wav", frames=800)
    write_manifest(real, [("question", "question.wav")])
    symlink_or_skip(tmp_path / "alias", real, directory=True)

    (cue,) = load_cue_manifest(tmp_path / "alias" / "cues.json").resolve(["question"])

    assert cue.recording == (real / "question.wav").resolve()


# ---------------------------------------------------------------------------
# Cue selection and local WAV recording validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("file_order", list(itertools.permutations(ORDER)), ids="-".join)
def test_scene_order_decides_playback_order_not_the_file(lesson, file_order):
    path = write_manifest(lesson.directory, [(cue, f"{cue}.wav") for cue in file_order], name="shuffled.json")
    manifest = load_cue_manifest(path)

    assert [cue.cue_id for cue in manifest.resolve(ORDER)] == list(ORDER)
    assert [cue.cue_id for cue in manifest.resolve(ORDER[::-1])] == list(ORDER[::-1])
    assert [cue.recording.name for cue in manifest.resolve(ORDER)] == [f"{cue}.wav" for cue in ORDER]


def test_resolved_cue_records_hash_and_duration_as_evidence(lesson):
    cues = load_cue_manifest(lesson.manifest).resolve(ORDER)

    assert all(isinstance(cue, ResolvedCue) for cue in cues)
    assert [cue.recording for cue in cues] == [(lesson.directory / f"{cue}.wav").resolve() for cue in ORDER]
    assert [cue.sha256 for cue in cues] == [sha256(lesson.directory / f"{cue}.wav") for cue in ORDER]
    assert [cue.duration for cue in cues] == [0.5, 0.75, 1.0]


def test_unknown_cue_is_rejected(lesson):
    manifest = load_cue_manifest(lesson.manifest)

    with pytest.raises(UnknownCueError) as error:
        manifest.resolve(["question", "summary", "decision"])

    assert str(error.value) == f"Unknown cue 'summary'; cue manifest {lesson.manifest} binds: calculation, decision, question"


def test_cue_ids_are_case_sensitive(lesson):
    with pytest.raises(UnknownCueError, match="^Unknown cue 'Question';"):
        load_cue_manifest(lesson.manifest).resolve(["Question"])


def test_scene_may_request_a_cue_more_than_once(lesson, cache_dir, monkeypatch):
    reads = []
    original_read = cue_bindings.read_recording
    monkeypatch.setattr(cue_bindings, "read_recording", lambda path: reads.append(Path(path).name) or original_read(path))

    cues = load_cue_manifest(lesson.manifest).resolve(["question", "calculation", "question"])

    assert [cue.cue_id for cue in cues] == ["question", "calculation", "question"]
    assert cues[0] is cues[2]
    assert reads == ["question.wav", "calculation.wav"]  # each distinct recording is validated once
    writes = record_cache_writes(monkeypatch)
    scene = play_cues(cues, cache_dir)
    assert scene.sounds[0] == scene.sounds[2] and len(writes) == 2  # the repeat reuses its cached copy
    assert scene.waits == [pytest.approx(0.5), pytest.approx(0.75), pytest.approx(0.5)]


def test_repeated_request_of_an_unknown_cue_is_still_rejected(lesson):
    with pytest.raises(UnknownCueError, match="^Unknown cue 'summary';"):
        load_cue_manifest(lesson.manifest).resolve(["question", "summary", "summary"])


def test_cue_order_must_be_a_non_empty_sequence(lesson):
    manifest = load_cue_manifest(lesson.manifest)

    with pytest.raises(TypeError, match="^cue_order must be a sequence of cue IDs, not a single string$"):
        manifest.resolve("question")
    with pytest.raises(CueManifestError) as error:
        manifest.resolve([])
    assert type(error.value) is CueManifestError
    assert str(error.value) == "cue_order must name at least one cue"


def test_unused_bindings_are_allowed_and_never_opened(lesson):
    path = write_manifest(
        lesson.directory,
        [
            *[(cue, f"{cue}.wav") for cue in ORDER],
            ("method", "section_1_missing.wav"),
            ("recap", "not_audio.wav"),
        ],
        name="extra.json",
    )
    (lesson.directory / "not_audio.wav").write_bytes(b"plain text")

    cues = load_cue_manifest(path).resolve(ORDER)

    assert [cue.cue_id for cue in cues] == list(ORDER)


def test_two_cues_may_share_one_recording(lesson, cache_dir):
    path = write_manifest(
        lesson.directory,
        [("question", "question.wav"), ("calculation", "calculation.wav"), ("decision", "question.wav")],
        name="shared.json",
    )
    cues = load_cue_manifest(path).resolve(ORDER)

    assert cues[0].recording == cues[2].recording and cues[0].sha256 == cues[2].sha256
    scene = play_cues(cues, cache_dir)
    source = (lesson.directory / "question.wav").read_bytes()
    assert scene.sounds[0].read_bytes() == scene.sounds[2].read_bytes() == source


def test_missing_recording_uses_local_wav_error_with_the_cue_named(lesson):
    (lesson.directory / "calculation.wav").unlink()
    manifest = load_cue_manifest(lesson.manifest)

    with pytest.raises(FileNotFoundError) as error:
        manifest.resolve(ORDER)

    recording = lesson.directory.resolve() / "calculation.wav"
    assert str(error.value) == f"Recording not found or not a regular file: {recording}"
    assert error.value.__notes__ == [f"Cue 'calculation' in cue manifest {lesson.manifest}"]


def test_directory_instead_of_recording_is_rejected(lesson):
    (lesson.directory / "calculation.wav").unlink()
    (lesson.directory / "calculation.wav").mkdir()

    with pytest.raises(FileNotFoundError, match="not found or not a regular file"):
        load_cue_manifest(lesson.manifest).resolve(ORDER)


@pytest.mark.parametrize("case", INVALID_RECORDINGS)
def test_invalid_recording_uses_local_wav_validation(lesson, case):
    content, error_type, message = INVALID_RECORDINGS[case]
    (lesson.directory / "decision.wav").write_bytes(content)
    manifest = load_cue_manifest(lesson.manifest)

    with pytest.raises(error_type, match=message) as error:
        manifest.resolve(ORDER)

    assert error.value.__notes__ == [f"Cue 'decision' in cue manifest {lesson.manifest}"]
    with pytest.raises(error_type, match=message):  # exactly what LocalWavService reports for the same file
        LocalWavService(lesson.directory / "decision.wav", cache_dir=lesson.directory.parent / "unused-cache")


@pytest.mark.parametrize("layout", FFMPEG_LAYOUTS)
def test_recording_that_needs_missing_ffmpeg_uses_local_wav_dependency_error(lesson, monkeypatch, layout):
    (lesson.directory / "question.wav").write_bytes(FFMPEG_LAYOUTS[layout](tone_wav(1)))
    manifest = load_cue_manifest(lesson.manifest)

    monkeypatch.setattr(local_wav, "_missing_decoder_tools", lambda: ["ffmpeg", "ffprobe"])
    with pytest.raises(AudioDecoderDependencyError, match="missing: ffmpeg, ffprobe") as error:
        manifest.resolve(ORDER)
    assert error.value.__notes__ == [f"Cue 'question' in cue manifest {lesson.manifest}"]

    monkeypatch.setattr(local_wav, "_missing_decoder_tools", lambda: [])
    assert manifest.resolve(ORDER)[0].sha256 == sha256(lesson.directory / "question.wav")


def test_every_requested_recording_is_validated_before_any_is_returned(lesson, cache_dir):
    (lesson.directory / "decision.wav").write_bytes(b"")
    manifest = load_cue_manifest(lesson.manifest)

    with pytest.raises(InvalidRecordingError, match="empty"):
        manifest.resolve(ORDER)

    assert not cache_dir.exists()
    assert [cue.cue_id for cue in manifest.resolve(ORDER[:2])] == ["question", "calculation"]


def test_loading_and_resolving_only_read_files(lesson):
    before = tree_identity(lesson.directory)

    load_cue_manifest(lesson.manifest).resolve(ORDER)

    assert tree_identity(lesson.directory) == before


# ---------------------------------------------------------------------------
# Playback and local WAV caching
# ---------------------------------------------------------------------------


def test_each_cue_plays_its_own_recording_for_its_full_duration(lesson, cache_dir):
    cues = load_cue_manifest(lesson.manifest).resolve(ORDER)

    scene = play_cues(cues, cache_dir)

    assert [path.read_bytes() for path in scene.sounds] == [cue.recording.read_bytes() for cue in cues]
    assert all(path.parent == cache_dir for path in scene.sounds)
    assert scene.tracker_durations == [pytest.approx(cue.duration) for cue in cues] == [0.5, 0.75, 1.0]
    assert scene.waits == [pytest.approx(cue.duration) for cue in cues]
    assert " ".join(scene.captions) == " ".join(TRANSCRIPTS[cue] for cue in ORDER)


def test_service_uses_local_wav_cache_key_and_records_the_resolved_hash(lesson, cache_dir):
    cues = load_cue_manifest(lesson.manifest).resolve(ORDER)

    play_cues(cues, cache_dir)

    assert [entry["input_data"] for entry in cache_entries(cache_dir)] == [
        {"input_text": TRANSCRIPTS[cue.cue_id], "service": "local_wav", "config": {"sha256": cue.sha256}} for cue in cues
    ]
    assert isinstance(cues[0].service(cache_dir=cache_dir), LocalWavService)


def test_rebinding_calculation_a_b_b_rewrites_no_neighbouring_copy(lesson, cache_dir, monkeypatch):
    manifest_b = write_manifest(
        lesson.directory,
        [("decision", "decision.wav"), ("calculation", "calculation_alt.wav"), ("question", "question.wav")],
        name="cues_b.json",
    )
    sources_before = tree_identity(lesson.directory)
    a, b = load_cue_manifest(lesson.manifest), load_cue_manifest(manifest_b)
    assert {cue: (a.bindings[cue], b.bindings[cue]) for cue in ORDER if a.bindings[cue] != b.bindings[cue]} == {
        "calculation": ("calculation.wav", "calculation_alt.wav")
    }
    writes = record_cache_writes(monkeypatch)

    # A: three new copies.
    cues_a = a.resolve(ORDER)
    scene_a = play_cues(cues_a, cache_dir)
    copies_a = audio_files(cache_dir)
    assert sorted(writes) == sorted(path.name for path in scene_a.sounds) and len(copies_a) == 3

    # B: only the calculation is new; the neighbouring copies are reused without being rewritten.
    writes.clear()
    cues_b = b.resolve(ORDER)
    scene_b = play_cues(cues_b, cache_dir)
    copies_b = audio_files(cache_dir)
    assert writes == [scene_b.sounds[1].name]
    assert scene_b.sounds[0] == scene_a.sounds[0] and scene_b.sounds[2] == scene_a.sounds[2]
    assert scene_b.sounds[1] != scene_a.sounds[1]
    assert scene_b.sounds[1].read_bytes() == (lesson.directory / "calculation_alt.wav").read_bytes()
    assert {name: copies_b[name] for name in copies_a} == copies_a  # A's copies untouched, including A's calculation
    assert len(copies_b) == 4
    assert scene_b.waits == [pytest.approx(0.5), pytest.approx(1.25), pytest.approx(1.0)]
    assert [cue.sha256 for cue in cues_b] == [
        cues_a[0].sha256,
        sha256(lesson.directory / "calculation_alt.wav"),
        cues_a[2].sha256,
    ]

    # B again: nothing is written and every copy is unchanged.
    writes.clear()
    scene_b2 = play_cues(b.resolve(ORDER), cache_dir)
    assert writes == []
    assert scene_b2.sounds == scene_b.sounds
    assert audio_files(cache_dir) == copies_b

    # One shared cache; every entry records the hash of the recording that was resolved.
    resolved_hashes = [cue.sha256 for cue in (*cues_a, *cues_b, *cues_b)]
    assert [entry["input_data"]["config"]["sha256"] for entry in cache_entries(cache_dir)] == resolved_hashes
    assert tree_identity(lesson.directory) == sources_before


def test_replacing_recording_bytes_without_editing_the_manifest_is_picked_up(lesson, cache_dir):
    """Hashes are evidence, not requirements: the content-addressed replacement of LocalWavService still works."""
    manifest = load_cue_manifest(lesson.manifest)
    first = manifest.resolve(ORDER)
    play_cues(first, cache_dir)
    copies = audio_files(cache_dir)

    write_pcm16(lesson.directory / "calculation.wav", frames=7000, value=2500)
    second = manifest.resolve(ORDER)
    scene = play_cues(second, cache_dir)

    assert second[1].sha256 != first[1].sha256 and second[1].duration == 0.875
    assert [cue.sha256 for cue in (second[0], second[2])] == [first[0].sha256, first[2].sha256]
    assert scene.sounds[1].read_bytes() == (lesson.directory / "calculation.wav").read_bytes()
    assert {name: audio_files(cache_dir)[name] for name in copies} == copies


@pytest.mark.integration
def test_real_render_plays_resolved_cues_in_scene_order(tmp_path):
    directory = tmp_path / "lesson"
    directory.mkdir()
    frequencies = {"question": 300, "calculation": 600, "decision": 900}
    seconds = {"question": 0.6, "calculation": 0.8, "decision": 1.0}
    for cue in ORDER:
        write_tone(directory / f"{cue}.wav", frequencies[cue], seconds[cue])
    manifest = write_manifest(directory, [(cue, f"{cue}.wav") for cue in ORDER[::-1]])
    sources = tree_identity(directory)
    cues = load_cue_manifest(manifest).resolve(ORDER)

    class CueScene(VoiceoverScene):
        def construct(self):
            self.blocks = []
            for cue in cues:
                self.set_speech_service(cue.service())
                with self.voiceover(text=TRANSCRIPTS[cue.cue_id]) as tracker:
                    self.blocks.append((tracker.start_t, tracker.duration))
                self.wait(0.3)

    settings = {
        "media_dir": str(tmp_path / "media"),
        "pixel_height": 36,
        "pixel_width": 64,
        "frame_rate": 10,
        "disable_caching": True,
        "output_file": "cues",
        "verbosity": "ERROR",
        "progress_bar": "none",
    }
    with tempconfig(settings):
        scene = CueScene()
        scene.render()
        movie = Path(scene.renderer.file_writer.movie_file_path)

    assert [duration for _, duration in scene.blocks] == [pytest.approx(seconds[cue]) for cue in ORDER]
    with av.open(str(movie)) as container:
        rate = container.streams.audio[0].rate
        audio = np.concatenate([frame.to_ndarray().mean(axis=0) for frame in container.decode(audio=0)])
    dominant = []
    for start, duration in scene.blocks:
        window = audio[int((start + 0.1) * rate) : int((start + duration - 0.1) * rate)]
        spectrum = np.abs(np.fft.rfft(window))
        dominant.append(float(np.fft.rfftfreq(len(window), 1 / rate)[np.argmax(spectrum)]))
    assert dominant == [pytest.approx(frequencies[cue], abs=5) for cue in ORDER]
    assert tree_identity(directory) == sources


# ---------------------------------------------------------------------------
# The repository's lesson recordings and regression pins
# ---------------------------------------------------------------------------

ASSETS = ROOT / "examples" / "rl_lessons" / "assets"
needs_repository_assets = pytest.mark.skipif(
    not ASSETS.is_dir(), reason="lesson assets are not copied into mutmut's generated worktree"
)
# SHA-256 and frame counts from the export provenance, docs/history/TAIGA_EXPORT_PROVENANCE.json (mono, 16-bit PCM, 22050 Hz).
LESSON_RECORDINGS = {
    "question": ("section_0.wav", "054e095cc4176e37fdfd48a2e8d5024c7b6a634e354d598e2b37f861c934a6d7", 265874),
    "calculation": ("section_2.wav", "23a264b385bab216e832b3d7b98e9b09e96237b43beeafc063284ae1874f20b2", 161039),
    "decision": ("section_3.wav", "bae895aab3f02e806087829c8fe68c88626a8722769ad5cfd1f8c5174b1a9c4a", 317010),
}
UNCHANGED_BINARY = {
    "examples/rl_lessons/assets/pilot.wav": "472b69fab7309701eb51b923bcd5be67958f22f43e00b43d400249d40882a735",
    "examples/rl_lessons/assets/section_0.wav": LESSON_RECORDINGS["question"][1],
    "examples/rl_lessons/assets/section_1.wav": "a3e4606654c37e775723041216b341b470ddad0b171d2ee2b92a0d644ad6648d",
    "examples/rl_lessons/assets/section_2.wav": LESSON_RECORDINGS["calculation"][1],
    "examples/rl_lessons/assets/section_3.wav": LESSON_RECORDINGS["decision"][1],
    "examples/rl_lessons/assets/take_a.wav": "0ea70d61a12b90e79dae7fd91d3d2abfa93dab94188e59645a6ca7dbe7f95d77",
    "examples/rl_lessons/assets/take_b.wav": "74db0b5b1648e0add47641916a988e92822488ced7af0c93cde8fc894a4ce44e",
}
# Text files are compared with LF line endings so a Windows checkout with autocrlf still matches.
UNCHANGED_TEXT = {
    "examples/rl_lessons/local_smoke.py": "a142aa5c726010c09a54b133308912af27f95f4c92b8e959f0713f4b51bebd40",
    "examples/rl_lessons/assets/narration.json": "b2b20f266e0930377f01fe59c8bba9242d2cfbc89faae9ab9b33914458f5e901",
    "examples/local_wav_baseline.py": "9f668440a50fcacd22b5c2d59f0b131f555c2b07ba40e453e5916388be364f2e",
}


@needs_repository_assets
def test_lesson_sections_0_2_3_resolve_as_the_three_cues(tmp_path):
    for _, (name, _, _) in LESSON_RECORDINGS.items():
        shutil.copyfile(ASSETS / name, tmp_path / name)
    manifest = write_manifest(tmp_path, [(cue, name) for cue, (name, _, _) in reversed(LESSON_RECORDINGS.items())])

    cues = load_cue_manifest(manifest).resolve(ORDER)

    assert [(cue.cue_id, cue.recording.name, cue.sha256) for cue in cues] == [
        (cue, name, digest) for cue, (name, digest, _) in LESSON_RECORDINGS.items()
    ]
    assert [cue.duration for cue in cues] == [frames / 22050 for _, _, frames in LESSON_RECORDINGS.values()]
    narration = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))
    assert [narration["durations"][index] - 0.3 for index in (0, 2, 3)] == pytest.approx([cue.duration for cue in cues])


@needs_repository_assets
@pytest.mark.parametrize("relative", UNCHANGED_BINARY)
def test_original_lesson_recordings_are_unchanged(relative):
    assert sha256(ROOT / relative) == UNCHANGED_BINARY[relative]


@needs_repository_assets
@pytest.mark.parametrize("relative", UNCHANGED_TEXT)
def test_original_lesson_and_local_wav_example_are_unchanged(relative):
    content = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(content).hexdigest() == UNCHANGED_TEXT[relative]
