import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var authenticated = false
    @Published var loading = false
    @Published var errorMessage = ""
    @Published var companhias: [Companhia] = []
    @Published var selectedCompany = ""
    @Published var recursos: [RecursoTurno] = []
    @Published var fila: [FilaRadio] = []
    @Published var selectedForAnnouncement: Set<String> = []
    @Published var capturedDraft: RadioDraft?

    let api = Base44API()
    let radio = SpeechCaptureManager()

    init() {
        authenticated = api.hasToken
        radio.onDispatch = { [weak self] transcript in
            Task { @MainActor in
                await self?.receiveRadio(transcript)
            }
        }
        if authenticated {
            Task { await bootstrap() }
        }
    }

    func login(email: String, password: String) async {
        loading = true
        errorMessage = ""
        defer { loading = false }
        do {
            try await api.login(email: email, password: password)
            authenticated = true
            await bootstrap()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() {
        radio.stop()
        api.logout()
        authenticated = false
        companhias = []
        recursos = []
        fila = []
    }

    func bootstrap() async {
        await loadCompanies()
        await refresh()
    }

    func loadCompanies() async {
        do {
            companhias = try await api.companhias()
            if selectedCompany.isEmpty {
                selectedCompany = companhias.first(where: { $0.principal == true })?.sigla
                    ?? companhias.first?.sigla
                    ?? companhias.first?.nome
                    ?? "220ª Cia"
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refresh() async {
        loading = true
        defer { loading = false }
        do {
            let day = Self.apiDate.string(from: Date())
            async let r = api.recursos(data: day, companhia: selectedCompany)
            async let f = api.filaRadio(companhia: selectedCompany)
            recursos = try await r
            fila = try await f
            selectedForAnnouncement.formUnion(recursos.filter(\.incluirAnuncio).map(\.id))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func chooseCompany(_ value: String) {
        selectedCompany = value
        recursos = []
        fila = []
        selectedForAnnouncement.removeAll()
        Task { await refresh() }
    }

    func setStatus(_ resource: RecursoTurno, _ status: String) async {
        do {
            try await api.atualizarSituacao(recursoID: resource.id, situacao: status)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func receiveRadio(_ transcript: String) async {
        let company = selectedCompany.isEmpty ? "220ª Cia" : selectedCompany
        let draft = DispatchParser.parse(transcript, company: company)
        capturedDraft = draft
        do {
            _ = try await api.enviarRadio(draft)
            fila = try await api.filaRadio(companhia: selectedCompany)
        } catch {
            errorMessage = "O áudio foi transcrito, mas não consegui enviar o empenho: \(error.localizedDescription)"
        }
    }

    func assign(_ item: FilaRadio, principal: RecursoTurno, supports: [RecursoTurno]) async {
        do {
            let supportIDs = supports.map(\.id)
            let payload = EmpenhoPayload(
                recurso_id: principal.id,
                apoio_recurso_ids: supportIDs,
                companhia: selectedCompany,
                equipe: principal.titulo,
                origem_radio_id: item.id,
                inicio: ISO8601DateFormatter().string(from: Date()),
                natureza: item.natureza ?? "Empenho via rede rádio",
                local: item.local ?? "",
                referencia: item.referencia ?? "",
                descricao: item.resumo ?? item.transcricao ?? "",
                transcricao_radio: item.transcricao ?? "",
                status: "em_andamento",
                observacoes: "",
                origem: "radio"
            )
            try await api.criarEmpenho(payload)
            try await api.atualizarSituacao(recursoID: principal.id, situacao: "empenhado")
            for resource in supports {
                try await api.atualizarSituacao(recursoID: resource.id, situacao: "apoio")
            }
            try await api.atualizarFila(item.id, principal: principal.id, apoios: supportIDs)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func saveResourceChange(_ resource: RecursoTurno, integrantes: [String], viatura: String, inicio: String, fim: String, observacao: String, motivo: String) async {
        do {
            try await api.alterarRecurso(
                resource,
                integrantes: integrantes,
                viatura: viatura,
                inicio: inicio,
                fim: fim,
                observacao: observacao,
                motivo: motivo
            )
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func announcement() -> String {
        let selected = recursos.filter { selectedForAnnouncement.contains($0.id) }
        var out = "*Senhor Comandante e Senhores Oficiais, boa tarde!*\n\n"
        let turnName = selectedCompany.contains("220") ? "Tático Móvel" : selectedCompany
        out += "Anuncio início do turno *\(turnName)*.\n\n"

        for resource in selected {
            out += "- [ ] \(resource.icone) *\(resource.titulo)*"
            if !resource.horario.isEmpty { out += " - \(resource.horario.replacingOccurrences(of: ":", with: "h"))" }
            out += "\n"
            for member in resource.integrantes ?? [] { out += "\(member)\n" }
            out += "\n"
        }

        out += "Respeitosamente,\n\n*Rodrigo, 2º Ten PM*\nCOTM do 42º BPM"
        return out
    }

    static let apiDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "pt_BR")
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
