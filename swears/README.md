# Swear lists (AI lexicon source)

JSON files in this folder drive **AI → Generate suggestions**. Each file is a category; every object needs a `"key"` field (the canonical word).

**Edit files here** at the repo root. On `pip install -e .`, they are bundled into the package as `corrcut/resources/swears`. Reinstall after changes when running from an installed venv, or run from the editable checkout so the loader picks up this folder directly.

## Files

| File | Category |
|------|----------|
| `profanity.json` | General profanity |
| `sexual.json` | Sexual / anatomy terms |
| `childish.json` | Mild / childish insults |
| `blasphemy.json` | Religious exclamations |
| `racial.json` | Slurs (high false-positive risk — review carefully) |

## Entry fields

```json
{
  "key": "balls",
  "language": "en-US",
  "filter_constituents": false,
  "plural": true,
  "forms": ["nut"],
  "phrases": [
    { "text": "balls deep", "mute_span": "phrase" }
  ],
  "expansions": ["shitty", "set of balls"],
  "exceptions": ["golf balls", "basket balls"]
}
```

| Field | Behavior |
|-------|----------|
| **key** | Canonical term; always registered for single-word matching (plus stemming/leet). |
| **language** | Optional string or array (`"en-US"`, `["en-US","en-GB"]`). Omitted = all languages. Entries are skipped when the dialogue track language does not match. |
| **plural** | When `true`, auto-adds regular English plurals (`y`→`ies`, `s`/`es`). Use **forms** for irregulars (`tits`, `children`). |
| **forms** | Extra single-word aliases mapped to **key** (not split from phrases). |
| **expansions** | Single words → **forms**; multi-word strings → **phrase patterns** (see below). |
| **phrases** | Optional explicit phrase objects (preferred for new data). |
| **filter_constituents** | On phrase hits from **expansions**: `true` = mute the **whole phrase**; `false` = mute only the **key** token inside the phrase. Same as `mute_span`: `"phrase"` / `"trigger"`. |
| **exceptions** | Consecutive-token phrases that **veto** a match near the hit (e.g. `golf balls`). |

### Phrase objects (`phrases` array)

```json
{
  "text": "son of a bitch",
  "mute_span": "phrase"
}
```

`mute_span`: `"phrase"` (entire match) or `"trigger"` (only the **key** word in the pattern).

## Detection order

1. **Phrase pass** — longest phrases first on consecutive Whisper words.  
2. **Word pass** — remaining words against **key** + **forms** + inflections + stems.  
3. **Exceptions** — token-sequence check around the hit; overrides a match.

Multi-word **expansions** are **not** split into separate single-word aliases (avoids e.g. `you` ← `fuck you`).

## Override path

```powershell
$env:CORRCUT_SWEARS_DIR = "D:\my-swears"
```

Restart the app after editing JSON files (lexicon loads at startup).
