package app.pumpkin

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.View
import android.view.WindowInsets
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

/** Pumpkin's window: its web interface, served by Pumpkin itself on this phone. */
class MainActivity : Activity() {

    companion object {
        var current: MainActivity? = null
        private const val PICK_FILES = 7
    }

    private lateinit var web: WebView
    private var pickCallback: ValueCallback<Array<Uri>>? = null
    private val handler = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        current = this
        PumpkinService.start(this)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }

        web = WebView(this)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            setSupportZoom(false)
            userAgentString = "$userAgentString PumpkinApp"   // listed as "Pumpkin app" under signed-in devices
        }
        CookieManager.getInstance().setAcceptCookie(true)
        setContentView(web)
        fitInsets(web)

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host != "127.0.0.1" && u.host != "localhost" && (u.scheme == "http" || u.scheme == "https")) {
                    startActivity(Intent(Intent.ACTION_VIEW, u))     // other sites open in the browser
                    return true
                }
                return false
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                pickCallback?.onReceiveValue(null)
                pickCallback = callback
                return try {
                    startActivityForResult(params.createIntent().putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE), PICK_FILES)
                    true
                } catch (e: Exception) {
                    pickCallback = null
                    false
                }
            }
        }
        web.setDownloadListener { url, _, disposition, mime, _ -> download(url, disposition, mime) }

        web.loadDataWithBaseURL(null, page("Starting Pumpkin…"), "text/html", "utf-8", null)
        waitForPumpkin(0)
    }

    /** Shows Pumpkin as soon as its server answers. */
    private fun waitForPumpkin(tries: Int) {
        val url = PumpkinService.url
        val err = PumpkinService.error
        when {
            url != null -> web.loadUrl(url)
            err != null -> web.loadDataWithBaseURL(null, page("Pumpkin did not start.<pre>${err.replace("<", "&lt;")}</pre>"), "text/html", "utf-8", null)
            else -> handler.postDelayed({ waitForPumpkin(tries + 1) }, 250)
        }
    }

    /** Edge to edge on new Android: keep the page clear of the status bar, the gesture bar and the keyboard. */
    private fun fitInsets(v: View) {
        if (Build.VERSION.SDK_INT >= 30) {
            v.setOnApplyWindowInsetsListener { view, insets ->
                val i = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime() or WindowInsets.Type.displayCutout())
                view.setPadding(i.left, i.top, i.right, i.bottom)
                WindowInsets.CONSUMED
            }
        }
    }

    /** Files from the Files page go to the phone's Downloads folder. */
    private fun download(url: String, disposition: String?, mime: String?) {
        val name = URLUtil.guessFileName(url, disposition, mime)
        val cookie = CookieManager.getInstance().getCookie(url)
        Toast.makeText(this, "Downloading $name", Toast.LENGTH_SHORT).show()
        Thread {
            val ok = try {
                val c = URL(url).openConnection() as HttpURLConnection
                if (cookie != null) c.setRequestProperty("Cookie", cookie)
                c.inputStream.use { input ->
                    if (Build.VERSION.SDK_INT >= 29) {
                        val values = ContentValues().apply {
                            put(MediaStore.Downloads.DISPLAY_NAME, name)
                            put(MediaStore.Downloads.MIME_TYPE, mime ?: "application/octet-stream")
                        }
                        val item = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)!!
                        contentResolver.openOutputStream(item)!!.use { input.copyTo(it) }
                    } else {
                        @Suppress("DEPRECATION")
                        val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                        dir.mkdirs()
                        FileOutputStream(File(dir, name)).use { input.copyTo(it) }
                    }
                }
                true
            } catch (e: Exception) {
                false
            }
            runOnUiThread {
                Toast.makeText(this, if (ok) "Saved $name in Downloads" else "Could not save $name", Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    @Deprecated("Activity result API without AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == PICK_FILES) {
            val uris = WebChromeClient.FileChooserParams.parseResult(resultCode, data)
                ?: data?.clipData?.let { c -> Array(c.itemCount) { c.getItemAt(it).uri } }
            pickCallback?.onReceiveValue(uris)
            pickCallback = null
            return
        }
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
    }

    @Deprecated("Back handling without AndroidX")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else moveTaskToBack(true)   // Pumpkin keeps running in the background
    }

    override fun onDestroy() {
        if (current === this) current = null
        super.onDestroy()
    }

    private fun page(text: String) =
        "<html><head><meta name='viewport' content='width=device-width'><style>body{font:16px system-ui;color:#666;" +
            "display:flex;align-items:center;justify-content:center;height:90vh;margin:0;padding:16px;text-align:center}" +
            "@media(prefers-color-scheme:dark){body{background:#1c1c1c;color:#aaa}}pre{white-space:pre-wrap;font-size:12px;text-align:left}" +
            "</style></head><body><div>$text</div></body></html>"
}
