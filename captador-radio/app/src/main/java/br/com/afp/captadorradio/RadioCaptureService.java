package br.com.afp.captadorradio;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class RadioCaptureService extends Service {
    public static final String ACTION_STATE = "br.com.afp.captadorradio.STATE";
    public static final String ACTION_TRANSCRIPT = "br.com.afp.captadorradio.TRANSCRIPT";
    public static final String EXTRA_TEXT = "text";
    public static final String EXTRA_STATE = "state";

    private static final int NOTIFICATION_ID = 4201;
    private static final String CHANNEL_ID = "radio_capture";
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private SpeechRecognizer recognizer;
    private Intent recognizerIntent;
    private boolean running;
    private PowerManager.WakeLock wakeLock;
    private String lastFinal = "";
    private long lastFinalAt = 0L;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        acquireWakeLock();
        configureRecognizer();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if ("STOP".equals(action)) {
            stopCapture();
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, buildNotification("Escuta ativa"));
        running = true;
        broadcastState("Escutando a rede rádio");
        startListeningSoon(250);
        return START_STICKY;
    }

    private void configureRecognizer() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            broadcastState("Reconhecimento de voz indisponível neste aparelho");
            return;
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) { broadcastState("Microfone ativo — aguardando rádio"); }
            @Override public void onBeginningOfSpeech() { broadcastState("Comunicação detectada"); }
            @Override public void onRmsChanged(float rmsdB) {}
            @Override public void onBufferReceived(byte[] buffer) {}
            @Override public void onEndOfSpeech() { broadcastState("Processando comunicação"); }

            @Override
            public void onError(int error) {
                if (!running) return;
                long delay = (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY || error == SpeechRecognizer.ERROR_SERVER) ? 1800 : 700;
                broadcastState("Escuta ativa — reiniciando reconhecimento");
                startListeningSoon(delay);
            }

            @Override
            public void onResults(Bundle results) {
                ArrayList<String> list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list != null && !list.isEmpty()) acceptFinal(list.get(0));
                if (running) startListeningSoon(450);
            }

            @Override
            public void onPartialResults(Bundle partialResults) {
                ArrayList<String> list = partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list != null && !list.isEmpty()) broadcastTranscript(list.get(0), false);
            }

            @Override public void onEvent(int eventType, Bundle params) {}
        });

        recognizerIntent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "pt-BR");
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "pt-BR");
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1200L);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 700L);
        recognizerIntent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 500L);
    }

    private void acceptFinal(String text) {
        if (text == null) return;
        String clean = text.trim().replaceAll("\\s+", " ");
        if (clean.length() < 4) return;
        long now = System.currentTimeMillis();
        if (clean.equalsIgnoreCase(lastFinal) && now - lastFinalAt < 8000) return;
        lastFinal = clean;
        lastFinalAt = now;
        broadcastTranscript(clean, true);

        SharedPreferences p = getSharedPreferences("captador_prefs", MODE_PRIVATE);
        String company = p.getString("company", "220ª Cia");

        io.execute(() -> {
            try {
                DispatchRecord d = DispatchParser.parse(clean, company);
                AppDb db = new AppDb(getApplicationContext());
                db.insert(d);

                Base44Client client = new Base44Client(getApplicationContext());
                if (client.hasToken()) {
                    try {
                        if (client.send(d)) db.markSynced(d.id);
                    } catch (Exception e) {
                        broadcastState("Salvo localmente; envio pendente: " + shortMessage(e));
                    }
                } else {
                    broadcastState("Salvo localmente; faça login Base44 para sincronizar");
                }
            } catch (Exception e) {
                broadcastState("Erro ao salvar comunicação: " + shortMessage(e));
            }
        });
    }

    private void startListeningSoon(long delay) {
        main.removeCallbacksAndMessages(null);
        main.postDelayed(() -> {
            if (!running || recognizer == null) return;
            try {
                recognizer.cancel();
                recognizer.startListening(recognizerIntent);
            } catch (Exception e) {
                broadcastState("Falha ao iniciar microfone: " + shortMessage(e));
                startListeningSoon(1500);
            }
        }, delay);
    }

    private void stopCapture() {
        running = false;
        main.removeCallbacksAndMessages(null);
        if (recognizer != null) {
            try { recognizer.cancel(); } catch (Exception ignored) {}
        }
        broadcastState("Captador parado");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        running = false;
        main.removeCallbacksAndMessages(null);
        if (recognizer != null) {
            try { recognizer.destroy(); } catch (Exception ignored) {}
            recognizer = null;
        }
        io.shutdownNow();
        releaseWakeLock();
        super.onDestroy();
    }

    private Notification buildNotification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Captador Rádio")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true)
                .setContentIntent(pi)
                .build();
    }

    private void createNotificationChannel() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "Captador Rádio", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Mantém a escuta da rede rádio ativa em segundo plano.");
        nm.createNotificationChannel(ch);
    }

    private void acquireWakeLock() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CaptadorRadio:AudioWakeLock");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
    }

    private void broadcastState(String state) {
        Intent i = new Intent(ACTION_STATE);
        i.setPackage(getPackageName());
        i.putExtra(EXTRA_STATE, state);
        sendBroadcast(i);
    }

    private void broadcastTranscript(String text, boolean isFinal) {
        Intent i = new Intent(ACTION_TRANSCRIPT);
        i.setPackage(getPackageName());
        i.putExtra(EXTRA_TEXT, text);
        i.putExtra("final", isFinal);
        sendBroadcast(i);
    }

    private String shortMessage(Exception e) {
        String m = e.getMessage();
        if (m == null || m.isBlank()) return e.getClass().getSimpleName();
        return m.length() > 160 ? m.substring(0, 160) : m;
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
