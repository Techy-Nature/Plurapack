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

1. React to one of your system's proxied messages with ✏️ or 📝.
2. Send the replacement text in the same channel.

The next eligible message becomes the replacement. Editing does not generate replacement speech audio.

## Delete a proxy

React to the proxied message with ❌ or 🗑️. An authorized owner of that system may manage proxies sent by another linked owner.

## Change who sent an existing message

Reply to an existing proxy with only a member's full name, stable member ID, alias, proxy prefix, or form ID. Plurapack reposts the existing content with the selected presentation and removes the old proxy and selector reply after the replacement succeeds.

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

With autofront Off, changing the current front does not change the autoproxy
member. You can choose each independently.

## Autofront

Autofront makes autoproxy follow the first/current fronter. Select a front,
then enable it:

```text
p;front MEMBER
p;autofront on
```

The shortcut is `p;af on`. The current fronter becomes the autoproxy member,
and later front switches update it. If no front is selected yet, enabling
autofront keeps any existing autoproxy selection until a front is chosen.

Autofront follows the member identity and presentation. A form ID passed to
`p;front` selects its linked member for autoproxy and applies the form's display
name and picture to new untagged proxies. To apply a form's presentation to an
existing proxy, reply to that proxy with the form ID.

## Turn automatic proxying off

`p;autofront off` disconnects fronting from autoproxy without disabling or
changing the selected autoproxy member.

`p;autoproxy off` clears the autoproxy selection without disabling autofront.
If autofront remains On, a later front switch can enable autoproxy again.

To stop both automatic proxying and front following:

```text
p;autofront off
p;autoproxy off
```

Explicit proxy tags continue to work when both features are Off.
