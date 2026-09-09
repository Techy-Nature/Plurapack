# Members, aliases, and forms

## Stable identity

Member IDs are permanent five-character selectors. Names and presentation can change without changing the identity used for stored proxy records. Prefer IDs in administrative commands when names might be ambiguous.

## Add an alias

```text
p;alias MEMBER Alex
```

`MEMBER` can be an ID, full name, current alias, or proxy prefix. The alias is a short command selector; ordinary proxied messages continue to display the member's full name. Clear it by omitting the new alias:

```text
p;alias MEMBER
```

Aliases are unique within a system and may contain 1–24 characters without spaces or colons.

## Add a form

A form changes presentation without creating a separate member:

```text
p;form MEMBER "Alex at Sea" https://example.test/alex-sea.png Blue fins and a long tail.
```

The picture and soma are optional. Picture URLs must use HTTP or HTTPS. The bot returns the form's own stable five-character ID.

## Switch the current front

```text
p;front MEMBER
p;front FORM_ID
```

Selecting a member uses their base presentation. Selecting a form switches the member and form together. A form ID can also be used when replying to re-proxy an existing message.

## Make autoproxy follow the front

Autoproxy and autofront are both Off by default. Without autofront, changing
the current front leaves the independently selected autoproxy member unchanged.

After selecting a front, run `p;autofront on` (or `p;af on`) to make
autoproxy use that member and follow later front switches. This follows the
member identity, rather than the selected form's presentation.

`p;autofront off` stops following front changes but keeps the autoproxy
selection. To disable both features, run `p;autofront off` followed by
`p;autoproxy off`. These settings are shared across linked system accounts.

See [Autoproxy and autofront](proxying.md#autoproxy) for examples and tag precedence.
