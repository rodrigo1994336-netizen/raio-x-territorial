"""Trava de processo: verde de ramo so vale sobre a base em que ele vai entrar.

Por que existe (16/09/2026). Os PRs #78 e #79 sairam da MESMA base (a8a296c), cada um passou VERDE no seu
ramo, e foram mesclados um atras do outro. Juntos deixaram a `main` vermelha: o #78 criou uma regra que
enumera modulos de remendo pela propriedade "levanta quando a ancora nao casa" e o #79, no mesmo dia,
trocou o canal de sinal de um desses modulos (passou a imprimir o marcador em vez de levantar). Nenhum dos
dois CIs podia ver o defeito: ele so existe na combinacao, e nenhum dos dois rodou sobre a combinacao.

O que esta trava reprova, e o que ela NAO promete:
  * `merge_nao_stale` - no commit que esta na `main` agora: se for merge, o ramo mesclado tinha de CONTER o
    commit que era a ponta da `main` antes do merge. Se nao continha, o verde daquele ramo era sobre outra
    arvore: reprova, nomeia os dois commits e diz o que fazer. Isto pega o erro no ato em que ele entra,
    mesmo quando todos os outros portoes ficam verdes por sorte - que e o caso silencioso e o pior deles.
  * `pr_base_atualizada` - num PR: a ponta da `main` de AGORA tem de estar contida no head do PR. Enquanto
    o ramo estiver atrasado, o check fica vermelho no proprio PR, antes do merge.
  * `ci_roda_a_trava` - o fluxo de trabalho tem de chamar esta trava nos dois momentos E no job que a chama
    o checkout tem de trazer o historico inteiro (`fetch-depth: 0`). Portao que some do CI vira arquivo
    morto sem ninguem notar; portao que roda sem historico e pior, porque continua imprimindo verde.
NAO promete impedir o merge: o check do PR e uma fotografia do instante em que rodou, e ninguem re-roda o CI
de um PR parado quando outro PR entra. Quem IMPEDE e a protecao de branch do GitHub com "require branches to
be up to date before merging" (ligar e decisao do dono, porque muda quem consegue mesclar). Ate la a ordem
escrita vale: atualizar o ramo com a main, esperar o CI novo, e so entao mesclar.

Historico raso e o caso silencioso desta trava (conserto de 16/09). Num `clone --depth 1` o commit da ponta
vira enxerto e reporta ZERO pais: sem guarda, a leitura dos pais concluiria "nao e merge, nada a conferir" e
o portao imprimiria PASSA exatamente no caso que ele existe para pegar. Por isso a primeira coisa que as
regras de historico fazem e recusar o clone raso, com a mensagem do `fetch-depth: 0` - antes de qualquer
conclusao. E a regra do CI passou a EXIGIR a linha no job, porque guarda que depende de configuracao
externa some numa edicao e deixa o portao verde para sempre.

Controles positivos por construcao: cada regra e exercitada num repositorio git sintetico montado na hora,
na configuracao em que ela vai rodar, e cada controle cobra a MENSAGEM daquela guarda - controle que aceita
"levantou alguma coisa" mede o otimismo do autor, nao a guarda. Entre eles, um `git clone --depth 1` de
verdade do repositorio atrasado (tem de REPROVAR pelo clone raso), o mesmo repositorio clonado inteiro (tem
de passar pela guarda de raso e ainda acusar o atraso), e as mutacoes do YAML que tiram a chamada, a linha
`fetch-depth: 0` e o gatilho `pull_request` (cada uma tem de REPROVAR com a sua marca).
"""
from __future__ import annotations

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

# Marcas de cada guarda. O controle positivo cobra a marca da guarda que ele diz exercitar: sem isso um
# controle fica verde com QUALQUER erro anterior - inclusive o erro que prova o contrario do que se quer
# provar (foi assim que o caso do clone raso passou despercebido).
MARCA_RASO = "clone e raso"
MARCA_FETCH_DEPTH = "fetch-depth: 0"
MARCA_PAI_AUSENTE = "nao esta no clone"
MARCA_PR_ATRASADO = "ramo do PR atrasado"
MARCA_HEAD_PR = "RX_PR_HEAD_SHA"
MARCA_SEM_CHAMADA = "o CI nao chama"
MARCA_SEM_GATILHO = "nao dispara em"


class Reprova(AssertionError):
    pass


def fail(msg: str):
    raise Reprova(msg)


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

    Num clone raso o `git` responde com meia-verdade: o commit da ponta e um enxerto e reporta zero pais.
    Sem esta guarda a leitura dos pais concluiria "nao e merge, nada a conferir" e o portao imprimiria
    PASSA justamente no caso que ele existe para pegar - o pior jeito de falhar, porque parece sucesso.
    """
    if repo_raso(repo):
        fail(f"instrumento quebrado: o {MARCA_RASO} (historico cortado no enxerto). Sem o historico "
             "completo esta trava nao ve os pais do merge e passaria calada justamente no caso que ela "
             f"existe para pegar. O passo precisa de actions/checkout com {MARCA_FETCH_DEPTH}.")


def _merge_stale(repo: Path, commit: str = "HEAD") -> tuple[str, str, list[str], bool]:
    """(sha, base, pais atrasados, e_merge). Reprova se o historico nao veio - clone raso ou pai ausente.

    A conclusao "nao e merge" sai da MESMA leitura que passou pela guarda; nenhuma regra deduz isso com uma
    segunda chamada de git, que e como a guarda deixava de ser alcancada.
    """
    exige_historico_completo(repo)
    campos = git("rev-list", "--parents", "-n", "1", commit, cwd=repo).split()
    if not campos:
        fail("instrumento quebrado: `git rev-list --parents` nao devolveu commit nenhum para a ponta")
    sha, pais = campos[0], campos[1:]
    if len(pais) < 2:
        return sha, "", [], False                        # nao e merge: nada a conferir
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
        # Em PR o checkout entrega o merge automatico do GitHub (base + head), e quem responde por ele e a
        # regra do PR, com a ponta da base lida ao vivo. O desvio so vale se o head do PR existir MESMO
        # neste clone: senao uma variavel solta num push desligaria esta regra sem ninguem notar.
        if not existe_commit(head_pr):
            fail(f"instrumento quebrado: {MARCA_HEAD_PR}={head_pr[:12]} nao e um commit deste clone. Ou o "
                 "checkout veio sem o head do PR, ou a variavel foi definida fora de um PR - nos dois "
                 "casos esta regra ficaria desligada em silencio.")
        return "em PR: quem confere e pr_base_atualizada"
    sha, base, atrasados, e_merge = _merge_stale(ROOT)
    if atrasados:
        fail(f"merge sobre base desatualizada: {sha[:12]} juntou um ramo que NAO continha {base[:12]}, a "
             f"ponta da `{BASE_REF}` de antes do merge ({', '.join(p[:12] for p in atrasados)}). O verde "
             "daquele ramo foi medido em outra arvore e nao vale para esta. Antes de mesclar: atualizar o "
             f"ramo com a `{BASE_REF}`, esperar o CI novo, mesclar so entao.")
    if not e_merge:
        return f"{sha[:12]} nao e merge (nada a conferir)"
    return f"{sha[:12]} e merge e o ramo continha a ponta anterior da `{BASE_REF}`"


@regra("pr_base_atualizada")
def r_pr_base_atualizada() -> str:
    """No PR: o head tem de conter a ponta ATUAL da base. Fora de PR, nao ha o que conferir."""
    head = (os.environ.get("RX_PR_HEAD_SHA") or "").strip()
    if not head:
        return "fora de PR (sem RX_PR_HEAD_SHA)"
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


@regra("ci_roda_a_trava")
def r_ci_roda_a_trava() -> str:
    """O fluxo chama esta trava nos dois momentos, e o job que a chama traz o historico inteiro."""
    if not WORKFLOW.exists():
        fail(f"instrumento quebrado: fluxo de trabalho nao encontrado em {WORKFLOW}")
    texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore")
    gatilhos = _chaves_indent2(_bloco_top(texto, "on"))
    faltando = [g for g in ("push", "pull_request") if g not in gatilhos]
    if faltando:
        fail(f"o fluxo de trabalho {MARCA_SEM_GATILHO} {', '.join(faltando)}: a trava so vale se rodar no "
             "PR (ramo atrasado) e no push da base (merge ja feito sobre base velha)")
    jobs = _jobs(texto)
    if not jobs:
        fail("instrumento quebrado: nenhum job separado do fluxo de trabalho. O leitor de estrutura desta "
             "regra parou de enxergar o arquivo e responderia sobre um YAML vazio - zero nao e ausencia")
    donos = [n for n, corpo in jobs.items() if CHAMADA in corpo]
    if not donos:
        fail(f"{MARCA_SEM_CHAMADA} {CHAMADA} em job nenhum: a trava de base atualizada sairia do caminho "
             "sem ninguem notar")
    sem_historico = [n for n in donos if not re.search(r"(?m)^\s*fetch-depth:\s*0\s*(#.*)?$", jobs[n])]
    if sem_historico:
        fail(f"o job `{', '.join(sem_historico)}` chama a trava sem `{MARCA_FETCH_DEPTH}` no checkout: com "
             "historico raso o commit da ponta reporta zero pais e a trava nao consegue conferir merge "
             "nenhum. A linha faz parte da trava, nao e detalhe do checkout.")
    return (f"{WORKFLOW.name}: job `{', '.join(donos)}` chama a trava com {MARCA_FETCH_DEPTH}; dispara em "
            f"{', '.join(g for g in gatilhos if g in ('push', 'pull_request'))}")


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


def _repo_sintetico(td: Path, em_dia: bool) -> Path:
    """Monta base + ramo e mescla. `em_dia=False` reproduz o caso do #78/#79: ramo que nao contem a ponta."""
    _SEQ[0] += 1
    repo = td / f"{'em_dia' if em_dia else 'atrasado'}_{_SEQ[0]}"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", BASE_REF, cwd=repo)
    git("config", "user.email", "gate@example.invalid", cwd=repo)
    git("config", "user.name", "gate", cwd=repo)
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


def _clona(origem: Path, destino: Path, raso: bool) -> Path:
    """Clone de verdade. `file://` e obrigatorio: em clone local por caminho o git IGNORA o --depth."""
    args = ["clone", "-q"] + (["--depth", "1"] if raso else []) + [origem.as_uri(), str(destino)]
    git(*args, cwd=origem.parent)
    return destino


def _reprova_com(fn, *marcas: str) -> tuple[bool, str]:
    """Exige Reprova COM as marcas daquela guarda. Qualquer outra excecao NAO conta como reprovacao: um
    controle que aceita 'levantou alguma coisa' fica verde com o erro que prova o contrario do esperado."""
    try:
        saida = fn()
    except Reprova as exc:
        msg = str(exc)
        falta = [m for m in marcas if m not in msg]
        if falta:
            return False, f"reprovou pela mensagem errada, sem {falta}: {msg[:150]}"
        return True, f"reprovou pela guarda certa: {msg[:130]}"
    except Exception as exc:                                             # noqa: BLE001
        return False, (f"levantou {type(exc).__name__} ANTES da guarda (o controle nao chegou a exercita-la)"
                       f": {str(exc)[:130]}")
    return False, f"PASSOU em silencio: {str(saida)[:130]}"


def _passa(fn) -> tuple[bool, str]:
    try:
        return True, f"passou: {str(fn())[:130]}"
    except Exception as exc:                                             # noqa: BLE001
        return False, f"reprovou sem motivo: {type(exc).__name__}: {str(exc)[:130]}"


def controles() -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="rx_base_atu_") as td:
        raiz = Path(td)

        # 1-2. merge_nao_stale sobre repositorio completo: atrasado reprova, em dia passa
        for em_dia in (False, True):
            repo = _repo_sintetico(raiz, em_dia)
            try:
                _sha, _base, atrasados, _m = _merge_stale(repo)
                erro = ""
            except Reprova as exc:
                atrasados, erro = ["<reprovou>"], str(exc)
            if em_dia:
                ok = not atrasados and not erro
                saida.append(("merge_nao_stale <- ramo em dia (tem de PASSAR)", ok,
                              erro or ("passou" if ok else f"reprovou sem motivo: {atrasados}")))
            else:
                ok = bool(atrasados) and not erro
                saida.append(("merge_nao_stale <- ramo atrasado (tem de REPROVAR)", ok,
                              erro or (f"reprovou: {[p[:12] for p in atrasados]}" if ok
                                       else "PASSOU com o defeito posto")))

        # 3-5. clone raso DE VERDADE do mesmo repositorio atrasado: a armadilha existe, a guarda pega,
        #      e o clone completo do mesmo repositorio nao e confundido com ela (controle negativo).
        origem = _repo_sintetico(raiz, em_dia=False)
        raso = _clona(origem, raiz / "clone_raso", raso=True)
        cheio = _clona(origem, raiz / "clone_cheio", raso=False)
        pais_no_raso = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=raso).split()[1:]
        saida.append(("clone raso apaga os pais do merge (a armadilha que a guarda existe para pegar)",
                      not pais_no_raso,
                      "o enxerto reporta zero pais - sem guarda a trava diria 'nao e merge'" if not
                      pais_no_raso else f"clone nao ficou raso: pais={[p[:12] for p in pais_no_raso]}"))
        ok, detalhe = _reprova_com(lambda: _merge_stale(raso), MARCA_RASO, MARCA_FETCH_DEPTH)
        saida.append(("merge_nao_stale <- clone --depth 1 (tem de REPROVAR pelo historico raso)", ok,
                      detalhe))
        try:
            _sha, _base, atrasados_cheio, _m = _merge_stale(cheio)
            erro_cheio = ""
        except Exception as exc:                                         # noqa: BLE001
            atrasados_cheio, erro_cheio = [], f"{type(exc).__name__}: {exc}"
        ok = bool(atrasados_cheio) and not erro_cheio
        saida.append(("NEGATIVO: clone completo do MESMO repositorio nao cai na guarda de raso e ainda "
                      "acusa o atraso", ok,
                      erro_cheio or (f"acusou o atraso: {[p[:12] for p in atrasados_cheio]}" if ok
                                     else "nao acusou o atraso")))

        # 6-7. pr_base_atualizada exercitada PELA REGRA (nao por um atalho que nunca entra nela)
        repo = _repo_sintetico(raiz, em_dia=False)
        head_atrasado = git("rev-parse", "ramo", cwd=repo)
        base_atual = git("rev-parse", BASE_REF, cwd=repo)
        with _aponta(repo=repo, RX_PR_HEAD_SHA=head_atrasado, RX_PR_BASE_SHA=base_atual):
            ok, detalhe = _reprova_com(r_pr_base_atualizada, MARCA_PR_ATRASADO)
        saida.append(("pr_base_atualizada <- head atrasado (tem de REPROVAR)", ok, detalhe))
        repo_ok = _repo_sintetico(raiz, em_dia=True)
        with _aponta(repo=repo_ok, RX_PR_HEAD_SHA=git("rev-parse", "ramo", cwd=repo_ok),
                     RX_PR_BASE_SHA=git("rev-parse", BASE_REF + "~1", cwd=repo_ok)):
            ok, detalhe = _passa(r_pr_base_atualizada)
        saida.append(("pr_base_atualizada <- head em dia (tem de PASSAR)", ok, detalhe))

        # 8-9. o desvio "estou em PR" nao pode virar chave de desligar o merge_nao_stale
        with _aponta(repo=repo, RX_PR_HEAD_SHA="0" * 40):
            ok, detalhe = _reprova_com(r_merge_nao_stale, MARCA_HEAD_PR)
        saida.append(("merge_nao_stale <- RX_PR_HEAD_SHA que nao existe no clone (tem de REPROVAR)", ok,
                      detalhe))
        with _aponta(repo=repo, RX_PR_HEAD_SHA=head_atrasado):
            ok, detalhe = _passa(r_merge_nao_stale)
        saida.append(("merge_nao_stale <- head de PR real (desvia para a regra do PR)", ok, detalhe))

        # 10-12. mutacoes do YAML: cada linha que sustenta a trava, apagada, tem de REPROVAR com a sua marca
        texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore") if WORKFLOW.exists() else ""
        mutacoes = (
            ("chamada removida", texto.replace(CHAMADA, "scripts/nao_existe.py"), (MARCA_SEM_CHAMADA,)),
            ("fetch-depth: 0 removido", re.sub(r"(?m)^[ \t]*fetch-depth:[ \t]*0[ \t]*(#.*)?\n", "", texto),
             (MARCA_FETCH_DEPTH,)),
            ("gatilho pull_request removido", texto.replace("\n  pull_request:", "\n  pull_request_x:"),
             (MARCA_SEM_GATILHO,)),
        )
        for nome, mutado_txt, marcas in mutacoes:
            if mutado_txt == texto:
                saida.append((f"ci_roda_a_trava <- {nome}", False,
                              "mutacao nao mudou o arquivo: a ancora da mutacao nao casa mais com o YAML"))
                continue
            # nome do arquivo saneado: ":" num caminho do Windows vira fluxo alternativo em vez de arquivo
            alvo = raiz / f"quality-gate_{re.sub(r'[^a-z0-9]+', '-', nome.lower()).strip('-')}.yml"
            alvo.write_text(mutado_txt, encoding="utf-8", newline="\n")
            with _aponta(workflow=alvo):
                ok, detalhe = _reprova_com(r_ci_roda_a_trava, *marcas)
            saida.append((f"ci_roda_a_trava <- {nome} (tem de REPROVAR)", ok, detalhe))
    return saida


def main() -> int:
    args = sys.argv[1:]
    falhas: list[str] = []
    for nome, fn in RULES.items():
        try:
            print(f"PASSA {nome}: {fn()}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FALHA {nome}: {type(exc).__name__}: {exc}", flush=True)
            falhas.append(nome)
    if "--sem-controles" not in args:
        controlados = controles()
        if not controlados:
            print("FALHA controles: nenhum controle rodou (instrumento quebrado)", flush=True)
            falhas.append("controles:vazio")
        for titulo, ok, detalhe in controlados:
            print(f"{'CONTROLE_OK' if ok else 'CONTROLE_FALHOU'} {titulo}: {detalhe[:200]}", flush=True)
            if not ok:
                falhas.append(f"controle:{titulo}")
    if falhas:
        print(f"RX_BASE_ATUALIZADA_GATE=FALHA {falhas}", flush=True)
        return 1
    print(f"RX_BASE_ATUALIZADA_GATE=PASS regras={len(RULES)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
