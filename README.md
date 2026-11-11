# RL Studio

Tools and examples for advanced educational reinforcement-learning videos, based on [Manim Voiceover v0.4.0](https://github.com/ManimCommunity/manim-voiceover/tree/v0.4.0).

The initial baseline includes a reproducible WSL2/Ubuntu setup, Conda Python 3.12, uv-locked dependencies, focused upstream tests, and a short mathematical lesson with local narration. The Python package remains `manim-voiceover` / `manim_voiceover`; the upstream source and MIT notices are preserved.

**Validation status:** S01 passed 237 full-suite tests (97.85% coverage), all 3,468 mutation checks, formatting, lint, type checks, the dependency audit and Slophammer. Its focused workflow passed 158 tests and both verified A/B renders. See [S01 integration validation](local_setup/s01-validation.md), [baseline validation](local_setup/baseline-checks.md) and the historical [task-sandbox record](docs/s01-task-record.md).

See [local setup and validation](LOCAL_SETUP.md) for the tested environment, commands and limitations. From PowerShell in the checkout:

```powershell
.\local.ps1 status
.\local.ps1 test
.\local.ps1 render
.\local.ps1 shell
```

Scenes and recording fixtures are under `examples/rl_lessons/`. Rendered media and local environment files are excluded from Git. S01 adds `LocalWavService` for mono/stereo 16-bit PCM WAV narration. See the [S01 workflow, setup and limitations](docs/s01.md).

The upstream release-publishing workflow is restricted to the upstream repository. RL Studio does not publish the `manim-voiceover` package to PyPI.

## Upstream project

The original project overview follows; its badges and links refer to Manim Voiceover.

# Manim Voiceover

<p>
    <a href="https://github.com/ManimCommunity/manim-voiceover/workflows/Build/badge.svg"><img src="https://github.com/ManimCommunity/manim-voiceover/workflows/Build/badge.svg" alt="Github Actions Status"></a>
    <a href="https://pypi.org/project/manim_voiceover/"><img src="https://img.shields.io/pypi/v/manim_voiceover.svg?style=flat&logo=pypi" alt="PyPI Latest Release"></a>
    <a href="https://pepy.tech/project/manim_voiceover"><img src="https://pepy.tech/badge/manim_voiceover/month?" alt="Downloads"> </a>
    <a href="https://voiceover.manim.community/en/latest"><img src="https://readthedocs.org/projects/manim_voiceover/badge/?version=latest" alt="Documentation Status"></a>
    <a href="https://github.com/ManimCommunity/manim-voiceover/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ManimCommunity/manim-voiceover.svg?color=blue" alt="License"></a>
    <a href="https://manim.community/discord"><img src="https://dcbadge.vercel.app/api/server/qY23bthHTY?style=flat" alt="Discord"></a>
</p>

Manim Voiceover is a [Manim](https://manim.community) plugin for all things voiceover:

- Add voiceovers to Manim videos *directly in Python* without having to use a video editor.
- Record voiceovers with your microphone during rendering with a simple command line interface.
- Develop animations with auto-generated AI voices from various free and proprietary services.
- Per-word timing of animations, i.e. trigger animations at specific words in the voiceover, even for the recordings. This works thanks to [OpenAI Whisper](https://github.com/openai/whisper).

Here is a demo:

https://github.com/user-attachments/assets/76b01569-ffcd-4ddb-b9d3-08685c5d375f

Currently supported TTS services (aside from the CLI that allows you to records your own voice):

- [Gemini Text to Speech](https://ai.google.dev/gemini-api/docs/speech-generation) (Recommended for AI voices)
- [Azure Text to Speech](https://azure.microsoft.com/en-us/services/cognitive-services/text-to-speech/)
- [gTTS](https://github.com/pndurette/gTTS/)
- [pyttsx3](https://github.com/nateshmbhat/pyttsx3)

[Check out the documentation for more details.](https://voiceover.manim.community/)

## Installation

[Installation instructions in Manim Voiceover docs.](https://voiceover.manim.community/en/latest/installation.html)

## Get started

[Check out the docs to get started with Manim Voiceover.](https://voiceover.manim.community/en/latest/quickstart.html)

## Examples

[Check out the example gallery to get inspired.](https://voiceover.manim.community/en/latest/examples.html)

## Translate

Manim Voiceover can use machine translation services like [DeepL](https://www.deepl.com/) to translate voiceovers into other languages. [Check out the docs for more details.](https://voiceover.manim.community/en/latest/translate.html)
