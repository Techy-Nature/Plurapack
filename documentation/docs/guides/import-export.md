# Import, export, and backups

## Import member data

Plurapack accepts pasted JSON from PluralKit, Tupperbox, and Plurapack:

```text
p;import pluralkit {"name":"My system","members":[]}
p;import tupperbox {"tuppers":[]}
p;import plurapack {"format":"plurapack",...}
```

A fenced JSON code block is also accepted. If your account has no system, the import creates one. Otherwise it adds members to the existing system.

Imports are all-or-nothing when a name or proxy tag conflicts. External IDs are not reused; imported members receive new Plurapack IDs. Owner accounts, link codes, proxy-message history, and voice configuration are not imported.

## Export portable metadata

```text
p;export
p;export pluralkit
p;export tupperbox
```

The default format is Plurapack. The bot attaches a JSON document containing the system name and portable member metadata.

!!! warning
    Exports contain names and proxy metadata. Store and transfer them as private files even though they exclude account IDs, link secrets, message content, and proxy-message records.

## Back up the installation

Operators should also back up the SQLite database to preserve stable IDs and proxy-management records. An export is useful for portability, but it is not a complete replacement for the database. Test restoration rather than assuming a backup works.
