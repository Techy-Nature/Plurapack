# Public API for dashboards and viewers

Plurapack has an API that outside tools can use, such as dashboards, member viewers, or front-status widgets. It runs on the same server as the Plurapack dashboard, so if your dashboard is at `https://plurapack.example`, the API is at `https://plurapack.example/api/v1`. Interactive documentation for every endpoint is at `/docs`.

!!! note "Everything starts private"
    Outside tools see nothing until you choose to share it. A member you never make public is left out of every response completely. It doesn't show up as a blank entry.

## Step 1: Choose what is public

Use `p;privacy` to see your current settings. Then turn on only what you want to share:

1. Make the system card visible. Nothing else can be seen until you do this:

    ```text
    p;privacy system public
    ```

2. Make each member you want to share public:

    ```text
    p;privacy member Nova public
    ```

3. Optionally, hide some of a public member's fields. The fields are `description`, `pronouns`, `avatar`, `banner`, `color`, and `forms`. Use `all` to hide or show every field.

    ```text
    p;privacy member Nova hide pronouns forms
    p;privacy member Nova show all
    ```

4. Optionally, share who is fronting. If a private member is fronting, the API answers as though nobody is fronting.

    ```text
    p;privacy front public
    ```

5. Optionally, share groups. A public group only lists its public members.

    ```text
    p;privacy group heal public
    ```

To undo any of these, use `private` instead of `public`.

!!! warning
    These settings control the API only. Bot cards such as `p;info` work the same as before.

## Step 2: Create a token (optional)

Public data needs no token. Use a token when a tool needs to know which system it belongs to, or when your own tool should see private members too.

1. Open a **direct message** with the bot. Plurapack won't create tokens in a server or group chat.
2. Create a token:

    ```text
    p;token create read:public Front widget
    p;token create read:private My own dashboard
    p;token create write Stream Deck front switcher
    ```

    - `read:public` sees the same things as an anonymous visitor, and lets the tool look up your system ID.
    - `read:private` sees your whole system, including private members, hidden fields, proxy tags, and privacy settings. Only give it to tools you run or fully trust.
    - `write` can do everything `read:private` can, and it can also switch who is fronting and edit member profiles. It acts as the account that created it. Only give it to tools you run yourself.

3. Copy the token right away. It's shown once, and Plurapack only keeps a hash of it.
4. Use `p;token list` to see your tokens, and `p;token revoke TOKEN_ID` (or `p;token revoke all`) to cancel them. Every owner account linked to the system can manage its tokens.

## Step 3: Call the API

Send the token in the `Authorization` header:

```bash
curl -H "Authorization: Bearer ppk_..." https://plurapack.example/api/v1/me
```

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/me` | The token's system ID, name, and scope. A token is required. |
| `GET /api/v1/systems/{systemId}` | Name, description, tag, logo, banner, and visible member count. |
| `GET /api/v1/systems/{systemId}/members` | Visible members with their visible fields and forms. |
| `GET /api/v1/systems/{systemId}/members/{memberId}` | One visible member. |
| `GET /api/v1/systems/{systemId}/groups` | Visible groups, each with its visible member IDs. |
| `GET /api/v1/systems/{systemId}/groups/{groupId}` | One visible group. |
| `GET /api/v1/systems/{systemId}/fronters` | `{"fronters": [{"member": {...}, "formId": ...}]}`, or an empty list. |

With a `write` token, a tool can also make changes:

| Endpoint | Does |
| --- | --- |
| `PUT /api/v1/systems/{systemId}/fronters` | Switch front. Send `{"memberId": "..."}` (uses their default form) or `{"formId": "..."}`. Returns the new fronters. |
| `DELETE /api/v1/systems/{systemId}/fronters` | Clear the current front. |
| `PATCH /api/v1/systems/{systemId}/members/{memberId}` | Change any of `name`, `pronouns`, `description`, `color` (`#RRGGBB`), `avatar`, and `banner`. Returns the updated member. |

```bash
curl -X PUT -H "Authorization: Bearer ppk_..." -H "Content-Type: application/json" \
  -d '{"memberId": "abcde"}' https://plurapack.example/api/v1/systems/abcde12345/fronters
```

Proxy tags, voices, groups, privacy, and deleting things stay in the bot and dashboard on purpose. A read-only token gets `403` from these endpoints, and a token can never change another system.

Anything that's private, or doesn't exist, returns `404` with `{"message": "Resource not found"}`, so a tool can't tell the difference. Hidden fields are returned as `null`, and hidden forms as `[]`. An invalid or revoked token returns `401`. It never falls back to the public view.

## Built-in member viewer

Every Plurapack server includes a ready-made, dark member viewer at `/viewer`. It shows the system card, who is fronting, a member search, and group filters. Share a link like this:

```text
https://plurapack.example/viewer?system=abcde12345
```

Visitors only see what you've made public. To see your own private members, open **Viewing your own system? Add a token** and paste a `read:private` token. The token stays in that browser tab and is never added to the link.

### Example: build your own viewer

Pages on any website may call the API from the browser. This small page lists a system's public members:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Member viewer</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 24px 16px; background: #14121b; color: #ece8f6;
           font: 16px/1.5 system-ui, sans-serif; }
    main { max-width: 720px; margin: 0 auto; display: grid; gap: 12px; }
    .card { display: flex; gap: 14px; align-items: center; padding: 14px;
            background: #1f1b2b; border: 1px solid #2f2942; border-radius: 12px; }
    .card img { width: 56px; height: 56px; border-radius: 50%; object-fit: cover; background: #2f2942; }
    .name { font-weight: 600; }
    .meta { color: #a59fbb; font-size: 14px; }
  </style>
</head>
<body>
  <main id="members"><p>Loading…</p></main>
  <script>
    const API = "https://plurapack.example/api/v1";
    const SYSTEM_ID = "abcde12345";

    function card(member) {
      const element = document.createElement("article");
      element.className = "card";
      element.style.borderLeft = `4px solid ${member.color || "#7765A8"}`;
      const image = document.createElement("img");
      image.alt = "";
      if (member.avatar) image.src = member.avatar;
      const text = document.createElement("div");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = member.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = [member.pronouns, member.description].filter(Boolean).join(" · ");
      text.append(name, meta);
      element.append(image, text);
      return element;
    }

    fetch(`${API}/systems/${SYSTEM_ID}/members`)
      .then((response) => response.ok ? response.json() : Promise.reject(response.status))
      .then(({ members }) => document.getElementById("members").replaceChildren(...members.map(card)))
      .catch(() => { document.getElementById("members").textContent = "This system isn't public."; });
  </script>
</body>
</html>
```

Never put a `read:private` token in a page that other people can open, because anyone viewing the page source could copy it.

## Limits

- Each token, or each visitor without a token, can make 120 requests per minute by default. Server owners can change this with `PLURAPACK_API_RATE_LIMIT`. Extra requests get `429` with a `Retry-After` header.
- Responses are never cached, so front changes show up right away.
- The API never returns account IDs, link codes, login data, voices, or proxied-message records.
