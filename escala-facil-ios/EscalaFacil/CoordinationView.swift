import SwiftUI

struct CoordinationView: View {
    @EnvironmentObject var model: AppModel
    @State private var editing: RecursoTurno?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                CompanyTabs()
                statusHeader

                if model.loading && model.recursos.isEmpty {
                    Spacer()
                    ProgressView("Carregando recursos...")
                    Spacer()
                } else if model.recursos.isEmpty {
                    ContentUnavailableView(
                        "Sem recursos",
                        systemImage: "person.3.sequence",
                        description: Text("Não há recursos carregados para \(model.selectedCompany) hoje.")
                    )
                } else {
                    ScrollView {
                        LazyVStack(spacing: 10) {
                            ForEach(model.recursos) { resource in
                                ResourceCard(resource: resource) {
                                    editing = resource
                                }
                            }
                        }
                        .padding()
                    }
                    .refreshable { await model.refresh() }
                }
            }
            .navigationTitle("Coordenação")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .sheet(item: $editing) { resource in
                EditResourceSheet(resource: resource)
            }
        }
    }

    private var statusHeader: some View {
        HStack {
            metric("Disponíveis", count: model.recursos.filter { $0.situacaoAtual == "disponivel" }.count)
            metric("Empenhados", count: model.recursos.filter { $0.situacaoAtual == "empenhado" }.count)
            metric("Apoio", count: model.recursos.filter { $0.situacaoAtual == "apoio" }.count)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.thinMaterial)
    }

    private func metric(_ label: String, count: Int) -> some View {
        VStack(spacing: 2) {
            Text("\(count)").font(.title3.bold())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

struct CompanyTabs: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(model.companhias) { company in
                    let value = company.sigla ?? company.nome
                    Button {
                        model.chooseCompany(value)
                    } label: {
                        Text(company.titulo)
                            .font(.subheadline.bold())
                            .padding(.horizontal, 14)
                            .padding(.vertical, 9)
                            .background(model.selectedCompany == value ? Color.accentColor : Color.secondary.opacity(0.13))
                            .foregroundStyle(model.selectedCompany == value ? Color.white : Color.primary)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
        }
    }
}

struct ResourceCard: View {
    @EnvironmentObject var model: AppModel
    let resource: RecursoTurno
    let edit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Button {
                    if model.selectedForAnnouncement.contains(resource.id) {
                        model.selectedForAnnouncement.remove(resource.id)
                    } else {
                        model.selectedForAnnouncement.insert(resource.id)
                    }
                } label: {
                    Image(systemName: model.selectedForAnnouncement.contains(resource.id) ? "checkmark.square.fill" : "square")
                        .font(.title3)
                }

                VStack(alignment: .leading, spacing: 3) {
                    Text("\(resource.icone) \(resource.titulo)")
                        .font(.headline)
                    if !resource.horario.isEmpty {
                        Text(resource.horario).font(.subheadline).foregroundStyle(.secondary)
                    }
                }

                Spacer()
                StatusPill(status: resource.situacaoAtual)
            }

            if let members = resource.integrantes, !members.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(members, id: \.self) { Text($0).font(.subheadline) }
                }
            }

            HStack {
                if let vp = resource.viatura, !vp.isEmpty {
                    Label("VP \(vp)", systemImage: "car.fill")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Alterar", action: edit)
                    .buttonStyle(.bordered)
            }

            HStack(spacing: 8) {
                quick("Disponível", "disponivel")
                quick("Empenhar", "empenhado")
                quick("PB", "pb")
                quick("Apoio", "apoio")
            }
        }
        .padding(14)
        .background(Color(uiColor: .secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func quick(_ label: String, _ status: String) -> some View {
        Button(label) {
            Task { await model.setStatus(resource, status) }
        }
        .buttonStyle(.bordered)
        .font(.caption)
        .tint(resource.situacaoAtual == status ? .accentColor : .secondary)
    }
}

struct StatusPill: View {
    let status: String

    var body: some View {
        Text(status.replacingOccurrences(of: "_", with: " ").capitalized)
            .font(.caption2.bold())
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(color.opacity(0.14))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private var color: Color {
        switch status {
        case "disponivel": return .green
        case "empenhado": return .red
        case "apoio": return .orange
        case "pb": return .blue
        case "indisponivel": return .gray
        case "encerrado": return .secondary
        default: return .secondary
        }
    }
}

struct EditResourceSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let resource: RecursoTurno

    @State private var members = ""
    @State private var vehicle = ""
    @State private var start = ""
    @State private var end = ""
    @State private var note = ""
    @State private var reason = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Composição atual") {
                    TextEditor(text: $members)
                        .frame(minHeight: 100)
                    TextField("Viatura", text: $vehicle)
                    HStack {
                        TextField("Início", text: $start)
                        TextField("Fim", text: $end)
                    }
                }
                Section("Coordenação") {
                    TextField("Motivo da alteração", text: $reason)
                    TextEditor(text: $note).frame(minHeight: 90)
                }
                if let original = resource.origem_original, !original.isEmpty {
                    Section("Original da escala") {
                        Text(original).font(.footnote)
                    }
                }
            }
            .navigationTitle(resource.titulo)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Salvar") {
                        let list = members.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                        Task {
                            await model.saveResourceChange(resource, integrantes: list, viatura: vehicle, inicio: start, fim: end, observacao: note, motivo: reason)
                            dismiss()
                        }
                    }
                }
            }
            .onAppear {
                members = (resource.integrantes ?? []).joined(separator: "\n")
                vehicle = resource.viatura ?? ""
                start = resource.hora_inicio ?? ""
                end = resource.hora_fim ?? ""
                note = resource.observacaoCoordenador
            }
        }
    }
}
