# FreeCard — One-Tap AI — Configuration

Create an Anki card from any selected text with a single hotkey. Copy a word
or phrase, press your hotkey, and the addon asks an AI provider to build the
card and adds it to your chosen deck.

## API keys

You need a free API key from at least one provider:

- **`gemini_api_key`** — Google Gemini key. Get one free at
  <https://aistudio.google.com/apikey>.
- **`groq_api_key`** — Groq key. Get one free at
  <https://console.groq.com/keys>.

Keys are stored locally in your Anki profile and never shared.

## Provider selection

- **`ai_platform`** — `"google"` (Gemini) or `"groq"`. The provider used to
  generate cards.
- **`gemini_model`** — Gemini model name, e.g. `gemini-2.5-flash-lite`.
- **`groq_model`** — Groq model name, e.g. `llama-3.3-70b-versatile`.

## Card generation

- **`custom_prompt`** — The instruction sent to the AI. The default produces a
  two-line card (Front = the word, Back = translation + definition + examples).
  Edit it to fit your language and study style.
- **`prompt_presets`** — Named prompt presets you can switch between.
- **`prompt_preset_index`** — Index of the active preset.
- **`note_model_name`** — Anki note type to use (default `"Basic"`).
- **`ai_deck_id`** — Deck ID that generated cards are added to.
- **`settings_deck_id`** — Deck ID used by the settings UI.
- **`auto_add_enabled`** — If `true`, add the generated card automatically; if
  `false`, show a preview first.

## Hotkey

- **`hotkey_mode`** — `"mac"` or `"win"`. Match your operating system.
- **`hotkey_combo`** — The global hotkey, e.g. `"cmd+option+t"` (macOS) or
  `"ctrl+shift+x"` (Windows).

On macOS you must grant Accessibility permission to Anki
(System Settings → Privacy & Security → Accessibility) for the global hotkey.

## Notifications

- **`ai_notify_enabled`** — Show a notification when an AI card is added.
- **`manual_notify_enabled`** — Show a notification for manually added cards.

## Extra panels

- **`reverso_panel_enabled`** — Show the Reverso translations/examples panel.
- **`reverso_source_lang`** / **`reverso_target_lang`** — Language codes for
  Reverso, e.g. `"en"` and `"es"`.
- **`audio_panel_enabled`** — Show the text-to-speech (gTTS) audio panel.
- **`sidebar_visible`** — Show the addon sidebar.
