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
| `autofront on\|off` | `af` | Opt in to making autoproxy follow the first/current fronter. |
| `color MEMBER HEX` | `c` | Set a six-digit username color. |
| `link` | `l` | Create a single-use account connection code. |
| `verify CODE` | `v` | Connect an account using a code. |
| `import FORMAT JSON` | `i` | Import PluralKit, Tupperbox, or Plurapack JSON. |
| `export [FORMAT]` | `x` | Export portable metadata. |
| `viewinfo [SYSTEM_OR_MEMBER]` | `vi` | Show a system card, then its member cards, or one member card. |
| `viewmembers [SYSTEM]` | `ml` | Show only a system's paginated member cards. |
| `viewmember MEMBER` | `vm` | Show one embedded member card. |
| `deletemember MEMBER` | `dm` | Permanently delete an owned member, their forms, and associated records. |
| `deletesystem` | `ds` | Start confirmation for permanent deletion of all system data. |
| `voice MEMBER FILE [PLAYBACK] [SETTINGS]` | `vo` | Configure approved speech reference audio. |
| `voiceoff MEMBER` | `of` | Disable speech for a member. |
| `voiceformat MEMBER on\|off [MODE]` | `vf` | Configure semantic speech formatting. |

## Autoproxy and autofront

Both features are Off by default. These settings belong to the system and are
shared by its linked accounts.

- `p;autoproxy MEMBER` (shortcut `p;ap MEMBER`) selects the member for
  untagged, non-command messages. Explicit proxy tags take priority.
- `p;autofront on` (shortcut `p;af on`) makes autoproxy follow the
  first/current fronter and subsequent front switches.
- `p;autofront off` stops following front changes but keeps the selected
  autoproxy member.
- `p;autoproxy off` disables automatic proxying without turning autofront
  off; a later front switch can select an autoproxy member again.

To disable both, run `p;autofront off`, then `p;autoproxy off`.
See [Proxy messages](../guides/proxying.md#autoproxy) for a walkthrough.

## Selector rules

Where a command accepts `MEMBER`, use a stable member ID, full display name, alias, or proxy prefix. Where it accepts `MEMBER_OR_FORM`, a form ID is also valid.

## Viewing and deleting data

`viewinfo` without an argument uses the system connected to your account. A
system ID or exact system name shows that system; a member ID or exact member
name shows that member. System results begin with the name, logo, description,
stable ID, and member count. Use the left and right arrow reactions to move
through responsive pages of up to two member embeds. `viewmembers` skips the
system page, while `viewmember` always returns a single member embed. Member
cards include the stable ID, hex color (or `default`), description, default
form, and form names. Forms with pictures have a small linked preview marker.

The paginator accepts the normal Unicode `⬅️` and `➡️` emoji. A Stoat server
using custom emoji can set `PLURAPACK_PREVIOUS_EMOJI_ID` and
`PLURAPACK_NEXT_EMOJI_ID` to those emoji IDs; both custom and Unicode reactions
are then recognized. Only the person who opened a paginator can change it.

`deletemember` is immediate and also removes every form, current-front entry,
autoproxy selection, and stored proxy attribution associated with that member.
`deletesystem` first sends a warning. Export a private JSON backup if needed,
then reply directly to that warning with the exact ten-character system ID.
An ordinary message, a reply from another account, or a non-matching ID cannot
confirm deletion. Successful system deletion permanently removes every member,
form, owner link, connection code, setting, and proxy attribution in the system.

Quote arguments containing spaces. For example:

```text
p;member "Alex North" [alex]
p;form abc12 "Alex at Sea" https://example.test/alex.png "Blue fins and a long tail."
```
