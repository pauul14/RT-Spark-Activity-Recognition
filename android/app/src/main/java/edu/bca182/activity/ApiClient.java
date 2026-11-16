package edu.bca182.activity;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.IOException;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** System trust/hostname verification, no redirect or plaintext fallback. */
final class ApiClient {
    static final class ApiError extends IOException {
        final String userMessage;
        final boolean authentication;
        ApiError(String message) { this(message,false); }
        ApiError(String message, boolean auth) { super(message); userMessage = message; authentication=auth; }
    }
    private volatile HttpsURLConnection active;
    void cancel() { HttpsURLConnection connection = active; if (connection != null) connection.disconnect(); }
    JSONObject get(SecretStore.Config config, String path) throws Exception {
        if (Thread.currentThread().isInterrupted()) throw new IOException("Request cancelled.");
        HttpsURLConnection connection = (HttpsURLConnection)new URL(config.base + path).openConnection();
        active = connection;
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(2000); connection.setReadTimeout(2000); connection.setUseCaches(false);
            connection.setRequestProperty("Connection", "close");
            connection.setRequestProperty("Authorization", "Bearer " + config.token);
            connection.setRequestProperty("Accept", "application/json");
            int code = connection.getResponseCode();
            if (code == 401 || code == 403) throw new ApiError("Authentication failed. Check the API token.",true);
            if (code >= 300 && code < 400) throw new ApiError("Server redirected. Check the HTTPS endpoint and port authorization.");
            if (code != 200) throw new ApiError("Server returned HTTP " + code);
            String type = connection.getContentType();
            if (type == null || !type.toLowerCase(java.util.Locale.ROOT).startsWith("application/json")) throw new IOException("Server did not return API JSON.");
            try (InputStream input = connection.getInputStream(); ByteArrayOutputStream body = new ByteArrayOutputStream()) {
                byte[] block = new byte[2048]; int count;
                long deadline = android.os.SystemClock.elapsedRealtime() + 3000;
                while ((count = input.read(block)) != -1) {
                    if (Thread.currentThread().isInterrupted() || android.os.SystemClock.elapsedRealtime() > deadline) throw new IOException("Request cancelled or timed out.");
                    if (body.size() + count > 65536) throw new IOException("API response exceeded its size limit.");
                    body.write(block, 0, count);
                }
                String json = new String(body.toByteArray(), StandardCharsets.UTF_8);
                int depth = 0; boolean quoted = false, escaped = false;
                for (int index = 0; index < json.length(); ++index) {
                    char c = json.charAt(index);
                    if (quoted) {
                        if (escaped) escaped = false;
                        else if (c == '\\') escaped = true;
                        else if (c == '"') quoted = false;
                    } else if (c == '"') quoted = true;
                    else if (c == '{' || c == '[') { if (++depth > 16) throw new IOException("API JSON nesting limit exceeded."); }
                    else if (c == '}' || c == ']') --depth;
                }
                return new JSONObject(json);
            }
        } finally { connection.disconnect(); if (active == connection) active = null; }
    }
    static String deviceQuery(String device) throws Exception { return URLEncoder.encode(device, "UTF-8"); }
}
