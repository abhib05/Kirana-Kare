package com.kiranacopilot.companion

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * Reads UPI credit notifications (PhonePe / GPay / Paytm / bank SMS apps) and forwards the raw text
 * to the Kirana Copilot backend, which does the parsing + reconciliation (POST /api/upi-alert).
 *
 * Setup: add to an Android app module with the OkHttp dependency, declare the service in the manifest with
 * permission android.permission.BIND_NOTIFICATION_LISTENER_SERVICE, then grant "Notification access"
 * to the app in system settings. Set BACKEND_URL to your server (Cloud Run URL in production).
 */
class UpiAlertListener : NotificationListenerService() {
    private val http = OkHttpClient()
    private val io = Executors.newSingleThreadExecutor()
    private val watched = setOf(
        "com.phonepe.app", "com.google.android.apps.nbu.paisa.user", "net.one97.paytm",
        "com.google.android.apps.messaging", "com.android.mms",
    )

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in watched) return
        val extras = sbn.notification.extras
        val text = listOfNotNull(extras.getCharSequence("android.title"), extras.getCharSequence("android.bigText")
            ?: extras.getCharSequence("android.text")).joinToString(" ")
        if (!Regex("received|credited", RegexOption.IGNORE_CASE).containsMatchIn(text)) return
        io.execute {
            val body = JSONObject().put("raw", text).toString().toRequestBody("application/json".toMediaType())
            runCatching { http.newCall(Request.Builder().url("$BACKEND_URL/api/upi-alert").post(body).build()).execute().close() }
        }
    }

    companion object { const val BACKEND_URL = "https://YOUR-SERVICE.run.app" }
}
