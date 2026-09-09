# Share a system between accounts

Multiple authorized Stoat accounts can share one Plurapack system and its stable member IDs.

## Connect an account

1. From an existing owner account, create a code:

    ```text
    p;link
    ```

2. Send the code privately to the person connecting their account.
3. From the new account, redeem it:

    ```text
    p;verify CODE
    ```

The code can be used once and expires after 15 minutes. Only a hash of the code is stored.

## Safety guidance

- Confirm the recipient through a trusted channel before sharing a code.
- Do not post codes publicly or include them in screenshots.
- If a code is exposed, do not use it; wait for it to expire and create another.
- Linking gives the account access to the shared system, including the ability to manage linked owners' proxy messages.
