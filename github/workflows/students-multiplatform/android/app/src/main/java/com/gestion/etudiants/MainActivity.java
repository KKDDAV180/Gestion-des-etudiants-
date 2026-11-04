package com.gestion.etudiants;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * Coque Android de l'application "Gestion des etudiants".
 *
 * Au premier lancement, l'utilisateur saisit l'adresse du serveur
 * (ex. http://192.168.1.20:5000 = le PC qui execute GestionEtudiants.exe
 * en mode --lan, ou un serveur Docker / hebergeur en https://...).
 * L'interface web est ensuite chargee dans un WebView plein ecran.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "student_app";
    private static final String KEY_URL = "server_url";

    private SharedPreferences prefs;
    private WebView web;
    private String serverUrl;
    private boolean loadFailed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverUrl = prefs.getString(KEY_URL, "");
        if (serverUrl.isEmpty()) {
            showSetup("");
        } else {
            showWeb();
        }
    }

    // ------------------------------------------------------------------ //
    // Ecran de configuration
    // ------------------------------------------------------------------ //
    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void showSetup(String prefill) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setPadding(dp(24), dp(24), dp(24), dp(24));
        root.setBackgroundColor(Color.parseColor("#0b1220"));

        TextView title = new TextView(this);
        title.setText("Gestion des etudiants");
        title.setTextColor(Color.WHITE);
        title.setTextSize(24);
        root.addView(title);

        TextView help = new TextView(this);
        help.setText("Saisissez l'adresse du serveur.\n"
                + "Exemple (PC sur le meme Wi-Fi) : http://192.168.1.20:5000\n"
                + "Exemple (serveur en ligne) : https://etudiants.mon-ecole.org");
        help.setTextColor(Color.parseColor("#9fb0c8"));
        help.setPadding(0, dp(12), 0, dp(16));
        root.addView(help);

        final EditText input = new EditText(this);
        input.setHint("http://192.168.1.20:5000");
        input.setText(prefill);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.parseColor("#5b6b82"));
        input.setTextSize(16);
        root.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        final TextView error = new TextView(this);
        error.setTextColor(Color.parseColor("#f87171"));
        error.setPadding(0, dp(8), 0, 0);
        root.addView(error);

        Button ok = new Button(this);
        ok.setText("Se connecter");
        ok.setMinHeight(dp(48));
        ok.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String url = normalize(input.getText().toString());
                if (url == null) {
                    error.setText("Adresse invalide.");
                    return;
                }
                serverUrl = url;
                prefs.edit().putString(KEY_URL, url).apply();
                showWeb();
            }
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(16);
        root.addView(ok, lp);

        web = null;
        setContentView(root);
    }

    /** Ajoute http:// si besoin et verifie un minimum la forme de l'adresse. */
    private static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty() || s.contains(" ")) return null;
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        Uri u = Uri.parse(s);
        if (u.getHost() == null || u.getHost().isEmpty()) return null;
        return s;
    }

    // ------------------------------------------------------------------ //
    // WebView
    // ------------------------------------------------------------------ //
    private void showWeb() {
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0b1220"));

        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);          // la cle API est gardee dans localStorage
        st.setDatabaseEnabled(true);
        st.setLoadWithOverviewMode(true);
        st.setUseWideViewPort(true);
        st.setSupportZoom(false);
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);

        web.addJavascriptInterface(new Bridge(), "AndroidBridge");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (!url.startsWith("data:") && !url.startsWith("about:")) loadFailed = false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    loadFailed = true;
                    view.loadDataWithBaseURL(null, errorPage(serverUrl), "text/html", "utf-8", null);
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                // Reste dans l'application pour le serveur configure, ouvre le reste dehors.
                Uri target = req.getUrl();
                Uri base = Uri.parse(serverUrl);
                if (target.getHost() != null && target.getHost().equals(base.getHost())) return false;
                if ("data".equals(target.getScheme()) || "about".equals(target.getScheme())) return false;
                try {
                    startActivity(new android.content.Intent(android.content.Intent.ACTION_VIEW, target));
                } catch (Exception ignored) { /* pas d'application pour ce lien */ }
                return true;
            }
        });
        web.setDownloadListener((url, ua, disp, mime, len) -> {
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                String name = android.webkit.URLUtil.guessFileName(url, disp, mime);
                r.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, "Telechargement : " + name, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Toast.makeText(this, "Telechargement impossible", Toast.LENGTH_LONG).show();
            }
        });

        setContentView(web);
        web.loadUrl(serverUrl);
    }

    private static String errorPage(String url) {
        return "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;background:#0b1220;color:#e2e8f0;padding:24px'>"
                + "<h2>Serveur injoignable</h2>"
                + "<p>Impossible de contacter <b>" + android.text.TextUtils.htmlEncode(url) + "</b>.</p>"
                + "<ul><li>Le serveur (PC / Docker) est-il demarre ?</li>"
                + "<li>Le telephone est-il sur le meme reseau Wi-Fi ?</li>"
                + "<li>L'adresse est-elle correcte ?</li></ul>"
                + "<p><button style='font-size:18px;padding:12px 20px' onclick='AndroidBridge.retry()'>Reessayer</button> "
                + "<button style='font-size:18px;padding:12px 20px' onclick='AndroidBridge.changeServer()'>Changer l'adresse</button></p>"
                + "</body></html>";
    }

    /** Pont JavaScript <-> Java (appele depuis index.html et la page d'erreur). */
    private class Bridge {
        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> {
                if (web != null) web.loadUrl(serverUrl);
            });
        }

        @JavascriptInterface
        public void changeServer() {
            runOnUiThread(() -> showSetup(serverUrl));
        }

        @JavascriptInterface
        public void saveFile(final String name, final String mime, final String base64) {
            try {
                byte[] data = Base64.decode(base64, Base64.DEFAULT);
                String safe = name.replaceAll("[\\\\/:*?\"<>|]", "_");
                String where;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.Downloads.DISPLAY_NAME, safe);
                    cv.put(MediaStore.Downloads.MIME_TYPE, mime);
                    cv.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                    Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (uri == null) throw new IllegalStateException("insert");
                    try (OutputStream os = getContentResolver().openOutputStream(uri)) {
                        os.write(data);
                    }
                    where = "Telechargements";
                } else {
                    File dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
                    File out = new File(dir, safe);
                    try (FileOutputStream os = new FileOutputStream(out)) {
                        os.write(data);
                    }
                    where = out.getParent();
                }
                final String msg = safe + " enregistre dans " + where;
                runOnUiThread(() -> Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show());
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(MainActivity.this,
                        "Enregistrement impossible", Toast.LENGTH_LONG).show());
            }
        }
    }

    // ------------------------------------------------------------------ //
    // Bouton retour : navigation, puis menu Quitter / Changer de serveur
    // ------------------------------------------------------------------ //
    @Override
    public void onBackPressed() {
        if (web != null && !loadFailed && web.canGoBack()) {
            web.goBack();
            return;
        }
        if (web == null) {
            if (!serverUrl.isEmpty()) {
                showWeb();            // depuis l'ecran de configuration : retour a l'appli
            } else {
                super.onBackPressed();
            }
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Gestion des etudiants")
                .setItems(new String[]{"Quitter", "Changer l'adresse du serveur"}, (d, which) -> {
                    if (which == 0) finish();
                    else showSetup(serverUrl);
                })
                .setNegativeButton("Annuler", null)
                .show();
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
