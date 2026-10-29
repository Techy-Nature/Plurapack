# Plurapack app (desktop and Android)

This folder turns the Plurapack dashboard into a real app with [Tauri 2](https://v2.tauri.app/). One project builds:

| Platform | Engine | Output |
| --- | --- | --- |
| macOS | WebKit (WKWebView) | `.dmg` and `.app` |
| Linux | WebKit (WebKitGTK) | `.AppImage`, `.deb`, `.rpm` |
| Windows | WebView2 | `.msi` and `-setup.exe` |
| Android | Android System WebView | `.apk` |

## How it works

The app bundles one small page, [`launcher/index.html`](launcher/index.html). It asks for your Plurapack server address, checks that the server answers, and then opens that server's dashboard in the app window. Sign-in, members, groups, and API tokens all come from the server, so the app never needs updating when the dashboard changes.

The launcher adds `?plurapack-app=…` to the first dashboard address. The dashboard uses it to show a **🔁 Switch server** button that goes back to the launcher. Only the app's own launcher addresses (`tauri://localhost` and `tauri.localhost`) are accepted.

The dashboard pages get no access to Tauri commands, files, or the device. The app is a window, nothing more.

```
native/
├── launcher/index.html     The server picker shown when the app starts
├── icon-source.png         1024×1024 source for every app icon
├── package.json            Tauri command-line scripts
└── src-tauri/
    ├── tauri.conf.json     App name, window size, dark theme, bundle settings
    ├── Cargo.toml          Rust package for the native shell
    ├── src/lib.rs          Starts the window (shared by desktop and Android)
    ├── src/main.rs         Desktop entry point
    └── icons/              Generated icons (desktop and Android)
```

## Option A: let GitHub build it (no setup)

The [App builds](../.github/workflows/app.yml) workflow builds every platform on GitHub's computers.

1. On GitHub, open **Actions → App builds**.
2. Select **Run workflow**, keep the `root` branch, and select **Run workflow** again.
3. Wait for the run to finish (about 15 to 25 minutes, mostly Android).
4. Open the run and download the artifacts at the bottom of the page.

It also runs on pull requests that change `native/`, so you can download test builds from a pull request's checks.

To publish a release with the files attached, push a tag that starts with `app-v`:

```bash
git tag app-v0.1.0
git push origin app-v0.1.0
```

### Keep Android updates installable

Android only installs an update over an older copy when both are signed with the same key. Without a saved key, each build is signed with a new throwaway key, and you have to uninstall the app before installing a newer build (this also signs you out).

To sign every build with the same key:

1. Create a key once, on any computer with Java installed:

    ```bash
    keytool -genkeypair -v -keystore plurapack-release.jks -alias plurapack \
      -keyalg RSA -keysize 2048 -validity 10000
    ```

    Pick a strong password and keep the `.jks` file somewhere safe. If you lose it, existing installs can't be updated.

2. Turn it into text:

    ```bash
    base64 -w0 plurapack-release.jks > plurapack-release.txt   # Linux
    base64 -i plurapack-release.jks -o plurapack-release.txt   # macOS
    ```

3. On GitHub, open **Settings → Secrets and variables → Actions** and add four repository secrets:

    | Secret | Value |
    | --- | --- |
    | `ANDROID_KEYSTORE_BASE64` | the contents of `plurapack-release.txt` |
    | `ANDROID_KEYSTORE_PASSWORD` | the keystore password |
    | `ANDROID_KEY_ALIAS` | `plurapack` |
    | `ANDROID_KEY_PASSWORD` | the key password (often the same as the keystore password) |

4. Delete `plurapack-release.txt`. Never commit the `.jks` file; `native/.gitignore` already ignores it.

## Option B: build on your own computer

### Step 1: Install the tools every platform needs

1. Install [Node.js 22 or newer](https://nodejs.org/).
2. Install Rust with [rustup](https://rustup.rs/):

    ```bash
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    ```

    On Windows, run `rustup-init.exe` from the same site instead.

3. Install the app's JavaScript tools:

    ```bash
    cd native
    npm ci
    ```

### Step 2: Install your platform's extras

**macOS**

```bash
xcode-select --install
```

**Windows**

1. Install the [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) and tick **Desktop development with C++**.
2. WebView2 is already part of Windows 10 (version 1803 and later) and Windows 11.

**Linux (Debian or Ubuntu)**

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

For other distributions, see [Tauri's Linux prerequisites](https://v2.tauri.app/start/prerequisites/#linux).

### Step 3: Run or build the desktop app

Run it in a window while you work on it:

```bash
npm run dev
```

Build installers for the computer you're on:

```bash
npm run build
```

The files land in `src-tauri/target/release/bundle/`. The first build takes a few minutes; later ones are much faster.

On macOS, to build one app that runs on both Apple silicon and Intel Macs:

```bash
rustup target add aarch64-apple-darwin x86_64-apple-darwin
npx tauri build --target universal-apple-darwin
```

### Step 4: Build the Android APK

1. Install [Android Studio](https://developer.android.com/studio).
2. In Android Studio, open **Settings → Languages & Frameworks → Android SDK → SDK Tools** and tick:
    - Android SDK Platform-Tools
    - Android SDK Build-Tools
    - NDK (Side by side)
    - Android SDK Command-line Tools
3. Point your terminal at the SDK, the NDK, and Android Studio's Java. On macOS:

    ```bash
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
    export ANDROID_HOME="$HOME/Library/Android/sdk"
    export NDK_HOME="$ANDROID_HOME/ndk/$(ls -1 "$ANDROID_HOME/ndk" | tail -n 1)"
    ```

    On Linux, use `JAVA_HOME=/opt/android-studio/jbr` and `ANDROID_HOME=$HOME/Android/Sdk`. On Windows, set the same three variables under **System Properties → Environment Variables** (the SDK is in `%LOCALAPPDATA%\Android\Sdk`).

4. Add Rust's Android targets:

    ```bash
    rustup target add aarch64-linux-android armv7-linux-androideabi i686-linux-android x86_64-linux-android
    ```

5. Generate the Android project (once; it goes in `src-tauri/gen/android`, which is not committed):

    ```bash
    npm run android:init
    ```

6. Try it on a phone with USB debugging on, or on an emulator from Android Studio:

    ```bash
    npx tauri android dev
    ```

7. Build the APK:

    ```bash
    npm run android:build
    ```

    The unsigned file is in `src-tauri/gen/android/app/build/outputs/apk/universal/release/`.

8. Sign it so Android will install it (use the key from [Keep Android updates installable](#keep-android-updates-installable)):

    ```bash
    TOOLS="$ANDROID_HOME/build-tools/$(ls -1 "$ANDROID_HOME/build-tools" | tail -n 1)"
    "$TOOLS/zipalign" -p -f 4 src-tauri/gen/android/app/build/outputs/apk/universal/release/app-universal-release-unsigned.apk aligned.apk
    "$TOOLS/apksigner" sign --ks plurapack-release.jks --ks-key-alias plurapack --out Plurapack.apk aligned.apk
    ```

9. Copy `Plurapack.apk` to your phone and open it.

## Changing the icon

Replace `icon-source.png` with a square PNG of at least 1024×1024 pixels, then regenerate every size:

```bash
npm run icons
```

If `src-tauri/gen/android` exists, this also updates the Android launcher icons.

The committed Android icons have two hand tweaks that `npm run icons` overwrites, so redo them afterwards:

- Each `src-tauri/icons/android/mipmap-*/ic_launcher_foreground.png` has the logo shrunk to about 62% of the canvas, centred on a transparent background. Android launchers crop adaptive icons to a circle or squircle, and a full-size logo gets its edges cut off.
- `src-tauri/icons/android/values/ic_launcher_background.xml` uses `#00000000` (transparent) instead of `#FFFFFF`, so the logo has no white backing.

## Good to know

- **Use `https://`.** Android blocks plain `http://` servers in release builds. Railway addresses are already `https://`.
- **Signing out** works as normal. Uninstalling the app also clears its sign-in.
- **Downloads and links that open new tabs**, such as **Export system** and the member viewer link, depend on each platform's WebView. If one does nothing in the app, use the dashboard in a browser for that task.
- **iOS** is possible with the same project (`npx tauri ios init`) but needs a Mac, Xcode, and an Apple developer account to install outside the simulator.
