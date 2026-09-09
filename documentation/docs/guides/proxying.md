# Proxy messages

## Send with a tag

Place the configured member prefix before the message and the suffix after it. If no suffix was configured, only the prefix is required.

```text
[alex] This appears as Alex.
A: This tag has two sides. :A
```

Plurapack ignores bot messages, commands, messages already being processed, and source IDs it has already proxied.

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

Autoproxy is Off by default. Select a member to proxy all your non-command,
untagged messages as that member, or disable it again:

```text
p;autoproxy MEMBER
p;autoproxy off
```

Autoproxy is separate from fronting: changing the current front does not change
the autoproxy member. To connect them explicitly, turn on autofront:

```text
p;autofront on
```

When enabled, the first/current fronter becomes the autoproxy member, and later
front switches update it. `p;autofront off` disconnects them without disabling
or changing the selected autoproxy member. Explicit proxy tags still select the
tagged member for that message.
