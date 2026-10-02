import Foundation

final class Base44API {
    static let appID = "6ac00e0b1b497c55d2b6762e"
    static let baseURL = URL(string: "https://base44.app/api")!

    private let tokenKey = "escala_facil_base44_token"
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    var hasToken: Bool { !(token ?? "").isEmpty }

    private var token: String? {
        get { UserDefaults.standard.string(forKey: tokenKey) }
        set { UserDefaults.standard.set(newValue, forKey: tokenKey) }
    }

    func logout() {
        token = nil
    }

    func login(email: String, password: String) async throws {
        struct LoginBody: Encodable { let email: String; let password: String }
        struct LoginResponse: Decodable { let access_token: String }

        let response: LoginResponse = try await request(
            "/apps/\(Self.appID)/auth/login",
            method: "POST",
            body: LoginBody(email: email, password: password),
            authenticated: false
        )
        guard !response.access_token.isEmpty else { throw APIError.message("Token de acesso não retornado.") }
        token = response.access_token
    }

    func companhias() async throws -> [Companhia] {
        let values: [Companhia] = try await request("/apps/\(Self.appID)/entities/Companhia?sort=ordem&limit=100")
        return values.sorted { ($0.ordem ?? 999) < ($1.ordem ?? 999) }
    }

    func recursos(data: String, companhia: String?) async throws -> [RecursoTurno] {
        var q: [String: String] = ["data": data]
        if let companhia, !companhia.isEmpty { q["companhia"] = companhia }
        let query = try queryString(q)
        let values: [RecursoTurno] = try await request("/apps/\(Self.appID)/entities/RecursoTurno?q=\(query)&sort=hora_inicio&limit=200")
        return values
    }

    func filaRadio(companhia: String?) async throws -> [FilaRadio] {
        var q: [String: String] = [:]
        if let companhia, !companhia.isEmpty { q["companhia"] = companhia }
        let suffix = q.isEmpty ? "?sort=-data_hora&limit=100" : "?q=\(try queryString(q))&sort=-data_hora&limit=100"
        let values: [FilaRadio] = try await request("/apps/\(Self.appID)/entities/FilaRadio\(suffix)")
        return values
    }

    func enviarRadio(_ draft: RadioDraft) async throws -> FilaRadio {
        struct Payload: Encodable {
            let captador_id: String
            let data_hora: String
            let companhia: String
            let transcricao: String
            let resumo: String
            let natureza: String
            let local: String
            let referencia: String
            let prioridade: String
            let status: String
            let observacoes: String
        }

        let payload = Payload(
            captador_id: "iPhone",
            data_hora: ISO8601DateFormatter().string(from: Date()),
            companhia: draft.company,
            transcricao: draft.transcript,
            resumo: draft.summary,
            natureza: draft.nature,
            local: draft.location,
            referencia: draft.reference,
            prioridade: draft.priority,
            status: "novo",
            observacoes: "Captado e transcrito no próprio iPhone."
        )
        return try await request("/apps/\(Self.appID)/entities/FilaRadio", method: "POST", body: payload)
    }

    func atualizarFila(_ id: String, principal: String, apoios: [String]) async throws {
        struct Payload: Encodable {
            let status: String
            let recurso_principal_id: String
            let apoio_ids: [String]
        }
        let _: FilaRadio = try await request(
            "/apps/\(Self.appID)/entities/FilaRadio/\(id)",
            method: "PUT",
            body: Payload(status: "atribuido", recurso_principal_id: principal, apoio_ids: apoios)
        )
    }

    func criarEmpenho(_ payload: EmpenhoPayload) async throws {
        struct Created: Decodable { let id: String }
        let _: Created = try await request("/apps/\(Self.appID)/entities/Empenho", method: "POST", body: payload)
    }

    func atualizarSituacao(recursoID: String, situacao: String) async throws {
        struct Payload: Encodable { let situacao: String; let status: String; let atualizado_em: String }
        let _: RecursoTurno = try await request(
            "/apps/\(Self.appID)/entities/RecursoTurno/\(recursoID)",
            method: "PUT",
            body: Payload(situacao: situacao, status: situacao, atualizado_em: ISO8601DateFormatter().string(from: Date()))
        )
    }

    func alterarRecurso(_ recurso: RecursoTurno, integrantes: [String], viatura: String, inicio: String, fim: String, observacao: String, motivo: String) async throws {
        struct Payload: Encodable {
            let integrantes: [String]
            let viatura: String
            let hora_inicio: String
            let hora_fim: String
            let observacoes_coordenador: String
            let atualizado_em: String
        }

        let antes = "\(recurso.integrantes?.joined(separator: ", ") ?? "") | VP \(recurso.viatura ?? "") | \(recurso.horario)"
        let depois = "\(integrantes.joined(separator: ", ")) | VP \(viatura) | \(inicio) às \(fim)"

        let _: RecursoTurno = try await request(
            "/apps/\(Self.appID)/entities/RecursoTurno/\(recurso.id)",
            method: "PUT",
            body: Payload(
                integrantes: integrantes,
                viatura: viatura,
                hora_inicio: inicio,
                hora_fim: fim,
                observacoes_coordenador: observacao,
                atualizado_em: ISO8601DateFormatter().string(from: Date())
            )
        )

        let change = AlteracaoPayload(
            recurso_id: recurso.id,
            companhia: recurso.companhia ?? "",
            data_hora: ISO8601DateFormatter().string(from: Date()),
            tipo: "composicao",
            antes: antes,
            depois: depois,
            motivo: motivo,
            autor: "Coordenador"
        )
        struct Created: Decodable { let id: String }
        let _: Created = try await request("/apps/\(Self.appID)/entities/AlteracaoRecurso", method: "POST", body: change)
    }

    private func queryString(_ dictionary: [String: String]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: dictionary)
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return json.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "%7B%7D"
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET", authenticated: Bool = true) async throws -> T {
        var request = URLRequest(url: Self.baseURL.appending(path: path))
        request.httpMethod = method
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(Self.appID, forHTTPHeaderField: "X-App-Id")
        if authenticated {
            guard let token, !token.isEmpty else { throw APIError.unauthenticated }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func request<T: Decodable, B: Encodable>(_ path: String, method: String, body: B, authenticated: Bool = true) async throws -> T {
        var request = URLRequest(url: Self.baseURL.appending(path: path))
        request.httpMethod = method
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(Self.appID, forHTTPHeaderField: "X-App-Id")
        if authenticated {
            guard let token, !token.isEmpty else { throw APIError.unauthenticated }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func validate(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401 { token = nil; throw APIError.unauthenticated }
            let text = String(data: data, encoding: .utf8) ?? "Erro HTTP \(http.statusCode)"
            throw APIError.message(text)
        }
    }
}

enum APIError: LocalizedError {
    case unauthenticated
    case message(String)

    var errorDescription: String? {
        switch self {
        case .unauthenticated: return "Sessão expirada. Entre novamente."
        case .message(let value): return value
        }
    }
}
