package app.plurapack.voiceplayer

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.webkit.WebView
import androidx.core.content.ContextCompat
import app.tauri.PermissionState
import app.tauri.annotation.Command
import app.tauri.annotation.Permission
import app.tauri.annotation.PermissionCallback
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.Plugin

private const val NOTIFICATIONS = "notifications"

/**
 * Lets the dashboard start and stop [VoicePlayerService]. The server address is
 * read from the window rather than taken from the page, so a page can only ask
 * for its own server's voice clips.
 */
@TauriPlugin(
    permissions = [
        Permission(strings = [Manifest.permission.POST_NOTIFICATIONS], alias = NOTIFICATIONS)
    ]
)
class VoicePlayerPlugin(private val activity: Activity) : Plugin(activity) {
    private var webView: WebView? = null

    override fun load(webView: WebView) {
        super.load(webView)
        this.webView = webView
    }

    @Command
    fun status(invoke: Invoke) {
        invoke.resolve(VoicePlayerService.snapshot())
    }

    @Command
    fun start(invoke: Invoke) {
        // Android 13+ asks before showing notifications. The player still runs if
        // this is refused; the media controls just stay out of the tray.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            getPermissionState(NOTIFICATIONS) != PermissionState.GRANTED
        ) {
            requestPermissionForAlias(NOTIFICATIONS, invoke, "startAfterPermission")
        } else {
            begin(invoke)
        }
    }

    @PermissionCallback
    private fun startAfterPermission(invoke: Invoke) {
        begin(invoke)
    }

    private fun begin(invoke: Invoke) {
        activity.runOnUiThread {
            val server = serverOrigin(webView?.url)
            if (server == null) {
                invoke.reject("Open your Plurapack dashboard before starting the player.")
                return@runOnUiThread
            }
            val intent = Intent(activity, VoicePlayerService::class.java)
                .setAction(VoicePlayerService.ACTION_START)
                .putExtra(VoicePlayerService.EXTRA_SERVER, server)
            try {
                ContextCompat.startForegroundService(activity, intent)
                invoke.resolve(VoicePlayerService.snapshot(starting = true))
            } catch (error: Exception) {
                invoke.reject("Android would not start the voice player.", error)
            }
        }
    }

    @Command
    fun stop(invoke: Invoke) {
        activity.runOnUiThread {
            if (VoicePlayerService.running) {
                activity.startService(
                    Intent(activity, VoicePlayerService::class.java)
                        .setAction(VoicePlayerService.ACTION_STOP)
                )
            }
            invoke.resolve(VoicePlayerService.snapshot(stopping = true))
        }
    }

    private fun serverOrigin(url: String?): String? {
        val uri = Uri.parse(url ?: return null)
        val scheme = uri.scheme?.lowercase()
        val host = uri.host?.lowercase()
        if (scheme != "https" && scheme != "http") return null
        // The bundled launcher page is served from this host, not a Plurapack server.
        if (host.isNullOrEmpty() || host == "tauri.localhost") return null
        return "$scheme://${uri.encodedAuthority}"
    }
}
