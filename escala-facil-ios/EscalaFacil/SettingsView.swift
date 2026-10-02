import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section("Rede rádio") {
                    LabeledContent("Estado", value: model.radio.isRecording ? "Ativa" : "Parada")
                    LabeledContent("Companhia", value: model.selectedCompany)
                    Text("A transcrição usa o reconhecimento de fala do próprio iPhone. O áudio permanece no aparelho; o Escala Fácil envia ao backend o texto e o resumo do empenho.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("Conta") {
                    Button("Sair", role: .destructive) {
                        model.logout()
                    }
                }
                Section("Versão") {
                    Text("Escala Fácil iPhone 1.0")
                }
            }
            .navigationTitle("Ajustes")
        }
    }
}
