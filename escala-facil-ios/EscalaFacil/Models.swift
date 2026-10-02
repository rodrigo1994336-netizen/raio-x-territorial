import Foundation

struct Companhia: Codable, Identifiable, Hashable {
    let id: String
    var nome: String
    var sigla: String?
    var batalhao: String?
    var ordem: Int?
    var principal: Bool?

    var titulo: String { sigla?.isEmpty == false ? sigla! : nome }
}

struct EventoEscala: Codable, Hashable {
    var nome: String?
    var local: String?
    var inicio: String?
    var fim: String?
    var orientacao: String?
}

struct OperacaoRegistro: Codable, Hashable {
    var codigo: String?
    var quantidade: Int?
    var descricao: String?
    var obrigatoria: Bool?
}

struct RecursoTurno: Codable, Identifiable, Hashable {
    let id: String
    var escala_id: String?
    var companhia: String?
    var data: String?
    var nome: String?
    var equipe: String?
    var emoji: String?
    var hora_inicio: String?
    var hora_fim: String?
    var comandante: String?
    var condutor: String?
    var integrantes: [String]?
    var viatura: String?
    var situacao: String?
    var status: String?
    var marcada_anuncio: Bool?
    var selecionado: Bool?
    var observacoes_escala: String?
    var observacoes_coordenador: String?
    var observacao_coordenador: String?
    var missoes: [String]?
    var locais_prioritarios: [String]?
    var eventos: [EventoEscala]?
    var operacoes_registros: [OperacaoRegistro]?
    var origem_original: String?

    var titulo: String {
        if let nome, !nome.isEmpty { return nome }
        if let equipe, !equipe.isEmpty { return equipe }
        return "Recurso"
    }

    var icone: String {
        if let emoji, !emoji.isEmpty { return emoji }
        let t = titulo.lowercased()
        if t.contains("águia") || t.contains("aguia") { return "🦅" }
        if t.contains("rocca") { return "🦮" }
        if t.contains("rural") { return "🌾" }
        if t.contains("moto") || t.hasPrefix("mp") { return "🏍️" }
        if t.contains("cpu") || t.contains("comando") { return "🛡️" }
        return "🚓"
    }

    var situacaoAtual: String {
        if let situacao, !situacao.isEmpty { return situacao }
        if let status, !status.isEmpty { return status }
        return "aguardando"
    }

    var horario: String {
        let ini = hora_inicio ?? ""
        let fim = hora_fim ?? ""
        if ini.isEmpty && fim.isEmpty { return "" }
        if fim.isEmpty { return ini }
        return "\(ini) às \(fim)"
    }

    var observacaoCoordenador: String {
        if let observacoes_coordenador, !observacoes_coordenador.isEmpty { return observacoes_coordenador }
        return observacao_coordenador ?? ""
    }

    var incluirAnuncio: Bool { marcada_anuncio ?? selecionado ?? true }
}

struct FilaRadio: Codable, Identifiable, Hashable {
    let id: String
    var captador_id: String?
    var data_hora: String?
    var companhia: String?
    var transcricao: String?
    var resumo: String?
    var natureza: String?
    var local: String?
    var referencia: String?
    var prioridade: String?
    var status: String?
    var recurso_principal_id: String?
    var apoio_ids: [String]?
    var observacoes: String?
}

struct EmpenhoPayload: Encodable {
    var recurso_id: String
    var apoio_recurso_ids: [String]
    var companhia: String
    var equipe: String
    var origem_radio_id: String?
    var inicio: String
    var natureza: String
    var local: String
    var referencia: String
    var descricao: String
    var transcricao_radio: String
    var status: String
    var observacoes: String
    var origem: String
}

struct AlteracaoPayload: Encodable {
    var recurso_id: String
    var companhia: String
    var data_hora: String
    var tipo: String
    var antes: String
    var depois: String
    var motivo: String
    var autor: String
}

struct RadioDraft: Hashable {
    var transcript: String
    var summary: String
    var nature: String
    var location: String
    var reference: String
    var priority: String
    var company: String
}
