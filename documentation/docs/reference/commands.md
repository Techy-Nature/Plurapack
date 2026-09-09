# Command reference

Examples use the default `p;` prefix. Every command has a unique one- or two-letter shortcut.

| Command | Shortcut | Purpose |
| --- | --- | --- |
| `setup [SYSTEM_NAME]` | `s` | Create the account's system, if needed. |
| `member NAME PREFIX [SUFFIX]` | `m` | Add a member and proxy tag. |
| `alias MEMBER [ALIAS]` | `a` | Set or clear a short selector. |
| `form MEMBER DISPLAY_NAME [PICTURE_URL] [SOMA]` | `f` | Create an alternate presentation. |
| `front MEMBER_OR_FORM` | `fr` | Switch the current member and optional form. |
| `autoproxy MEMBER_OR_OFF` | `ap` | Proxy untagged messages as a member; Off by default. |
| `autofront on|off` | `af` | Opt in to making autoproxy follow the first/current fronter. |
| `color MEMBER HEX` | `c` | Set a six-digit username color. |
| `link` | `l` | Create a single-use account connection code. |
| `verify CODE` | `v` | Connect an account using a code. |
| `import FORMAT JSON` | `i` | Import PluralKit, Tupperbox, or Plurapack JSON. |
| `export [FORMAT]` | `x` | Export portable metadata. |
| `voice MEMBER FILE [PLAYBACK] [SETTINGS]` | `vo` | Configure approved speech reference audio. |
| `voiceoff MEMBER` | `of` | Disable speech for a member. |
| `voiceformat MEMBER on\|off [MODE]` | `vf` | Configure semantic speech formatting. |

## Selector rules

Where a command accepts `MEMBER`, use a stable member ID, full display name, alias, or proxy prefix. Where it accepts `MEMBER_OR_FORM`, a form ID is also valid.

Quote arguments containing spaces. For example:

```text
p;member "Alex North" [alex]
p;form abc12 "Alex at Sea" https://example.test/alex.png "Blue fins and a long tail."
```
