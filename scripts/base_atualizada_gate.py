"""Trava de processo: verde de ramo so vale sobre a base em que ele vai entrar.

Por que existe (16/09/2026). Os PRs #78 e #79 sairam da MESMA base (a8a296c), cada um passou VERDE no seu
ramo, e foram mesclados um atras do outro. Juntos deixaram a `main` vermelha: o #78 criou uma regra que
enumera modulos de remendo pela propriedade "levanta quando a ancora nao casa" e o #79, no mesmo dia,
trocou o canal de sinal de um desses modulos (passou a imprimir o marcador em vez de levantar). Nenhum dos
dois CIs podia ver o defeito: ele so existe na combinacao, e nenhum dos dois rodou sobre a combinacao.

O que esta trava reprova, e o que ela NAO promete:
  * `merge_nao_stale` - no commit que esta na `main` agora: se for merge, o ramo mesclado tinha de CONTER o
    commit que era a ponta da `main` antes do merge. Se nao continha, o verde daquele ramo era sobre outra
    arvore: reprova, nomeia os dois commits e diz o que fazer.
  * `pr_base_atualizada` - num PR: a ponta da `main` de AGORA tem de estar contida no head do PR. Enquanto
    o ramo estiver atrasado, o check fica vermelho no proprio PR, antes do merge.
  * `ci_roda_a_trava` - o fluxo de trabalho tem de EXECUTAR esta trava (num passo `run:`/`uses:`, nao em
    qualquer linha do job), nos dois gatilhos, sem filtro que impeca o gatilho de disparar na base, sem
    `if:` no job, e com `fetch-depth: 0` em TODO checkout daquele job.
NAO promete impedir o merge: o check do PR e uma fotografia do instante em que rodou, e ninguem re-roda o CI
de um PR parado quando outro PR entra. Quem IMPEDE e a protecao de branch do GitHub com "require branches to
be up to date before merging" (ligar e decisao do dono, porque muda quem consegue mesclar). Ate la a ordem
escrita vale: atualizar o ramo com a main, esperar o CI novo, e so entao mesclar.

Tres saidas por regra, e a do meio e a que faltava (conserto de 16/09, revisao do PR #81). PASSA = conferiu
e esta certo. FALHA = conferiu e esta errado. NAO CONFERIDO = nao tinha o que conferir nesta execucao, dito
em voz alta. A trava nasceu de um PASS que nao tinha olhado nada (clone raso); consertar so a porta por onde
aquele caso entrou deixaria a sala aberta, porque a MESMA conclusao "nada a conferir" sai tambem de um merge
por squash/rebase (um pai so, sem rastro no historico) e de qualquer execucao fora de push e de PR (no
projeto, o `workflow_dispatch` que roda o CI de PR empilhado). Nenhuma dessas tem evidencia: viraram
NAO CONFERIDO, e quando nenhuma das duas regras de base confere, a linha SEM CONFERENCIA diz isso.

Historico raso e o caso silencioso desta trava. Num `clone --depth 1` o commit da ponta vira enxerto e
`git rev-list --parents` reporta ZERO pais: sem guarda, a leitura concluiria "nao e merge, nada a conferir"
e o portao imprimiria PASSA exatamente no caso que ele existe para pegar. Duas coisas fecham isso, nao uma:
a guarda que recusa o clone raso antes de qualquer conclusao, e a leitura dos pais no PROPRIO objeto do
commit (`git cat-file commit`), que nao caminha no grafo e por isso nao aceita o enxerto como verdade - com
ela, historico cortado sem o marcador `shallow` tambem reprova pela guarda, em vez de virar erro cru.

Controles positivos por construcao: cada controle chama a REGRA registrada (a funcao que o `main()`
executa), nunca um ajudante abaixo dela - um controle e uma afirmacao sobre a funcao que ele CHAMA, nao
sobre a que ele NOMEIA -, exercita um repositorio git sintetico montado na hora e cobra a MENSAGEM daquela
guarda. E a cobertura nao se le pela contagem de controles verdes: o proprio portao enumera, pelo AST do seu
arquivo, todo ponto que reprova ou que declara "nao conferido" e exige que algum controle verde tenha
levantado NAQUELE ponto. N controles verdes convive com zero guardas cobertas - foi assim que o unico
`fail()` que justifica esta trava (o merge sobre base velha) ficou sem controle nenhum ate a revisao do #81:
tres controles se chamavam `merge_nao_stale <- ...` e chamavam o ajudante `_merge_stale()`.

E a cobertura das guardas ainda deixava uma camada inteira de fora (segunda revisao do #81). Guarda que
levanta so vale o que a camada acima faz com ela, e essa camada nao levanta nada: ela SOMA e devolve um
codigo de saida. Por isso cinco edicoes de uma linha passavam com todos os controles verdes - a pior delas
trocava o `return 1` por `return 0` e o portao imprimia a MESMA linha FALHA saindo com codigo 0, ou seja,
check verde no CI. Controle em processo nao alcanca isso, porque em processo se le o valor de uma funcao e
o CI le OUTRA coisa. Quem cobre e o bloco ponta a ponta: o portao executa A SI MESMO em subprocesso contra
repositorios sinteticos e cobra o CODIGO DE SAIDA junto com a linha PASSA/FALHA/SEM CONFERENCIA daquele
caso. A populacao da cobertura passou a ter duas partes - os pontos que LEVANTAM (pelo AST dos `fail()`) e
os pontos em que o resultado se INVERTE (pelo AST dos `return` de `main()`), cada `return` exigindo ter sido
observado pelo codigo de saida de um controle verde.

Duas coisas fecham a volta em torno do proprio bloco, e nenhuma delas sozinha: ele roda FORA do
`--sem-controles`, senao a mesma linha que desliga os controles em processo desligaria o unico observador
que le o portao de fora; e a chamada dele mora no `main()` antes de `controles()`, para que a cobertura do
veredito - conferida la dentro - fique vermelha se essa chamada sumir. A recursao e barrada por duas travas
com motivo no ambiente (a variavel que o pai poe em todo filho e o contador de profundidade que todo filho
propaga), e uma delas tem controle proprio: sem as duas variaveis, a suspensao nao pode existir - suspensao
sem motivo seria chave de desligar.
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("RX_BASE_ATU_ROOT") or Path(__file__).resolve().parents[1])
WORKFLOW = Path(os.environ.get("RX_BASE_ATU_WORKFLOW") or (ROOT / ".github/workflows/quality-gate.yml"))
BASE_REF = os.environ.get("RX_BASE_REF", "main")
CHAMADA = "scripts/base_atualizada_gate.py"
RULES: dict[str, callable] = {}
REGRAS_DE_BASE = ("merge_nao_stale", "pr_base_atualizada")   # as que respondem por base atualizada

# Marcas de cada guarda. O controle positivo cobra a marca da guarda que ele diz exercitar: sem isso um
# controle fica verde com QUALQUER erro anterior - inclusive o erro que prova o contrario do que se quer
# provar (foi assim que o caso do clone raso passou despercebido).
MARCA_RASO = "clone e raso"
MARCA_FETCH_DEPTH = "fetch-depth: 0"
MARCA_PAI_AUSENTE = "nao esta no clone"
MARCA_OBJETO_VAZIO = "objeto do commit veio vazio"
MARCA_MERGE_STALE = "merge sobre base desatualizada"
MARCA_PR_ATRASADO = "ramo do PR atrasado"
MARCA_HEAD_PR = "RX_PR_HEAD_SHA"
MARCA_SEM_FLUXO = "fluxo de trabalho nao encontrado"
MARCA_SEM_JOBS = "nenhum job separado"
MARCA_SEM_CHAMADA = "o CI nao chama"
MARCA_SEM_EXECUCAO = "nenhum passo executa"
MARCA_SEM_GATILHO = "nao dispara em"
MARCA_GATILHO_FILTRADO = "filtro de gatilho"
MARCA_JOB_CONDICIONAL = "condicao `if:` no job"
# Marcas das saidas que NAO conferem nada. Tambem sao enumeradas e tambem precisam de controle: uma saida
# que afirma "nada a conferir" e a forma exata de um PASS falso.
MARCA_NC_EM_PR = "quem confere e pr_base_atualizada"
MARCA_NC_UM_PAI = "tem um pai so"
MARCA_NC_FORA_PR = "fora de PR"

# ---- camada de VEREDITO: a que traduz o que foi levantado em codigo de saida, e que nao levanta nada
# A cobertura pelo AST enumera os pontos que REPROVAM. A soma que vira `return 0`/`return 1` nao esta entre
# eles, e por isso cinco mutacoes de uma linha sobreviviam aos 26 controles verdes (revisao do #81): trocar
# o `return 1` por `return 0`, nao executar o bloco de controles, contar NaoConferido como regra conferida,
# acrescentar uma regra que sempre passa a REGRAS_DE_BASE, e tirar o decorador que registra a regra. Nenhum
# controle em processo alcanca essa camada, porque em processo se le o valor de uma funcao e o CI le OUTRA
# coisa: o codigo de saida. Quem cobre e o bloco ponta a ponta - o portao roda A SI MESMO em subprocesso,
# contra repositorios sinteticos, e cobra o codigo de saida junto com a linha PASSA/FALHA correspondente.
VAR_FILHO = "RX_BASE_ATU_FILHO"                  # "1": execucao filha de um controle - nao repetir o bloco
VAR_PROFUNDIDADE = "RX_BASE_ATU_PROFUNDIDADE"    # teto duro, propagado por TODO filho: ninguem desce alem
PROFUNDIDADE_MAX = 2
MARCA_E2E = "PONTA A PONTA <-"                   # prefixo do titulo de todo controle deste bloco
MARCA_E2E_SUSPENSO = "PONTA A PONTA: suspenso"   # a linha que a execucao filha imprime no lugar do bloco
# linha que SO a suite em processo (`controles()`) imprime. E por ela que o filho prova que o bloco de
# controles foi executado: procurar "CONTROLE_OK" solto nao serve, porque o proprio ponta a ponta imprime
# CONTROLE_OK de fora do interruptor - a primeira versao deste controle aceitou essa evidencia errada e a
# mutacao `if "--sem-controles" not in args:` -> `if False:` sobreviveu.
MARCA_SUITE = "COBERTURA: toda guarda deste arquivo tem controle"

# O que o bloco precisa ter OBSERVADO para a camada de veredito estar coberta. Nomes com o motivo escrito, e
# nao uma contagem: numero congelado registra o tamanho da divida, nunca a razao dela.
VEREDITOS_EXIGIDOS: dict[str, str] = {
    "saida 1 no merge atrasado": (
        "e a unica observacao que mata `return 1` -> `return 0`: com a mutacao, a MESMA linha FALHA sai e o "
        "CI ve verde"),
    "saida 0 no repositorio em dia": (
        "fecha o lado oposto (`return 0` -> `return 1`), que transformaria a trava em vermelho permanente"),
    "saida 0 com SEM CONFERENCIA na ponta de um pai so": (
        "mata as duas mutacoes da classificacao: contar NaoConferido como regra conferida, e acrescentar a "
        "REGRAS_DE_BASE uma regra que sempre passa - as duas apagam a linha SEM CONFERENCIA"),
    "o bloco de controles e executado pelo portao": (
        "mata `if \"--sem-controles\" not in args:` -> `if False:`, que apaga do CI a auto-verificacao "
        "inteira sem mudar nenhum veredito de regra"),
    "sem a variavel de suspensao o portao roda tudo": (
        "prova que VAR_FILHO nao e chave de desligar: a suspensao existe para o filho nao chamar a si mesmo "
        "sem fim, e fora dela o bloco roda"),
}
_VEREDITOS_VISTOS: set[str] = set()   # nomes acima registrados por controle ponta a ponta VERDE
_SAIDAS_VISTAS: set[int] = set()      # codigos de saida observados por controle ponta a ponta VERDE
_VERMELHOS: list[str] = []            # segundo canal do vermelho ate o CI (ver o fim do arquivo)
# As duas que rodam a SUITE INTEIRA num subprocesso so sao exigidas na execucao de cima: repetidas em cada
# nivel, o custo do portao multiplicaria por nivel sem cobrir nada de novo. A execucao filha diz isso na
# saida, em vez de calar - e a de cima continua exigindo as duas.
VEREDITOS_SO_NO_TOPO = frozenset({"o bloco de controles e executado pelo portao",
                                  "sem a variavel de suspensao o portao roda tudo"})


class Reprova(AssertionError):
    pass


class NaoConferido(Exception):
    """A regra nao teve o que conferir nesta execucao. Nao e sucesso: e ausencia de conferencia."""


_ULTIMA_GUARDA = [0]      # linha do fail()/nao_conferido() que levantou por ultimo - e assim que a
_COBERTAS: set[int] = set()   # cobertura sabe QUAL guarda cada controle verde matou


def fail(msg: str):
    _ULTIMA_GUARDA[0] = sys._getframe(1).f_lineno
    raise Reprova(msg)


def nao_conferido(msg: str):
    _ULTIMA_GUARDA[0] = sys._getframe(1).f_lineno
    raise NaoConferido(msg)


def regra(nome: str):
    def deco(fn):
        RULES[nome] = fn
        return fn
    return deco


def git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True, timeout=120)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} -> {proc.returncode}: "
                           f"{(proc.stdout + proc.stderr).decode('utf-8', 'ignore')[:300]}")
    return proc.stdout.decode("utf-8", "ignore").strip()


def contem(antigo: str, novo: str, cwd: Path | None = None) -> bool:
    """`novo` contem `antigo` (antigo e ancestral de novo, ou o mesmo commit)."""
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", antigo, novo],
                          cwd=str(cwd or ROOT), capture_output=True, timeout=120)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"merge-base --is-ancestor {antigo} {novo} -> {proc.returncode}: "
                           f"{(proc.stdout + proc.stderr).decode('utf-8', 'ignore')[:300]}")
    return proc.returncode == 0


def existe_commit(sha: str, repo: Path | None = None) -> bool:
    proc = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=str(repo or ROOT),
                          capture_output=True, timeout=120)
    return proc.returncode == 0


# ------------------------------------------------------------------ historico completo, antes de concluir
def _dir_git(repo: Path) -> Path:
    for opcao in ("--git-common-dir", "--git-dir"):   # common-dir responde certo tambem em worktree
        try:
            saida = git("rev-parse", opcao, cwd=repo)
        except RuntimeError:
            continue
        if saida:
            p = Path(saida)
            return p if p.is_absolute() else (repo / p)
    return repo / ".git"


def repo_raso(repo: Path) -> bool:
    try:
        if git("rev-parse", "--is-shallow-repository", cwd=repo).strip().lower() == "true":
            return True
    except RuntimeError:
        pass                      # git antigo nao conhece a opcao: quem responde e o arquivo `shallow`
    return (_dir_git(repo) / "shallow").exists()


def exige_historico_completo(repo: Path) -> None:
    """Reprova ANTES de qualquer conclusao quando o clone e raso.

    Num clone raso o `git` responde com meia-verdade: para quem caminha no grafo, o commit da ponta e um
    enxerto e reporta zero pais. Sem esta guarda a leitura concluiria "nao e merge, nada a conferir" e o
    portao imprimiria PASSA justamente no caso que ele existe para pegar - o pior jeito de falhar.
    """
    if repo_raso(repo):
        fail(f"instrumento quebrado: o {MARCA_RASO} (historico cortado no enxerto). Sem o historico "
             "completo esta trava nao ve os pais do merge e passaria calada justamente no caso que ela "
             f"existe para pegar. O passo precisa de actions/checkout com {MARCA_FETCH_DEPTH}.")


def _pais_do_objeto(repo: Path, commit: str) -> tuple[str, list[str]]:
    """(sha, pais) lidos do PROPRIO objeto do commit.

    `git cat-file commit` nao caminha no grafo: responde os pais reais mesmo quando eles nao estao no clone,
    que e exatamente o que distingue "ponta com um pai so" de "historico cortado". A leitura que caminha
    (`rev-list --parents`, `log`) faz o oposto: no clone raso mente zero pais, e no clone sem o marcador
    `shallow` levanta erro cru, sem a mensagem que diz o que fazer.
    """
    sha = git("rev-parse", f"{commit}^{{commit}}", cwd=repo)
    bruto = git("cat-file", "commit", sha, cwd=repo)
    if not bruto.strip():
        fail(f"instrumento quebrado: o {MARCA_OBJETO_VAZIO} para a ponta {sha[:12]}")
    pais: list[str] = []
    for ln in bruto.splitlines():
        if not ln.strip():
            break                 # o cabecalho acaba na primeira linha em branco; depois vem a mensagem
        if ln.startswith("parent "):
            pais.append(ln.split()[1])
    return sha, pais


def _merge_stale(repo: Path, commit: str = "HEAD") -> tuple[str, str, list[str], bool]:
    """(sha, base, pais atrasados, e_merge). Reprova se o historico nao veio - clone raso ou pai ausente.

    A conclusao "nao e merge" sai da MESMA leitura que passou pela guarda; nenhuma regra deduz isso com uma
    segunda chamada de git, que e como a guarda deixava de ser alcancada.
    """
    exige_historico_completo(repo)
    sha, pais = _pais_do_objeto(repo, commit)
    if len(pais) < 2:
        return sha, "", [], False                        # ponta com um pai so: quem decide e a regra
    for p in pais:
        if not existe_commit(p, repo):
            fail(f"instrumento quebrado: o commit pai {p[:12]} {MARCA_PAI_AUSENTE} (historico incompleto). "
                 f"O passo precisa de actions/checkout com {MARCA_FETCH_DEPTH}.")
    base = pais[0]
    return sha, base, [p for p in pais[1:] if not contem(base, p, cwd=repo)], True


@regra("merge_nao_stale")
def r_merge_nao_stale() -> str:
    """O merge que esta na ponta entrou sobre a base em que o CI do ramo rodou (ou sobre uma mais nova)."""
    head_pr = (os.environ.get("RX_PR_HEAD_SHA") or "").strip()
    if head_pr:
        # Em PR o checkout entrega o merge automatico do GitHub (base + head), que nao e o commit que vai
        # para a `main`. O desvio so vale se o head do PR existir MESMO neste clone: senao uma variavel
        # solta num push desligaria esta regra sem ninguem notar.
        if not existe_commit(head_pr):
            fail(f"instrumento quebrado: {MARCA_HEAD_PR}={head_pr[:12]} nao e um commit deste clone. Ou o "
                 "checkout veio sem o head do PR, ou a variavel foi definida fora de um PR - nos dois "
                 "casos esta regra ficaria desligada em silencio.")
        nao_conferido("em PR a ponta e o merge automatico do GitHub, que nao e o commit que vai para a "
                      f"`{BASE_REF}`: {MARCA_NC_EM_PR}, com a ponta da base lida ao vivo")
    sha, base, atrasados, e_merge = _merge_stale(ROOT)
    if atrasados:
        fail(f"{MARCA_MERGE_STALE}: {sha[:12]} juntou um ramo que NAO continha {base[:12]}, a ponta da "
             f"`{BASE_REF}` de antes do merge ({', '.join(p[:12] for p in atrasados)}). O verde daquele "
             "ramo foi medido em outra arvore e nao vale para esta. Antes de mesclar: atualizar o ramo "
             f"com a `{BASE_REF}`, esperar o CI novo, mesclar so entao.")
    if not e_merge:
        nao_conferido(f"a ponta {sha[:12]} {MARCA_NC_UM_PAI}: no historico, push direto e merge por "
                      "squash/rebase sao indistinguiveis, e num squash/rebase o verde do ramo foi medido "
                      "em outra arvore sem deixar rastro. Esta regra nao tem como ver isso; quem pega e o "
                      "check do PR (pr_base_atualizada) e a protecao de branch do GitHub.")
    return f"{sha[:12]} e merge e o ramo continha a ponta anterior da `{BASE_REF}` ({base[:12]})"


@regra("pr_base_atualizada")
def r_pr_base_atualizada() -> str:
    """No PR: o head tem de conter a ponta ATUAL da base. Fora de PR, nao ha o que conferir."""
    head = (os.environ.get("RX_PR_HEAD_SHA") or "").strip()
    if not head:
        nao_conferido(f"{MARCA_NC_FORA_PR} (sem {MARCA_HEAD_PR}): nesta execucao quem responde pela base e "
                      "merge_nao_stale, sobre o commit que esta na ponta")
    # num clone raso o `merge-base` responde sobre um historico cortado: a resposta nao vale como verde
    exige_historico_completo(ROOT)
    base_sha = (os.environ.get("RX_PR_BASE_SHA") or "").strip()
    if not base_sha:
        # sem --depth: um fetch raso num clone completo cria fronteira e o merge-base passa a mentir
        git("fetch", "--no-tags", "origin", BASE_REF)
        base_sha = git("rev-parse", "FETCH_HEAD")
    if not contem(base_sha, head):
        fail(f"{MARCA_PR_ATRASADO}: o head {head[:12]} nao contem {base_sha[:12]}, a ponta atual da "
             f"`{BASE_REF}`. O verde deste CI seria sobre uma base que nao e a do merge. Atualizar o ramo "
             f"(`git merge origin/{BASE_REF}` ou rebase) e deixar o CI rodar de novo.")
    return f"head {head[:12]} contem a ponta atual da `{BASE_REF}` ({base_sha[:12]})"


# ------------------------------------------------------------------ leitura da estrutura do fluxo de CI
def _indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip())


def _bloco_top(texto: str, chave: str) -> str:
    """Corpo de uma chave de primeiro nivel do YAML (tudo indentado abaixo dela). Sem dependencia externa:
    o job que roda esta trava so tem o Python de base, e portao nao pode depender de pip para existir."""
    dentro, corpo = False, []
    for ln in texto.splitlines():
        if not dentro:
            if re.match(rf"^{re.escape(chave)}:\s*(#.*)?$", ln):
                dentro = True
            continue
        if ln.strip() and not ln[:1].isspace():
            break
        corpo.append(ln)
    return "\n".join(corpo)


def _chave(texto: str, chave: str, indent: int) -> tuple[str | None, str]:
    """(valor na mesma linha, corpo indentado abaixo) da chave NAQUELE nivel. Valor None = chave ausente."""
    linhas = texto.splitlines()
    for i, ln in enumerate(linhas):
        if _indent(ln) != indent or not re.match(rf"^\s*{re.escape(chave)}:(\s|$)", ln):
            continue
        inline = ln.split(":", 1)[1].strip()
        if inline.startswith("#"):
            inline = ""
        corpo = []
        for prox in linhas[i + 1:]:
            if prox.strip() and _indent(prox) <= indent:
                break
            corpo.append(prox)
        return inline, "\n".join(corpo)
    return None, ""


def _lista(inline: str, corpo: str) -> list[str]:
    itens: list[str] = []
    if inline.startswith("["):
        itens += [x.strip().strip("'\"") for x in inline.strip("[]").split(",") if x.strip()]
    elif inline:
        itens.append(inline.strip("'\""))
    for ln in corpo.splitlines():
        m = re.match(r"^\s*-\s*(.+?)\s*$", ln)
        if m:
            itens.append(m.group(1).strip("'\""))
    return itens


def _chaves_indent2(bloco: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"(?m)^  ([A-Za-z0-9_.-]+):", bloco)]


def _jobs(texto: str) -> dict[str, str]:
    saida: dict[str, str] = {}
    atual, corpo = None, []
    for ln in _bloco_top(texto, "jobs").splitlines():
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(#.*)?$", ln)
        if m:
            if atual:
                saida[atual] = "\n".join(corpo)
            atual, corpo = m.group(1), []
            continue
        if atual:
            corpo.append(ln)
    if atual:
        saida[atual] = "\n".join(corpo)
    return saida


def _passos(corpo_job: str) -> list[str]:
    """Cada item da lista `steps:` do job, como texto. Um passo e a unidade que EXECUTA alguma coisa; o
    corpo inteiro do job nao e - foi por ler o corpo inteiro que a regra dizia PASSA com o `run:` virado
    comentario e com `fetch-depth: 0` num passo que nao era o checkout."""
    inline, corpo = _chave(corpo_job, "steps", 4)
    if inline is None:
        return []
    passos: list[str] = []
    atual: list[str] | None = None
    ind: int | None = None
    for ln in corpo.splitlines():
        m = re.match(r"^(\s*)-\s", ln)
        if m and (ind is None or len(m.group(1)) == ind):
            ind = len(m.group(1))
            if atual is not None:
                passos.append("\n".join(atual))
            atual = [ln]
            continue
        if atual is not None:
            atual.append(ln)
    if atual is not None:
        passos.append("\n".join(atual))
    return passos


def _valor_no_passo(passo: str, chave: str) -> str | None:
    """Valor de uma chave do passo (inclusive escalar em bloco, `run: |`). Linha comentada nao conta."""
    linhas = passo.splitlines()
    padrao = re.compile(rf"^(\s*(?:-\s+)?){re.escape(chave)}:(\s.*|)$")
    for i, ln in enumerate(linhas):
        m = padrao.match(ln)
        if not m:
            continue
        col = len(m.group(1))
        valor = [m.group(2).strip()]
        for prox in linhas[i + 1:]:
            if prox.strip() and _indent(prox) <= col:
                break
            valor.append(prox.strip())
        return "\n".join(v for v in valor if v)
    return None


def _passo_executa(passo: str, alvo: str) -> bool:
    return any(alvo in (_valor_no_passo(passo, k) or "") for k in ("run", "uses"))


def _e_checkout(passo: str) -> bool:
    return "actions/checkout" in (_valor_no_passo(passo, "uses") or "")


@regra("ci_roda_a_trava")
def r_ci_roda_a_trava() -> str:
    """O fluxo EXECUTA esta trava nos dois momentos, sem filtro nem condicao, e com o historico inteiro."""
    if not WORKFLOW.exists():
        fail(f"instrumento quebrado: {MARCA_SEM_FLUXO} em {WORKFLOW}")
    texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore")
    bloco_on = _bloco_top(texto, "on")
    gatilhos = _chaves_indent2(bloco_on)
    faltando = [g for g in ("push", "pull_request") if g not in gatilhos]
    if faltando:
        fail(f"o fluxo de trabalho {MARCA_SEM_GATILHO} {', '.join(faltando)}: a trava so vale se rodar no "
             "PR (ramo atrasado) e no push da base (merge ja feito sobre base velha)")
    problemas: list[str] = []
    for g in ("push", "pull_request"):
        _inl, corpo_g = _chave(bloco_on, g, 2)
        inl_b, corpo_b = _chave(corpo_g, "branches", 4)
        if inl_b is not None:
            ramos = _lista(inl_b, corpo_b)
            if not any(fnmatch.fnmatch(BASE_REF, p) for p in ramos):
                problemas.append(f"`{g}` so dispara em {ramos}, que nao alcanca `{BASE_REF}`")
        inl_i, corpo_i = _chave(corpo_g, "branches-ignore", 4)
        if inl_i is not None and any(fnmatch.fnmatch(BASE_REF, p) for p in _lista(inl_i, corpo_i)):
            problemas.append(f"`{g}` ignora `{BASE_REF}`")
        for chave in ("paths", "paths-ignore"):
            if _chave(corpo_g, chave, 4)[0] is not None:
                problemas.append(f"`{g}` tem `{chave}`: a trava passaria a depender do diff do commit")
    if problemas:
        fail(f"{MARCA_GATILHO_FILTRADO}: {'; '.join(problemas)}. Gatilho que nao dispara nao deixa check "
             "vermelho nenhum - some em silencio, que e o contrario do que uma trava faz.")
    jobs = _jobs(texto)
    if not jobs:
        fail(f"instrumento quebrado: {MARCA_SEM_JOBS} do fluxo de trabalho. O leitor de estrutura desta "
             "regra parou de enxergar o arquivo e responderia sobre um YAML vazio - zero nao e ausencia")
    donos = [n for n, corpo in jobs.items() if any(_passo_executa(p, CHAMADA) for p in _passos(corpo))]
    if not donos:
        if CHAMADA in texto:
            fail(f"{MARCA_SEM_EXECUCAO} `{CHAMADA}`: o caminho aparece no fluxo de trabalho, mas em nenhum "
                 "passo `run:`/`uses:` - comentario, nome de passo ou texto solto nao roda portao nenhum")
        fail(f"{MARCA_SEM_CHAMADA} {CHAMADA} em job nenhum: a trava de base atualizada sairia do caminho "
             "sem ninguem notar")
    condicionais = [n for n in donos if re.search(r"(?m)^    if:", jobs[n])]
    if condicionais:
        fail(f"{MARCA_JOB_CONDICIONAL} `{', '.join(condicionais)}`: job que nao roda nao produz check "
             "vermelho, entao uma condicao ali desliga metade da trava sem deixar sinal. A trava roda "
             "sempre; quem decide o que conferir em cada evento sao as regras, que dizem NAO CONFERIDO.")
    sem_checkout = [n for n in donos if not any(_e_checkout(p) for p in _passos(jobs[n]))]
    rasos = [n for n in donos if any(_e_checkout(p) and not
                                     re.search(r"(?m)^\s*fetch-depth:\s*0\s*(#.*)?$", p)
                                     for p in _passos(jobs[n]))]
    if sem_checkout or rasos:
        alvo = ", ".join(sorted(set(sem_checkout + rasos)))
        fail(f"o job `{alvo}` executa a trava sem `{MARCA_FETCH_DEPTH}` em TODO passo de checkout: com "
             "historico raso o commit da ponta reporta zero pais e a trava nao consegue conferir merge "
             "nenhum. A linha faz parte da trava, nao e detalhe do checkout - e ter a linha em outro "
             "passo nao vale, porque quem traz o historico e o checkout.")
    return (f"{WORKFLOW.name}: job `{', '.join(donos)}` executa a trava, com {MARCA_FETCH_DEPTH} no "
            f"checkout, sem `if:` no job, e o fluxo dispara em push e pull_request sobre `{BASE_REF}` sem "
            "filtro. Esta regra le a configuracao: ela nao prova que o job rodou - job que nunca dispara "
            "nao deixa vermelho nenhum -, so que a configuracao continua plausivel.")


# ------------------------------------------------------------------ controles positivos por construcao
_SEQ = [0]


class _aponta:
    """Aponta as regras para um repositorio/YAML sintetico e devolve tudo ao lugar, mesmo se levantar."""

    def __init__(self, repo: Path | None = None, workflow: Path | None = None, **env: str | None):
        self.repo, self.workflow, self.env = repo, workflow, env

    def __enter__(self):
        self._antes = (globals()["ROOT"], globals()["WORKFLOW"])
        self._env_antes = {k: os.environ.get(k) for k in self.env}
        if self.repo is not None:
            globals()["ROOT"] = self.repo
        if self.workflow is not None:
            globals()["WORKFLOW"] = self.workflow
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return self

    def __exit__(self, *_):
        globals()["ROOT"], globals()["WORKFLOW"] = self._antes
        for k, v in self._env_antes.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return False


def _sem_pr(repo: Path) -> _aponta:
    """Repositorio sintetico e nenhuma variavel de PR no ambiente: e assim que a regra roda num push."""
    return _aponta(repo=repo, RX_PR_HEAD_SHA=None, RX_PR_BASE_SHA=None)


def _git_novo(repo: Path) -> None:
    repo.mkdir(parents=True)
    git("init", "-q", "-b", BASE_REF, cwd=repo)
    git("config", "user.email", "gate@example.invalid", cwd=repo)
    git("config", "user.name", "gate", cwd=repo)


def _repo_sintetico(td: Path, em_dia: bool) -> Path:
    """Monta base + ramo e mescla. `em_dia=False` reproduz o caso do #78/#79: ramo que nao contem a ponta."""
    _SEQ[0] += 1
    repo = td / f"{'em_dia' if em_dia else 'atrasado'}_{_SEQ[0]}"
    _git_novo(repo)
    (repo / "a.txt").write_text("1\n", encoding="utf-8", newline="\n")
    git("add", "a.txt", cwd=repo)
    git("commit", "-q", "-m", "base", cwd=repo)
    git("checkout", "-q", "-b", "ramo", cwd=repo)
    (repo / "b.txt").write_text("2\n", encoding="utf-8", newline="\n")
    git("add", "b.txt", cwd=repo)
    git("commit", "-q", "-m", "ramo", cwd=repo)
    git("checkout", "-q", BASE_REF, cwd=repo)
    (repo / "c.txt").write_text("3\n", encoding="utf-8", newline="\n")   # a base anda depois do verde
    git("add", "c.txt", cwd=repo)
    git("commit", "-q", "-m", "outro PR entrou", cwd=repo)
    if em_dia:
        git("checkout", "-q", "ramo", cwd=repo)
        git("merge", "-q", "--no-edit", BASE_REF, cwd=repo)             # atualiza o ramo: o que falta fazer
        git("checkout", "-q", BASE_REF, cwd=repo)
    git("merge", "-q", "--no-ff", "--no-edit", "ramo", cwd=repo)
    return repo


def _repo_um_pai(td: Path) -> Path:
    """Ponta com UM pai so - o que um push direto e um merge por squash/rebase produzem igualzinho."""
    _SEQ[0] += 1
    repo = td / f"um_pai_{_SEQ[0]}"
    _git_novo(repo)
    for n in ("1", "2"):
        (repo / f"{n}.txt").write_text(f"{n}\n", encoding="utf-8", newline="\n")
        git("add", f"{n}.txt", cwd=repo)
        git("commit", "-q", "-m", f"commit {n}", cwd=repo)
    return repo


def _clona(origem: Path, destino: Path, raso: bool) -> Path:
    """Clone de verdade. `file://` e obrigatorio: em clone local por caminho o git IGNORA o --depth."""
    args = ["clone", "-q"] + (["--depth", "1"] if raso else []) + [origem.as_uri(), str(destino)]
    git(*args, cwd=origem.parent)
    return destino


def _reprova_com(fn, *marcas: str) -> tuple[bool, str]:
    """Exige Reprova COM as marcas daquela guarda. Qualquer outra excecao NAO conta como reprovacao: um
    controle que aceita 'levantou alguma coisa' fica verde com o erro que prova o contrario do esperado."""
    _ULTIMA_GUARDA[0] = 0
    try:
        saida = fn()
    except Reprova as exc:
        msg = str(exc)
        falta = [m for m in marcas if m not in msg]
        if falta:
            return False, f"reprovou pela mensagem errada, sem {falta}: {msg[:150]}"
        if not _ULTIMA_GUARDA[0]:
            return False, "Reprova levantada fora de fail(): a cobertura nao sabe qual guarda foi exercida"
        _COBERTAS.add(_ULTIMA_GUARDA[0])
        return True, f"reprovou pela guarda certa: {msg[:130]}"
    except NaoConferido as exc:
        return False, f"disse NAO CONFERIDO em vez de reprovar: {str(exc)[:130]}"
    except Exception as exc:                                             # noqa: BLE001
        return False, (f"levantou {type(exc).__name__} ANTES da guarda (o controle nao chegou a exercita-la)"
                       f": {str(exc)[:130]}")
    return False, f"PASSOU em silencio: {str(saida)[:130]}"


def _nao_confere(fn, *marcas: str) -> tuple[bool, str]:
    """Exige a saida do meio: nao conferiu, e disse por que. PASSAR aqui e afirmar sucesso sem evidencia."""
    _ULTIMA_GUARDA[0] = 0
    try:
        saida = fn()
    except NaoConferido as exc:
        msg = str(exc)
        falta = [m for m in marcas if m not in msg]
        if falta:
            return False, f"nao conferiu, mas pela mensagem errada, sem {falta}: {msg[:150]}"
        if not _ULTIMA_GUARDA[0]:
            return False, "NaoConferido levantado fora de nao_conferido(): a cobertura nao sabe a origem"
        _COBERTAS.add(_ULTIMA_GUARDA[0])
        return True, f"nao conferiu, e disse por que: {msg[:130]}"
    except Reprova as exc:
        return False, f"reprovou em vez de dizer NAO CONFERIDO: {str(exc)[:130]}"
    except Exception as exc:                                             # noqa: BLE001
        return False, f"levantou {type(exc).__name__}: {str(exc)[:130]}"
    return False, f"AFIRMOU SUCESSO sem ter o que conferir: {str(saida)[:130]}"


def _passa(fn) -> tuple[bool, str]:
    try:
        return True, f"passou: {str(fn())[:130]}"
    except NaoConferido as exc:
        return False, f"nao conferiu (aqui era para conferir e passar): {str(exc)[:130]}"
    except Exception as exc:                                             # noqa: BLE001
        return False, f"reprovou sem motivo: {type(exc).__name__}: {str(exc)[:130]}"


# ------------------------------------------------------------------ cobertura: toda guarda tem um controle
# Guarda sem controle so pode ficar aqui com o motivo escrito. Numero congelado sem motivo por item registra
# o tamanho da divida, nunca a razao dela - e divida sem razao volta a crescer na revisao seguinte.
ISENTAS: dict[str, str] = {
    # a chave e o NOME da constante como ele aparece no fonte da guarda, nao o texto dela: o que o AST
    # devolve e o codigo (`{MARCA_...}`), e nao a mensagem depois de formatada
    "MARCA_OBJETO_VAZIO": (
        "instrumento quebrado inalcancavel por entrada: `git cat-file commit` de um sha ja resolvido por "
        "`rev-parse ^{commit}` ou devolve o objeto ou sai com codigo de erro (que vira RuntimeError antes "
        "daqui). Nenhum repositorio sintetico produz saida vazia com codigo 0. Fica declarada, com motivo, "
        "em vez de virar um controle de mentira."),
}


def _guardas_no_fonte(fonte: str) -> list[tuple[int, int, str]]:
    """(primeira linha, ultima linha, nome) de cada chamada a fail()/nao_conferido() NESTE arquivo, pelo
    AST - nao pela grafia, que conta comentario e docstring junto e da um numero que ninguem confere."""
    saida: list[tuple[int, int, str]] = []
    for no in ast.walk(ast.parse(fonte)):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id in ("fail", "nao_conferido")):
            saida.append((no.lineno, no.end_lineno or no.lineno, no.func.id))
    return sorted(saida)


def _cobertura(sitios, cobertas: set[int], linhas: list[str]) -> tuple[list[str], list[str]]:
    """(guardas sem controle, isentas declaradas). Funcao unica: o controle da cobertura chama ESTA, nao
    uma copia do raciocinio dela."""
    sem_controle: list[str] = []
    isentas: list[str] = []
    for ini, fim, nome in sitios:
        if any(ini <= linha <= fim for linha in cobertas):
            continue
        texto = " ".join(linhas[ini - 1:fim])
        motivo = next((m for chave, m in ISENTAS.items() if chave in texto), None)
        if motivo:
            isentas.append(f"linha {ini} ({nome}): {motivo}")
            continue
        sem_controle.append(f"linha {ini} ({nome})")
    return sem_controle, isentas


def _controles_de_cobertura() -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    fonte = Path(__file__).read_text(encoding="utf-8", errors="ignore")
    linhas = fonte.splitlines()
    sitios = _guardas_no_fonte(fonte)

    # o instrumento que enumera as guardas nao pode responder vazio: zero aqui seria "nao perguntei", e a
    # cobertura sairia verde sem ter olhado guarda nenhuma. Segunda contagem, por outro caminho.
    por_grafia = {i + 1 for i, ln in enumerate(linhas) if re.match(r"^\s*(fail|nao_conferido)\(", ln)}
    inicios = {i for i, _f, _n in sitios}
    ok = bool(sitios) and bool(por_grafia) and por_grafia <= inicios
    saida.append(("INSTRUMENTO: o AST enxerga todas as guardas que a grafia enxerga", ok,
                  f"AST={len(sitios)} guardas, grafia={len(por_grafia)} chamadas em posicao de comando"
                  if ok else f"AST={len(sitios)}, grafia={len(por_grafia)}, so na grafia: "
                             f"{sorted(por_grafia - inicios)}"))

    sem_controle, isentas = _cobertura(sitios, _COBERTAS, linhas)
    saida.append((f"{MARCA_SUITE} que reprova pela mensagem DELA", not sem_controle,
                  f"{len(sitios)} guardas: {len(sitios) - len(sem_controle) - len(isentas)} mortas por "
                  f"controle, {len(isentas)} isentas declaradas" if not sem_controle
                  else f"SEM CONTROLE NENHUM: {sem_controle}"))
    for texto in isentas:
        saida.append((f"COBERTURA isenta declarada, {texto.split(':')[0]}", True, texto.split(": ", 1)[-1]))

    # a guarda que exige cobertura tambem precisa do seu proprio controle: tirar UMA guarda do conjunto
    # coberto tem de ser acusado por ESTA funcao, a mesma que o portao usa
    alvo = next((s for s in sitios if any(s[0] <= linha <= s[1] for linha in _COBERTAS)), None)
    if alvo is None:
        saida.append(("COBERTURA <- controle da propria cobertura", False,
                      "nenhuma guarda coberta para mutar: o controle da cobertura nao pode ser escrito"))
    else:
        menos_uma = {linha for linha in _COBERTAS if not (alvo[0] <= linha <= alvo[1])}
        acusadas, _isentas = _cobertura(sitios, menos_uma, linhas)
        ok = any(f"linha {alvo[0]} " in f"{x} " for x in acusadas)
        saida.append((f"COBERTURA <- apagar o controle da guarda da linha {alvo[0]} (tem de ACUSAR)", ok,
                      f"acusou {acusadas}"[:190] if ok else f"NAO acusou: {acusadas}"[:190]))

    saida.extend(_controles_de_cobertura_do_veredito(fonte))
    return saida


def _veredito_no_fonte(fonte: str) -> list[tuple[int, int]]:
    """(linha, valor) de cada `return <inteiro>` de main() - os pontos em que o resultado se INVERTE.

    A outra populacao, a das guardas, enumera os pontos que LEVANTAM. Sao populacoes diferentes, e foi por
    enumerar so a primeira que a camada de veredito ficou inteira fora da cobertura.
    """
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.FunctionDef) and no.name == "main":
            return sorted((r.lineno, r.value.value) for r in ast.walk(no)
                          if isinstance(r, ast.Return) and isinstance(r.value, ast.Constant)
                          and isinstance(r.value.value, int) and not isinstance(r.value.value, bool))
    return []


def _controles_de_cobertura_do_veredito(fonte: str) -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    suspenso = _ponta_a_ponta_suspensa()
    if suspenso:
        saida.append((f"COBERTURA DO VEREDITO: nao exigida nesta execucao ({suspenso})", True,
                      "esta execucao e filha de um controle ponta a ponta e por isso nao roda o bloco que "
                      "observa codigo de saida; quem exige a cobertura do veredito e a execucao de cima"))
        return saida

    retornos = _veredito_no_fonte(fonte)
    # segunda contagem, por outro caminho: zero aqui seria "nao perguntei", e a cobertura do veredito sairia
    # verde sem ter olhado ponto nenhum. A grafia e so a conferencia - quem enumera e o AST
    por_grafia = {i + 1 for i, ln in enumerate(fonte.splitlines())
                  if re.match(r"^\s+return \d+\s*$", ln)}
    linhas_ast = {ln for ln, _valor in retornos}
    ok = bool(retornos) and bool(por_grafia) and por_grafia <= linhas_ast
    saida.append(("INSTRUMENTO: o AST enxerga os pontos de veredito que a grafia enxerga", ok,
                  f"{len(retornos)} retornos constantes em main(): {retornos}" if ok else
                  f"AST={sorted(linhas_ast)}, grafia={sorted(por_grafia)}, so na grafia: "
                  f"{sorted(por_grafia - linhas_ast)} - zero nao e ausencia"))

    descobertos = [f"linha {ln} (return {valor})" for ln, valor in retornos if valor not in _SAIDAS_VISTAS]
    saida.append(("COBERTURA DO VEREDITO: todo `return` de main() foi observado pelo CODIGO DE SAIDA de um "
                  "controle ponta a ponta verde", bool(retornos) and not descobertos,
                  f"{len(retornos)} pontos de veredito, codigos observados {sorted(_SAIDAS_VISTAS)}"
                  if retornos and not descobertos else f"SEM OBSERVACAO: {descobertos}"))

    exigidas = {n: m for n, m in VEREDITOS_EXIGIDOS.items()
                if not (_profundidade() and n in VEREDITOS_SO_NO_TOPO)}
    adiadas = sorted(set(VEREDITOS_EXIGIDOS) - set(exigidas))
    if adiadas:
        saida.append((f"COBERTURA DO VEREDITO: {len(adiadas)} observacoes ficam para a execucao de cima "
                      f"({VAR_PROFUNDIDADE}={_profundidade()})", True, f"adiadas nesta execucao: {adiadas}"))
    # simetrica de proposito: "falta observacao" acusa controle apagado, e "observacao a mais" acusa a lista
    # de exigencias esvaziada - uma lista vazia conferida contra si mesma daria verde sem exigir nada
    faltando = sorted(set(exigidas) - _VEREDITOS_VISTOS)
    sobrando = sorted(_VEREDITOS_VISTOS - set(exigidas))
    saida.append(("COBERTURA DO VEREDITO: o que o bloco ponta a ponta observou e exatamente o que a lista de "
                  "exigencias declara", bool(exigidas) and not faltando and not sobrando,
                  f"{len(exigidas)} observacoes exigidas, todas registradas por controle verde"
                  if exigidas and not faltando and not sobrando else
                  f"faltando={faltando} sobrando={sobrando} exigidas={len(exigidas)}"))
    for nome, motivo in exigidas.items():
        saida.append((f"COBERTURA DO VEREDITO exige `{nome}`, e o motivo", nome in _VEREDITOS_VISTOS,
                      motivo if nome in _VEREDITOS_VISTOS else f"NAO observada. Existe porque: {motivo}"))
    return saida


# ------------------------------------------------------------------ mutacoes do YAML, uma por guarda
def _mutacoes_do_yaml(texto: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """(nome, YAML mutado, marcas exigidas). Cada linha que sustenta a trava, apagada, tem de REPROVAR."""
    saida: list[tuple[str, str, tuple[str, ...]]] = []
    saida.append(("chamada trocada por um caminho que nao existe",
                  texto.replace(CHAMADA, "scripts/nao_existe.py"), (MARCA_SEM_CHAMADA,)))
    saida.append(("`run:` da trava virou comentario (o caminho continua no corpo do job)",
                  re.sub(rf"(?m)^([ \t]*)(run:.*{re.escape(CHAMADA)}.*)$", r"\1# \2", texto),
                  (MARCA_SEM_EXECUCAO,)))
    sem_fd = re.sub(r"(?m)^[ \t]*fetch-depth:[ \t]*0[ \t]*(#.*)?\n", "", texto)
    saida.append(("`fetch-depth: 0` apagado do checkout", sem_fd, (MARCA_FETCH_DEPTH,)))
    saida.append(("`fetch-depth: 0` mudado para um passo que nao e checkout",
                  _insere_passo(sem_fd, ["- name: ruido que cita fetch-depth sem ser checkout",
                                         "  with:", "    fetch-depth: 0", "  run: echo ok"]),
                  (MARCA_FETCH_DEPTH,)))
    saida.append(("`if:` no job que executa a trava",
                  _insere_no_job(texto, "    if: github.event_name == 'push'"), (MARCA_JOB_CONDICIONAL,)))
    saida.append(("gatilho `pull_request` removido", texto.replace("\n  pull_request:", "\n  pull_request_x:"),
                  (MARCA_SEM_GATILHO,)))
    bloco_on = _bloco_top(texto, "on")
    saida.append(("`push` filtrado para um ramo que nao e a base",
                  texto.replace(bloco_on, re.sub(r"(?m)^(    branches:).*$", r"\1 [ nao-existe ]",
                                                 bloco_on, count=1), 1), (MARCA_GATILHO_FILTRADO,)))
    bloco_jobs = _bloco_top(texto, "jobs")
    saida.append(("bloco `jobs:` sem job nenhum", texto.replace(bloco_jobs, "", 1), (MARCA_SEM_JOBS,)))
    return saida


def _linha_da_execucao(texto: str) -> int:
    """Linha do passo `run:` que executa a trava - e dela que as mutacoes do YAML se penduram."""
    for i, ln in enumerate(texto.splitlines()):
        if CHAMADA in ln and re.match(r"^(?:-\s+)?run:", ln.strip()):
            return i
    return -1


def _insere_passo(texto: str, bloco: list[str]) -> str:
    """Insere um passo novo imediatamente ANTES do passo que executa a trava."""
    linhas = texto.splitlines()
    alvo = _linha_da_execucao(texto)
    if alvo < 0:
        return texto
    ini = alvo
    while ini > 0 and not re.match(r"^\s*-\s", linhas[ini]):
        ini -= 1
    ind = " " * _indent(linhas[ini])
    return "\n".join(linhas[:ini] + [ind + ln for ln in bloco] + linhas[ini:]) + "\n"


def _insere_no_job(texto: str, linha_nova: str) -> str:
    """Insere uma chave logo abaixo da chave do job que executa a trava."""
    linhas = texto.splitlines()
    alvo = _linha_da_execucao(texto)
    if alvo < 0:
        return texto
    ini = alvo
    while ini > 0 and not re.match(r"^  [A-Za-z0-9_.-]+:\s*(#.*)?$", linhas[ini]):
        ini -= 1
    if ini == 0:
        return texto
    return "\n".join(linhas[:ini + 1] + [linha_nova] + linhas[ini + 1:]) + "\n"


# --------------------------------------------- ponta a ponta: o portao rodando A SI MESMO, pelo codigo de saida
def _profundidade() -> int:
    bruto = (os.environ.get(VAR_PROFUNDIDADE) or "0").strip()
    try:
        return max(0, int(bruto))
    except ValueError:
        return 0                  # valor estragado nao vale como teto: quem limita de verdade e o VAR_FILHO


def _ponta_a_ponta_suspensa() -> str:
    """Motivo pelo qual ESTA execucao nao roda o bloco ponta a ponta - vazio quando ela roda.

    Sem suspensao o portao chamaria a si mesmo sem fim. Sao duas travas, nao uma: a variavel que o pai poe
    em todo filho, e o contador de profundidade que TODO filho propaga - o contador continua valendo mesmo
    para o filho que roda de proposito sem a variavel (o controle que prova que ela nao e chave de desligar).
    """
    prof = _profundidade()
    if prof >= PROFUNDIDADE_MAX:
        return f"teto de recursao ({VAR_PROFUNDIDADE}={prof}, maximo {PROFUNDIDADE_MAX})"
    if (os.environ.get(VAR_FILHO) or "").strip() == "1":
        return f"{VAR_FILHO}=1, execucao filha de um controle ponta a ponta"
    return ""


def _roda_o_portao(repo: Path, *args: str, filho: bool = True,
                   segundos: int = 180) -> tuple[int | None, str]:
    """Executa ESTE arquivo em subprocesso, apontado para um repositorio sintetico. (codigo de saida, saida).

    E a MESMA interface que o CI consome: `python scripts/base_atualizada_gate.py` e o numero que ele devolve.
    Chamar `main()` em processo nao serviria - em processo se le o valor de uma funcao, e quem decide o check
    e o codigo de saida.
    """
    env = dict(os.environ)
    for k in ("RX_PR_HEAD_SHA", "RX_PR_BASE_SHA"):
        env.pop(k, None)                       # variavel de PR do ambiente de fora mudaria o caso do filho
    env["RX_BASE_ATU_ROOT"] = str(repo)
    env["RX_BASE_ATU_WORKFLOW"] = str(WORKFLOW)
    env["PYTHONIOENCODING"] = "utf-8"          # no Windows a saida canalizada cairia no codepage do console
    env[VAR_PROFUNDIDADE] = str(_profundidade() + 1)
    env.pop(VAR_FILHO, None)
    if filho:
        env[VAR_FILHO] = "1"
    try:
        # o teto fica ABAIXO do `timeout-minutes` do job: filho pendurado tem de virar controle vermelho com
        # mensagem, nunca job morto pelo GitHub - job morto nao diz o que aconteceu
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], cwd=str(repo),
                              capture_output=True, timeout=segundos, env=env)
    except subprocess.TimeoutExpired:
        return None, f"o portao filho nao terminou em {segundos}s (possivel recursao: a suspensao falhou)"
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "ignore")


def _exige_saida(nome: str, codigo: int | None, saida: str, esperado: int, *exigidos: str,
                 proibidos: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Cobra do filho o codigo de saida E as linhas daquele caso. So o verde registra a observacao `nome`."""
    if codigo is None:
        return False, saida
    problemas = [] if codigo == esperado else [f"codigo de saida {codigo}, esperado {esperado}"]
    problemas += [f"a saida do filho nao tem {t!r}" for t in exigidos if t not in saida]
    problemas += [f"a saida do filho tem {t!r}, que nao podia aparecer" for t in proibidos if t in saida]
    veredito = next((ln for ln in reversed(saida.splitlines())
                     if ln.startswith("RX_BASE_ATUALIZADA_GATE=")), "sem linha de veredito")
    if problemas:
        return False, f"{'; '.join(problemas)} | {veredito}"
    _VEREDITOS_VISTOS.add(nome)
    _SAIDAS_VISTAS.add(codigo)
    return True, f"saiu com codigo {codigo}, que e o que o CI le | {veredito}"


def _controles_ponta_a_ponta() -> list[tuple[str, bool, str]]:
    """Roda SEMPRE, mesmo com `--sem-controles`: se dependesse dessa linha, a mesma edicao que desliga os
    controles em processo desligaria tambem o unico observador que enxerga o portao de fora."""
    saida: list[tuple[str, bool, str]] = []
    _VEREDITOS_VISTOS.clear()
    _SAIDAS_VISTAS.clear()

    # a suspensao tem de ter MOTIVO no ambiente: sem as duas variaveis ela nao pode existir, senao seria uma
    # chave de desligar o bloco inteiro - e essa conferencia roda antes da propria suspensao, de proposito
    with _aponta(**{VAR_FILHO: None, VAR_PROFUNDIDADE: None}):
        sem_motivo = _ponta_a_ponta_suspensa()
    saida.append(("INSTRUMENTO: sem as duas variaveis a suspensao do ponta a ponta nao tem motivo (ela "
                  "existe contra recursao, nao para desligar o bloco)", sem_motivo == "",
                  f"com {VAR_FILHO} e {VAR_PROFUNDIDADE} fora do ambiente, o bloco roda" if not sem_motivo
                  else f"SUSPENSO mesmo sem as variaveis: {sem_motivo}"))

    # quem julga o ponta a ponta tambem e codigo, e seria o unico sem controle: entregar a ele casos FALSOS
    # e exigir o veredito falso. Sem isto, afrouxar `_exige_saida` - parar de comparar o codigo de saida, por
    # exemplo - nao mataria controle nenhum. O nome `_probe` nao esta em VEREDITOS_EXIGIDOS de proposito: se
    # uma dessas chamadas ficasse verde, ela se registraria e a conferencia simetrica acusaria "sobrando".
    probas = [("codigo de saida errado", _exige_saida("_probe", 0, "RX_BASE_ATUALIZADA_GATE=PASS", 1)),
              ("linha exigida ausente", _exige_saida("_probe", 1, "RX_BASE_ATUALIZADA_GATE=FALHA", 1,
                                                     "linha que o filho nunca imprimiu")),
              ("linha proibida presente", _exige_saida("_probe", 0, "SEM CONFERENCIA", 0,
                                                       proibidos=("SEM CONFERENCIA",))),
              ("filho que nao terminou", _exige_saida("_probe", None, "estourou o tempo", 0))]
    vivas = [nome for nome, (ok, _detalhe) in probas if ok]
    saida.append(("INSTRUMENTO: quem julga o ponta a ponta (_exige_saida) reprova os quatro casos falsos",
                  not vivas, f"os {len(probas)} casos falsos reprovaram" if not vivas
                  else f"ACEITOU como verde: {vivas}"))

    suspenso = _ponta_a_ponta_suspensa()
    if suspenso:
        saida.append((f"{MARCA_E2E_SUSPENSO} nesta execucao: {suspenso}", True,
                      "quem exige o codigo de saida e a execucao de cima, que criou este filho de proposito"))
        return saida
    with tempfile.TemporaryDirectory(prefix="rx_base_atu_e2e_") as td:
        saida.extend(_ponta_a_ponta_nos_repos(Path(td)))
    return saida


def _ponta_a_ponta_nos_repos(raiz: Path) -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    atrasado = _repo_sintetico(raiz, em_dia=False)
    em_dia = _repo_sintetico(raiz, em_dia=True)

    cod, txt = _roda_o_portao(atrasado, "--sem-controles")
    ok, detalhe = _exige_saida("saida 1 no merge atrasado", cod, txt, 1,
                               "FALHA merge_nao_stale", MARCA_MERGE_STALE, "RX_BASE_ATUALIZADA_GATE=FALHA",
                               proibidos=("RX_BASE_ATUALIZADA_GATE=PASS",))
    saida.append((f"{MARCA_E2E} repositorio com merge atrasado (codigo 1 E a linha FALHA; imprimir a linha "
                  "e sair 0 e o defeito)", ok, detalhe))

    cod, txt = _roda_o_portao(em_dia, "--sem-controles")
    ok, detalhe = _exige_saida("saida 0 no repositorio em dia", cod, txt, 0,
                               "PASSA merge_nao_stale", "RX_BASE_ATUALIZADA_GATE=PASS",
                               proibidos=("RX_BASE_ATUALIZADA_GATE=FALHA", "SEM CONFERENCIA"))
    saida.append((f"{MARCA_E2E} repositorio em dia (codigo 0 E a linha PASSA)", ok, detalhe))

    cod, txt = _roda_o_portao(_repo_um_pai(raiz), "--sem-controles")
    ok, detalhe = _exige_saida("saida 0 com SEM CONFERENCIA na ponta de um pai so", cod, txt, 0,
                               "SEM CONFERENCIA", "conferidas=['ci_roda_a_trava']", "regras=3",
                               "NAO_CONFERIDO merge_nao_stale", "NAO_CONFERIDO pr_base_atualizada",
                               "PASSA ci_roda_a_trava")
    saida.append((f"{MARCA_E2E} ponta com um pai so (codigo 0, linha SEM CONFERENCIA, e as tres regras "
                  "executadas com NaoConferido FORA de `conferidas`)", ok, detalhe))

    if _profundidade():
        saida.append((f"{MARCA_E2E_SUSPENSO} a parte que roda o portao INTEIRO, porque esta execucao ja e "
                      f"filha ({VAR_PROFUNDIDADE}={_profundidade()})", True,
                      "cada um desses dois controles roda a suite inteira num subprocesso; repeti-los em "
                      "cada nivel multiplicaria o custo do portao sem cobrir nada de novo"))
        return saida

    cod, txt = _roda_o_portao(em_dia)
    ok, detalhe = _exige_saida("o bloco de controles e executado pelo portao", cod, txt, 0,
                               f"CONTROLE_OK {MARCA_SUITE}", MARCA_E2E_SUSPENSO,
                               "RX_BASE_ATUALIZADA_GATE=PASS",
                               proibidos=("CONTROLE_FALHOU", MARCA_E2E))
    saida.append((f"{MARCA_E2E} o portao INTEIRO com {VAR_FILHO}=1 (a suite em processo roda - tem de sair "
                  f"a linha `{MARCA_SUITE}` - e so o ponta a ponta fica suspenso)", ok, detalhe))

    cod, txt = _roda_o_portao(em_dia, filho=False)
    ok, detalhe = _exige_saida("sem a variavel de suspensao o portao roda tudo", cod, txt, 0,
                               f"CONTROLE_OK {MARCA_SUITE}", MARCA_E2E, "RX_BASE_ATUALIZADA_GATE=PASS",
                               proibidos=("CONTROLE_FALHOU",))
    saida.append((f"{MARCA_E2E} o portao INTEIRO SEM {VAR_FILHO} (tem de rodar tambem o ponta a ponta: a "
                  "suspensao e contra recursao, nao chave de desligar)", ok, detalhe))
    return saida


def controles() -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    _COBERTAS.clear()
    with tempfile.TemporaryDirectory(prefix="rx_base_atu_") as td:
        raiz = Path(td)

        # ---- merge_nao_stale, sempre PELA REGRA (nunca pelo ajudante `_merge_stale`, que foi o furo do #81)
        atrasado = _repo_sintetico(raiz, em_dia=False)
        with _sem_pr(atrasado):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_MERGE_STALE)
        saida.append(("merge_nao_stale <- merge sobre base velha (tem de REPROVAR: e o unico fail que "
                      "justifica esta trava)", ok, detalhe))

        em_dia = _repo_sintetico(raiz, em_dia=True)
        with _sem_pr(em_dia):
            ok, detalhe = _passa(r_merge_nao_stale)
        saida.append(("merge_nao_stale <- merge com o ramo atualizado antes (tem de PASSAR)", ok, detalhe))

        with _sem_pr(_repo_um_pai(raiz)):
            ok, detalhe = _nao_confere(r_merge_nao_stale, MARCA_NC_UM_PAI)
        saida.append(("merge_nao_stale <- ponta com um pai so (tem de dizer NAO CONFERIDO, nunca PASSA)",
                      ok, detalhe))

        # ---- historico incompleto: a armadilha existe, e as duas leituras respondem diferente sobre ela
        raso = _clona(atrasado, raiz / "clone_raso", raso=True)
        cheio = _clona(atrasado, raiz / "clone_cheio", raso=False)
        pelo_grafo = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=raso).split()[1:]
        _sha_obj, pelo_objeto = _pais_do_objeto(raso, "HEAD")
        saida.append(("a armadilha: no clone raso a leitura pelo grafo diz ZERO pais e o objeto do commit "
                      "diz 2", not pelo_grafo and len(pelo_objeto) == 2,
                      f"grafo={[p[:12] for p in pelo_grafo]} objeto={[p[:12] for p in pelo_objeto]} - por "
                      "isso a trava le o objeto E recusa o clone raso antes de concluir"))
        with _sem_pr(raso):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_RASO, MARCA_FETCH_DEPTH)
        saida.append(("merge_nao_stale <- `git clone --depth 1` de verdade (tem de REPROVAR pelo raso)",
                      ok, detalhe))

        sem_marcador = _clona(atrasado, raiz / "clone_sem_marcador", raso=True)
        (_dir_git(sem_marcador) / "shallow").unlink()      # historico cortado, sem o aviso de que esta
        with _sem_pr(sem_marcador):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_PAI_AUSENTE, MARCA_FETCH_DEPTH)
        saida.append(("merge_nao_stale <- historico cortado SEM o marcador `shallow` (tem de REPROVAR pelo "
                      "pai ausente)", ok, detalhe))

        with _sem_pr(cheio):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_MERGE_STALE)
        saida.append(("NEGATIVO: clone completo do MESMO repositorio passa pela guarda de raso e reprova "
                      "pelo merge velho", ok, detalhe))

        # ---- o desvio "estou em PR" nao pode virar chave de desligar o merge_nao_stale
        head_atrasado = git("rev-parse", "ramo", cwd=atrasado)
        with _aponta(repo=atrasado, RX_PR_HEAD_SHA="0" * 40, RX_PR_BASE_SHA=None):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_HEAD_PR)
        saida.append(("merge_nao_stale <- RX_PR_HEAD_SHA que nao existe no clone (tem de REPROVAR)", ok,
                      detalhe))
        with _aponta(repo=atrasado, RX_PR_HEAD_SHA=head_atrasado, RX_PR_BASE_SHA=None):
            ok, detalhe = _nao_confere(r_merge_nao_stale, MARCA_NC_EM_PR)
        saida.append(("merge_nao_stale <- head de PR real (tem de dizer NAO CONFERIDO e apontar quem "
                      "confere)", ok, detalhe))

        # ---- pr_base_atualizada
        base_atual = git("rev-parse", BASE_REF, cwd=atrasado)
        with _aponta(repo=atrasado, RX_PR_HEAD_SHA=head_atrasado, RX_PR_BASE_SHA=base_atual):
            ok, detalhe = _reprova_com(r_pr_base_atualizada, MARCA_PR_ATRASADO)
        saida.append(("pr_base_atualizada <- head atrasado (tem de REPROVAR)", ok, detalhe))
        with _aponta(repo=em_dia, RX_PR_HEAD_SHA=git("rev-parse", "ramo", cwd=em_dia),
                     RX_PR_BASE_SHA=git("rev-parse", BASE_REF + "~1", cwd=em_dia)):
            ok, detalhe = _passa(r_pr_base_atualizada)
        saida.append(("pr_base_atualizada <- head em dia (tem de PASSAR)", ok, detalhe))
        with _sem_pr(atrasado):
            ok, detalhe = _nao_confere(r_pr_base_atualizada, MARCA_NC_FORA_PR)
        saida.append(("pr_base_atualizada <- fora de PR (tem de dizer NAO CONFERIDO, nunca PASSA)", ok,
                      detalhe))

        # ---- ci_roda_a_trava: o YAML de verdade passa, e cada mutacao reprova pela marca dela
        ok, detalhe = _passa(r_ci_roda_a_trava)
        saida.append(("NEGATIVO: ci_roda_a_trava <- o fluxo de trabalho de verdade (tem de PASSAR)", ok,
                      detalhe))
        with _aponta(workflow=raiz / "fluxo_que_nao_existe.yml"):
            ok, detalhe = _reprova_com(r_ci_roda_a_trava, MARCA_SEM_FLUXO)
        saida.append(("ci_roda_a_trava <- fluxo de trabalho ausente (tem de REPROVAR)", ok, detalhe))

        texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore") if WORKFLOW.exists() else ""
        for nome, mutado, marcas in _mutacoes_do_yaml(texto):
            if mutado == texto:
                saida.append((f"ci_roda_a_trava <- {nome}", False,
                              "mutacao nao mudou o arquivo: a ancora da mutacao nao casa mais com o YAML"))
                continue
            # nome do arquivo saneado: ":" num caminho do Windows vira fluxo alternativo em vez de arquivo
            alvo = raiz / f"quality-gate_{re.sub(r'[^a-z0-9]+', '-', nome.lower()).strip('-')[:60]}.yml"
            alvo.write_text(mutado, encoding="utf-8", newline="\n")
            with _aponta(workflow=alvo):
                ok, detalhe = _reprova_com(r_ci_roda_a_trava, *marcas)
            saida.append((f"ci_roda_a_trava <- {nome} (tem de REPROVAR)", ok, detalhe))

    saida.extend(_controles_de_cobertura())
    return saida


def main() -> int:
    args = sys.argv[1:]
    falhas: list[str] = []
    conferidas: list[str] = []
    nao_conferidas: list[str] = []
    for nome, fn in RULES.items():
        try:
            print(f"PASSA {nome}: {fn()}", flush=True)
            conferidas.append(nome)
        except NaoConferido as exc:
            print(f"NAO_CONFERIDO {nome}: {exc}", flush=True)
            nao_conferidas.append(nome)
        except Exception as exc:  # noqa: BLE001
            print(f"FALHA {nome}: {type(exc).__name__}: {exc}", flush=True)
            falhas.append(nome)
    # o bloco ponta a ponta roda ANTES e FORA do `--sem-controles`, por dois motivos que ja foram defeito:
    # fora, porque a linha que desliga os controles em processo nao pode desligar tambem o unico observador
    # que le o portao de fora (pelo codigo de saida); antes, porque e a cobertura do veredito - conferida la
    # dentro de `controles()` - que acusa quando ESTA chamada some daqui.
    controlados = _controles_ponta_a_ponta()
    if "--sem-controles" not in args:
        controlados += controles()
    if not controlados:
        print("FALHA controles: nenhum controle rodou (instrumento quebrado)", flush=True)
        falhas.append("controles:vazio")
    for titulo, ok, detalhe in controlados:
        print(f"{'CONTROLE_OK' if ok else 'CONTROLE_FALHOU'} {titulo}: {detalhe[:200]}", flush=True)
        if not ok:
            falhas.append(f"controle:{titulo}")
    # concluir e passar OU reprovar: quem reprovou conferiu e achou defeito. Fica de fora so a regra que
    # nao teve o que conferir - e quando NENHUMA das duas teve, o relatorio diz isso em vez de calar.
    if not [n for n in conferidas + falhas if n in REGRAS_DE_BASE]:
        print(f"SEM CONFERENCIA: nesta execucao nenhuma regra de base atualizada conferiu coisa alguma "
              f"({', '.join(nao_conferidas) or 'nenhuma saida'}). Isto NAO e um verde sobre a base - e a "
              "ausencia de conferencia, dita em voz alta. Quem fecha o buraco e a protecao de branch do "
              "GitHub com 'require branches to be up to date before merging'.", flush=True)
    _VERMELHOS.extend(falhas)
    if falhas:
        print(f"RX_BASE_ATUALIZADA_GATE=FALHA {falhas}", flush=True)
        return 1
    print(f"RX_BASE_ATUALIZADA_GATE=PASS regras={len(RULES)} conferidas={conferidas} "
          f"nao_conferidas={nao_conferidas}", flush=True)
    return 0


if __name__ == "__main__":
    # dois caminhos independentes do vermelho ate o CI, e e de proposito que sejam dois. A linha que
    # traduz falha em codigo de saida e ela mesma uma linha editavel: trocar o `return 1` por `return 0`
    # deixaria o relatorio cheio de CONTROLE_FALHOU e o check VERDE, que e o defeito desta branch uma
    # camada acima. Com o segundo canal, uma edicao de UMA linha nao fecha os dois.
    sys.exit(1 if (main() or _VERMELHOS) else 0)
