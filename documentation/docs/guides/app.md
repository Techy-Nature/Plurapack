# Desktop and Android app

The Plurapack app opens your dashboard in its own window instead of a browser tab. It runs on Windows, macOS, Linux, and Android. On macOS and Linux it uses the system's WebKit engine, on Windows it uses WebView2, and on Android it uses the Android System WebView.

The app shows the same dashboard as your server, so new dashboard features appear in the app as soon as the server is updated. You don't need to reinstall it.

## Install

1. Open the Plurapack repository on GitHub and select **Actions**, then **App builds**.
2. Open the newest successful run and scroll to **Artifacts**.
3. Download the file for your device:

    | Device | Artifact | File to open |
    | --- | --- | --- |
    | Windows | `plurapack-Windows` | the `.msi` or `-setup.exe` |
    | macOS | `plurapack-macOS` | the `.dmg` |
    | Linux | `plurapack-Linux` | the `.AppImage`, `.deb`, or `.rpm` |
    | Android | `plurapack-Android` | `Plurapack.apk` |

4. Unzip the download, then open the file inside it.

If a release has been published, the same files are attached to it under **Releases**.

!!! note "Security warnings"
    The app isn't signed with a paid developer certificate yet, so your device may warn you the first time.

    - **Windows:** select **More info**, then **Run anyway**.
    - **macOS:** open **System Settings → Privacy & Security** and select **Open Anyway** next to Plurapack.
    - **Android:** allow your browser or file manager to **install unknown apps** when asked.

## Connect to your server

1. Open Plurapack.
2. Enter your server address, the one you open in a browser, such as `https://plurapack.example.app`.
3. Leave **Open this server automatically next time** on if you only use one server.
4. Select **Open dashboard** and sign in with a one-time code, the same way as in a browser.

To use a different server later, select **🔁 Switch server** at the bottom of the dashboard sidebar.

## Voice in your notification tray (Android)

On Android the app can keep playing proxied voice messages from a media notification, even when you switch apps or close the app window.

1. Set the members whose voice you want to hear to **🖥️ Browser** or **🔊 Both** in their **Voice / TTS** settings.
2. In the dashboard sidebar, select **Start notification player**.
3. If Android asks to allow notifications, select **Allow**. This puts the controls in your tray.
4. Use the notification's buttons:
    - **Pause / Resume** holds new speech until you resume. The server keeps each clip for about two minutes, so older ones are dropped.
    - **Skip** stops the clip that is playing and moves to the next one.
    - **Stop** turns the player off and removes the notification.
5. Tap the notification itself to go back to the app.

Pausing lets the phone sleep again, which saves battery. Music and podcasts get quieter while a clip speaks. On desktop the button stays **Enable voice playback** and plays in the window as before.

## API tokens

Select **🔑 API tokens** in the sidebar to create, list, and revoke tokens. See [Public API for dashboards](public-api.md) for what each permission level can do.

## Building it yourself

The full build guide, including how to keep Android updates installable with your own signing key, is in [`native/README.md`](https://github.com/Techy-Nature/Plurapack/blob/root/native/README.md).
