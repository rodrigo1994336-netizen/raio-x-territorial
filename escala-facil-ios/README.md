# Escala Fácil — iPhone

Aplicativo nativo iOS 26+ para fazer **tudo no mesmo iPhone**:

- coordenação diária por companhia;
- recursos separados em cartões;
- situação rápida: disponível, empenhado, PB, apoio;
- alteração de composição, viatura e horário com histórico;
- geração do anúncio diário para WhatsApp;
- escuta da rede rádio com a tela bloqueada;
- transcrição local com SpeechAnalyzer/SpeechTranscriber;
- resumo inicial do empenho;
- fila de rádio;
- atribuição de recurso principal e múltiplos apoios;
- integração direta com o backend Base44 do Escala Fácil.

## Backend

Base44 App ID: `6ac00e0b1b497c55d2b6762e`

## Privacidade

A transcrição é feita no próprio iPhone usando o framework Speech do iOS 26. O aplicativo envia ao Base44 o texto transcrito e o resumo operacional; não envia o fluxo bruto de áudio.

## Background

O app habilita `UIBackgroundModes = audio` e mantém uma sessão de gravação `AVAudioSession` ativa. Assim, a captura do microfone pode continuar com a tela bloqueada enquanto a escuta estiver explicitamente iniciada pelo usuário.

## Build

Requer Xcode 26+ e XcodeGen.

```bash
cd escala-facil-ios
xcodegen generate
xcodebuild -project EscalaFacil.xcodeproj -scheme EscalaFacil -sdk iphoneos -configuration Release CODE_SIGNING_ALLOWED=NO build
```

Para instalar em um iPhone físico, o app precisa ser assinado por uma identidade Apple válida (TestFlight, Ad Hoc ou instalação de desenvolvimento).
