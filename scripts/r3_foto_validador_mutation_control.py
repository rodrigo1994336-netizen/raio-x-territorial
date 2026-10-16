"""Controle positivo do portão R3: cada mutação quebra uma regra, e o portão TEM de reprovar.

Regra que não enxerga o próprio defeito não prova nada. Aqui cada defeito possível entra
de volta no código, um por vez, e o portão roda inteiro contra a cópia mutada.

A cópia mutada entra na FRENTE do caminho de importação, e o portão foi escrito para
importar os módulos de lá (a raiz do repositório entra no fim do caminho). Sem isso, o
controle mediria sempre o arquivo original e passaria verde sem ter testado nada.

Uso manual, fora do CI (roda o portão uma vez por mutação):

    RX_RELEASE=OFF PYTHONPATH=. python scripts/r3_foto_validador_mutation_control.py

Termina com RX_R3_MUTACOES=ALL_CAUGHT (saída 0) ou com a lista das que passaram
despercebidas (saída 1).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAO = "scripts/r3_foto_validador_gate.py"

# nome da mutação -> (arquivo, [(trecho original, trecho mutado)])
MUTACOES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # ---------------------------------------------------------------- foto limpa
    "M01_fonte_escreve_a_data_na_imagem": ("sentinel_limpo.py", [(
        '    Image.fromarray(rgb, "RGB").save(caminho_cor, "JPEG", quality=qualidade, optimize=True)',
        '    from PIL import ImageDraw\n'
        '    _im = Image.fromarray(rgb, "RGB")\n'
        '    ImageDraw.Draw(_im).text((12, 12), "SENTINEL-2", fill=(255, 255, 255))\n'
        '    _im.save(caminho_cor, "JPEG", quality=qualidade, optimize=True)')],),
    "M02_fonte_grava_o_contorno_amarelo": ("sentinel_limpo.py", [(
        '    rgb = esticar_cor_natural(vermelho, verde, azul, valido)',
        '    rgb = esticar_cor_natural(vermelho, verde, azul, valido)\n'
        '    rgb[0, :] = [255, 238, 70]\n'
        '    rgb[:, 0] = [255, 238, 70]')],),
    "M03_verdor_pinta_classe_errada": ("sentinel_limpo.py", [(
        "        pintura[faixa] = cor", "        pintura[faixa] = cor[::-1]")],),
    "M04_contraste_banda_a_banda": ("sentinel_limpo.py", [(
        '    juntas = np.concatenate([x[valido & np.isfinite(x) & (x > 0)] for x in (vermelho, verde, azul)])',
        '    juntas = vermelho[valido & np.isfinite(vermelho) & (vermelho > 0)]')],),
    "M04b_cada_banda_com_o_proprio_corte": ("sentinel_limpo.py", [(
        '        x = np.clip((banda - baixo) / (alto - baixo), 0, 1)',
        '        _b = banda[valido & np.isfinite(banda) & (banda > 0)]\n'
        '        _lo = float(np.percentile(_b, 2)) if _b.size else baixo\n'
        '        _hi = float(np.percentile(_b, 98)) if _b.size else alto\n'
        '        x = np.clip((banda - _lo) / max(_hi - _lo, 1), 0, 1)')],),
    "M05_escolhe_a_mais_limpa_em_vez_da_mais_recente": ("sentinel_limpo.py", [(
        '            grupo.sort(key=lambda x: _iso_da_cena(x.get("properties") or {}), reverse=True)',
        '            grupo.sort(key=lambda x: _nuvem(x.get("properties") or {}) or 999)')],),
    "M06_aceita_cena_sem_data": ("sentinel_limpo.py", [(
        '    validas = [x for x in itens or [] if _iso_da_cena((x.get("properties") or {}))[:10]]',
        '    validas = list(itens or [])')],),
    "M07_sem_cena_devolve_escolha_vazia_como_boa": ("sentinel_limpo.py", [(
        '    return [], "nenhuma_cena"', '    return [], "nuvem_ate_12"')],),
    "M08_pendencia_sem_a_marca_de_pendente": ("sentinel_limpo.py", [(
        '    return {"ok": False, "pendente": True, "fonte": FONTE, "detalhe": detalhe,\n'
        '            "mensagem": "Imagem de satélite: consulta pendente nesta emissão."}',
        '    return {"ok": False, "fonte": FONTE, "detalhe": detalhe,\n'
        '            "mensagem": "Imagem de satélite indisponível."}')],),
    "M09_cena_velha_nao_avisa": ("sentinel_limpo.py", [(
        '    if antiga and nublada:', '    if False:'),
        ('    if antiga:\n        return f"Esta é a cena limpa mais recente disponível: ela tem {idade_dias} dias."',
         '    if False:\n        return ""')],),
    "M10_cena_boa_ganha_aviso_inventado": ("sentinel_limpo.py", [(
        '    if nublada:\n        return "A cena mais recente disponível ainda traz nuvem sobre parte da área."\n    return None',
        '    return "A cena mais recente disponível ainda traz nuvem sobre parte da área."')],),
    "M11_legenda_sem_a_ressalva": ("sentinel_limpo.py", [(
        'LEGENDA_NOTA = ("Verdor medido na cena de {data}: mede quanto a folhagem reflete naquele dia. "\n'
        '                "Não mede produtividade, não diz a espécie e não confirma safra.")',
        'LEGENDA_NOTA = "Verdor medido na cena de {data}."')],),
    "M12_legenda_sem_data_inventa_data": ("sentinel_limpo.py", [(
        '    if data_br:', '    if True:\n        data_br = data_br or "01/01/2026"')],),
    # ---------------------------------------------------------------- desenho por cima
    "M13_relatorio_escreve_na_foto": ("foto_limpa_no_relatorio.py", [(
        '        canvas.drawImage(self.caminho, 0, 0, width=self.largura, height=self.altura)',
        '        canvas.drawImage(self.caminho, 0, 0, width=self.largura, height=self.altura)\n'
        '        canvas.setFont("Helvetica", 9)\n'
        '        canvas.drawString(8, 8, "Sentinel-2")')],),
    "M14_limite_nao_e_desenhado": ("foto_limpa_no_relatorio.py", [(
        '        if not self.limite_px:\n            return', '        if True:\n            return')],),
    "M15_foto_pendente_some_do_relatorio": ("foto_limpa_no_relatorio.py", [(
        '    if not meta.get("ok"):\n        return [v5.P(PENDENTE, "small")]',
        '    if not meta.get("ok"):\n        return []')],),
    "M16_legenda_do_verdor_some": ("foto_limpa_no_relatorio.py", [(
        '    verdor = meta.get("verdor_path")\n    if verdor and Path(str(verdor)).exists():',
        '    verdor = meta.get("verdor_path")\n    if False:')],),
    # ---------------------------------------------------------------- caminho das imagens
    "M17_lente_fora_do_caminho_real": ("visual_hybrid.py", [(
        "        _timed('foto_limpa',build_clean_sentinel_photo(car_geometry,out_path.parent)),\n", ""),
        ("    high,sentinel,limpa=await asyncio.gather(", "    high,sentinel=await asyncio.gather("),
        ("        'foto_limpa':limpa,\n", "")],),
    "M18_lente_sem_cronometro": ("visual_hybrid.py", [(
        "_timed('foto_limpa',build_clean_sentinel_photo(car_geometry,out_path.parent))",
        "build_clean_sentinel_photo(car_geometry,out_path.parent)")],),
    # ---------------------------------------------------------------- código do documento
    "M19_contrato_do_formato_mudou_calado": ("report_validacao_codigo.py", [(
        '    ("embargo", "Áreas embargadas do IBAMA"),',
        '    ("embargo_novo", "Áreas embargadas do IBAMA"),')],),
    "M20_assinatura_nao_e_conferida": ("report_validacao_codigo.py", [(
        '    if not hmac.compare_digest(_mac(dados).to_bytes(6, "big"), assinatura.to_bytes(6, "big")):',
        '    if False:')],),
    "M21_sem_segredo_usa_um_padrao": ("report_validacao_codigo.py", [(
        '    if len(bruto) < SEGREDO_MINIMO:\n        return None',
        '    if len(bruto) < SEGREDO_MINIMO:\n        return b"raio-x-territorial-segredo-padrao"')],),
    "M22_sem_segredo_acusa_o_codigo": ("report_validacao_codigo.py", [(
        '        return {"ok": False, "estado": "indisponivel", "motivo": "sem_segredo"}',
        '        return _invalido("sem_segredo")')],),
    "M23_pendencia_nao_registrada_vira_nenhuma": ("report_validacao_codigo.py", [(
        '    registrou = bool((dados >> 15) & 1)', '    registrou = True')],),
    "M24_rotulo_desconhecido_some": ("report_validacao_codigo.py", [(
        '        if achado is None:\n            outras = True',
        '        if achado is None:\n            pass')],),
    "M25_hora_em_utc": ("report_validacao_codigo.py", [(
        '    momento = (EPOCA + timedelta(minutes=minuto)).astimezone(BRT)',
        '    momento = EPOCA + timedelta(minutes=minuto)')],),
    "M26_nao_aceita_letra_confundida": ("report_validacao_codigo.py", [(
        '    texto = "".join(_CONFUSAO.get(ch, ch) for ch in texto)', '    texto = texto')],),
    # ---------------------------------------------------------------- página pública
    "M27_sem_limite_de_tentativas": ("validacao_publica.py", [(
        '        if espera <= 0:\n            marcas.append(agora)',
        '        espera = 0\n        marcas.append(agora)')],),
    "M28_origem_vem_do_encaminhamento_inventado": ("validacao_publica.py", [(
        '        return partes[-1][:60]', '        return partes[0][:60]')],),
    "M29_pagina_roda_script": ("validacao_publica.py", [(
        '        f"<style>{_ESTILO}</style></head><body><div class=\\"folha\\">"',
        '        f"<style>{_ESTILO}</style><script>var x=1</script></head><body><div class=\\"folha\\">"')],),
    "M30_pagina_com_jargao": ("validacao_publica.py", [(
        '        "<p>Digite o código impresso no fim do relatório, ou aponte a câmera para o QR que "',
        '        "<p>O endpoint do backend confere o payload em cache; digite o código do relatório "')],),
    "M30b_jargao_no_texto_impresso": ("report_validacao_pdf.py", [(
        '    "Qualquer pessoa confere este relatório sozinha, sem conta e sem pedir nada a ninguém: "',
        '    "O endpoint de validação confere o payload assinado em cache; qualquer pessoa usa: "')],),
    "M31_pagina_nao_separa_documento_de_fonte": ("validacao_publica.py", [(
        '_RODAPE = (\n    "Esta página confere a autenticidade do documento: se o código foi emitido pelo "\n'
        '    "Raio-X Territorial e se nada nele foi alterado depois da emissão. Ela não repete as "\n'
        '    "consultas às fontes oficiais e não diz nada sobre o conteúdo delas."\n)',
        '_RODAPE = "Conferência do Raio-X Territorial."')],),
    "M32_resposta_de_maquina_entrega_o_motivo": ("validacao_publica.py", [(
        '    corpo = {k: v for k, v in resultado.items() if k != "motivo"}', '    corpo = dict(resultado)')],),
    "M33_pagina_pode_ficar_guardada": ("validacao_publica.py", [(
        '    "cache-control": "no-store",\n}', '}')],),
    "M34_pagina_sem_segredo_acusa": ("validacao_publica.py", [(
        '    if estado == "indisponivel":',
        '    if estado == "indisponivel" and False:')],),
    # ---------------------------------------------------------------- impressão no PDF
    "M35_pdf_promete_sem_poder_conferir": ("report_validacao_pdf.py", [(
        '    if not emitido.get("ok"):\n        return []',
        '    if not emitido.get("ok"):\n        return [Spacer(1, 5 * mm),\n'
        '                Table([[v5.P(TITULO, "cellb")]], colWidths=[165 * mm])]')],),
    "M36_codigo_estoura_a_linha": ("report_validacao_pdf.py", [(
        "CORPO_DO_CODIGO = 8.2", "CORPO_DO_CODIGO = 20.0")],),
    "M36b_celula_estreita_demais": ("report_validacao_pdf.py", [(
        "LARGURA_DO_CODIGO = 122 * mm", "LARGURA_DO_CODIGO = 30 * mm")],),
    "M37_bloco_de_autenticidade_nao_entra": ("report_blocos_r3.py", [(
        '        if title == SECAO_DA_AUTENTICIDADE:\n            return blocos + bloco_de_autenticidade(payload)',
        '        if False:\n            return blocos')],),
    "M38_foto_nao_entra_no_relatorio": ("report_blocos_r3.py", [(
        '        if title == SECAO_DA_FOTO:\n            return blocos + blocos_da_foto_limpa(payload)',
        '        if False:\n            return blocos')],),
}


def rodar(caminho_python: list[str]) -> tuple[int, str]:
    ambiente = dict(os.environ, PYTHONIOENCODING="utf-8", RX_RELEASE="OFF",
                    PYTHONPATH=os.pathsep.join(caminho_python))
    processo = subprocess.run([sys.executable, PORTAO], cwd=ROOT, env=ambiente, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=900)
    linhas = [x for x in (processo.stdout + processo.stderr).strip().splitlines()
              if x.startswith(("FALHA", "RX_R3_FOTO_VALIDADOR_GATE=", "Traceback"))]
    return processo.returncode, (linhas[0] if linhas else "")


def main() -> int:
    escolhidas = dict(MUTACOES)
    if len(sys.argv) > 1:  # repetir só o que acabou de ser corrigido, sem rodar as 40
        escolhidas = {k: v for k, v in MUTACOES.items() if any(x in k for x in sys.argv[1:])}
        assert escolhidas, f"nenhuma mutação casa com {sys.argv[1:]}"
    herdado = [x for x in (os.environ.get("PYTHONPATH") or "").split(os.pathsep) if x and x != "."]
    base = [str(ROOT)] + herdado
    codigo, ultima = rodar(base)
    print(f"SEM_MUTACAO exit={codigo} :: {ultima}", flush=True)
    if codigo != 0:
        print("RX_R3_MUTACOES=BASELINE_FALHA")
        return 1

    passaram: list[str] = []
    with tempfile.TemporaryDirectory(prefix="r3_mut_") as temporario:
        for nome, (arquivo, edicoes) in escolhidas.items():
            fonte = (ROOT / arquivo).read_text(encoding="utf-8")
            mutado = fonte
            for velho, novo in edicoes:
                if mutado.count(velho) != 1:
                    print(f"{nome} ÂNCORA_INVÁLIDA ({mutado.count(velho)} ocorrências em {arquivo})")
                    passaram.append(nome + "(âncora)")
                    mutado = None
                    break
                mutado = mutado.replace(velho, novo)
            if mutado is None:
                continue
            pasta = Path(temporario) / nome
            pasta.mkdir()
            (pasta / arquivo).write_text(mutado, encoding="utf-8", newline="\n")
            codigo, ultima = rodar([str(pasta)] + base)
            pegou = codigo != 0
            if not pegou:
                passaram.append(nome)
            print(f"{nome} exit={codigo} {'REPROVADA' if pegou else 'PASSOU_DESPERCEBIDA'} :: {ultima[:140]}",
                  flush=True)
    if passaram:
        print("RX_R3_MUTACOES=NAO_PEGAS:" + ",".join(passaram))
        return 1
    print(f"RX_R3_MUTACOES=ALL_CAUGHT total={len(escolhidas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
