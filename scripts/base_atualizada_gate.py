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
  * `ci_roda_a_trava` - o fluxo de trabalho tem de chamar esta trava nos dois momentos. Portao que some do
    CI vira arquivo morto sem ninguem notar.
NAO promete impedir o merge: o check do PR e uma fotografia do instante em que rodou, e ninguem re-roda o CI
de um PR parado quando outro PR entra. Quem IMPEDE e a protecao de branch do GitHub com "require branches to
be up to date before merging" (ligar e decisao do dono, porque muda quem consegue mesclar). Ate la a ordem
escrita vale: atualizar o ramo com a main, esperar o CI novo, e so entao mesclar.

Controles positivos por mutacao: cada regra e exercitada num repositorio git sintetico montado na hora, na
configuracao em que ela vai rodar - um com o merge atrasado (tem de REPROVAR), um com o ramo em dia (tem de
PASSAR), e a mutacao do YAML que tira a chamada do CI (tem de REPROVAR). Instrumento sem controle mede o
proprio otimismo.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("RX_BASE_ATU_ROOT") or Path(__file__).resolve().parents[1])
WORKFLOW = Path(os.environ.get("RX_BASE_ATU_WORKFLOW") or (ROOT / ".github/workflows/quality-gate.yml"))
BASE_REF = os.environ.get("RX_BASE_REF", "main")
RULES: dict[str, callable] = {}


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


def _merge_stale(repo: Path, commit: str = "HEAD") -> tuple[str, list[str]]:
    """Devolve (sha do commit, pais que NAO continham a ponta anterior). Levanta se o historico nao veio."""
    pais = git("rev-list", "--parents", "-n", "1", commit, cwd=repo).split()
    sha, pais = pais[0], pais[1:]
    if len(pais) < 2:
        return sha, []                      # nao e merge: nada a conferir
    for p in pais:
        # clone raso (fetch-depth padrao) nao tem os pais: passar aqui seria dizer "esta em dia" sem olhar
        proc = subprocess.run(["git", "cat-file", "-e", f"{p}^{{commit}}"], cwd=str(repo),
                              capture_output=True, timeout=120)
        if proc.returncode:
            fail(f"instrumento quebrado: o commit pai {p[:12]} nao esta no clone (historico raso). "
                 "O passo precisa de actions/checkout com fetch-depth: 0.")
    base = pais[0]
    atrasados = [p for p in pais[1:] if not contem(base, p, cwd=repo)]
    return sha, atrasados


@regra("merge_nao_stale")
def r_merge_nao_stale() -> str:
    """O merge que esta na ponta entrou sobre a base em que o CI do ramo rodou (ou sobre uma mais nova)."""
    # Em PR o checkout entrega o merge automatico do GitHub (base + head), e quem responde por ele e a regra
    # do PR, com a ponta da base lida ao vivo. Conferir os pais aqui diria a mesma coisa com a palavra errada.
    if (os.environ.get("RX_PR_HEAD_SHA") or "").strip():
        return "em PR: quem confere e pr_base_atualizada"
    sha, atrasados = _merge_stale(ROOT)
    if atrasados:
        base = git("rev-list", "--parents", "-n", "1", sha).split()[1]
        fail(f"merge sobre base desatualizada: {sha[:12]} juntou um ramo que NAO continha {base[:12]}, a "
             f"ponta da `{BASE_REF}` de antes do merge ({', '.join(p[:12] for p in atrasados)}). O verde "
             "daquele ramo foi medido em outra arvore e nao vale para esta. Antes de mesclar: atualizar o "
             f"ramo com a `{BASE_REF}`, esperar o CI novo, mesclar so entao.")
    if not atrasados and len(git("rev-list", "--parents", "-n", "1", sha).split()) < 3:
        return f"{sha[:12]} nao e merge (nada a conferir)"
    return f"{sha[:12]} e merge e o ramo continha a ponta anterior da `{BASE_REF}`"


@regra("pr_base_atualizada")
def r_pr_base_atualizada() -> str:
    """No PR: o head tem de conter a ponta ATUAL da base. Fora de PR, nao ha o que conferir."""
    head = (os.environ.get("RX_PR_HEAD_SHA") or "").strip()
    if not head:
        return "fora de PR (sem RX_PR_HEAD_SHA)"
    base_sha = (os.environ.get("RX_PR_BASE_SHA") or "").strip()
    if not base_sha:
        # sem --depth: um fetch raso num clone completo cria fronteira e o merge-base passa a mentir
        git("fetch", "--no-tags", "origin", BASE_REF)
        base_sha = git("rev-parse", "FETCH_HEAD")
    if not contem(base_sha, head):
        fail(f"ramo do PR atrasado: o head {head[:12]} nao contem {base_sha[:12]}, a ponta atual da "
             f"`{BASE_REF}`. O verde deste CI seria sobre uma base que nao e a do merge. Atualizar o ramo "
             f"(`git merge origin/{BASE_REF}` ou rebase) e deixar o CI rodar de novo.")
    return f"head {head[:12]} contem a ponta atual da `{BASE_REF}` ({base_sha[:12]})"


@regra("ci_roda_a_trava")
def r_ci_roda_a_trava() -> str:
    """O fluxo de trabalho chama esta trava nos dois momentos: no push da base e no PR."""
    if not WORKFLOW.exists():
        fail(f"instrumento quebrado: fluxo de trabalho nao encontrado em {WORKFLOW}")
    texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore")
    chamada = "scripts/base_atualizada_gate.py"
    if chamada not in texto:
        fail(f"o CI nao chama {chamada}: a trava de base atualizada sairia do caminho sem ninguem notar")
    faltando = [g for g in ("push:", "pull_request:") if g not in texto]
    if faltando:
        fail(f"o fluxo de trabalho nao dispara em {', '.join(faltando)}: a trava so vale se rodar no PR "
             "(ramo atrasado) e no push da base (merge ja feito sobre base velha)")
    return f"{WORKFLOW.name} chama a trava e dispara em push e pull_request"


# ------------------------------------------------------------------ controles positivos por construcao
_SEQ = [0]


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
    base_antiga = git("rev-parse", "HEAD", cwd=repo)
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
    assert base_antiga
    return repo


def controles() -> list[tuple[str, bool, str]]:
    saida: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="rx_base_atu_") as td:
        raiz = Path(td)
        for em_dia in (False, True):
            repo = _repo_sintetico(raiz, em_dia)
            try:
                _sha, atrasados = _merge_stale(repo)
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
        # clone raso: nao pode passar por falta de historico
        ok = False
        try:
            _merge_stale(repo, "0" * 40)
        except Reprova:
            ok = True
        except RuntimeError:
            ok = True                      # git recusa o sha inexistente: tambem nao passa em silencio
        saida.append(("merge_nao_stale <- commit ausente (nao pode passar calado)", ok,
                      "reprovou" if ok else "PASSOU sem historico"))
        # PR com o ramo atrasado tem de reprovar
        repo = _repo_sintetico(raiz, em_dia=False)
        head = git("rev-parse", "ramo", cwd=repo)
        base = git("rev-parse", BASE_REF, cwd=repo)
        ok = not contem(base, head, cwd=repo)
        saida.append(("pr_base_atualizada <- head atrasado (tem de REPROVAR)", ok,
                      "head nao contem a base" if ok else "head aceito com a base a frente"))
        # mutacao do YAML: sem a chamada, a regra do CI reprova
        mutado = raiz / "quality-gate.yml"
        texto = WORKFLOW.read_text(encoding="utf-8", errors="ignore") if WORKFLOW.exists() else ""
        mutado.write_text(texto.replace("scripts/base_atualizada_gate.py", "scripts/nao_existe.py"),
                          encoding="utf-8", newline="\n")
        anterior = globals()["WORKFLOW"]
        globals()["WORKFLOW"] = mutado
        try:
            r_ci_roda_a_trava()
            ok, detalhe = False, "PASSOU sem a chamada no CI"
        except Reprova as exc:
            ok, detalhe = True, str(exc)[:120]
        finally:
            globals()["WORKFLOW"] = anterior
        saida.append(("ci_roda_a_trava <- chamada removida (tem de REPROVAR)", ok, detalhe))
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
        for titulo, ok, detalhe in controles():
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
