package br.com.afp.captadorradio;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

public class AppDb extends SQLiteOpenHelper {
    private static final String DB_NAME = "captador_radio.db";
    private static final int DB_VERSION = 1;

    public AppDb(Context context) {
        super(context, DB_NAME, null, DB_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE dispatches (" +
                "id INTEGER PRIMARY KEY AUTOINCREMENT," +
                "created_at TEXT NOT NULL," +
                "transcription TEXT NOT NULL," +
                "summary TEXT NOT NULL," +
                "nature TEXT," +
                "location TEXT," +
                "reference_text TEXT," +
                "priority TEXT," +
                "company TEXT," +
                "synced INTEGER NOT NULL DEFAULT 0)");
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {}

    public synchronized long insert(DispatchRecord d) {
        ContentValues v = new ContentValues();
        d.createdAt = Instant.now().toString();
        v.put("created_at", d.createdAt);
        v.put("transcription", d.transcription);
        v.put("summary", d.summary);
        v.put("nature", d.nature);
        v.put("location", d.location);
        v.put("reference_text", d.reference);
        v.put("priority", d.priority);
        v.put("company", d.company);
        v.put("synced", 0);
        long id = getWritableDatabase().insertOrThrow("dispatches", null, v);
        d.id = id;
        return id;
    }

    public synchronized void markSynced(long id) {
        ContentValues v = new ContentValues();
        v.put("synced", 1);
        getWritableDatabase().update("dispatches", v, "id=?", new String[]{String.valueOf(id)});
    }

    public synchronized int pendingCount() {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM dispatches WHERE synced=0", null)) {
            return c.moveToFirst() ? c.getInt(0) : 0;
        }
    }

    public synchronized List<DispatchRecord> pending(int limit) {
        List<DispatchRecord> out = new ArrayList<>();
        try (Cursor c = getReadableDatabase().rawQuery(
                "SELECT id,created_at,transcription,summary,nature,location,reference_text,priority,company,synced " +
                        "FROM dispatches WHERE synced=0 ORDER BY id ASC LIMIT ?",
                new String[]{String.valueOf(limit)})) {
            while (c.moveToNext()) out.add(fromCursor(c));
        }
        return out;
    }

    public synchronized DispatchRecord latest() {
        try (Cursor c = getReadableDatabase().rawQuery(
                "SELECT id,created_at,transcription,summary,nature,location,reference_text,priority,company,synced " +
                        "FROM dispatches ORDER BY id DESC LIMIT 1", null)) {
            return c.moveToFirst() ? fromCursor(c) : null;
        }
    }

    private DispatchRecord fromCursor(Cursor c) {
        DispatchRecord d = new DispatchRecord();
        d.id = c.getLong(0);
        d.createdAt = c.getString(1);
        d.transcription = c.getString(2);
        d.summary = c.getString(3);
        d.nature = c.getString(4);
        d.location = c.getString(5);
        d.reference = c.getString(6);
        d.priority = c.getString(7);
        d.company = c.getString(8);
        d.synced = c.getInt(9) == 1;
        return d;
    }
}
