# Create your first system

## 1. Create the system

In a Stoat channel where Plurapack is available, send:

```text
p;setup My system
```

The bot replies with the system's stable ID. Save it with your backup information. Running setup again from the same account returns the existing system instead of creating another one.

## 2. Add a member

A member needs a display name and proxy prefix:

```text
p;member Alex [alex]
```

The bot returns a stable five-character member ID. To use both a prefix and suffix:

```text
p;member "Alex North" A: :A
```

Quote names that contain spaces. Choose a tag that will not appear accidentally in ordinary messages.

## 3. Send a message

```text
[alex] Hello!
```

Plurapack posts `Hello!` using Alex's proxy identity, records the minimum metadata needed to manage that proxy, and then tries to remove the tagged source message.

## 4. Personalize the member

```text
p;color MEMBER_ID 7b68ee
p;alias MEMBER_ID Al
```

Use the stable ID printed by the bot in place of `MEMBER_ID`. Colors are six-digit hexadecimal RGB values, with or without `#`.

## Next steps

- [Learn editing, deletion, and re-proxying](../guides/proxying.md).
- [Add aliases and alternate forms](../guides/members-and-forms.md).
- [Connect another trusted account](../guides/share-a-system.md).
