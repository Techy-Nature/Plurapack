//! The Plurapack app is a thin native window around the hosted dashboard.
//!
//! The bundled launcher page asks for the Plurapack server address, then the
//! window navigates to that server. The dashboard keeps using its own
//! same-origin cookie login. The only commands it can reach are the
//! notification voice player's (see capabilities/voice-player.json).

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_voice_player::init())
        .run(tauri::generate_context!())
        .expect("error while running the Plurapack app");
}
