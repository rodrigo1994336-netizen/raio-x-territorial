import SwiftUI

struct RadioView: View {
    @EnvironmentObject var model: AppModel
    @State private var assigning: FilaRadio?

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                radioHeader

                if let draft = model.capturedDraft {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("ÚLTIMO EMPENHO CAPTADO").font(.caption.bold()).foregroundStyle(.secondary)
                        Text(draft.nature).font(.headline)
                        if !draft.location.isEmpty { Text("📍 \(draft.location)") }
                        Text(draft.summary).font(.subheadline)
                    }
                    .padding()
                    .background(Color.orange.opacity(0.10))
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .padding(.horizontal)
                }

                List {
                    Section("Fila do rádio") {
                        ForEach(model.fila.filter { ($0.status ?? "novo") != "descartado" }) { item in
                            Button {
                                assigning = item
                            } label: {
                                RadioQueueRow(item: item)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { await model.refresh() }
            }
            .navigationTitle("Rede Rádio")
            .sheet(item: $assigning) { item in
                AssignDispatchSheet(item: item)
            }
        }
    }

    private var radioHeader: some View {
        VStack(spacing: 10) {
            HStack {
                Circle()
                    .fill(model.radio.isRecording ? Color.red : Color.gray)
                    .frame(width: 12, height: 12)
                Text(model.radio.statusText)
                    .font(.subheadline.bold())
                Spacer()
            }

            Button {
                model.radio.isRecording ? model.radio.stop() : model.radio.start()
            } label: {
                Label(model.radio.isRecording ? "PARAR ESCUTA" : "INICIAR ESCUTA", systemImage: model.radio.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                    .frame(maxWidth: .infinity)
                    .font(.headline)
            }
            .buttonStyle(.borderedProminent)
            .tint(model.radio.isRecording ? .red : .accentColor)

            Text("Com a escuta iniciada, o iPhone pode ficar bloqueado. O aplicativo mantém a sessão de gravação ativa em segundo plano.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .background(.thinMaterial)
    }
}

struct RadioQueueRow: View {
    let item: FilaRadio

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(item.natureza ?? "Empenho via rede rádio").font(.headline)
                Spacer()
                Text((item.prioridade ?? "normal").uppercased())
                    .font(.caption2.bold())
                    .foregroundStyle((item.prioridade ?? "") == "urgente" ? .red : .secondary)
            }
            if let local = item.local, !local.isEmpty {
                Text("📍 \(local)").font(.subheadline)
            }
            Text(item.resumo ?? item.transcricao ?? "")
                .font(.subheadline)
                .lineLimit(4)
                .foregroundStyle(.secondary)
            HStack {
                StatusPill(status: item.status ?? "novo")
                Spacer()
                Text("Toque para atribuir").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

struct AssignDispatchSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let item: FilaRadio

    @State private var principalID = ""
    @State private var supportIDs = Set<String>()

    var body: some View {
        NavigationStack {
            Form {
                Section("Empenho") {
                    Text(item.resumo ?? item.transcricao ?? "")
                    if let transcript = item.transcricao, !transcript.isEmpty {
                        DisclosureGroup("Transcrição original") {
                            Text(transcript).font(.footnote)
                        }
                    }
                }

                Section("Recurso principal") {
                    ForEach(model.recursos) { resource in
                        Button {
                            principalID = resource.id
                            supportIDs.remove(resource.id)
                        } label: {
                            HStack {
                                Image(systemName: principalID == resource.id ? "largecircle.fill.circle" : "circle")
                                Text("\(resource.icone) \(resource.titulo)")
                                Spacer()
                                StatusPill(status: resource.situacaoAtual)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }

                Section("Apoios") {
                    ForEach(model.recursos.filter { $0.id != principalID }) { resource in
                        Button {
                            if supportIDs.contains(resource.id) { supportIDs.remove(resource.id) }
                            else { supportIDs.insert(resource.id) }
                        } label: {
                            HStack {
                                Image(systemName: supportIDs.contains(resource.id) ? "checkmark.square.fill" : "square")
                                Text("\(resource.icone) \(resource.titulo)")
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .navigationTitle("Atribuir empenho")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Confirmar") {
                        guard let principal = model.recursos.first(where: { $0.id == principalID }) else { return }
                        let supports = model.recursos.filter { supportIDs.contains($0.id) }
                        Task {
                            await model.assign(item, principal: principal, supports: supports)
                            dismiss()
                        }
                    }
                    .disabled(principalID.isEmpty)
                }
            }
        }
    }
}
