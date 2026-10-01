# Import, export, and backups

Plurapack's JSON transfer service is separate from its SQLite database. The native,
versioned **Plurapack v1** format is the recommended lossless portable backup. It
contains system metadata, members, every proxy tag, forms, groups and memberships,
and portable presentation settings. Relationships use export-local IDs rather than
database primary keys.

## Commands

```text
p;export
p;export plurapack
p;export pluralkit
p;export tupperbox
p;export pluralkit --forms-members
p;export tupperbox --forms-members

p;import <attached JSON>
p;import plurapack <attached JSON>
p;import pluralkit <attached JSON>
p;import tupperbox <attached JSON>
```

`export` defaults to Plurapack. An omitted import format is detected from schema
metadata; ambiguous files are rejected rather than guessed. Import conflict modes
are `--merge` (safe default), `--skip-existing`, and explicit `--overwrite`. Merge
creates missing records and preserves matching records, skip-existing reports and
skips matches, and overwrite updates matching records without deleting records
absent from the file. Create a native backup before an overwrite.

PluralKit and Tupperbox do not have Plurapack's form model. Compatibility exports
therefore accept an explicit forms policy:

- `--forms-loss` (the default) omits forms and reports how many were omitted.
- `--forms-members` exports every form as a separate member/tupper, preserving its
  display name (qualified with its parent member name), picture, description/soma,
  pronouns where supported, banner, all proxy tags, and inherited groups/color
  where the destination supports them.

The dashboard API exposes the same policy as `forms=loss` or `forms=members` on
the system/group export endpoint. Native Plurapack backups always retain forms in
their original, lossless representation and do not need either option.

The dashboard's **📤 Export backup** and **📥 Import backup** controls use the same
transfer service. It offers the same merge, skip-existing, and overwrite choices.

## Compatibility

PluralKit compatibility targets its full JSON export v2 layout (`system`, `members`,
`groups`, and `switches`). Tupperbox compatibility targets the documented root
`tuppers` and `groups` arrays; its flat bracket sequence is read as prefix/suffix
pairs. External service IDs are used only as temporary relationship references and
never as Plurapack ownership or database IDs.

Compatibility formats are for migration and can lose Plurapack-only information.
PluralKit cannot represent forms as forms, aliases, speech settings, or system-tag
visibility. Tupperbox cannot represent forms as forms, colors, pronouns, aliases,
multiple group memberships, or Plurapack settings. Forms can instead be promoted
to standalone members/tuppers with `--forms-members`. Usage counters, timestamps, switches,
accounts, ownership IDs, and privacy fields without a safe equivalent are ignored.

JSON backups never contain bot tokens, login/link challenges, session or OAuth
credentials, server environment configuration, internal paths, temporary audio,
proxied-message history, account ownership, or voice reference configuration. Treat
the remaining personal metadata as private. SQLite backups remain appropriate for
operator disaster recovery; JSON is the portable user-owned backup.
