package app.pumpkin

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import com.chaquo.python.Python

/**
 * Keeps Pumpkin (its Python server and the model) running while the app is in use, with the notification
 * Android requires for that. Pumpkin itself is started once, in a background thread.
 */
class PumpkinService : Service() {

    companion object {
        @Volatile var url: String? = null
        @Volatile var error: String? = null
        @Volatile private var started = false
        private var instance: PumpkinService? = null
        private const val RUNNING = "running"
        private const val TASKS = "tasks"
        const val STOP = "app.pumpkin.STOP"

        fun start(context: Context) {
            val i = Intent(context, PumpkinService::class.java)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
        }

        /** Called from Python (pumpkin.android) when a scheduled task finishes. */
        @JvmStatic
        fun notifyTask(title: String, body: String) {
            val s = instance ?: return
            val n = s.builder(TASKS).setContentTitle(title).setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body)).setAutoCancel(true).build()
            s.getSystemService(NotificationManager::class.java).notify((System.currentTimeMillis() % 100000).toInt() + 10, n)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(RUNNING, "Pumpkin is running", NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel(TASKS, "Finished tasks", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            stopEverything()
            return START_NOT_STICKY
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, PumpkinService::class.java).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = builder(RUNNING).setContentTitle("Pumpkin is running").setContentText("Tap to open. Stop it to free the memory.")
            .setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Stop", stop).build()).build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
        if (!started) {
            started = true
            Thread {
                try {
                    val py = Python.getInstance()
                    url = py.getModule("pumpkin.android").callAttr(
                        "start", filesDir.absolutePath, applicationInfo.nativeLibraryDir, cacheDir.absolutePath
                    ).toString()
                } catch (t: Throwable) {
                    error = t.toString()
                    android.util.Log.e("Pumpkin", "Pumpkin did not start", t)
                }
            }.start()
        }
        return START_STICKY
    }

    private fun stopEverything() {
        try {
            Python.getInstance().getModule("pumpkin.android").callAttr("stop")
        } catch (_: Throwable) {
        }
        url = null
        started = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        MainActivity.current?.finishAndRemoveTask()
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    fun builder(channel: String): Notification.Builder =
        (if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, channel) else Notification.Builder(this))
            .setSmallIcon(R.drawable.ic_notification)
}
