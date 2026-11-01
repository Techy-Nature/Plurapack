# Proxy messages

## Send with a tag

Place the configured member prefix before the message and the suffix after it. If no suffix was configured, only the prefix is required.

Forms can also have their own prefix and suffix. A form tag selects that form's
name, picture, and pronouns for the message without changing who is currently
fronting. Configure one with `p;formproxy FORM_ID PREFIX [SUFFIX]`.

```text
[alex] This appears as Alex.
A: This tag has two sides. :A
```

Plurapack ignores bot messages, commands, messages already being processed, and source IDs it has already proxied.

## System tags

Set a system tag with `p;systemtag TAG`. When enabled, it is appended to every
member and form name. The tag belongs to the stable system ID, so all linked
owners share it.

Visibility is On by default. Use `p;systemtagshow system off` to change the
system default. In a server or channel, use `p;systemtagshow server off` or
`p;systemtagshow channel on` for an override; `default` removes a server or
channel override. Channel settings take precedence over server settings, which
take precedence over the system default.

## Edit a proxy

There are three ways to fix a proxied message. Pick whichever you're used to.

### Reply with the edit command

1. Reply to one of your system's proxied messages.
2. Type `p;edit` (or the shortcut `p;e`) followed by the corrected text.

```text
p;edit This is the fixed message.
```

Plurapack edits the message you replied to and removes your command message.

### Edit your latest message

Send `p;edit NEW_TEXT` without replying. Plurapack edits the newest message
you proxied in that channel.

### React with a pencil

1. React to one of your system's proxied messages with ✏️ or 📝.
2. Send the replacement text in the same channel.

The next eligible message becomes the replacement.

### Add to the start or end instead

Put `-append` (or `-a`) or `-prepend` (or `-p`) before the text to keep the
original message and add to it:

```text
p;edit -append (edit: wrong channel, sorry!)
p;edit -prepend Correction:
```

### Coming from PluralKit or Tupperbox?

Your old habits work. Every Plurapack command answers to `pk;`, `tul;`, and
`tup;` as well as `p;`, so `pk;front Alex` or `tup;help` behave exactly like
the `p;` versions. For editing, `pk;edit`, `pk!edit`, `pk;e`, `tul;edit`,
`tup;edit`, and `tul!edit` are all treated the same as `p;edit`, whether you
reply or not. PluralKit's
`-append` and `-prepend` options work too.

If PluralKit or Tupperbox is also in the server, Plurapack only answers the
borrowed spellings (`pk;`, `pk!`, and `tul!`) for messages it proxied itself, so it won't complain about
the other bot's messages. Replying to the exact message is the most reliable
way to edit when both bots are around.

If a member has speech turned on, the edited text is spoken again.

## Delete a proxy

React to the proxied message with ❌ or 🗑️. An authorized owner of that system may manage proxies sent by another linked owner.

## Change who sent an existing message

Proxied as the wrong member? There are three ways to switch it.

### Reply with the reproxy command

1. Reply to one of your system's proxied messages.
2. Type `p;reproxy` (or the shortcut `p;rp`) followed by who should have sent it.

```text
p;reproxy Sam
```

### Re-proxy your latest message

Send `p;reproxy MEMBER` without replying. Plurapack reposts the newest message
you proxied in that channel.

### Reply with just the name

Reply to an existing proxy with only a member's full name, stable member ID,
alias, proxy prefix, or form ID.

Every method accepts a member's full name, stable member ID, alias, proxy
prefix, or form ID. Plurapack reposts the existing content with the selected
presentation and removes the old proxy and your command after the replacement
succeeds. If a member has speech turned on, the message is spoken again in the
new member's voice.

`pk;reproxy`, `pk;rp`, `pk!reproxy`, `tul;reproxy`, and `tup;rp` work as well. As with editing, those
borrowed spellings stay quiet for messages another bot proxied.

## Failure behavior

Plurapack posts and records a replacement before deleting the source. If posting fails, the original message is preserved. If source deletion is not permitted, the new proxy remains posted and the source remains visible; ask the operator to check channel permissions.

## Autoproxy

Autoproxy and autofront are implemented, optional, and Off by default.
Their settings are shared across all linked accounts in the same system.

Select a member to proxy untagged, non-command messages as that member:

```text
p;autoproxy MEMBER
```

Replace `MEMBER` with a member ID, full name, alias, or proxy prefix.
The shortcut is `p;ap MEMBER`. Explicit proxy tags still select the tagged
member for that message. Commands and bot messages are not automatically proxied.

Choosing a member this way pins autoproxy to that member. Changing the current
front does not change it, and it turns off front following if it was On.

## Follow whoever is fronting

To make autoproxy follow the front, turn it on without naming a member:

```text
p;autoproxy on
```

`p;ap on`, `p;autoproxy front`, `p;autofront on` and `p;af on` all do the same
thing. Autoproxy starts as whoever is fronting right now, and every later
`p;front` switch (or a switch from the dashboard or API) changes who it
proxies as. If nobody is fronting yet, it starts with the next front switch.
If the front is cleared, untagged messages stay unproxied until someone
switches in again.

Front following uses the member identity and presentation. A form ID passed to
`p;front` selects its linked member for autoproxy and applies the form's display
name and picture to new untagged proxies. To apply a form's presentation to an
existing proxy, reply to that proxy with the form ID.

## Turn automatic proxying off

`p;autoproxy off` turns autoproxy off completely, including front following.

`p;autofront off` stops following the front but keeps proxying as whoever was
fronting when you turned it off. Use `p;autoproxy off` afterwards if you want
autoproxy fully off.

Explicit proxy tags continue to work when autoproxy is Off.
