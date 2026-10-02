import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            if model.authenticated {
                MainTabs()
            } else {
                LoginView()
            }
        }
        .alert("Escala Fácil", isPresented: Binding(
            get: { !model.errorMessage.isEmpty },
            set: { if !$0 { model.errorMessage = "" } }
        )) {
            Button("OK", role: .cancel) { model.errorMessage = "" }
        } message: {
            Text(model.errorMessage)
        }
    }
}

struct LoginView: View {
    @EnvironmentObject var model: AppModel
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Escala Fácil")
                        .font(.largeTitle.bold())
                    Text("Coordenação do turno + rede rádio no mesmo iPhone.")
                        .foregroundStyle(.secondary)
                }
                Section("Acesso") {
                    TextField("E-mail Base44", text: $email)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.emailAddress)
                    SecureField("Senha", text: $password)
                    Button(model.loading ? "Entrando..." : "Entrar") {
                        Task { await model.login(email: email.trimmingCharacters(in: .whitespaces), password: password) }
                    }
                    .disabled(model.loading || email.isEmpty || password.isEmpty)
                }
            }
            .navigationTitle("Escala Fácil")
        }
    }
}

struct MainTabs: View {
    var body: some View {
        TabView {
            CoordinationView()
                .tabItem { Label("Turno", systemImage: "person.3.fill") }
            RadioView()
                .tabItem { Label("Rádio", systemImage: "waveform") }
            AnnouncementView()
                .tabItem { Label("Anúncio", systemImage: "doc.on.clipboard") }
            SettingsView()
                .tabItem { Label("Ajustes", systemImage: "gearshape") }
        }
    }
}
