//! The Plurapack app is a thin native window around the hosted dashboard.
//!
//! The bundled launcher page asks for the Plurapack server address, then the
//! window navigates to that server. The dashboard keeps using its own
//! same-origin cookie login, so no Tauri commands are exposed to it.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running the Plurapack app");
}
