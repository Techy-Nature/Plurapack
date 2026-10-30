package app.plurapack.voiceplayer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.MediaPlayer
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import android.webkit.CookieManager
import app.tauri.plugin.JSObject
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Plays Plurapack voice clips from a lasting media notification.
 *
 * This mirrors the dashboard's browser playback (voice_playback.js): it polls
 * /api/voice/events every two seconds, takes each clip once from
 * /api/voice/audio/{id}, and plays them in order. It runs as a foreground
 * service, so it keeps going while the app is in the background or closed.
 * All state lives on the main thread; only HTTP runs on [network].
 */
class VoicePlayerService : Service() {
    companion object {
        const val ACTION_START = "app.plurapack.voiceplayer.START"
        const val ACTION_STOP = "app.plurapack.voiceplayer.STOP"
        const val ACTION_TOGGLE = "app.plurapack.voiceplayer.TOGGLE"
        const val ACTION_SKIP = "app.plurapack.voiceplayer.SKIP"
        const val EXTRA_SERVER = "server"

        private const val TAG = "PlurapackVoicePlayer"
        private const val CHANNEL = "voice_player"
        private const val NOTIFICATION_ID = 7106
        private const val POLL_MS = 2000L
        private const val TIMEOUT_MS = 15000
        private const val MAX_QUEUE = 100

        @Volatile var running = false
            private set
        @Volatile private var paused = false
        @Volatile private var status = "Voice player is off."

        fun snapshot(starting: Boolean = false, stopping: Boolean = false): JSObject {
            val state = JSObject()
            state.put("available", true)
            state.put("running", if (stopping) false else running || starting)
            state.put("paused", !stopping && paused)
            state.put("status", when {
                stopping -> "Voice player is off."
                starting && !running -> "Starting the voice player…"
                else -> status
            })
            return state
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var network: ExecutorService
    private lateinit var session: MediaSession
    private lateinit var audio: AudioManager
    private var focus: AudioFocusRequest? = null
    private var wakeLock: PowerManager.WakeLock? = null

    private var server: String? = null
    private var player: MediaPlayer? = null
    private var clip: File? = null
    private var polling = false
    private var fetching = false
    private var clipCount = 0
    private val queue = ArrayDeque<String>()
    private val consumed = LinkedHashSet<String>()

    private val pollLoop = object : Runnable {
        override fun run() {
            poll()
            main.postDelayed(this, POLL_MS)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        network = Executors.newSingleThreadExecutor()
        audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        session = MediaSession(this, "Plurapack voice").apply {
            setCallback(object : MediaSession.Callback() {
                override fun onPlay() = resume()
                override fun onPause() = pause()
                override fun onSkipToNext() = skip()
                override fun onStop() = shutdown("Voice player is off.")
                override fun onCustomAction(action: String, extras: android.os.Bundle?) {
                    if (action == ACTION_STOP) shutdown("Voice player is off.")
                }
            })
            launchIntent()?.let { setSessionActivity(it) }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(CHANNEL, "Voice player", NotificationManager.IMPORTANCE_LOW)
            channel.description = "Plays proxied voice messages with media controls."
            channel.setShowBadge(false)
            notifications().createNotificationChannel(channel)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> begin(intent.getStringExtra(EXTRA_SERVER))
            ACTION_TOGGLE -> if (paused) resume() else pause()
            ACTION_SKIP -> skip()
            ACTION_STOP -> shutdown("Voice player is off.")
        }
        // A stale notification button or a restart without a server has nothing to play.
        if (!running) stopSelf()
        return START_NOT_STICKY
    }

    private fun begin(address: String?) {
        if (address == null) {
            if (!running) stopSelf()
            return
        }
        val firstStart = !running
        if (address != server) {
            // A different server has different clips; forget the old queue.
            releasePlayer()
            queue.clear()
            consumed.clear()
        }
        server = address
        running = true
        paused = false
        status = "Waiting for new speech…"
        session.isActive = true
        refresh()
        if (firstStart) {
            val notification = buildNotification()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        }
        holdWakeLock(true)
        main.removeCallbacks(pollLoop)
        main.post(pollLoop)
        Log.i(TAG, "Voice player started")
    }

    private fun pause() {
        if (!running || paused) return
        paused = true
        player?.pause()
        status = "Paused. New speech waits until you resume."
        holdWakeLock(false)
        refresh()
    }

    private fun resume() {
        if (!running || !paused) return
        paused = false
        holdWakeLock(true)
        val current = player
        if (current != null) {
            requestFocus()
            current.start()
            status = "Playing speech…"
            refresh()
        } else {
            status = "Waiting for new speech…"
            refresh()
            pump()
        }
    }

    private fun skip() {
        if (!running) return
        if (player != null) {
            releasePlayer()
            status = if (paused) "Skipped. Still paused." else "Skipped."
            refresh()
        }
        pump()
    }

    private fun shutdown(message: String) {
        main.removeCallbacks(pollLoop)
        releasePlayer()
        queue.clear()
        running = false
        paused = false
        status = message
        holdWakeLock(false)
        session.isActive = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION") stopForeground(true)
        }
        stopSelf()
        Log.i(TAG, "Voice player stopped")
    }

    override fun onDestroy() {
        if (running) shutdown("Voice player is off.")
        session.release()
        network.shutdownNow()
        super.onDestroy()
    }

    private fun poll() {
        val address = server ?: return
        if (!running || polling) return
        polling = true
        val cookie = CookieManager.getInstance().getCookie(address)
        network.execute {
            var ids: List<String>? = null
            var code = 0
            try {
                val connection = open("$address/api/voice/events", cookie, "application/json")
                code = connection.responseCode
                if (code == 200) {
                    val events = JSONArray(connection.inputStream.bufferedReader().use { it.readText() })
                    ids = (0 until events.length()).mapNotNull {
                        events.optJSONObject(it)?.optString("id")?.takeIf { id -> id.isNotEmpty() }
                    }
                }
                connection.disconnect()
            } catch (error: Exception) {
                Log.w(TAG, "Voice event polling unavailable")
            }
            main.post {
                polling = false
                if (!running) return@post
                when {
                    code == 401 -> shutdown("Logged out. Log in to the dashboard and start the player again.")
                    ids == null -> if (player == null && !fetching) {
                        status = "Voice playback temporarily unavailable."
                        refresh()
                    }
                    else -> {
                        for (id in ids!!) {
                            if (queue.size >= MAX_QUEUE) break
                            if (id !in consumed && id !in queue) queue.addLast(id)
                        }
                        if (player == null && !fetching && queue.isEmpty() && !paused &&
                            status != "Waiting for new speech…"
                        ) {
                            status = "Waiting for new speech…"
                            refresh()
                        }
                        pump()
                    }
                }
            }
        }
    }

    private fun pump() {
        val address = server ?: return
        if (!running || paused || player != null || fetching) return
        val id = queue.removeFirstOrNull() ?: return
        // The audio endpoint is consume-on-read, so never ask for an ID twice.
        consumed.add(id)
        if (consumed.size > 256) consumed.remove(consumed.first())
        fetching = true
        val cookie = CookieManager.getInstance().getCookie(address)
        val file = File(cacheDir, "voice-clip-${++clipCount}.wav")
        network.execute {
            var code = 0
            try {
                val connection = open(
                    "$address/api/voice/audio/${URLEncoder.encode(id, "UTF-8")}", cookie, "audio/wav"
                )
                code = connection.responseCode
                if (code == 200) {
                    connection.inputStream.use { input -> file.outputStream().use { input.copyTo(it) } }
                }
                connection.disconnect()
            } catch (error: Exception) {
                file.delete()
                code = 0
            }
            main.post {
                fetching = false
                when {
                    !running -> file.delete()
                    code == 401 -> {
                        file.delete()
                        shutdown("Logged out. Log in to the dashboard and start the player again.")
                    }
                    code == 200 -> play(file)
                    else -> {
                        file.delete()
                        Log.w(TAG, "Voice clip unavailable")
                        status = "Voice clip unavailable."
                        refresh()
                        pump()
                    }
                }
            }
        }
    }

    private fun play(file: File) {
        clip = file
        try {
            val media = MediaPlayer()
            player = media
            media.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            media.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK)
            media.setDataSource(file.path)
            media.setOnCompletionListener { if (player === it) finishClip("Waiting for new speech…") }
            media.setOnErrorListener { mp, _, _ ->
                if (player === mp) finishClip("Voice clip unavailable.")
                true
            }
            media.prepare()
            if (paused) {
                status = "Paused. New speech waits until you resume."
            } else {
                requestFocus()
                media.start()
                status = "Playing speech…"
            }
            refresh()
        } catch (error: Exception) {
            Log.w(TAG, "Voice clip could not play")
            finishClip("Voice clip unavailable.")
        }
    }

    private fun finishClip(message: String) {
        releasePlayer()
        status = message
        refresh()
        pump()
    }

    private fun releasePlayer() {
        player?.let {
            it.setOnCompletionListener(null)
            it.setOnErrorListener(null)
            try { it.stop() } catch (_: IllegalStateException) {}
            it.release()
        }
        player = null
        clip?.delete()
        clip = null
        abandonFocus()
    }

    // Ask other apps (music, podcasts) to duck while a clip speaks.
    private fun requestFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val request = focus ?: AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .build().also { focus = it }
            audio.requestAudioFocus(request)
        } else {
            @Suppress("DEPRECATION")
            audio.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        }
    }

    private fun abandonFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            focus?.let { audio.abandonAudioFocusRequest(it) }
        } else {
            @Suppress("DEPRECATION") audio.abandonAudioFocus(null)
        }
    }

    // Polling has to keep running with the screen off, or speech would only
    // arrive when the phone wakes. Pausing releases it to save battery.
    private fun holdWakeLock(hold: Boolean) {
        if (hold) {
            if (wakeLock?.isHeld == true) return
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Plurapack:voice-player").apply {
                setReferenceCounted(false)
                acquire()
            }
        } else {
            wakeLock?.let { if (it.isHeld) it.release() }
            wakeLock = null
        }
    }

    private fun open(address: String, cookie: String?, accept: String): HttpURLConnection {
        val connection = URL(address).openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.useCaches = false
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("Accept", accept)
        connection.setRequestProperty("Cache-Control", "no-store")
        if (!cookie.isNullOrEmpty()) connection.setRequestProperty("Cookie", cookie)
        return connection
    }

    private fun refresh() {
        val playing = running && !paused
        val state = PlaybackState.Builder()
            .setActions(
                PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE or
                    PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_SKIP_TO_NEXT or
                    PlaybackState.ACTION_STOP
            )
            // Android 13+ builds the tray controls from the session, so Stop is a custom action there.
            .addCustomAction(
                PlaybackState.CustomAction.Builder(ACTION_STOP, "Stop", R.drawable.ic_voice_stop).build()
            )
            .setState(
                if (!running) PlaybackState.STATE_STOPPED
                else if (playing) PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED,
                PlaybackState.PLAYBACK_POSITION_UNKNOWN,
                if (playing) 1f else 0f
            )
            .build()
        session.setPlaybackState(state)
        session.setMetadata(
            MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, "Plurapack voice")
                .putString(MediaMetadata.METADATA_KEY_ARTIST, status)
                .putString(MediaMetadata.METADATA_KEY_ALBUM, "Plurapack")
                .build()
        )
        if (running) notifications().notify(NOTIFICATION_ID, buildNotification())
    }

    private fun buildNotification(): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(this)
        }
        val toggle = if (paused) {
            action(R.drawable.ic_voice_play, "Resume", ACTION_TOGGLE, 1)
        } else {
            action(R.drawable.ic_voice_pause, "Pause", ACTION_TOGGLE, 1)
        }
        builder
            .setSmallIcon(R.drawable.ic_voice_player)
            .setContentTitle("Plurapack voice")
            .setContentText(status)
            .setOngoing(true)
            .setShowWhen(false)
            .setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setCategory(Notification.CATEGORY_TRANSPORT)
            .addAction(toggle)
            .addAction(action(R.drawable.ic_voice_skip, "Skip", ACTION_SKIP, 2))
            .addAction(action(R.drawable.ic_voice_stop, "Stop", ACTION_STOP, 3))
            .setStyle(
                Notification.MediaStyle()
                    .setMediaSession(session.sessionToken)
                    .setShowActionsInCompactView(0, 1, 2)
            )
        launchIntent()?.let { builder.setContentIntent(it) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
        }
        return builder.build()
    }

    private fun action(icon: Int, title: String, command: String, request: Int): Notification.Action {
        val intent = PendingIntent.getService(
            this, request, Intent(this, VoicePlayerService::class.java).setAction(command),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Action.Builder(Icon.createWithResource(this, icon), title, intent).build()
    }

    private fun launchIntent(): PendingIntent? {
        val intent = packageManager.getLaunchIntentForPackage(packageName) ?: return null
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
        return PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun notifications() = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
}
