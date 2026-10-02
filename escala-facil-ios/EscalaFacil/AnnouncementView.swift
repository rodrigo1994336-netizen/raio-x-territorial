import SwiftUI
import UIKit

struct AnnouncementView: View {
    @EnvironmentObject var model: AppModel
    @State private var copied = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                CompanyTabs()

                ScrollView {
                    Text(model.announcement())
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .font(.body.monospaced())
                        .textSelection(.enabled)
                        .padding()
                        .background(Color(uiColor: .secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .padding(.horizontal)
                }

                HStack {
                    Button {
                        UIPasteboard.general.string = model.announcement()
                        copied = true
                    } label: {
                        Label(copied ? "COPIADO" : "COPIAR ANÚNCIO", systemImage: "doc.on.doc")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        UIPasteboard.general.string = model.announcement()
                        if let url = URL(string: "whatsapp://send?text=\(model.announcement().addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")") {
                            UIApplication.shared.open(url)
                        }
                    } label: {
                        Image(systemName: "paperplane.fill")
                            .frame(width: 44)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal)
                .padding(.bottom)
            }
            .navigationTitle("Anúncio diário")
        }
    }
}
