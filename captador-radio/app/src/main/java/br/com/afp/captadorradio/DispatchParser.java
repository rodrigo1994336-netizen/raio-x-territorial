package br.com.afp.captadorradio;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class DispatchParser {
    private DispatchParser() {}

    public static DispatchRecord parse(String transcript, String company) {
        String clean = transcript == null ? "" : transcript.trim().replaceAll("\\s+", " ");
        String lower = clean.toLowerCase(Locale.ROOT);

        DispatchRecord d = new DispatchRecord();
        d.transcription = clean;
        d.company = company == null || company.isBlank() ? "220ª Cia" : company.trim();
        d.nature = detectNature(lower);
        d.location = detectLocation(clean);
        d.reference = detectReference(clean);
        d.priority = detectPriority(lower);
        d.summary = buildSummary(d);
        return d;
    }

    private static String detectNature(String t) {
        if (containsAny(t, "disparo", "arma de fogo", "indivíduo armado", "individuo armado")) return "Possível ocorrência com arma de fogo";
        if (containsAny(t, "roubo", "assalto")) return "Roubo";
        if (containsAny(t, "furto", "arrombamento")) return "Furto/Arrombamento";
        if (containsAny(t, "violência doméstica", "violencia domestica", "maria da penha")) return "Violência doméstica";
        if (containsAny(t, "acidente", "colisão", "colisao", "atropelamento")) return "Acidente de trânsito";
        if (containsAny(t, "tráfico", "trafico", "droga", "entorpecente")) return "Tráfico/Drogas";
        if (containsAny(t, "briga", "rixa", "agressão", "agressao")) return "Agressão/Briga";
        if (containsAny(t, "apoio", "solicita apoio", "necessita apoio")) return "Solicitação de apoio";
        if (containsAny(t, "veículo suspeito", "veiculo suspeito", "indivíduo suspeito", "individuo suspeito")) return "Atitude suspeita";
        return "Empenho via rede rádio";
    }

    private static String detectPriority(String t) {
        if (containsAny(t, "disparo", "ferido", "arma de fogo", "indivíduo armado", "individuo armado", "urgente", "socorro", "em andamento")) {
            return "urgente";
        }
        if (containsAny(t, "prioridade", "apoio", "fuga", "perseguição", "perseguicao")) return "prioritaria";
        return "normal";
    }

    private static String detectLocation(String text) {
        Pattern p = Pattern.compile("(?i)\\b(rua|r\\.|avenida|av\\.|bairro|praça|praca|rodovia|br\\s*-?\\s*\\d+)\\s+([^,.;]{2,90})");
        Matcher m = p.matcher(text);
        if (m.find()) return (m.group(1) + " " + m.group(2)).trim();
        return "";
    }

    private static String detectReference(String text) {
        Pattern p = Pattern.compile("(?i)\\b(próximo(?:a| ao| à)?|proximo(?:a| ao)?|em frente(?: ao| à)?|ao lado(?: de| do| da)?|referência|referencia)\\s+([^,.;]{2,90})");
        Matcher m = p.matcher(text);
        if (m.find()) return (m.group(1) + " " + m.group(2)).trim();
        return "";
    }

    private static String buildSummary(DispatchRecord d) {
        StringBuilder sb = new StringBuilder();
        sb.append(d.nature);
        if (!d.location.isBlank()) sb.append(" em ").append(d.location);
        if (!d.reference.isBlank()) sb.append(" (").append(d.reference).append(")");
        if (!d.transcription.isBlank()) {
            String t = d.transcription.length() > 360 ? d.transcription.substring(0, 357) + "..." : d.transcription;
            sb.append(". Rádio: ").append(t);
        }
        return sb.toString();
    }

    private static boolean containsAny(String t, String... terms) {
        for (String term : terms) if (t.contains(term)) return true;
        return false;
    }
}
