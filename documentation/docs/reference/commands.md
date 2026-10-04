# Command reference

Examples use the default `p;` prefix. Every command has a unique one- or two-letter shortcut.

| Command | Shortcut | Purpose |
| --- | --- | --- |
| `help [COMMAND]` | `h` | List every current command, or show usage and aliases for one command. |
| `setup [SYSTEM_NAME] [DESCRIPTION]` | `s` | Create the account's system, with an optional description. |
| `member NAME PREFIX [SUFFIX] [--avatar URL\|--a URL] [DESCRIPTION]` | `m` | Add a member and proxy tag, with an optional avatar and description. |
| `avatar --member MEMBER URL\|--form FORM URL\|--system URL` | `av` | Change a member, form, or system avatar. Target shortcuts are `-m`, `-f`, and `-s`. |
| `banner --member MEMBER URL\|--form FORM URL\|--system URL` | `bn` | Change a member, form, or system banner. Target shortcuts are `-m`, `-f`, and `-s`. |
| `memberproxy MEMBER [PREFIX] [SUFFIX]` | `mt` | Add a member tag, or list tags by omitting the prefix. |
| `memberproxy-clear MEMBER` | `mc` | Clear all proxy tags from a member. |
| `alias MEMBER [ALIAS]` | `a` | Set or clear a short selector. |
| `form MEMBER DISPLAY_NAME [PICTURE_URL] [SOMA]` | `f` | Create an alternate presentation. |
| `formproxy FORM [PREFIX] [SUFFIX]` | `ft` | Add a form-specific proxy tag, or list tags by omitting the prefix. |
| `formproxy-clear FORM` | `fc` | Clear all proxy tags from a form. |
| `defaultform MEMBER [FORM_OR_OFF]` | `df` | Set a member's default form, or clear it with `off`. |
| `pronouns MEMBER [PRONOUNS]` | `p` | Set member pronouns, or clear them by omitting the value. |
| `formpronouns FORM [PRONOUNS]` | `fp` | Override form pronouns, or restore inheritance by omitting the value. |
| `systemtag [TAG]` | `st` | Set the tag shown after member and form names, or clear it. |
| `systemtagshow SCOPE on\|off\|default` | `ts` | Set system visibility or a server/channel override. |
| `front MEMBER_OR_FORM` | `fr` | Switch the current member and optional form. |
| `autoproxy MEMBER_OR_OFF` | `ap` | Proxy untagged messages as a member; Off by default. |
| `autofront on\|off` | `af` | Opt in to making autoproxy follow the first/current fronter. |
| `color MEMBER HEX` | `c` | Set a six-digit username color. |
| `link` | `l` | Create a single-use account connection code. |
| `verify CODE` | `v` | Connect an account using a code. |
| `import FORMAT JSON` | `i` | Import PluralKit, Tupperbox, or Plurapack JSON. |
| `export [FORMAT]` | `x` | Export portable metadata. |
| `info SYSTEM_GROUP_MEMBER_OR_FORM` | `in` | Show a system and its groups, a single group, a member, or a form profile. |
| `group create\|select\|add\|alias\|avatar ...` | `g` | Create, select, and edit the active group. |
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

## Groups

```text
p;group create "NAME" ALIAS
p;group select GROUP
p;group add MEMBER [MEMBER...]
p;group alias NEW_ALIAS
p;group avatar IMAGE_URL
```

Creating a group makes it the active group. Use `group select` to choose which
existing group later `group add`, `group alias`, and `group avatar` commands
change. A group selector may be its stable ID, full name, or alias. Quote group
names containing spaces. Member selectors may be stable IDs, full names,
aliases, or proxy prefixes; member names containing spaces must be quoted. The
`g` shortcut supports all of the same operations (for example,
`p;g select GROUP`).

`view` is accepted as a compatibility alias for `viewinfo`, and `fronter` is
accepted as a compatibility alias for `front`. Missing command arguments are
reported in chat with the missing argument and a pointer to the command's help
instead of only producing an operator-side traceback.

## Viewing and deleting data

`info` accepts a system, group, member, or form nickname/name, alias, or stable
ID. Pass a group name, nickname (alias), or ID directly to show only that
group. A system result includes an embed for each group, with its optional
avatar, name, stable ID, and member list. Each listed member includes their
name, ID, pronouns, and proxy tags; the embed's read-more control keeps longer
group lists compact until expanded.

`viewinfo` without an argument uses the system connected to your account. A
system ID or exact system name shows that system; a member ID or exact member
name shows that member. System results begin with the name, logo, description,
stable ID, and member count. Use the left and right arrow reactions to move
through responsive pages of up to two member embeds. `viewmembers` skips the
system page, while `viewmember` always returns a single member embed. Member
cards include the stable ID, hex color (or `default`), description, default
form, and form names. Forms with pictures have a small linked preview marker.
When a member has no description, their default form's soma description is
shown instead.

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
p;member "Alex North" [alex] --avatar https://example.test/alex.png
p;m "Alex North" [alex] --a https://example.test/alex.png
p;avatar --member abc12 https://example.test/new-avatar.png
p;av -f def34 https://example.test/form-avatar.png
p;banner --system https://example.test/system-banner.png
p;bn -m abc12 https://example.test/member-banner.png
p;form abc12 "Alex at Sea" https://example.test/alex.png "Blue fins and a long tail."
```

The final description argument for `setup`, `member`, and `form` consumes the
rest of the message, including line breaks. Quote names and earlier arguments
that contain spaces; use `""` for an omitted suffix or picture when a later
description is present:

```text
p;setup "The Crew" Our shared system
with a multi-line description.
p;member "Alex North" [alex] "" First line
Second line
p;form abc12 "Alex at Sea" "" First line
Second line
```
