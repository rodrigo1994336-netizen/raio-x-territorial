package br.com.afp.captadorradio;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int REQ_PERMS = 700;
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private TextView status;
    private TextView connection;
    private TextView lastTranscript;
    private TextView queueCount;
    private EditText email;
    private EditText password;
    private EditText company;

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (RadioCaptureService.ACTION_STATE.equals(intent.getAction())) {
                setStatus(intent.getStringExtra(RadioCaptureService.EXTRA_STATE));
                refreshQueue();
            } else if (RadioCaptureService.ACTION_TRANSCRIPT.equals(intent.getAction())) {
                String t = intent.getStringExtra(RadioCaptureService.EXTRA_TEXT);
                boolean fin = intent.getBooleanExtra("final", false);
                lastTranscript.setText((fin ? "Última comunicação:\n" : "Ouvindo:\n") + (t == null ? "" : t));
                if (fin) refreshQueue();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        loadPrefs();
        refreshConnection();
        refreshQueue();
        registerAppReceiver();
        ensurePermissions();
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(18), dp(18), dp(18), dp(30));
        scroll.addView(root);

        TextView title = text("CAPTADOR RÁDIO", 26, true);
        title.setTextColor(Color.rgb(20, 20, 20));
        root.addView(title);

        TextView sub = text("Escuta contínua da rede rádio → transcrição → fila de empenhos no Escala Fácil", 15, false);
        sub.setPadding(0, dp(4), 0, dp(14));
        root.addView(sub);

        status = card("Status: parado");
        root.addView(status);

        connection = card("Base44: verificando...");
        root.addView(connection);

        queueCount = card("Fila local: 0 pendente(s)");
        root.addView(queueCount);

        company = field("Companhia padrão (ex.: 220ª Cia)", false);
        root.addView(company);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        Button start = button("INICIAR ESCUTA");
        Button stop = button("PARAR");
        actions.addView(start, new LinearLayout.LayoutParams(0, dp(54), 2));
        LinearLayout.LayoutParams stopLp = new LinearLayout.LayoutParams(0, dp(54), 1);
        stopLp.setMargins(dp(8),0,0,0);
        actions.addView(stop, stopLp);
        root.addView(actions);

        lastTranscript = card("Nenhuma comunicação captada ainda.");
        lastTranscript.setMinHeight(dp(120));
        root.addView(lastTranscript);

        TextView authTitle = text("CONEXÃO COM O ESCALA FÁCIL", 17, true);
        authTitle.setPadding(0, dp(18), 0, dp(6));
        root.addView(authTitle);

        email = field("E-mail de acesso ao Escala Fácil/Base44", false);
        password = field("Senha", true);
        root.addView(email);
        root.addView(password);

        LinearLayout authActions = new LinearLayout(this);
        authActions.setOrientation(LinearLayout.HORIZONTAL);
        Button login = button("CONECTAR");
        Button resend = button("REENVIAR PENDENTES");
        authActions.addView(login, new LinearLayout.LayoutParams(0, dp(52), 1));
        LinearLayout.LayoutParams resendLp = new LinearLayout.LayoutParams(0, dp(52), 1);
        resendLp.setMargins(dp(8),0,0,0);
        authActions.addView(resend, resendLp);
        root.addView(authActions);

        TextView note = text("A tela pode ser bloqueada. O serviço continua em primeiro plano com microfone ativo e mantém apenas um wake lock parcial, sem manter a tela acesa.", 13, false);
        note.setPadding(0, dp(16), 0, 0);
        root.addView(note);

        start.setOnClickListener(v -> startCapture());
        stop.setOnClickListener(v -> stopCapture());
        login.setOnClickListener(v -> doLogin());
        resend.setOnClickListener(v -> resendPending());
        company.setOnFocusChangeListener((v, hasFocus) -> {
            if (!hasFocus) saveCompany();
        });

        setContentView(scroll);
    }

    private void startCapture() {
        saveCompany();
        if (!hasAudioPermission()) {
            ensurePermissions();
            return;
        }
        Intent i = new Intent(this, RadioCaptureService.class);
        i.setAction("START");
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i); else startService(i);
        setStatus("Iniciando captador...");
    }

    private void stopCapture() {
        Intent i = new Intent(this, RadioCaptureService.class);
        i.setAction("STOP");
        startService(i);
    }

    private void doLogin() {
        String e = email.getText().toString().trim();
        String p = password.getText().toString();
        if (e.isBlank() || p.isBlank()) {
            toast("Informe e-mail e senha.");
            return;
        }
        connection.setText("Base44: conectando...");
        io.execute(() -> {
            try {
                new Base44Client(this).login(e, p);
                getSharedPreferences("captador_prefs", MODE_PRIVATE).edit().putString("email", e).apply();
                runOnUiThread(() -> {
                    password.setText("");
                    connection.setText("Base44: conectado ao Escala Fácil");
                    resendPending();
                });
            } catch (Exception ex) {
                runOnUiThread(() -> connection.setText("Base44: falha no login — " + shortMessage(ex)));
            }
        });
    }

    private void resendPending() {
        io.execute(() -> {
            AppDb db = new AppDb(this);
            Base44Client client = new Base44Client(this);
            if (!client.hasToken()) {
                runOnUiThread(() -> {
                    connection.setText("Base44: faça login para sincronizar.");
                    refreshQueue();
                });
                return;
            }
            List<DispatchRecord> pending = db.pending(100);
            int sent = 0;
            String error = null;
            for (DispatchRecord d : pending) {
                try {
                    if (client.send(d)) {
                        db.markSynced(d.id);
                        sent++;
                    }
                } catch (Exception ex) {
                    error = shortMessage(ex);
                    break;
                }
            }
            int finalSent = sent;
            String finalError = error;
            runOnUiThread(() -> {
                refreshQueue();
                connection.setText(finalError == null
                        ? "Base44: conectado — " + finalSent + " envio(s) sincronizado(s)"
                        : "Base44: sincronização interrompida — " + finalError);
            });
        });
    }

    private void refreshQueue() {
        io.execute(() -> {
            AppDb db = new AppDb(this);
            int count = db.pendingCount();
            DispatchRecord latest = db.latest();
            runOnUiThread(() -> {
                queueCount.setText("Fila local: " + count + " pendente(s) de sincronização");
                if (latest != null && (lastTranscript.getText() == null || lastTranscript.getText().toString().startsWith("Nenhuma"))) {
                    lastTranscript.setText("Última comunicação:\n" + latest.transcription + "\n\nResumo:\n" + latest.summary);
                }
            });
        });
    }

    private void refreshConnection() {
        Base44Client client = new Base44Client(this);
        connection.setText(client.hasToken() ? "Base44: token salvo — pronto para sincronizar" : "Base44: não conectado");
    }

    private void saveCompany() {
        String c = company.getText().toString().trim();
        if (c.isBlank()) c = "220ª Cia";
        getSharedPreferences("captador_prefs", MODE_PRIVATE).edit().putString("company", c).apply();
    }

    private void loadPrefs() {
        String c = getSharedPreferences("captador_prefs", MODE_PRIVATE).getString("company", "220ª Cia");
        String e = getSharedPreferences("captador_prefs", MODE_PRIVATE).getString("email", "");
        company.setText(c);
        email.setText(e);
    }

    private void ensurePermissions() {
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS}, REQ_PERMS);
        } else if (!hasAudioPermission()) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_PERMS);
        }
    }

    private boolean hasAudioPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void registerAppReceiver() {
        IntentFilter f = new IntentFilter();
        f.addAction(RadioCaptureService.ACTION_STATE);
        f.addAction(RadioCaptureService.ACTION_TRANSCRIPT);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, f, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(receiver, f);
    }

    private void setStatus(String s) {
        status.setText("Status: " + (s == null ? "" : s));
    }

    private TextView text(String value, int sp, boolean bold) {
        TextView v = new TextView(this);
        v.setText(value);
        v.setTextSize(sp);
        if (bold) v.setTypeface(null, android.graphics.Typeface.BOLD);
        return v;
    }

    private TextView card(String value) {
        TextView v = text(value, 15, false);
        v.setPadding(dp(14), dp(14), dp(14), dp(14));
        v.setBackgroundColor(Color.rgb(242, 242, 242));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.setMargins(0, 0, 0, dp(8));
        v.setLayoutParams(lp);
        return v;
    }

    private EditText field(String hint, boolean secret) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setTextSize(16);
        e.setSingleLine(true);
        e.setPadding(dp(12), dp(8), dp(12), dp(8));
        if (secret) e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(54));
        lp.setMargins(0, 0, 0, dp(8));
        e.setLayoutParams(lp);
        return e;
    }

    private Button button(String label) {
        Button b = new Button(this);
        b.setText(label);
        b.setTextSize(14);
        b.setGravity(Gravity.CENTER);
        return b;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_SHORT).show(); }

    private String shortMessage(Exception e) {
        String m = e.getMessage();
        if (m == null || m.isBlank()) return e.getClass().getSimpleName();
        return m.length() > 180 ? m.substring(0, 180) : m;
    }

    @Override
    protected void onDestroy() {
        try { unregisterReceiver(receiver); } catch (Exception ignored) {}
        io.shutdownNow();
        super.onDestroy();
    }
}
