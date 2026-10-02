import AVFoundation
import Foundation
import Speech
import SwiftUI

enum CaptureError: Error {
    case localeNotSupported
    case analyzerUnavailable
    case recordPermissionDenied
    case invalidAudioFormat
}

final class BufferConverter {
    enum ConversionError: Error {
        case failedToCreateConverter
        case failedToCreateConversionBuffer
        case conversionFailed(NSError?)
    }

    private var converter: AVAudioConverter?

    func convert(_ buffer: AVAudioPCMBuffer, to format: AVAudioFormat) throws -> AVAudioPCMBuffer {
        let inputFormat = buffer.format
        guard inputFormat != format else { return buffer }

        if converter == nil || converter?.outputFormat != format {
            converter = AVAudioConverter(from: inputFormat, to: format)
            converter?.primeMethod = .none
        }
        guard let converter else { throw ConversionError.failedToCreateConverter }

        let ratio = converter.outputFormat.sampleRate / converter.inputFormat.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up))
        guard let output = AVAudioPCMBuffer(pcmFormat: converter.outputFormat, frameCapacity: capacity) else {
            throw ConversionError.failedToCreateConversionBuffer
        }

        var error: NSError?
        var consumed = false
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            defer { consumed = true }
            inputStatus.pointee = consumed ? .noDataNow : .haveData
            return consumed ? nil : buffer
        }
        guard status != .error else { throw ConversionError.conversionFailed(error) }
        return output
    }
}

@MainActor
final class SpeechCaptureManager: ObservableObject {
    @Published var isRecording = false
    @Published var statusText = "Rádio parado"
    @Published var volatileText = ""
    @Published var lastDispatch = ""

    var onDispatch: ((String) -> Void)?

    private let audioSession = AVAudioSession.sharedInstance()
    private let audioEngine = AVAudioEngine()
    private let converter = BufferConverter()
    private let transcriber: SpeechTranscriber
    private let analyzer: SpeechAnalyzer

    private var analyzerFormat: AVAudioFormat?
    private var inputSequence: AsyncStream<AnalyzerInput>?
    private var inputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var audioContinuation: AsyncStream<AVAudioPCMBuffer>.Continuation?
    private var recognitionTask: Task<Void, Never>?
    private var engineTask: Task<Void, Never>?
    private var flushTask: Task<Void, Never>?
    private var pendingParts: [String] = []

    init() {
        let locale = Locale(identifier: "pt-BR")
        let module = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: [.audioTimeRange]
        )
        transcriber = module
        analyzer = SpeechAnalyzer(modules: [module])
        observeAudioInterruptions()
    }

    func start() {
        guard !isRecording else { return }
        statusText = "Preparando reconhecimento..."
        engineTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.prepare()
                guard let inputSequence = self.inputSequence,
                      let inputContinuation = self.inputContinuation else {
                    throw CaptureError.analyzerUnavailable
                }

                guard await self.requestPermission() else {
                    throw CaptureError.recordPermissionDenied
                }

                try self.activateSession()
                try await self.analyzer.start(inputSequence: inputSequence)
                self.isRecording = true
                self.statusText = "🎙️ Rádio ativo — pode bloquear a tela"

                let stream = try self.audioBufferStream()
                for await buffer in stream {
                    guard !Task.isCancelled else { break }
                    guard let format = self.analyzerFormat else { throw CaptureError.invalidAudioFormat }
                    let converted = try self.converter.convert(buffer, to: format)
                    inputContinuation.yield(AnalyzerInput(buffer: converted))
                }
            } catch {
                self.statusText = "Falha na escuta: \(error.localizedDescription)"
                self.isRecording = false
            }
        }
    }

    func stop() {
        flushPending()
        stopAudioEngine()
        inputContinuation?.finish()
        recognitionTask?.cancel()
        recognitionTask = nil
        engineTask?.cancel()
        engineTask = nil

        Task {
            try? await analyzer.finalizeAndFinishThroughEndOfInput()
        }

        try? audioSession.setActive(false, options: .notifyOthersOnDeactivation)
        isRecording = false
        statusText = "Rádio parado"
    }

    private func prepare() async throws {
        analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber])
        try await ensureModel(locale: Locale(identifier: "pt-BR"))

        let pair = AsyncStream<AnalyzerInput>.makeStream()
        inputSequence = pair.stream
        inputContinuation = pair.continuation

        recognitionTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await result in self.transcriber.results {
                    let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { continue }

                    if result.isFinal {
                        await MainActor.run {
                            self.acceptFinalSegment(text)
                        }
                    } else {
                        await MainActor.run {
                            self.volatileText = text
                            self.statusText = "📻 Comunicação detectada"
                        }
                    }
                }
            } catch {
                await MainActor.run {
                    if self.isRecording { self.statusText = "Reconhecimento interrompido — tentando manter a captura" }
                }
            }
        }
    }

    private func acceptFinalSegment(_ text: String) {
        pendingParts.append(text)
        volatileText = ""
        statusText = "Processando comunicação..."
        flushTask?.cancel()
        flushTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            await MainActor.run { self?.flushPending() }
        }
    }

    private func flushPending() {
        flushTask?.cancel()
        flushTask = nil
        let full = pendingParts.joined(separator: " ")
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        pendingParts.removeAll()
        guard full.count >= 4 else { return }

        lastDispatch = full
        statusText = isRecording ? "🎙️ Rádio ativo — aguardando próxima comunicação" : "Rádio parado"
        onDispatch?(full)
    }

    private func activateSession() throws {
        try audioSession.setCategory(.record, mode: .measurement, options: [])
        try audioSession.setActive(true)
    }

    private func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    private func audioBufferStream() throws -> AsyncStream<AVAudioPCMBuffer> {
        let input = audioEngine.inputNode
        let format = input.inputFormat(forBus: 0)
        let stream = AsyncStream<AVAudioPCMBuffer>(bufferingPolicy: .bufferingNewest(24)) { continuation in
            self.audioContinuation = continuation
        }

        input.installTap(onBus: 0, bufferSize: 2048, format: format) { [weak self] buffer, _ in
            self?.audioContinuation?.yield(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
        return stream
    }

    private func stopAudioEngine() {
        if audioEngine.isRunning { audioEngine.stop() }
        audioEngine.inputNode.removeTap(onBus: 0)
        audioContinuation?.finish()
        audioContinuation = nil
    }

    private func ensureModel(locale: Locale) async throws {
        let supported = await SpeechTranscriber.supportedLocales
        let wanted = locale.identifier(.bcp47)
        guard supported.map({ $0.identifier(.bcp47) }).contains(wanted) else {
            throw CaptureError.localeNotSupported
        }

        let installed = await SpeechTranscriber.installedLocales
        if installed.map({ $0.identifier(.bcp47) }).contains(wanted) { return }

        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            statusText = "Baixando modelo de português..."
            try await request.downloadAndInstall()
        }
    }

    private func observeAudioInterruptions() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: audioSession,
            queue: .main
        ) { [weak self] note in
            guard let self else { return }
            let typeValue = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt ?? 0
            let type = AVAudioSession.InterruptionType(rawValue: typeValue)
            Task { @MainActor in
                if type == .began {
                    self.statusText = "Áudio interrompido pelo iPhone"
                } else if self.isRecording {
                    self.statusText = "Retomando escuta..."
                    try? self.audioSession.setActive(true)
                }
            }
        }
    }
}
