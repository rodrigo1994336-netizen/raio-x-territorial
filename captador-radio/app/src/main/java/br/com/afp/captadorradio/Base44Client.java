package br.com.afp.captadorradio;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class Base44Client {
    public static final String APP_ID = "6ac00e0b1b497c55d2b6762e";
    private static final String BASE = "https://base44.app/api";
    private static final String PREF = "captador_prefs";
    private static final String KEY_TOKEN = "base44_token";
    private final Context context;

    public Base44Client(Context context) {
        this.context = context.getApplicationContext();
    }

    public boolean hasToken() {
        return !token().isBlank();
    }

    public String login(String email, String password) throws Exception {
        JSONObject body = new JSONObject();
        body.put("email", email);
        body.put("password", password);

        HttpURLConnection c = connection("/apps/" + APP_ID + "/auth/login", "POST", false);
        writeJson(c, body);
        int code = c.getResponseCode();
        String raw = readResponse(c, code);
        if (code < 200 || code >= 300) throw new Exception("Login Base44 falhou (" + code + "): " + raw);

        JSONObject obj = new JSONObject(raw);
        String accessToken = obj.optString("access_token", "");
        if (accessToken.isBlank()) throw new Exception("Base44 não retornou token de acesso.");
        prefs().edit().putString(KEY_TOKEN, accessToken).apply();
        return accessToken;
    }

    public void logout() {
        prefs().edit().remove(KEY_TOKEN).apply();
    }

    public boolean send(DispatchRecord d) throws Exception {
        if (!hasToken()) return false;

        JSONObject body = new JSONObject();
        body.put("captador_id", deviceId());
        body.put("data_hora", d.createdAt);
        body.put("companhia", d.company);
        body.put("transcricao", d.transcription);
        body.put("resumo", d.summary);
        body.put("natureza", d.nature);
        body.put("local", d.location);
        body.put("referencia", d.reference);
        body.put("prioridade", d.priority);
        body.put("status", "novo");
        body.put("observacoes", "Capturado automaticamente pelo Captador Rádio Android");

        HttpURLConnection c = connection("/apps/" + APP_ID + "/entities/FilaRadio", "POST", true);
        writeJson(c, body);
        int code = c.getResponseCode();
        String raw = readResponse(c, code);

        if (code == 401 || code == 403) {
            if (code == 401) logout();
            throw new Exception("Base44 recusou o envio (" + code + "): " + raw);
        }
        if (code < 200 || code >= 300) throw new Exception("Falha ao enviar (" + code + "): " + raw);
        return true;
    }

    private HttpURLConnection connection(String path, String method, boolean authenticated) throws Exception {
        URL url = new URL(BASE + path);
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setDoInput(true);
        c.setRequestProperty("Content-Type", "application/json");
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("X-App-Id", APP_ID);
        if (authenticated && hasToken()) c.setRequestProperty("Authorization", "Bearer " + token());
        return c;
    }

    private void writeJson(HttpURLConnection c, JSONObject obj) throws Exception {
        c.setDoOutput(true);
        byte[] bytes = obj.toString().getBytes(StandardCharsets.UTF_8);
        c.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream os = c.getOutputStream()) {
            os.write(bytes);
        }
    }

    private String readResponse(HttpURLConnection c, int code) throws Exception {
        InputStream in = (code >= 200 && code < 400) ? c.getInputStream() : c.getErrorStream();
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }

    private SharedPreferences prefs() {
        return context.getSharedPreferences(PREF, Context.MODE_PRIVATE);
    }

    private String token() {
        return prefs().getString(KEY_TOKEN, "");
    }

    private String deviceId() {
        String androidId = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
        String suffix = androidId == null ? "semid" : androidId.substring(Math.max(0, androidId.length() - 6));
        return "Captador-" + Build.MODEL + "-" + suffix;
    }
}
