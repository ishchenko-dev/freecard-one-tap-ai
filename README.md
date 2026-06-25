# FreeCard — One-Tap AI

Create an Anki card from any text in **one tap**. Select a word or phrase
anywhere, press your global hotkey, and an AI provider builds a complete
flashcard (translation, definition, examples) and adds it to your deck.

Free and open source (AGPLv3). No account, no subscription — just your own
free API key.

## Features

- **One-tap capture** — global hotkey reads the clipboard/selection and
  generates a card without leaving the app you're reading in.
- **Multiple AI providers** — Google Gemini or Groq, with configurable models.
- **Custom prompts & presets** — control exactly how cards are formatted; the
  default produces a clean two-line Front/Back card.
- **Language-learning extras** — optional Reverso translations & examples
  panel, image search, and text-to-speech (gTTS) audio.
- **Smart adding** — duplicate detection and deck/note-type selection.
- **Cross-platform** — macOS and Windows global hotkeys.

> ⚠️ **Alpha (v0.1.5).** Early test build — expect rough edges. macOS tested;
> Windows is being tested. Please report bugs (see below).

## Installation

### From a release (.ankiaddon)
1. Download the latest **`freecard-x.y.z.ankiaddon`** from the
   [Releases](../../releases) page.
2. **Double-click** it (Anki opens and installs it) — or in Anki:
   **Tools → Add-ons → Install from file…**
3. Restart Anki.

### From AnkiWeb
*(coming soon — link added once published)*

## Setup

1. Get a **free** Google Gemini API key — <https://aistudio.google.com/apikey>
   (log in, a key is created automatically, copy it).
2. Open the addon (**Tools → Add card**) → click **🚀 Setup guide** and follow
   the 3 steps (key → languages → deck).
3. The hotkey and target deck are set in the wizard / settings.

On **macOS**, grant Accessibility permission to Anki so the global hotkey can
work: System Settings → Privacy & Security → Accessibility → enable Anki.

See [`config.md`](config.md) for every configuration option.

## Usage

1. Select a word or phrase in any app.
2. Press the copy shortcut **twice quickly** (⌘C ⌘C on macOS, Ctrl+C Ctrl+C on
   Windows), or your custom hotkey.
3. A review window opens with the AI-made card — click **Add**.

## Reporting bugs (alpha)

When something breaks, an error window appears with a code (e.g. `E001`) and a
hint. You can click **Send report to developer** — and please **leave your
Telegram or email** in the field, so the developer can reply and fix your bug
fast. You can also grab the report manually: settings → **Deck & notes** →
**📋 Copy debug report**.

### What a bug report contains (privacy)

A report is sent **only when you click Send** (nothing is sent automatically).
It includes:

- the addon version, your OS and Python/Qt versions;
- the recent app log (`ai.log`) — what the addon did, and any error traceback.

It does **not** include your API key (key-like strings are redacted before
sending). The log may contain the word/phrase you were generating a card for.
Reports go to the developer's private Telegram and are used only to fix bugs.
If you'd rather not send anything, just close the window.

## Development & building

The addon is a single-file Anki add-on (`__init__.py`).

- Build a release package: `./build_ankiaddon.sh` → `dist/freecard-<ver>.ankiaddon`.

## License

[GNU AGPLv3](LICENSE). FreeCard is free software; if you find it useful,
donations are welcome but never required.
