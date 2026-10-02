import Foundation

enum DispatchParser {
    static func parse(_ transcript: String, company: String) -> RadioDraft {
        let clean = transcript.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = clean.lowercased()

        let nature: String
        if containsAny(lower, ["disparo", "arma de fogo", "indivíduo armado", "individuo armado"]) {
            nature = "Possível ocorrência com arma de fogo"
        } else if containsAny(lower, ["roubo", "assalto"]) {
            nature = "Roubo"
        } else if containsAny(lower, ["furto", "arrombamento"]) {
            nature = "Furto/Arrombamento"
        } else if containsAny(lower, ["violência doméstica", "violencia domestica", "maria da penha"]) {
            nature = "Violência doméstica"
        } else if containsAny(lower, ["acidente", "colisão", "colisao", "atropelamento"]) {
            nature = "Acidente de trânsito"
        } else if containsAny(lower, ["tráfico", "trafico", "droga", "entorpecente"]) {
            nature = "Tráfico/Drogas"
        } else if containsAny(lower, ["briga", "rixa", "agressão", "agressao"]) {
            nature = "Agressão/Briga"
        } else if containsAny(lower, ["apoio", "solicita apoio", "necessita apoio"]) {
            nature = "Solicitação de apoio"
        } else if containsAny(lower, ["veículo suspeito", "veiculo suspeito", "indivíduo suspeito", "individuo suspeito"]) {
            nature = "Atitude suspeita"
        } else {
            nature = "Empenho via rede rádio"
        }

        let priority: String
        if containsAny(lower, ["disparo", "ferido", "arma de fogo", "indivíduo armado", "individuo armado", "urgente", "socorro", "em andamento"]) {
            priority = "urgente"
        } else if containsAny(lower, ["prioridade", "apoio", "fuga", "perseguição", "perseguicao"]) {
            priority = "prioritaria"
        } else {
            priority = "normal"
        }

        let location = firstMatch(
            in: clean,
            pattern: #"(?i)\b(rua|r\.|avenida|av\.|bairro|praça|praca|rodovia|br\s*-?\s*\d+)\s+([^,.;]{2,90})"#
        )
        let reference = firstMatch(
            in: clean,
            pattern: #"(?i)\b(próximo(?:a| ao| à)?|proximo(?:a| ao)?|em frente(?: ao| à)?|ao lado(?: de| do| da)?|referência|referencia)\s+([^,.;]{2,90})"#
        )

        var summary = nature
        if !location.isEmpty { summary += " em \(location)" }
        if !reference.isEmpty { summary += " (\(reference))" }
        summary += ". Rádio: " + String(clean.prefix(420))

        return RadioDraft(
            transcript: clean,
            summary: summary,
            nature: nature,
            location: location,
            reference: reference,
            priority: priority,
            company: company
        )
    }

    private static func containsAny(_ text: String, _ terms: [String]) -> Bool {
        terms.contains { text.contains($0) }
    }

    private static func firstMatch(in text: String, pattern: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return "" }
        return (1..<match.numberOfRanges)
            .compactMap { Range(match.range(at: $0), in: text).map { String(text[$0]) } }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
