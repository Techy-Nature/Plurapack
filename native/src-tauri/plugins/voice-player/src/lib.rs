//! A lasting media notification that plays Plurapack voice clips on Android.
//!
//! The dashboard is a remote page, so it reaches these commands through the
//! `voice-player` capability. The Kotlin side reads the server address from
//! the window itself and reuses the window's login cookie, so no address or
//! credential ever passes through these commands.

use serde::{Deserialize, Serialize};
use tauri::{
    plugin::{Builder, TauriPlugin},
    Runtime,
};
#[cfg(target_os = "android")]
use tauri::Manager;

/// What the dashboard shows next to its voice playback button.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayerState {
    /// False on desktop (and iOS), where the dashboard keeps its own browser playback.
    pub available: bool,
    pub running: bool,
    pub paused: bool,
    #[serde(default)]
    pub status: String,
}

#[cfg(target_os = "android")]
struct Native<R: Runtime>(tauri::plugin::PluginHandle<R>);

#[cfg(target_os = "android")]
fn call<R: Runtime>(app: &tauri::AppHandle<R>, command: &str) -> Result<PlayerState, String> {
    app.state::<Native<R>>()
        .0
        .run_mobile_plugin::<PlayerState>(command, ())
        .map_err(|error| error.to_string())
}

#[cfg(not(target_os = "android"))]
fn call<R: Runtime>(_app: &tauri::AppHandle<R>, command: &str) -> Result<PlayerState, String> {
    match command {
        "status" => Ok(PlayerState::default()),
        _ => Err("The notification player is only available on Android.".into()),
    }
}

// Mobile plugin calls block until Kotlin answers, so keep them off the main thread.
#[tauri::command]
async fn status<R: Runtime>(app: tauri::AppHandle<R>) -> Result<PlayerState, String> {
    call(&app, "status")
}

#[tauri::command]
async fn start<R: Runtime>(app: tauri::AppHandle<R>) -> Result<PlayerState, String> {
    call(&app, "start")
}

#[tauri::command]
async fn stop<R: Runtime>(app: tauri::AppHandle<R>) -> Result<PlayerState, String> {
    call(&app, "stop")
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("voice-player")
        .invoke_handler(tauri::generate_handler![status, start, stop])
        .setup(|_app, _api| {
            #[cfg(target_os = "android")]
            _app.manage(Native(
                _api.register_android_plugin("app.plurapack.voiceplayer", "VoicePlayerPlugin")?,
            ));
            Ok(())
        })
        .build()
}
